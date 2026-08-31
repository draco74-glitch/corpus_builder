"""Б: приоритет «config.yaml vs настройки GUI», учёт «трогал ли», поля блока А."""
from __future__ import annotations

import copy

import pytest

from corpus_builder.app_settings import (AppSettings, OVERRIDE_ALL, OVERRIDE_FILE,
                                         OVERRIDE_TOUCHED)
from corpus_builder.models import AppConfig


def _cfg() -> AppConfig:
    return AppConfig(
        sources=[{"url": "http://x", "type": "html"}],
        output={"corpus_file": "o/raw.jsonl", "download_dir": "o/dl",
                "request_delay": 7.5},
        quality={"min_chars": 5000},
        pipeline={"min_checkpoint_seconds": 42.0},
        dedup={"auto_streaming": "force", "auto_streaming_threshold_mb": 64},
    )


def test_mode_touched_applies_only_edited_fields():
    s = AppSettings()
    s.crawl.request_delay = 0.4
    s.mark_touched(["crawl.request_delay"])
    cfg = _cfg()
    applied = s.apply_to_config(cfg)
    assert "output.request_delay" in applied
    assert cfg.output.request_delay == pytest.approx(0.4)
    assert cfg.quality.min_chars == 5000, "нетронутое поле файла обязано выжить"


def test_mode_file_never_overrides_config():
    cfg = _cfg()
    s = AppSettings()
    s.crawl.request_delay = 0.1
    s.quality.min_chars = 10
    s.set_override_mode(OVERRIDE_FILE)
    assert s.apply_to_config(cfg) == []
    assert cfg.output.request_delay == 7.5
    assert cfg.quality.min_chars == 5000


def test_mode_all_overrides_everything():
    cfg = _cfg()
    s = AppSettings()
    s.set_override_mode(OVERRIDE_ALL)
    applied = s.apply_to_config(cfg)
    assert "output.request_delay" in applied
    assert cfg.output.request_delay == s.crawl.request_delay
    assert cfg.quality.min_chars == s.quality.min_chars


def test_switching_mode_back_keeps_touched_list():
    """Раньше переключатель «all → touched» терял список тронутых полей."""
    s = AppSettings()
    s.mark_touched(["crawl.request_delay", "quality.min_chars"])
    s.set_override_mode(OVERRIDE_ALL)
    assert s.overridden_fields() == {"*"}
    s.set_override_mode(OVERRIDE_TOUCHED)
    assert s.touched_fields() == {"crawl.request_delay", "quality.min_chars"}
    assert {"crawl.request_delay", "quality.min_chars"} <= s.overridden_fields()


def test_legacy_settings_file_is_migrated_once():
    """Файл настроек старее учёта приоритета: помечаем тронутым то, что уже
    отличается от дефолта, и предупреждаем один раз."""
    legacy = {"crawl": {"request_delay": 0.25}, "quality": {"min_chars": 50}}
    s = AppSettings._from_dict(legacy)
    assert s.gui.override_migrated is True
    assert "crawl.request_delay" in s.gui.ui_overridden
    assert s.legacy_migration_notice, "нужно предупредить пользователя"
    # второй проход (обычная загрузка) миграцию не повторяет
    again = AppSettings._from_dict(copy.deepcopy(s.to_dict()))
    assert again.legacy_migration_notice == []


def test_diff_from_snapshot_skips_ui_only_state():
    a = AppSettings()
    b = AppSettings._from_dict(a.to_dict())
    b.gui.theme = "light"
    assert b.diff_from_snapshot(a.snapshot()) == []
    b.quality.min_chars = a.quality.min_chars + 1
    assert b.diff_from_snapshot(a.snapshot()) == ["quality.min_chars"]


def test_unchosen_overrides_are_visible_in_report():
    """«Сами собой» перекрывающие файл поля должны быть перечислимы (индикатор)."""
    s = AppSettings()
    s.quality.min_chars = 9999
    assert "quality.min_chars" in s.overridden_fields()
    assert "quality.min_chars" not in s.touched_fields()
    cfg_paths = dict(s.mapping())["quality.min_chars"]
    assert cfg_paths in s.unchosen_overrides()


def test_block_a_fields_reach_the_engine_from_settings():
    """А: min_checkpoint_seconds и auto_streaming были только в движке."""
    cfg = AppConfig(sources=[{"url": "http://x", "type": "html"}],
                    output={"corpus_file": "o.jsonl", "download_dir": "d"})
    s = AppSettings()
    s.crawl.min_checkpoint_seconds = 0.5
    s.dedup.auto_streaming = "force"
    s.dedup.auto_streaming_threshold_mb = 64
    s.mark_touched(["crawl.min_checkpoint_seconds", "dedup.auto_streaming",
                    "dedup.auto_streaming_threshold_mb"])
    applied = s.apply_to_config(cfg)
    assert {"pipeline.min_checkpoint_seconds", "dedup.auto_streaming",
            "dedup.auto_streaming_threshold_mb"} <= set(applied)
    assert cfg.pipeline.min_checkpoint_seconds == 0.5
    assert cfg.dedup.auto_streaming == "force"
    assert cfg.dedup.auto_streaming_threshold_mb == 64


# ------------------------------------------------------------- GUI-проводка

