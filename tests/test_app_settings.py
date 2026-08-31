"""Тесты на app_settings — хранение настроек приложения."""
import json
import os
from unittest import mock

from corpus_builder.app_settings import (
    AppSettings,
)


def test_default_settings():
    """Настройки по умолчанию создаются корректно."""
    s = AppSettings()
    assert s.crawl.request_timeout == 30
    assert s.crawl.request_delay == 2.0
    assert s.html.extract_mode == "trafilatura"
    assert s.pdf.ocr_enabled is True
    assert s.github.crawl_issues is False
    assert s.quality.min_chars == 200
    assert s.dedup.exact is True
    assert s.gui.theme == "dark"


def test_save_and_load(tmp_path, monkeypatch):
    """Сохранение и загрузка настроек."""
    # Подменяем путь к файлу настроек
    settings_file = tmp_path / "settings.json"

    with mock.patch.object(AppSettings, '_settings_file', lambda cls=None: settings_file):
        # Создаём и сохраняем
        s = AppSettings()
        s.crawl.request_delay = 5.0
        s.quality.min_chars = 500
        s.github.token = "ghp_test123"
        s.pdf.ocr_enabled = False
        s.save()

        assert settings_file.exists()
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert data["crawl"]["request_delay"] == 5.0
        assert data["quality"]["min_chars"] == 500
        assert data["github"]["token"] == "ghp_test123"
        assert data["pdf"]["ocr_enabled"] is False

        # Загружаем
        loaded = AppSettings.load()
        assert loaded.crawl.request_delay == 5.0
        assert loaded.quality.min_chars == 500
        assert loaded.github.token == "ghp_test123"
        assert loaded.pdf.ocr_enabled is False


def test_load_nonexistent_returns_defaults(tmp_path):
    """Если файла настроек нет — возвращаются defaults."""
    with mock.patch.object(AppSettings, '_settings_file', lambda cls=None: tmp_path / "nonexistent.json"):
        s = AppSettings.load()
        assert s.crawl.request_timeout == 30  # default


def test_apply_to_config():
    """Применение настроек к AppConfig."""
    from corpus_builder.models import AppConfig, SourceItem
    s = AppSettings()
    s.crawl.request_delay = 10.0
    s.crawl.user_agent = "TestAgent/1.0"
    s.html.extract_mode = "bs4"
    s.pdf.ocr_enabled = False
    s.github.crawl_issues = True
    s.quality.min_chars = 1000
    s.dedup.minhash_threshold = 0.9

    # Создаём минимальный AppConfig
    cfg = AppConfig(
        sources=[SourceItem(url="https://example.com", type="html")],
        output={"corpus_file": "test.jsonl", "download_dir": "test", "user_agent": "old"},
    )
    s.apply_to_config(cfg)

    assert cfg.output.request_delay == 10.0
    assert cfg.output.user_agent == "TestAgent/1.0"
    assert cfg.crawlers.html.extract_mode == "bs4"
    assert cfg.crawlers.pdf.ocr_enabled is False
    assert cfg.crawlers.github.crawl_issues is True
    assert cfg.quality.min_chars == 1000
    assert cfg.dedup.minhash_threshold == 0.9


def test_setup_env_vars():
    """Установка переменных окружения из настроек."""
    s = AppSettings()
    s.github.token = "ghp_env_test"
    s.stackexchange.api_key = "se_env_test"
    s.crawl.proxy_list = "http://proxy1:8080,http://proxy2:8080"

    # Очищаем env
    for key in ["GITHUB_TOKEN", "STACKEXCHANGE_KEY", "CORPUS_BUILDER_PROXIES"]:
        if key in os.environ:
            del os.environ[key]

    s.setup_env_vars()

    assert os.environ.get("GITHUB_TOKEN") == "ghp_env_test"
    assert os.environ.get("STACKEXCHANGE_KEY") == "se_env_test"
    assert os.environ.get("CORPUS_BUILDER_PROXIES") == "http://proxy1:8080,http://proxy2:8080"


def test_to_dict():
    """Сериализация в dict."""
    s = AppSettings()
    d = s.to_dict()
    assert "crawl" in d
    assert "quality" in d
    assert "dedup" in d
    assert "gui" in d
    assert isinstance(d["crawl"], dict)
    assert isinstance(d["quality"], dict)


