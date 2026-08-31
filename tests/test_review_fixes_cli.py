"""Б: валидация конфига из CLI/GUI и маскирование секретов при экспорте."""
from __future__ import annotations

import json

import pytest

from corpus_builder.cli import validate_config_file

BASE = """
sources:
  - url: "https://example.com/a"
    type: html
output:
  corpus_file: out/raw.jsonl
  download_dir: out/dl
  contact_email: "me@example.com"
quality:
  min_chars: 200
"""


def _write(tmp_path, text, name="c.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_clean_config_has_no_problems(tmp_path):
    assert validate_config_file(_write(tmp_path, BASE)) == []


def test_missing_file_is_reported_not_raised(tmp_path):
    problems = validate_config_file(tmp_path / "nope.yaml")
    assert len(problems) == 1 and "не найден" in problems[0]


def test_yaml_syntax_error_reports_line(tmp_path):
    path = _write(tmp_path, 'sources:\n  - url: "http://a\n')
    problems = validate_config_file(path)
    assert len(problems) == 1
    assert "синтаксис YAML" in problems[0] and "строка" in problems[0]


def test_schema_errors_list_field_paths(tmp_path):
    path = _write(tmp_path, BASE.replace("type: html", "type: telegram")
                  + "\n  extra_root: 1\n")
    problems = validate_config_file(path)
    assert any("sources.0.type" in x for x in problems), problems


def test_root_must_be_mapping(tmp_path):
    assert any("словарём" in x for x in validate_config_file(_write(tmp_path, "- 1\n- 2\n")))


def test_empty_file_reported(tmp_path):
    assert validate_config_file(_write(tmp_path, "# только комментарий\n"))


def test_polite_email_warning_for_api_sources(tmp_path):
    """Без контакта вежливые API отдают 403 — об этом надо сказать до прогона."""
    text = BASE.replace('  contact_email: "me@example.com"\n', "").replace("type: html",
                                                                           "type: wikipedia")
    problems = validate_config_file(_write(tmp_path, text))
    assert any("e-mail" in x for x in problems), problems
    # для чистого html-краула контакта не требуем
    assert validate_config_file(_write(tmp_path, BASE.replace(
        '  contact_email: "me@example.com"\n', ""))) == []


def test_duplicate_url_after_canonicalization_is_reported(tmp_path):
    """Дубль «с utm и без» краул схлопывает в один — надо сказать это заранее."""
    text = BASE.replace('    type: html\n',
                        '    type: html\n  - url: "https://example.com/a?utm_source=x"\n'
                        '    type: html\n')
    problems = validate_config_file(_write(tmp_path, text))
    assert any("после нормализации" in x for x in problems), problems


def test_empty_url_and_silly_settings_are_reported(tmp_path):
    text = (BASE.replace('  - url: "https://example.com/a"', '  - url: ""')
            .replace("min_chars: 200", "min_chars: 0")
            + "\npipeline:\n  per_url_timeout_minutes: 0\n")
    problems = validate_config_file(_write(tmp_path, text))
    assert any("пустой url" in x for x in problems), problems
    assert any("min_chars" in x for x in problems), problems
    assert any("per_url_timeout_minutes" in x for x in problems), problems


def test_aggressive_delay_is_reported(tmp_path):
    text = BASE.replace("  contact_email: \"me@example.com\"",
                        "  contact_email: \"me@example.com\"\n  request_delay: 0.0")
    problems = validate_config_file(_write(tmp_path, text))
    assert any("request_delay" in x for x in problems), problems


# ---------------------------------------------------------------- CLI-обвязка

click = pytest.importorskip("click")
from click.testing import CliRunner  # noqa: E402

from corpus_builder.cli import cli as cli_group  # noqa: E402


def test_cli_validate_exit_codes(tmp_path):
    good = _write(tmp_path, BASE)
    res = CliRunner().invoke(cli_group, ["-c", good, "validate"])
    assert res.exit_code == 0, res.output
    assert "Валидно" in res.output

    bad = _write(tmp_path, BASE.replace("type: html", "type: nope"))
    res2 = CliRunner().invoke(cli_group, ["-c", bad, "validate"])
    assert res2.exit_code == 1
    assert "Невалидно" in res2.output


def test_cli_validate_runs_with_broken_minus_c(tmp_path):
    """`validate --config` обязан работать, даже если -c ссылается на битый файл."""
    good = _write(tmp_path, BASE, "good.yaml")
    _write(tmp_path, "sources: [\n", "broken.yaml")
    res = CliRunner().invoke(cli_group, ["-c", str(tmp_path / "broken.yaml"),
                                         "validate", "--config", good])
    assert res.exit_code == 0, (res.exit_code, res.output)


def test_cli_other_commands_fail_cleanly_on_broken_config(tmp_path):
    broken = _write(tmp_path, "output: 5\n")
    res = CliRunner().invoke(cli_group, ["-c", broken, "stats"])
    assert res.exit_code == 2
    assert "Ошибка конфигурации" in res.output
    assert "Traceback" not in res.output


def test_cli_schema_is_valid_json(tmp_path):
    out = tmp_path / "schema.json"
    res = CliRunner().invoke(cli_group, ["schema", "--out", str(out)])
    assert res.exit_code == 0, res.output
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert "sources" in schema["properties"] and "output" in schema["properties"]


# ------------------------------------------------- экспорт настроек из GUI (Б)

def _gui_export(tmp_path, monkeypatch, answer, token="ghp_supersecret"):
    """Реальный путь «Настройки → Экспорт»: что физически попадает в файл."""
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from corpus_builder import gui

    app = QApplication.instance() or QApplication([])   # noqa: F841 — держим процесс Qt
    window = gui.MainWindow()
    assert app.applicationName() is not None
    window.app_settings.github.token = token
    window.app_settings.stackexchange.api_key = "se_secret"
    monkeypatch.setattr(window.app_settings, "save", lambda: None)

    out = tmp_path / "settings.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "JSON (*.json)")))
    asked: list = []

    def fake_question(*a, **k):
        asked.append(a[2] if len(a) > 2 else a)
        return answer

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    window._export_settings()
    window.close()
    data = json.loads(out.read_text(encoding="utf-8"))
    return data, asked, shown


def test_gui_export_hides_tokens_by_default(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    data, asked, shown = _gui_export(tmp_path, monkeypatch, QMessageBox.StandardButton.No)
    assert asked, "о секретах нужно спросить"
    blob = json.dumps(data)
    assert "ghp_supersecret" not in blob and "se_secret" not in blob
    assert data["github"]["token"] == "***redacted***"
    assert any("Скрыты" in s or "hidden" in s.lower() for s in shown), shown


def test_gui_export_with_secrets_when_user_confirms(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    data, _, shown = _gui_export(tmp_path, monkeypatch, QMessageBox.StandardButton.Yes)
    assert data["github"]["token"] == "ghp_supersecret"
    assert not any("Скрыты" in s for s in shown)
