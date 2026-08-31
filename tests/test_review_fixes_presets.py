"""В5: пресеты настроек — валидность, применение, свой профиль, диалог, CLI."""
from __future__ import annotations

import json

import pytest

from corpus_builder.app_settings import AppSettings
from corpus_builder.presets import (BUILTIN_PRESETS, Preset, all_presets,
                                    apply_preset, capture_preset,
                                    delete_user_preset, load_user_presets,
                                    preset_by_key, save_user_preset,
                                    validate_preset)


@pytest.fixture()
def isolated_presets(tmp_path, monkeypatch):
    from corpus_builder import presets as P
    target = tmp_path / "presets.json"
    monkeypatch.setattr(P, "user_presets_file", lambda: target)
    return target


# ------------------------------------------------------------- встроенные

def test_builtin_presets_are_four_and_named_as_spec():
    assert [p.key for p in BUILTIN_PRESETS] == ["polite", "own_site",
                                                "academic", "big_corpus"]
    for preset in BUILTIN_PRESETS:
        assert preset.title and preset.description
        assert len(preset.values) >= 4, f"{preset.key}: слишком пусто для пресета"


def test_every_builtin_preset_is_valid_and_reaches_the_engine():
    from corpus_builder.models import AppConfig, SourceItem

    for preset in BUILTIN_PRESETS:
        assert validate_preset(preset) == [], preset.key

        settings = AppSettings()
        changed = apply_preset(settings, preset.key, mark_touched=False)
        assert changed, f"{preset.key}: пресет ничего не меняет"

        cfg = AppConfig(sources=[SourceItem(url="http://x", type="html")],
                        output={"corpus_file": "o.jsonl", "download_dir": "d"})
        settings.set_override_mode("all")
        applied = settings.apply_to_config(cfg)
        assert applied, "настройки не доехали до движка"
        # перечитать модель — валидаторы pydantic должны быть довольны
        AppConfig.model_validate(cfg.model_dump())


def test_presets_do_the_thing_their_names_promose():
    polite = AppSettings()
    apply_preset(polite, "polite", mark_touched=False)
    assert polite.crawl.request_delay >= 2.0
    assert polite.crawl.respect_robots_txt is True
    assert polite.async_crawl.enabled is False

    own = AppSettings()
    apply_preset(own, "own_site", mark_touched=False)
    assert own.crawl.request_delay == 0.0
    assert own.crawl.use_cache is True
    assert own.crawl.min_checkpoint_seconds > 0

    academic = AppSettings()
    apply_preset(academic, "academic", mark_touched=False)
    assert academic.crawl.request_delay >= 1.0
    assert academic.pdf.ocr_enabled is True
    assert academic.quality.min_chars > 200

    big = AppSettings()
    apply_preset(big, "big_corpus", mark_touched=False)
    assert big.dedup.use_streaming is True
    assert big.dedup.use_incremental is True
    assert big.export.gzip_output is True
    assert big.export.parallel_postproc is True
    assert big.crawl.save_checkpoint_every >= 100


def test_preset_values_do_not_hide_config_warnings():
    """Пресет не обязан знать e-mail пользователя — но предупреждение остаётся."""
    from corpus_builder.cli import config_warnings
    from corpus_builder.models import AppConfig, SourceItem

    s = AppSettings()
    apply_preset(s, "academic", mark_touched=False)
    cfg = AppConfig(sources=[SourceItem(url="http://x", type="wikipedia")],
                    output={"corpus_file": "o.jsonl", "download_dir": "d"})
    s.set_override_mode("all")
    s.apply_to_config(cfg)
    warns = config_warnings(cfg)
    assert any("e-mail" in w for w in warns), warns


# ------------------------------------------------------------- защита валидации

def test_validate_preset_rejects_unknown_type_secret_and_empty():
    bad = Preset("bad", "плохой", "", values={
        "crawl.nope": 1,
        "nope.min_chars": 10,
        "github.token": "ghp_leak",
        "quality.min_chars": "много",
    })
    problems = validate_preset(bad)
    text = "\n".join(problems)
    assert "нет секции «nope»" in text
    assert "нет настройки «crawl.nope»" in text
    assert "секретное поле" in text
    assert "не того типа" in text or "движок не принимает" in text

    assert any("пустой" in p for p in validate_preset(Preset("e", "e", "", values={})))


def test_apply_preset_marks_fields_touched_and_is_idempotent():
    s = AppSettings()
    first = apply_preset(s, "polite")
    assert first and set(first) <= s.touched_fields(), "режим touched не пропустит пресет"
    assert apply_preset(s, "polite") == [], "повторное применение ничего не меняет"


def test_apply_unknown_preset_raises():
    with pytest.raises(KeyError):
        apply_preset(AppSettings(), "no-such-preset")


# ------------------------------------------------------------- свои пресеты

def test_capture_preset_skips_defaults_gui_and_secrets():
    s = AppSettings()
    s.crawl.request_delay = 0.42
    s.dedup.use_streaming = True
    s.github.token = "ghp_secret"
    s.gui.theme = "light"
    preset = capture_preset(s, "mine", "Мой")
    assert preset.values == {"crawl.request_delay": 0.42, "dedup.use_streaming": True}
    assert preset.builtin is False
    assert validate_preset(preset) == []


def test_user_preset_roundtrip_and_delete(isolated_presets):
    s = AppSettings()
    s.crawl.request_timeout = 11
    preset = capture_preset(s, "fast", "Быстрый", "для проверки")
    save_user_preset(preset)
    assert isolated_presets.exists()

    assert list(load_user_presets()) == ["fast"]
    assert preset_by_key("fast").title == "Быстрый"
    assert [p.key for p in all_presets()] == ["polite", "own_site", "academic",
                                              "big_corpus", "fast"]
    assert delete_user_preset("fast") is True
    assert load_user_presets() == {}
    assert delete_user_preset("fast") is False


