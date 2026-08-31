"""Тесты на app_settings — одна модель настроек поверх AppConfig (В3)."""
import json
import os
import stat
from unittest import mock

import pytest

from corpus_builder.app_settings import (REDACTED_SECRET, AppSettings,
                                         OVERRIDE_ALL, OVERRIDE_FILE, is_secret_field)


def _file(tmp_path):
    target = tmp_path / "settings.json"
    mock.patch.object(AppSettings, "_settings_file",
                      classmethod(lambda cls: target)).start()
    return target


# ------------------------------------------------------------- дефолты и пути

def test_defaults_come_from_the_engine_model():
    """Дефолт настройки = дефолт `AppConfig`: второй копии больше нет (В3)."""
    from corpus_builder.models import OutputConfig, QualityConfig

    s = AppSettings()
    assert s.get("output.request_delay") == OutputConfig.model_fields["request_delay"].default
    assert s.get("quality.min_chars") == QualityConfig().min_chars
    assert s.get("ui.theme") == "dark"
    assert s.changed() == set(), "в чистых настройках не должно быть никаких «явно заданных»"


def test_paths_are_appconfig_paths():
    s = AppSettings()
    s.set("crawlers.pdf.ocr_enabled", False)
    s.set("dedup.streaming", True)
    assert s.get("crawlers.pdf.ocr_enabled") is False
    assert "crawlers.pdf.ocr_enabled" in s.changed()
    with pytest.raises(KeyError):
        s.set("crawlers.pdf.нет_такого", 1)
    with pytest.raises(ValueError):
        s.set("quality.min_chars", "много")


@pytest.mark.parametrize("value", ["ten", None, [], "10秒"])
def test_bad_value_is_rejected_not_swallowed(value):
    """Тип проверяет pydantic движка; «потом упадёт в краулинге» — не вариант."""
    s = AppSettings()
    with pytest.raises((ValueError,)):
        s.set("quality.min_chars", value)


def test_set_never_stores_a_second_copy_of_the_value():
    """Настройки и конфиг — разные объекты, но один класс на секцию (В3)."""
    from corpus_builder.models import GuiEngineConfig, QualityConfig

    s = AppSettings()
    assert isinstance(s.engine.quality, QualityConfig)
    assert type(s.engine.quality) is type(GuiEngineConfig().quality)


# ------------------------------------------------------------ сохранение/чтение

def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.undo()
    target = _file(tmp_path)
    s = AppSettings()
    s.set("output.request_delay", 5.0)
    s.set("quality.min_chars", 500)
    s.set("crawlers.github.branch", "dev")
    s.secrets.github_token = "ghp_test123"
    s.save()

    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["format"] == 2
    assert doc["engine"]["output"]["request_delay"] == 5.0, "движок пишется разреженно"
    assert "corpus_file" not in doc["engine"].get("output", {}), "пути прогона не храним"
    assert doc["secrets"]["github_token"] == "ghp_test123"

    loaded = AppSettings.load()
    assert loaded.get("output.request_delay") == 5.0
    assert loaded.get("quality.min_chars") == 500
    assert loaded.get("crawlers.github.branch") == "dev"
    assert loaded.changed() == {"output.request_delay", "quality.min_chars",
                                "crawlers.github.branch"}, "провенанс должен пережить запись"


def test_saved_defaults_do_not_become_user_choice(tmp_path, monkeypatch):
    """Главная инвариант В3: 70 сохранённых полей ≠ 70 «изменённых» полей."""
    monkeypatch.undo()
    _file(tmp_path)
    s = AppSettings()
    for _ in range(3):
        s.save()
        s = AppSettings.load()
    assert s.changed() == set()


def test_settings_file_is_owner_only(tmp_path, monkeypatch):
    """В2: в файле лежат токены → 0600, даже если файл был 0644."""
    monkeypatch.undo()
    target = _file(tmp_path)
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o644)
    s = AppSettings()
    s.secrets.github_token = "ghp_secret"
    s.save()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert AppSettings.load().secrets.github_token == "ghp_secret"