@pytest.fixture()
def qapp(tmp_path, monkeypatch):
    """Qt-приложение + изоляция файла настроек (диалог пишет в home)."""
    from PySide6.QtWidgets import QApplication
    from corpus_builder.app_settings import AppSettings as A
    monkeypatch.setattr(A, "_settings_file", classmethod(lambda cls: tmp_path / "settings.json"))
    return QApplication.instance() or QApplication([])


def test_gui_defaults_match_engine_defaults():
    """Дефолт GUI не имеет права отличаться от дефолта движка (Б).

    Расхождение (было: устаревший User-Agent, use_browser_headers, workers OCR,
    min_score/max_questions) в режиме «все настройки GUI» молча уезжало в
    прогон вместо того, что дал бы config.yaml без этого поля.
    """
    from corpus_builder.models import AppConfig, SourceItem

    ref = AppConfig(sources=[SourceItem(url="http://x", type="html")],
                    output={"corpus_file": "o.jsonl", "download_dir": "d"})
    s = AppSettings()
    diverged = []
    for setting_path, config_path in s.mapping():
        obj = ref
        for part in config_path.split(".")[:-1]:
            obj = getattr(obj, part)
        engine_default = getattr(obj, config_path.split(".")[-1])
        if s._get(setting_path) != engine_default:
            diverged.append((setting_path, config_path,
                             s._get(setting_path), engine_default))
    assert not diverged, f"дефолты разошлись: {diverged}"


def _no_modals(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))


def test_dialog_marks_only_edited_widget_fields(qapp, monkeypatch, tmp_path):
    from corpus_builder.settings_dialog import SettingsDialog
    _no_modals(monkeypatch)
    s = AppSettings()
    s.gui.ui_overridden = []
    dlg = SettingsDialog(s)
    dlg.spin_delay.setValue(0.7)
    dlg.combo_override_mode.setCurrentIndex(2)          # «все настройки GUI»
    dlg._on_save()
    assert s.touched_fields() == {"crawl.request_delay"}, s.gui.ui_overridden
    assert s.override_mode() == OVERRIDE_ALL


def test_main_window_keeps_loaded_config_pure(qapp, monkeypatch, tmp_path):
    """«Настройки → Сохранить» не имеет права переписывать загруженный config."""
    from PySide6.QtWidgets import QApplication
    import corpus_builder.gui as G
    from corpus_builder.settings_dialog import SettingsDialog

    _no_modals(monkeypatch)
    assert QApplication.instance() is not None
    window = G.MainWindow()
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'sources:\n  - {url: "http://x", type: html}\n'
        'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl", '
        'request_delay: 7.5}\nquality: {min_chars: 5000}\n', encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window._on_config_path_changed()
    # «папка результатов» в tmp, чтобы тест не плодил каталоги в репозитории
    window.output_edit.setText(str(tmp_path / "res"))
    assert window.config is not None

    dlg = SettingsDialog(window.app_settings, window)
    dlg.spin_delay.setValue(0.3)
    dlg._on_save()
    assert (window.config.output.request_delay, window.config.quality.min_chars) \
        == (7.5, 5000), "config.yaml в памяти был переписан настройками GUI"

    eff = window._build_effective_config()
    assert eff.output.corpus_file == str(tmp_path / "res/raw_corpus.jsonl")
    assert eff.output.request_delay == pytest.approx(0.3)   # тронутое — применилось
    assert eff.quality.min_chars == 5000                    # нетронутое — из файла

    window.app_settings.set_override_mode(OVERRIDE_FILE)
    eff2 = window._build_effective_config()
    assert eff2.output.request_delay == pytest.approx(7.5), "режим «файл важнее»"
    window.close()


def test_status_bar_reflects_journal_only_checkpoint(qapp, monkeypatch, tmp_path):
    """А5: чекпойнт живёт в журнале — строка состояния обязана это видеть.

    Плюс проверка, что `_refresh_status` не прячет собственные ошибки: раньше
    там был `except Exception: pass`, и опечатка в методе выглядела как
    «счётчики просто не обновляются».
    """
    import corpus_builder.gui as G

    _no_modals(monkeypatch)
    window = G.MainWindow()
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'sources:\n  - {url: "http://x", type: html}\n'
        'output: {corpus_file: "%s/raw.jsonl", download_dir: "%s/dl"}\n'
        % (tmp_path, tmp_path), encoding="utf-8")
    window.config_edit.setText(str(cfg_file))
    window._on_config_path_changed()

    from corpus_builder.state import State
    state = State(tmp_path / "state.json")
    for i in range(7):
        state.mark_done(f"http://x/{i}")
    state.mark_error("http://x/bad")
    state.save(compact=True)                    # только журнал, снимок не тронут
    assert not (tmp_path / "state.json").exists()

    window._refresh_status()
    text = window.status.currentMessage()
    assert "7" in text and "1" in text, text
    assert "_refresh_status" in inspect.getsource(G.MainWindow)
    window.close()


import inspect  # noqa: E402  (нужен для проверки исходника метода)


def test_refresh_status_does_not_swallow_errors_silently():
    import corpus_builder.gui as G

    src = inspect.getsource(G.MainWindow._refresh_status)
    assert "except Exception:" not in src.replace("except Exception as e:", ""), \
        "голый except пряжет поломку строки состояния"