def test_user_preset_cannot_override_builtin(isolated_presets):
    with pytest.raises(ValueError):
        save_user_preset(BUILTIN_PRESETS[0])


def test_broken_preset_file_is_survivable(isolated_presets):
    isolated_presets.write_text("{ это не json", encoding="utf-8")
    assert load_user_presets() == {}
    isolated_presets.write_text(json.dumps({"x": {"title": "X", "values": {
        "github.token": "secret", "crawl.request_delay": 1.0}}}), encoding="utf-8")
    loaded = load_user_presets()["x"]
    assert loaded.values == {"crawl.request_delay": 1.0}, "секрет обязан быть вырезан"


# ------------------------------------------------------------------ диалог

def test_dialog_apply_and_save_preset(qapp_dialog, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    _window, dialog = qapp_dialog
    _no_modals(monkeypatch)
    assert [dialog.combo_preset.itemText(i) for i in range(dialog.combo_preset.count())][:4] \
        == ["Вежливый", "Свой сайт", "Научные источники", "Большой корпус"]
    assert dialog.btn_preset_delete.isEnabled() is False       # встроенный не удалить

    dialog.combo_preset.setCurrentIndex(3)                     # big_corpus
    dialog._on_apply_preset()
    s = dialog.settings
    assert s.dedup.use_streaming is True and s.export.gzip_output is True
    assert "big_corpus" not in s.touched_fields()              # ключ, а не поле
    assert "dedup.use_streaming" in s.touched_fields()
    assert dialog.chk_gzip.isChecked() is True, "виджет не отразил применение"

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Мой режим", True)))
    dialog._on_save_preset()
    assert len(load_user_presets()) == 1, list(load_user_presets())
    assert dialog.combo_preset.count() == 5
    dialog.combo_preset.setCurrentIndex(4)
    assert dialog.btn_preset_delete.isEnabled() is True


def test_dialog_rejects_broken_preset(qapp_dialog, monkeypatch):
    """Кнопка «Применить» не имеет права молча ничего не делать."""
    from PySide6.QtWidgets import QMessageBox

    from corpus_builder import presets as P

    _no_modals(monkeypatch)
    _window, dialog = qapp_dialog
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    broken = Preset("broken", "Сломанный", "", values={"crawl.nope": 1}, builtin=False)
    monkeypatch.setattr(P, "preset_by_key", lambda key: broken)
    dialog.combo_preset.addItem("Сломанный", "broken")
    dialog.combo_preset.setCurrentIndex(dialog.combo_preset.count() - 1)
    dialog._on_apply_preset()
    assert shown and "нет настройки «crawl.nope»" in shown[0], shown


# --------------------------------------------------------------------- CLI

def test_cli_preset_list_show_and_yaml(tmp_path):
    from click.testing import CliRunner
    from corpus_builder.cli import cli

    res = CliRunner().invoke(cli, ["preset"])
    assert res.exit_code == 0, res.output
    listed = json.loads(res.output.split("показать:")[0])
    assert [x["key"] for x in listed][:4] == ["polite", "own_site", "academic", "big_corpus"]

    res2 = CliRunner().invoke(cli, ["preset", "academic"])
    values = json.loads(res2.output)["values"]
    assert values["pdf.ocr_enabled"] is True

    out = tmp_path / "overlay.yaml"
    res3 = CliRunner().invoke(cli, ["preset", "big_corpus", "--yaml", str(out)])
    assert res3.exit_code == 0, res3.output
    import yaml
    tree = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert tree["pipeline"]["parallel_postproc"] is True
    assert tree["dedup"]["streaming"] is True
    assert "log_level" not in json.dumps(tree), "gui-поле не должно попадать в конфиг"

    assert CliRunner().invoke(cli, ["preset", "nope"]).exit_code != 0


def test_cli_preset_yaml_is_loadable_by_the_engine(tmp_path):
    """Накидка пресета обязана сливаться с config.yaml без сюрпризов."""
    import yaml
    from click.testing import CliRunner
    from corpus_builder.cli import cli
    from corpus_builder.config import load_config

    base = tmp_path / "config.yaml"
    base.write_text('sources:\n  - {url: "http://x", type: html}\n'
                    'output: {corpus_file: "o/raw.jsonl", download_dir: "o/dl"}\n'
                    'quality: {min_chars: 5000}\n', encoding="utf-8")
    overlay = tmp_path / "ov.yaml"
    res = CliRunner().invoke(cli, ["preset", "academic", "--yaml", str(overlay)])
    assert res.exit_code == 0

    cfg = yaml.safe_load(base.read_text(encoding="utf-8"))
    merged = _deep_merge(cfg, yaml.safe_load(overlay.read_text(encoding="utf-8")))
    target = tmp_path / "merged.yaml"
    target.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")

    loaded = load_config(target)
    assert loaded.quality.min_chars == 400     # пресет перекрыл файл
    assert loaded.crawlers.pdf.ocr_enabled is True
    assert str(loaded.output.corpus_file) == "o/raw.jsonl"


# --------------------------------------------------------------- фикстуры

def _no_modals(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


@pytest.fixture()
def qapp_dialog(tmp_path, monkeypatch, isolated_presets):
    from PySide6.QtWidgets import QApplication
    from corpus_builder.gui import MainWindow
    from corpus_builder.settings_dialog import SettingsDialog

    QApplication.instance() or QApplication([])
    window = MainWindow()
    dialog = SettingsDialog(window.app_settings, window)
    yield window, dialog
    dialog.deleteLater()
    window.close()


def _deep_merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