def test_broken_file_falls_back_to_clean_defaults(tmp_path, monkeypatch):
    monkeypatch.undo()
    target = _file(tmp_path)
    target.write_text("{ это не json", encoding="utf-8")
    assert AppSettings.load().changed() == set()
    target.write_text(json.dumps({"format": 2, "ui": {}, "engine": {"quality": {"min_chars": "x"}}}),
                       encoding="utf-8")
    assert AppSettings.load().changed() == set(), "битые значения не должны «жить»"


def test_unknown_paths_in_file_are_dropped(tmp_path, monkeypatch):
    monkeypatch.undo()
    target = _file(tmp_path)
    target.write_text(json.dumps({"format": 2, "engine": {"quality": {"no_such": 1},
                                                          "no_section": {"a": 1}}}),
                      encoding="utf-8")
    s = AppSettings.load()
    assert s.changed() == set()


# ------------------------------------------------------------------- применение

def _cfg(**over):
    from corpus_builder.models import AppConfig, SourceItem
    base = {"corpus_file": "o.jsonl", "download_dir": "d"}
    base.update(over.pop("output", {}))
    return AppConfig(sources=[SourceItem(url="http://x", type="html")],
                     output=base, **over)


def test_applying_clean_settings_changes_nothing():
    cfg = _cfg(output={"request_delay": 0.2}, quality={"min_chars": 5000})
    before = (cfg.output.request_delay, cfg.quality.min_chars)
    assert AppSettings().apply_to_config(cfg) == []
    assert (cfg.output.request_delay, cfg.quality.min_chars) == before, \
        "значения из config.yaml перезаписаны дефолтами GUI"


def test_applying_changed_settings_overrides_only_those_paths():
    cfg = _cfg(output={"request_delay": 0.2}, quality={"min_chars": 5000})
    s = AppSettings()
    s.set("output.request_timeout", 90)
    s.set("quality.min_chars", 123)
    applied = s.apply_to_config(cfg)
    assert sorted(applied) == ["output.request_timeout", "quality.min_chars"]
    assert cfg.quality.min_chars == 123
    assert cfg.output.request_delay == 0.2, "не тронутое файлом поле не трогается"


def test_mode_file_ignores_gui_settings_entirely():
    cfg = _cfg(output={"request_delay": 9.5})
    s = AppSettings()
    s.set("output.request_delay", 0.0)
    s.ui.override_mode = OVERRIDE_FILE
    assert s.apply_to_config(cfg) == []
    assert cfg.output.request_delay == 9.5


def test_mode_all_applies_defaults_too_for_legacy_users():
    cfg = _cfg(output={"request_delay": 9.5})
    s = AppSettings()
    s.ui.override_mode = OVERRIDE_ALL
    applied = s.apply_to_config(cfg)
    assert "output.request_delay" in applied
    assert cfg.output.request_delay == s.get("output.request_delay")


def test_reset_returns_a_field_to_the_file(tmp_path):
    """«Взять из config.yaml» — то, чего не хватало в п.1 (кнопка в диалоге)."""
    from corpus_builder.config import load_config

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text('sources:\n  - {url: "http://x", type: html}\n'
                        'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl"}\n'
                        'quality: {min_chars: 5000}\n', encoding="utf-8")
    s = AppSettings()
    s.set("quality.min_chars", 10)
    cfg = load_config(cfg_file)
    s.apply_to_config(cfg)
    assert cfg.quality.min_chars == 10
    assert s.reset("quality.min_chars") is True
    cfg2 = load_config(cfg_file)                  # новый прогон: файл снова важнее
    s.apply_to_config(cfg2)
    assert cfg2.quality.min_chars == 5000
    assert s.changed() == set()
    assert s.reset("quality.min_chars") is False, "второй reset — no-op"
    assert s.reset_all() == 0


def test_reset_all_clears_every_override():
    cfg = _cfg(quality={"min_chars": 5000}, output={"request_delay": 0.2})
    s = AppSettings()
    s.set("quality.min_chars", 1)
    s.set("pipeline.per_url_timeout_minutes", 0.5)
    assert s.reset_all() == 2
    s.apply_to_config(cfg)
    assert cfg.quality.min_chars == 5000


