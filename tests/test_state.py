"""Тесты на State."""
import json
import os

from corpus_builder.state import State


def test_state_save_load(tmp_path):
    f = tmp_path / "state.json"
    s = State(f)
    s.mark_done("https://example.com/1")
    s.mark_done("https://example.com/2")
    s.mark_error("https://example.com/3")
    s.save()

    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "https://example.com/1" in data["done"]
    assert "https://example.com/3" in data["errors"]


def test_state_resume(tmp_path):
    f = tmp_path / "state.json"
    s = State(f)
    s.mark_done("https://example.com/1")
    s.save()

    s2 = State(f)
    assert s2.is_done("https://example.com/1")
    assert not s2.is_done("https://example.com/2")


def test_state_atomic_write(tmp_path):
    """Проверить, что запись идёт через tmp + os.replace."""
    f = tmp_path / "state.json"
    s = State(f)
    s.mark_done("url1")
    s.save()
    # После записи tmp-файла не должно остаться
    assert not (tmp_path / "state.json.tmp").exists()
    assert f.exists()


def test_state_counters(tmp_path):
    s = State(tmp_path / "state.json")
    s.mark_done("a")
    s.mark_done("b")
    s.mark_error("c")
    assert s.done_count == 2
    assert s.error_count == 1
