"""Б/В: приоритет config.yaml vs настроек GUI, чистота-loaded конфига, поля блока А.

После В3 «что перекрывает файл» — это провенанс полей (`model_fields_set`), а не
сравнение со вторым набором дефолтов: тесты ниже проверяют именно это.
"""
from __future__ import annotations

import inspect

import pytest

from corpus_builder.app_settings import (OVERRIDE_ALL, OVERRIDE_CHANGED, OVERRIDE_FILE,
                                          AppSettings)
from corpus_builder.models import AppConfig, SourceItem, QualityConfig


def _cfg(**over):
    base = {"corpus_file": "o.jsonl", "download_dir": "d"}
    base.update(over.pop("output", {}))
    return AppConfig(sources=[SourceItem(url="http://x", type="html")], output=base, **over)


# ------------------------------------------------------------- режимы приоритета

def test_only_explicitly_set_fields_override_the_file():
    cfg = _cfg(output={"request_delay": 0.2}, quality={"min_chars": 5000})
    s = AppSettings()
    s.set("quality.min_chars", 123)
    assert s.apply_to_config(cfg) == ["quality.min_chars"]
    assert cfg.quality.min_chars == 123
    assert cfg.output.request_delay == 0.2, "не заданное поле перезаписано дефолтом"


def test_mode_file_lets_the_yaml_config_win_entirely():
    cfg = _cfg(output={"request_delay": 7.5}, quality={"min_chars": 5000})
    s = AppSettings()
    s.set("output.request_delay", 0.0)
    s.set("quality.min_chars", 10)
    s.ui.override_mode = OVERRIDE_FILE
    assert s.apply_to_config(cfg) == []
    assert (cfg.output.request_delay, cfg.quality.min_chars) == (7.5, 5000)


def test_mode_all_is_the_legacy_behaviour():
    cfg = _cfg(output={"request_delay": 7.5})
    s = AppSettings()
    s.ui.override_mode = OVERRIDE_ALL
    applied = s.apply_to_config(cfg)
    assert "output.request_delay" in applied
    assert cfg.output.request_delay == s.get("output.request_delay")


def test_changing_mode_does_not_forget_what_the_user_set():
    """Переключение режима не имеет права стирать явно заданные поля."""
    s = AppSettings()
    s.set("output.request_delay", 1.5)
    s.set("quality.min_chars", 900)
    before = s.changed()
    for mode in (OVERRIDE_ALL, OVERRIDE_FILE, OVERRIDE_CHANGED):
        s.ui.override_mode = mode
        assert s.changed() == before, f"провенанс потерян при режиме {mode}"


# ------------------------------------------------- одна модель вместо двух

def test_settings_have_no_private_copy_of_engine_fields():
    """Секций v1 (`crawl`, `html`, `gui`, …) в настройках больше нет (В3)."""
    s = AppSettings()
    for gone in ("crawl", "async_crawl", "html", "pdf", "github", "stackexchange", "gui"):
        assert not hasattr(s, gone), f"старая секция {gone} жива — дублирование вернулось"
    assert isinstance(s.engine.quality, QualityConfig), "качество — модель движка"


def test_no_setting_mode_hides_a_field_from_the_engine():
    """Значение, помеченное «заданным», доезжает до движка всегда (В1/В3)."""
    cfg = _cfg()
    s = AppSettings()
    for path, value in (("pipeline.min_checkpoint_seconds", 0.5),
                        ("dedup.streaming", True),
                        ("dedup.auto_streaming", "force"),
                        ("dedup.auto_streaming_threshold_mb", 64),
                        ("pipeline.per_url_timeout_minutes", 2.5)):
        s.set(path, value)
    applied = s.apply_to_config(cfg)
    assert {"pipeline.min_checkpoint_seconds", "dedup.streaming", "dedup.auto_streaming",
            "dedup.auto_streaming_threshold_mb", "pipeline.per_url_timeout_minutes"} <= set(applied)
    assert cfg.pipeline.min_checkpoint_seconds == 0.5
    assert cfg.dedup.auto_streaming == "force"
    assert cfg.dedup.streaming is True