# ------------------------------------------------------------------ конфликт

def test_conflicts_show_file_value_and_gui_value(tmp_path):
    from corpus_builder.config import load_config
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text('sources:\n  - {url: "http://x", type: html}\n'
                        'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl", '
                        'request_delay: 7.5}\nquality: {min_chars: 5000}\n', encoding="utf-8")
    cfg = load_config(cfg_file)
    s = AppSettings()
    s.set("quality.min_chars", 800)
    s.set("output.request_delay", 0.7)
    s.set("pipeline.per_url_timeout_minutes", 3.0)       # в файле этого нет
    conflicts = dict((p, (a, b)) for p, a, b in s.conflicts_with(cfg))
    assert conflicts["quality.min_chars"] == (5000, 800)
    assert conflicts["output.request_delay"] == (7.5, 0.7)
    assert "pipeline.per_url_timeout_minutes" not in conflicts, \
        "то, чего в файле не было, конфликтом не считается"
    # значения файла не «плывут» после того, как настройки уже наложены
    s.apply_to_config(cfg)
    assert dict((p, (a, b)) for p, a, b in s.conflicts_with(cfg))["quality.min_chars"] \
        == (5000, 800)


def test_paths_of_the_run_are_never_overridable(tmp_path):
    cfg = _cfg()
    s = AppSettings()
    s.set("output.corpus_file", "/tmp/подмена.jsonl")     # явного пути в файле не было
    assert "output.corpus_file" not in s.changed(), "пути прогона — не настройка GUI"
    s.apply_to_config(cfg)
    assert cfg.output.corpus_file == "o.jsonl"


# ---------------------------------------------------------------- секреты / ui

def test_secrets_never_reach_the_engine_config():
    cfg = _cfg()
    s = AppSettings()
    s.secrets.github_token = "ghp_topsecret"
    s.secrets.proxy_list = "http://p:1"
    assert s.apply_to_config(cfg) == []
    assert "crawlers.github.token" not in s.changed()
    blob = json.dumps(s.to_export_dict())
    assert "ghp_topsecret" not in blob, "экспорт настроек не должен уносить токен"


def test_secret_field_detection_is_narrow():
    assert is_secret_field("token") and is_secret_field("api_key")
    assert is_secret_field("client_secret") and is_secret_field("github_access_token")
    assert not is_secret_field("url") and not is_secret_field("max_workers")
    assert not is_secret_field("token_env"), "имя переменной окружения — не секрет"


def test_importing_redacted_export_keeps_real_token(tmp_path, monkeypatch):
    monkeypatch.undo()
    _file(tmp_path)
    s = AppSettings()
    s.secrets.github_token = "ghp_keepme"
    s.save()
    exported = s.to_export_dict()
    assert exported["secrets"]["github_token"] == REDACTED_SECRET
    loaded = AppSettings.from_dict(exported)
    assert loaded.secrets.github_token == "", "заглушка не должна стать токеном"
    full = s.to_export_dict(redact=False)
    assert AppSettings.from_dict(full).secrets.github_token == "ghp_keepme"


def test_setup_env_vars(tmp_path, monkeypatch):
    for key in ("GITHUB_TOKEN", "STACKEXCHANGE_KEY", "CORPUS_BUILDER_PROXIES"):
        monkeypatch.delenv(key, raising=False)
    s = AppSettings()
    s.secrets.github_token = "ghp_env_test"
    s.secrets.stackexchange_api_key = "se_env_test"
    s.secrets.proxy_list = "http://proxy1:8080,http://proxy2:8080"
    s.setup_env_vars()
    assert os.environ["GITHUB_TOKEN"] == "ghp_env_test"
    assert os.environ["STACKEXCHANGE_KEY"] == "se_env_test"
    assert os.environ["CORPUS_BUILDER_PROXIES"].startswith("http://proxy1")


