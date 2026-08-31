"""В5: пресеты — валидность путей, применение, свой профиль, диалог, CLI."""
from __future__ import annotations

import json

import pytest

from corpus_builder.app_settings import AppSettings
from corpus_builder.presets import (BUILTIN_PRESETS, Preset, all_presets, apply_preset,
                                    capture_preset, delete_user_preset, load_user_presets,
                                    preset_by_key, preset_to_yaml, save_user_preset,
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
        assert len(preset.values) >= 4, f"{preset.key}: слишком пусто для профиля"


def test_every_builtin_preset_is_valid_and_reaches_the_engine():
    """Пресет говорит на путях AppConfig и доезжает до движка без перевода (В3)."""
    for preset in BUILTIN_PRESETS:
        assert validate_preset(preset) == [], preset.key

        s = AppSettings()
        changed = apply_preset(s, preset.key)
        assert changed, f"{preset.key}: пресет ничего не меняет"
        # поле UI в config.yaml не едет — сверяем остальное отдельно
        assert set(changed) - {"ui.log_level"} == s.changed(), \
            f"{preset.key}: применённое не помечено заданным"

        from corpus_builder.models import AppConfig, SourceItem
        cfg = AppConfig(sources=[SourceItem(url="http://x", type="html")],
                        output={"corpus_file": "o.jsonl", "download_dir": "d"})
        applied = s.apply_to_config(cfg)
        assert set(applied) == s.changed(), f"{preset.key}: часть полей потерялась"


def test_presets_do_the_thing_their_names_promise():
    polite = AppSettings()
    apply_preset(polite, "polite")
    assert polite.get("output.request_delay") >= 2.0
    assert polite.get("output.respect_robots_txt") is True
    assert polite.get("pipeline.use_async") is False

    own = AppSettings()
    apply_preset(own, "own_site")
    assert own.get("output.request_delay") == 0.0
    assert own.get("output.use_http_cache") is True
    assert own.get("pipeline.min_checkpoint_seconds") > 0

    academic = AppSettings()
    apply_preset(academic, "academic")
    assert academic.get("output.request_delay") >= 1.0
    assert academic.get("crawlers.pdf.ocr_enabled") is True
    assert academic.get("quality.min_chars") > 200

    big = AppSettings()
    apply_preset(big, "big_corpus")
    assert big.get("dedup.streaming") is True
    assert big.get("dedup.incremental") is True
    assert big.get("export.write_gzip") is True
    assert big.get("pipeline.parallel_postproc") is True
    assert big.get("pipeline.save_checkpoint_every") >= 100


def test_preset_values_do_not_hide_config_warnings():
    """Пресет не знает e-mail пользователя — предупреждение об этом обязано остаться."""
    from corpus_builder.cli import config_warnings
    from corpus_builder.models import AppConfig, SourceItem

    s = AppSettings()
    apply_preset(s, "academic")
    cfg = AppConfig(sources=[SourceItem(url="http://x", type="wikipedia")],
                    output={"corpus_file": "o.jsonl", "download_dir": "d"})
    s.apply_to_config(cfg)
    assert any("e-mail" in w for w in config_warnings(cfg))


# ------------------------------------------------------------- защита валидации

def test_validate_preset_rejects_unknown_type_secret_and_empty():
    bad = Preset("bad", "плохой", "", values={
        "crawl.nope": 1,                        # секции v1 больше нет
        "quality.no_such": 10,
        "secrets.github_token": "ghp_leak",
        "quality.min_chars": "много",
        "output.corpus_file": "/tmp/подмена",   # пути прогона — не настройка
    })
    text = "\n".join(validate_preset(bad))
    assert "нет настройки «crawl.nope»" in text
    assert "нет настройки «quality.no_such»" in text
    assert "в пресет не кладём" in text
    assert "не подходит" in text
    assert "в пресет не кладём" in text
    assert any("пустой" in p for p in validate_preset(Preset("e", "e", "", values={})))


def test_apply_preset_is_idempotent_and_records_provenance():
    s = AppSettings()
    first = apply_preset(s, "polite")
    assert first and set(first) == s.changed()
    assert apply_preset(s, "polite") == [], "повторное применение ничего не меняет"


def test_apply_preset_refuses_broken_profile():
    """Половину пресета применять нельзя — это тот же сюрприз, что мёртвый чекбокс."""
    broken = Preset("broken", "Сломанный", "", values={
        "output.request_delay": 3.0, "quality.nope": 1}, builtin=False)
    from corpus_builder import presets as P
    s = AppSettings()
    original = P.preset_by_key
    P.preset_by_key = lambda key: broken
    try:
        with pytest.raises(ValueError):
            apply_preset(s, "broken")
        assert s.changed() == set(), "битый пресет не должен был что-то изменить"
    finally:
        P.preset_by_key = original


def test_apply_unknown_preset_raises():
    with pytest.raises(KeyError):
        apply_preset(AppSettings(), "no-such-preset")


# ------------------------------------------------------------- свои пресеты

def test_capture_preset_skips_defaults_ui_and_secrets():
    s = AppSettings()
    s.set("output.request_delay", 0.42)
    s.set("dedup.streaming", True)
    s.secrets.github_token = "ghp_secret"
    s.ui.theme = "light"
    preset = capture_preset(s, "mine", "Мой")
    assert preset.values == {"output.request_delay": 0.42, "dedup.streaming": True}
    assert preset.builtin is False
    assert validate_preset(preset) == []


def test_capture_preset_carries_only_explicit_ui_choice():
    s = AppSettings()
    s.set("quality.min_chars", 250)
    s.ui.log_level = "WARNING"
    preset = capture_preset(s, "x", "X")
    assert preset.values == {"quality.min_chars": 250, "ui.log_level": "WARNING"}
    assert "ui.theme" not in preset.values


def test_user_preset_roundtrip_and_delete(isolated_presets):
    s = AppSettings()
    s.set("output.request_timeout", 11)
    save_user_preset(capture_preset(s, "fast", "Быстрый", "для проверки"))
    assert isolated_presets.exists()
    assert preset_by_key("fast").title == "Быстрый"
    assert [p.key for p in all_presets()] == ["polite", "own_site", "academic",
                                              "big_corpus", "fast"]
    assert apply_preset(AppSettings(), "fast") == ["output.request_timeout"]
    assert delete_user_preset("fast") is True
    assert load_user_presets() == {}
    assert delete_user_preset("fast") is False


def test_user_preset_cannot_override_builtin(isolated_presets):
    with pytest.raises(ValueError):
        save_user_preset(BUILTIN_PRESETS[0])


def test_broken_preset_file_and_stray_secrets_are_survivable(isolated_presets):
    isolated_presets.write_text("{ это не json", encoding="utf-8")
    assert load_user_presets() == {}
    isolated_presets.write_text(json.dumps({"x": {"title": "X", "values": {
        "secrets.github_token": "secret", "output.request_delay": 1.0}}}),
        encoding="utf-8")
    assert load_user_presets()["x"].values == {"output.request_delay": 1.0}, \
        "секрет обязан быть вырезан при чтении"


# ------------------------------------------------------------------ диалог

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
    target = tmp_path / "settings.json"
    monkeypatch.setattr(AppSettings, "_settings_file", classmethod(lambda cls: target))
    window = MainWindow()
    dialog = SettingsDialog(window.app_settings, window)
    yield window, dialog
    dialog.deleteLater()
    window.close()


def test_dialog_apply_and_save_preset(qapp_dialog, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    _no_modals(monkeypatch)
    _window, dialog = qapp_dialog
    assert [dialog.combo_preset.itemText(i) for i in range(dialog.combo_preset.count())][:4] \
        == ["Вежливый", "Свой сайт", "Научные источники", "Большой корпус"]
    assert dialog.btn_preset_delete.isEnabled() is False       # встроенный не удалить

    dialog.combo_preset.setCurrentIndex(3)                     # big_corpus
    dialog._on_apply_preset()
    s = dialog.settings
    assert s.get("dedup.streaming") is True and s.get("export.write_gzip") is True
    assert "dedup.streaming" in s.changed(), "провенанс не помечен → пресет не поедет"
    assert dialog.chk_streaming.isChecked() is True, "виджет не отразил применение"

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Мой режим", True)))
    dialog._on_save_preset()
    assert len(load_user_presets()) == 1
    assert dialog.combo_preset.count() == 5
    dialog.combo_preset.setCurrentIndex(4)
    assert dialog.btn_preset_delete.isEnabled() is True


def test_dialog_rejects_broken_preset_without_touching_settings(qapp_dialog, monkeypatch):
    """Кнопка «Применить» не имеет права молча применять половину профиля."""
    from PySide6.QtWidgets import QMessageBox

    from corpus_builder import presets as P

    _no_modals(monkeypatch)
    _window, dialog = qapp_dialog
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    broken = Preset("broken", "Сломанный", "", values={"quality.nope": 1}, builtin=False)
    monkeypatch.setattr(P, "preset_by_key", lambda key: broken)
    before = dialog.settings.changed()
    dialog.combo_preset.addItem("Сломанный", "broken")
    dialog.combo_preset.setCurrentIndex(dialog.combo_preset.count() - 1)
    dialog._on_apply_preset()
    assert shown and "нет настройки «quality.nope»" in shown[0], shown
    assert dialog.settings.changed() == before


# --------------------------------------------------------------------- CLI

def test_cli_preset_list_show_and_yaml(tmp_path):
    from click.testing import CliRunner

    from corpus_builder.cli import cli

    res = CliRunner().invoke(cli, ["preset"])
    assert res.exit_code == 0, res.output
    listed = json.loads(res.output.split("показать:")[0])
    assert [x["key"] for x in listed][:4] == ["polite", "own_site", "academic", "big_corpus"]

    values = json.loads(CliRunner().invoke(cli, ["preset", "academic"]).output)["values"]
    assert values["crawlers.pdf.ocr_enabled"] is True

    out = tmp_path / "overlay.yaml"
    res3 = CliRunner().invoke(cli, ["preset", "big_corpus", "--yaml", str(out)])
    assert res3.exit_code == 0, res3.output
    import yaml
    tree = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert tree["pipeline"]["parallel_postproc"] is True
    assert tree["dedup"]["streaming"] is True
    assert tree["export"]["write_gzip"] is True
    assert "log_level" not in json.dumps(tree), "UI-настройки в config.yaml не пишутся"
    assert CliRunner().invoke(cli, ["preset", "nope"]).exit_code != 0


def test_cli_preset_yaml_merges_into_a_real_config(tmp_path):
    """Накидка пресета обязана сливаться с config.yaml без сюрпризов."""
    import yaml
    from click.testing import CliRunner

    from corpus_builder.cli import cli
    from corpus_builder.config import load_config

    base = tmp_path / "config.yaml"
    base.write_text('sources:\n  - {url: "http://x", type: html}\n'
                    f'output: {{corpus_file: "{tmp_path}/o/raw.jsonl", '
                    f'download_dir: "{tmp_path}/o/dl"}}\nquality: {{min_chars: 5000}}\n',
                    encoding="utf-8")
    overlay = tmp_path / "ov.yaml"
    assert CliRunner().invoke(cli, ["preset", "academic", "--yaml", str(overlay)]).exit_code == 0

    merged = json.loads(json.dumps(yaml.safe_load(base.read_text(encoding="utf-8"))))

    def deep_merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                deep_merge(a[k], v)
            else:
                a[k] = v
        return a

    deep_merge(merged, yaml.safe_load(overlay.read_text(encoding="utf-8")))
    target = tmp_path / "merged.yaml"
    target.write_text(yaml.safe_dump(merged, allow_unicode=True), encoding="utf-8")

    cfg = load_config(target)
    assert cfg.quality.min_chars == 400, "пресет не перекрыл файл"
    assert cfg.crawlers.pdf.ocr_enabled is True
    assert cfg.output.corpus_file == f"{tmp_path}/o/raw.jsonl"


def test_preset_overlay_yaml_is_loadable_for_every_builtin():
    import yaml

    from corpus_builder.models import AppConfig

    for preset in BUILTIN_PRESETS:
        tree = yaml.safe_load(preset_to_yaml(preset))
        assert isinstance(tree, dict), preset.key
        for section in tree:
            assert section in {"output", "crawlers", "quality", "dedup", "pipeline",
                              "export", "finetune"}, f"{preset.key}: секция {section}"
        raw = {"sources": [{"url": "http://x", "type": "html"}],
               "output": {"corpus_file": "o.jsonl", "download_dir": "d"}}
        for key, value in tree.items():
            if isinstance(value, dict) and isinstance(raw.get(key), dict):
                raw[key].update(value)
            else:
                raw[key] = value
        cfg = AppConfig(**raw)                             # не должно бросить
        assert cfg.output.corpus_file == "o.jsonl", f"{preset.key}: пути прогона затёрты"


def test_settings_overlay_yaml_roundtrip(tmp_path, monkeypatch):
    """`AppSettings.to_overlay_yaml` — то же представление что и накидка пресета."""
    import yaml

    s = AppSettings()
    s.set("output.request_delay", 1.25)
    s.set("crawlers.pdf.ocr_enabled", False)
    tree = yaml.safe_load(s.to_overlay_yaml())
    assert tree == {"output": {"request_delay": 1.25},
                    "crawlers": {"pdf": {"ocr_enabled": False}}}