def test_reset_of_a_field_returns_it_to_the_file_value(tmp_path):
    from corpus_builder.config import load_config

    f = tmp_path / "c.yaml"
    f.write_text('sources:\n  - {url: "http://x", type: html}\n'
                 'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl"}\n'
                 'quality: {min_chars: 4321}\n', encoding="utf-8")
    s = AppSettings()
    s.set("quality.min_chars", 1)
    cfg = load_config(f)
    s.apply_to_config(cfg)
    assert cfg.quality.min_chars == 1
    s.reset("quality.min_chars")
    cfg2 = load_config(f)
    s.apply_to_config(cfg2)
    assert cfg2.quality.min_chars == 4321, "«взять из config.yaml» не сработало"


def test_ui_and_secrets_never_leak_into_engine_overrides():
    cfg = _cfg()
    s = AppSettings()
    s.ui.theme = "light"
    s.ui.log_level = "DEBUG"
    s.secrets.github_token = "ghp_x"
    s.secrets.proxy_list = "http://p:1"
    assert s.apply_to_config(cfg) == []


# ------------------------------------------------------------ миграция v1

V1 = {"crawl": {"request_delay": 0.25, "user_agent": "Old/1", "save_checkpoint_every": 7},
      "quality": {"min_chars": 1234},
      "dedup": {"use_streaming": True},
      "github": {"token": "ghp_old"},
      "gui": {"theme": "light"}}


def test_v1_file_is_readable_and_marks_what_it_carries():
    s = AppSettings.from_dict(V1)
    assert s.get("output.request_delay") == 0.25
    assert s.get("pipeline.save_checkpoint_every") == 7
    assert s.get("dedup.streaming") is True
    assert s.secrets.github_token == "ghp_old"
    assert s.ui.theme == "light"
    assert {"output.request_delay", "quality.min_chars", "dedup.streaming",
            "pipeline.save_checkpoint_every"} <= s.changed()
    assert s.legacy_notice, "угадывание «что было изменено» надо показывать"


def test_v1_fields_equal_to_engine_defaults_are_not_carried_over():
    data = dict(V1)
    data["quality"] = {"min_chars": QualityConfig().min_chars}
    s = AppSettings.from_dict(data)
    assert "quality.min_chars" not in s.changed(), \
        "совпавшее с дефолтом значение не должно перекрывать config.yaml"


def test_v2_file_has_no_migration_notice():
    s = AppSettings.from_dict({"format": 2, "engine": {"quality": {"min_chars": 7}}})
    assert s.legacy_notice == []
    assert s.changed() == {"quality.min_chars"}


# ------------------------------------------------------------------- GUI

@pytest.fixture()
def qapp(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppSettings, "_settings_file", classmethod(lambda cls: target))
    yield app
    target.unlink(missing_ok=True)


def _no_modals(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))


def test_dialog_records_only_edited_widgets(qapp, monkeypatch):
    """Один изменённый виджет = одна запись. Раньше «Сохранить» помечал все 70."""
    from corpus_builder.settings_dialog import SettingsDialog

    _no_modals(monkeypatch)
    s = AppSettings()
    dlg = SettingsDialog(s)
    dlg._save_values()
    assert s.changed() == set(), "нетронутый диалог не должен ничего решать"

    dlg.spin_delay.setValue(0.7)
    dlg._on_save()
    assert s.changed() == {"output.request_delay"}, s.changed()
    assert s.get("output.request_delay") == 0.7

    dlg.combo_override_mode.setCurrentIndex(2)        # «все настройки GUI»
    dlg._on_save()
    assert s.ui.override_mode == OVERRIDE_ALL


def test_dialog_reverting_a_field_to_default_clears_the_override(qapp, monkeypatch):
    from corpus_builder.settings_dialog import SettingsDialog

    _no_modals(monkeypatch)
    s = AppSettings()
    dlg = SettingsDialog(s)
    dlg.spin_min_chars.setValue(111)
    dlg._save_values()
    assert s.changed() == {"quality.min_chars"}
    dlg.spin_min_chars.setValue(s.engine_default("quality.min_chars"))
    dlg._save_values()
    assert s.changed() == set(), "вернул дефолт — отметка «задавал» обязана сняться"


