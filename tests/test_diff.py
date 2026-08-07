"""Тесты на diff корпусов (Этап 12)."""
import json
from pathlib import Path

import pytest

from corpus_builder.diff import diff_corpora, _load_corpus


def _write_corpus(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_diff_added(tmp_path):
    """Новые записи в new, которых нет в old."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    _write_corpus(old, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
    ])
    _write_corpus(new, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
        {"source_url": "https://example.com/2", "content_sha1": "abc2", "content": "text2"},
    ])
    result = diff_corpora(old, new)
    assert result["stats"]["total_added"] == 1
    assert result["stats"]["total_removed"] == 0
    assert len(result["added"]) == 1
    assert result["added"][0]["source_url"] == "https://example.com/2"


def test_diff_removed(tmp_path):
    """Записи, которые есть в old, но исчезли в new."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    _write_corpus(old, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
        {"source_url": "https://example.com/2", "content_sha1": "abc2", "content": "text2"},
    ])
    _write_corpus(new, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
    ])
    result = diff_corpora(old, new)
    assert result["stats"]["total_removed"] == 1
    assert result["stats"]["total_added"] == 0


def test_diff_changed(tmp_path):
    """Тот же URL, но другой content_sha1 = изменённая запись."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    _write_corpus(old, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "old text"},
    ])
    _write_corpus(new, [
        {"source_url": "https://example.com/1", "content_sha1": "abc2", "content": "new text"},
    ])
    result = diff_corpora(old, new)
    assert result["stats"]["total_changed"] == 1
    assert result["changed"][0]["url"] == "https://example.com/1"
    assert result["changed"][0]["old_sha1"] == "abc1"
    assert result["changed"][0]["new_sha1"] == "abc2"


def test_diff_identical(tmp_path):
    """Идентичные корпуса — diff пустой."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    records = [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
        {"source_url": "https://example.com/2", "content_sha1": "abc2", "content": "text2"},
    ]
    _write_corpus(old, records)
    _write_corpus(new, records)
    result = diff_corpora(old, new)
    assert result["stats"]["total_added"] == 0
    assert result["stats"]["total_removed"] == 0
    assert result["stats"]["total_changed"] == 0


def test_diff_html_output(tmp_path):
    """HTML-отчёт генерируется корректно."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    html_path = tmp_path / "report.html"
    _write_corpus(old, [
        {"source_url": "https://example.com/1", "content_sha1": "abc1", "content": "text1"},
    ])
    _write_corpus(new, [
        {"source_url": "https://example.com/2", "content_sha1": "abc2",
         "content": "text2", "source_type": "html", "language": "en"},
    ])
    diff_corpora(old, new, html_output=html_path)
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "Отчёт сравнения корпусов" in html
    assert "+1" in html  # Добавлено
    assert "example.com/2" in html  # URL в таблице


def test_diff_empty_files(tmp_path):
    """Пустые файлы — diff тоже пустой."""
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    old.write_text("", encoding="utf-8")
    new.write_text("", encoding="utf-8")
    result = diff_corpora(old, new)
    assert result["stats"]["total_old"] == 0
    assert result["stats"]["total_new"] == 0


def test_diff_file_not_found(tmp_path):
    """Если файла нет — выбрасывается FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        diff_corpora(tmp_path / "nonexistent.jsonl", tmp_path / "also_nonexistent.jsonl")