def test_env_vars_are_not_set_for_empty_secrets(monkeypatch):
    for key in ("GITHUB_TOKEN", "STACKEXCHANGE_KEY", "CORPUS_BUILDER_PROXIES"):
        monkeypatch.delenv(key, raising=False)
    AppSettings().setup_env_vars()
    assert "GITHUB_TOKEN" not in os.environ


def test_ui_state_does_not_affect_the_engine():
    cfg = _cfg()
    s = AppSettings()
    s.ui.theme = "light"
    s.ui.window_width = 999
    s.ui.log_level = "DEBUG"
    assert s.apply_to_config(cfg) == []
    assert s.changed() == set()


def test_export_contains_a_readable_full_snapshot():
    s = AppSettings()
    s.set("quality.min_chars", 42)
    doc = s.to_export_dict()
    assert doc["engine"]["quality"]["min_chars"] == 42        # разреженно
    assert doc["ui"]["theme"]
    assert doc["secrets"]["github_token"] == REDACTED_SECRET or doc["secrets"]["github_token"] == ""
    assert doc["format"] == 2


# --------------------------------------------------------------- миграция v1

V1_FLAT = {
    "crawl": {"user_agent": "Legacy/1.0", "request_delay": 0.25, "request_timeout": 77,
              "proxy_list": "http://old:1", "per_url_timeout_minutes": 3},
    "async_crawl": {"enabled": True, "max_concurrent_total": 5},
    "html": {"extract_mode": "bs4", "image_extensions": "png,jpg"},
    "pdf": {"ocr_enabled": False},
    "github": {"token": "ghp_legacy", "crawl_issues": True},
    "stackexchange": {"api_key": "se_legacy", "max_questions": 33},
    "quality": {"min_chars": 1234, "languages_allowed": "ru,en,de"},
    "dedup": {"use_streaming": True},
    "export": {"gzip_output": True},
    "gui": {"theme": "light", "ui_overridden": ["quality.min_chars", "output.request_delay"]},
}


def test_v1_file_is_migrated_with_provenance():
    """Плоский файл v1 → разреженный v2: переносится то, что помечено, и всё, что
    отличается от дефолта движка, когда отметки нет."""
    s = AppSettings.from_dict(V1_FLAT)
    assert s.get("output.user_agent") == "Legacy/1.0"
    assert s.get("pipeline.use_async") is True
    assert s.get("crawlers.html.extract_mode") == "bs4"
    assert s.get("crawlers.html.image_extensions") == ["png", "jpg"], "CSV → список"
    assert s.get("quality.languages_allowed") == ["ru", "en", "de"]
    assert s.get("crawlers.stackexchange.max_list_questions") == 33
    assert s.get("dedup.streaming") is True
    assert s.get("export.write_gzip") is True
    assert s.secrets.github_token == "ghp_legacy"
    assert s.secrets.stackexchange_api_key == "se_legacy"
    assert s.secrets.proxy_list == "http://old:1"
    assert s.ui.theme == "light"
    # ui_overridden v1 ограничил перенос: request_timeout ≠ дефолт, но не помечен
    assert s.get("output.request_timeout") == 77
    assert {"output.request_delay", "quality.min_chars"} <= s.changed()


def test_v1_migration_without_marks_keeps_only_differences():
    data = {k: v for k, v in V1_FLAT.items() if k != "gui"}
    s = AppSettings.from_dict(data)
    assert "quality.min_chars" in s.changed(), "отличается от дефолта → переносим"
    assert "crawlers.github.crawl_wiki" not in s.changed(), "= дефолт → не переносим"
    assert s.legacy_notice, "пользователя надо предупредить об угадывании"


def test_v1_migration_marks_all_when_legacy_star():
    data = json.loads(json.dumps(V1_FLAT))
    data["gui"] = {"theme": "dark", "ui_overridden": ["*"]}
    s = AppSettings.from_dict(data)
    assert "output.save_missing" not in s.changed()
    assert "crawlers.pdf.ocr_lang" not in s.changed()     # не хранилось в v1 → не выдумываем
    assert "crawlers.pdf.ocr_enabled" in s.changed()