def test_main_window_keeps_the_loaded_config_pure(qapp, monkeypatch, tmp_path):
    """«Настройки → Сохранить» не имеет права переписывать загруженный config.yaml."""
    import corpus_builder.gui as G
    from corpus_builder.settings_dialog import SettingsDialog

    _no_modals(monkeypatch)
    window = G.MainWindow()
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'sources:\n  - {url: "http://x", type: html}\n'
        'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl", '
        'request_delay: 7.5}\nquality: {min_chars: 5000}\n', encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window._on_config_path_changed()
    window.output_edit.setText(str(tmp_path / "res"))
    assert window.config is not None

    dlg = SettingsDialog(window.app_settings, window)
    dlg.spin_delay.setValue(0.3)
    dlg._on_save()
    assert (window.config.output.request_delay, window.config.quality.min_chars) \
        == (7.5, 5000), "config.yaml в памяти переписан настройками GUI"

    eff = window._build_effective_config()
    assert eff.output.corpus_file == str(tmp_path / "res/raw_corpus.jsonl")
    assert eff.output.request_delay == pytest.approx(0.3)    # тронутое применилось
    assert eff.quality.min_chars == 5000                     # нетронутое — из файла

    window.app_settings.ui.override_mode = OVERRIDE_FILE
    assert window._build_effective_config().output.request_delay == pytest.approx(7.5)
    window.close()


def test_status_bar_reflects_journal_only_checkpoint(qapp, monkeypatch, tmp_path):
    """А5: чекпойнт живёт в журнале — строка состояния обязана это видеть."""
    import corpus_builder.gui as G

    _no_modals(monkeypatch)
    window = G.MainWindow()
    cfg_file = tmp_path / "c.yaml"
    payload = (f'sources:\n  - {{url: "http://x", type: html}}\n'
               f'output: {{corpus_file: "{tmp_path}/o/raw.jsonl", '
               f'download_dir: "{tmp_path}/o/dl"}}\n')
    cfg_file.write_text(payload, encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window._on_config_path_changed()
    assert window.config is not None, "конфиг не загрузился — тест бесцвелен"

    from corpus_builder.state import State
    # state_file переезжает к corpus_file (модель это делает сама) — идти надо туда
    state = State(window.config.output.state_file)
    for i in range(7):
        state.mark_done(f"http://x/{i}")
    state.mark_error("http://x/bad")
    state.save(compact=True)                      # только журнал, снимок не тронут
    assert not (tmp_path / "state.json").exists()

    window._refresh_status()
    text = window.status.currentMessage()
    assert "7" in text and "1" in text, text
    window.close()


def test_refresh_status_does_not_swallow_errors_silently():
    import corpus_builder.gui as G

    src = inspect.getsource(G.MainWindow._refresh_status)
    assert "except Exception:" not in src.replace("except Exception as e:", ""), \
        "голый except пряжет поломку строки состояния"


def test_conflict_indicator_lists_file_values_not_defaults(qapp, monkeypatch, tmp_path):
    """Индикатор обязан называть поле и значение из файла (В1: «я поменял YAML»)."""
    import corpus_builder.gui as G

    _no_modals(monkeypatch)
    window = G.MainWindow()
    cfg_file = tmp_path / "c.yaml"
    payload = (f'sources:\n  - {{url: "http://x", type: html}}\n'
               f'output: {{corpus_file: "{tmp_path}/o/raw.jsonl", '
               f'download_dir: "{tmp_path}/o/dl", request_delay: 7.5}}\n')
    cfg_file.write_text(payload, encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window._on_config_path_changed()
    assert window.config is not None, "конфиг не загрузился — тест был бы зелёным впустую"
    assert window.config.values_from_file().get("output.request_delay") == 7.5

    window.app_settings.set("output.request_delay", 0.1)
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_log", lambda lvl, msg: logs.append((lvl, msg)))
    window._build_effective_config()
    warn = [m for lvl, m in logs if lvl == "WARNING"]
    assert warn and "output.request_delay" in warn[0], warn
    assert "7.5" in warn[0], "надо показывать значение из файла, а не только новое"
    window.close()