def test_import_export_settings(tmp_path):
    """Импорт настроек из dict (как при импорте из JSON)."""
    data = {
        "crawl": {"request_delay": 7.5, "user_agent": "Imported/1.0"},
        "quality": {"min_chars": 300, "spam_check": False},
        "unknown_section": {"foo": "bar"},  # неизвестная секция — должна игнорироваться
    }
    s = AppSettings._from_dict(data)
    assert s.crawl.request_delay == 7.5
    assert s.crawl.user_agent == "Imported/1.0"
    assert s.quality.min_chars == 300
    assert s.quality.spam_check is False
    # Неизвестные секции игнорируются
    assert not hasattr(s, "unknown_section")


# ------------------------------------------------------------------ секреты (Б)

def test_export_dict_hides_secrets_by_default():
    from corpus_builder.app_settings import (REDACTED_SECRET, AppSettings,
                                             secret_paths, to_export_dict)
    s = AppSettings()
    s.github.token = "ghp_topsecret"
    s.stackexchange.api_key = "se_topsecret"
    s.crawl.request_delay = 1.5
    assert set(secret_paths(s)) == {"github.token", "stackexchange.api_key"}
    d = to_export_dict(s)
    assert d["github"]["token"] == REDACTED_SECRET
    assert d["stackexchange"]["api_key"] == REDACTED_SECRET
    assert d["crawl"]["request_delay"] == 1.5, "не секреты не трогаем"
    assert to_export_dict(s, redact=False)["github"]["token"] == "ghp_topsecret"


def test_export_dict_leaves_empty_secrets_alone():
    from corpus_builder.app_settings import AppSettings, to_export_dict
    d = to_export_dict(AppSettings())
    assert d["github"]["token"] == "", "пустое поле не превращаем в заглушку"
    assert d["stackexchange"]["api_key"] == ""


def test_reimporting_redacted_export_keeps_real_token():
    """Круг «экспорт → импорт» не должен стирать токен заглушкой."""
    from corpus_builder.app_settings import REDACTED_SECRET, AppSettings
    original = AppSettings()
    original.github.token = "ghp_keepme"
    exported = json.loads(json.dumps(original.to_export_dict()))
    exported["github"]["token"] = REDACTED_SECRET

    restored = AppSettings._from_dict(exported)
    assert restored.github.token == "", "заглушка не должна становиться токеном"

    loaded = AppSettings._from_dict({"github": {"token": "ghp_keepme"}})
    again = AppSettings._from_dict(exported)
    assert again.github.token == ""
    assert loaded.github.token == "ghp_keepme"


def test_secret_field_detection_is_narrow():
    from corpus_builder.app_settings import is_secret_field
    assert is_secret_field("token") and is_secret_field("api_key")
    assert is_secret_field("client_secret") and is_secret_field("github_access_token")
    assert not is_secret_field("url") and not is_secret_field("max_workers")
    assert not is_secret_field("token_env"), "имя переменной окружения — не секрет"


def test_settings_file_is_owner_only(tmp_path):
    """В2: в этом файле лежат токены → 0600, даже если файл был 0644."""
    import stat
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o644)
    with mock.patch.object(AppSettings, "_settings_file", lambda cls=None: target):
        s = AppSettings()
        s.github.token = "ghp_secret"
        s.save()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    with mock.patch.object(AppSettings, "_settings_file", lambda cls=None: target):
        assert AppSettings.load().github.token == "ghp_secret"


def test_example_config_points_at_the_json_schema():
    """В4: редактор должен подсказывать схему прямо из примера конфига."""
    from pathlib import Path
    text = Path("config.example.yaml").read_text(encoding="utf-8")
    assert "# yaml-language-server: $schema=" in text.splitlines()[0]


def test_committed_schema_matches_the_model():
    """corpus.schema.json в корне обязан совпадать с моделью (иначе подсказка врёт)."""
    import json
    from pathlib import Path
    from corpus_builder.models import AppConfig
    committed = json.loads(Path("corpus.schema.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(AppConfig.model_json_schema()))