def test_v1_migration_survives_garbage():
    data = {k: v for k, v in V1_FLAT.items() if k != "gui"}
    data["crawl"]["request_delay"] = "не число"
    data["no_section"] = {"x": 1}
    s = AppSettings.from_dict(data)                      # не бросает
    assert s.get("output.request_delay") == 2.0, "битое поле → дефолт движка"
    assert s.get("quality.min_chars") == 1234


def test_v2_doc_does_not_trigger_migration_notice():
    s = AppSettings.from_dict({"format": 2, "ui": {}, "engine": {}})
    assert s.legacy_notice == []
    assert s.changed() == set()


def test_v1_marks_are_read_in_v1_names(tmp_path):
    """v1 писал `ui_overridden` в своих именах — миграция обязана их понять."""
    data = {"crawl": {"user_agent": "CorpusBuilder/0.2 (research)", "request_delay": 0.25},
            "html": {"extract_mode": "bs4"},
            "pdf": {"ocr_parallel_workers": 4},
            "gui": {"ui_overridden": ["crawl.user_agent", "html.extract_mode",
                                      "pdf.ocr_parallel_workers"]}}
    s = AppSettings.from_dict(data)
    assert s.changed() == {"output.user_agent", "crawlers.html.extract_mode",
                           "crawlers.pdf.ocr_parallel_workers", "output.request_delay"}


def test_v1_stale_gui_defaults_are_not_migrated_as_user_choice():
    """Старые дефолты GUI (их чинили отдельно) — не «выбор пользователя».

    Иначе миграция вернула бы ровно тот баг, ради которого всё переделывали:
    устаревший User-Agent и `use_browser_headers` молча перекрывали бы YAML.
    """
    data = {"crawl": {"user_agent": "CorpusBuilder/0.2 (research)",
                      "use_browser_headers": True, "request_delay": 0.25},
            "pdf": {"ocr_parallel_workers": 4},
            "stackexchange": {"min_score": 5, "max_questions": 100}}
    s = AppSettings.from_dict(data)
    assert s.changed() == {"output.request_delay"}, s.changed()
    marked = dict(data)
    marked["gui"] = {"ui_overridden": ["crawl.user_agent"]}
    assert "output.user_agent" in AppSettings.from_dict(marked).changed(), \
        "явно помеченное переносится"


def test_v1_real_world_file_migrates_without_inventing_choices():
    """Файл, реально написанный предыдущей версией программы (снимок с диска)."""
    payload = {"legacy_migration_notice": [], "crawl": {"user_agent": "CorpusBuilder/0.2 (research)",
                                                        "request_timeout": 30,
                                                        "request_delay": 0.5,
                                                        "max_file_size_mb": 50,
                                                        "use_cache": True, "cache_ttl_hours": 168,
                                                        "revalidate_cached_files": True,
                                                        "use_proxy": False, "proxy_list": "",
                                                        "use_browser_headers": True,
                                                        "respect_robots_txt": True,
                                                        "contact_email": "a@b.c",
                                                        "save_checkpoint_every": 50,
                                                        "min_checkpoint_seconds": 5.0,
                                                        "progress_bar": True,
                                                        "per_url_timeout_minutes": 10.0},
               "gui": {"theme": "dark", "ui_overridden": ["crawl.request_delay"],
                       "override_mode": "all"},
               "github": {"token": ""}}
    s = AppSettings.from_dict(payload)
    assert s.ui.override_mode == "all"
    assert s.get("output.contact_email") == "a@b.c"
    # устаревший дефолт не переносится вообще: остаётся актуальный дефолт движка
    assert s.get("output.user_agent") == "CorpusBuilder/0.2.1"
    # …но «выбранным» не считается: переносится только помеченное и то, что
    # отличается от дефолта движка (contact_email — как раз он и есть)
    assert s.changed() == {"output.request_delay", "output.contact_email"}, s.changed()
    assert "output.user_agent" not in s.changed(), "старый дефолт GUI стал выбором пользователя"
    assert "output.use_browser_headers" not in s.changed()
