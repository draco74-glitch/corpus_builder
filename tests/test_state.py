"""Тесты на State."""
import json

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


# ============================================================ А5: журнал + компакция

def test_checkpoint_writes_only_new_events(tmp_path):
    """Промежуточный чекпойнт не имеет права переписывать всё состояние."""
    f = tmp_path / "state.json"
    s = State(f)
    for i in range(2000):
        s.mark_done(f"http://x/{i}")
    s.save(compact=False)                       # финальный снимок прошлого рана
    snapshot_size = f.stat().st_size

    s.mark_done("http://x/new")
    s.save(compact=True)

    assert f.stat().st_size == snapshot_size, "снимок переписан на промежуточном чекпойнте"
    assert s.journal_file.exists()
    assert s.journal_file.read_bytes().count(b"\n") == 1
    assert State(f).is_done("http://x/new")


def test_checkpoint_cost_does_not_grow_with_state(tmp_path):
    """Было O(n) на запись → O(n²) за ран; стало — константа (A5)."""
    import time

    timings = {}
    for n in (2000, 40000):
        f = tmp_path / f"state_{n}.json"
        s = State(f)
        for i in range(n):
            s.mark_done(f"http://e.com/{i}?pad={'x' * 40}")
        s.save(compact=False)
        t0 = time.perf_counter()
        for k in range(20):
            s.mark_done(f"http://e.com/tick{k}")
            s.save(compact=True)
        timings[n] = time.perf_counter() - t0
    # 20-кратный рост состояния не должен дать и двукратного роста стоимости
    assert timings[40000] < max(0.25, timings[2000] * 4), timings


def test_compaction_rewrites_snapshot_and_clears_journal(tmp_path):
    f = tmp_path / "state.json"
    s = State(f, compact_after=50)
    for i in range(40):
        s.mark_done(f"http://a/{i}")
    s.save(compact=True)
    assert s.journal_file.exists()
    for i in range(40, 120):
        s.mark_done(f"http://a/{i}")
    s.save(compact=True)                        # порог пройден → компакция
    assert not s.journal_file.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert len(data["done"]) == 120 and data["sorted"] is True
    assert State(f).done_count == 120


def test_reset_and_clear_errors_survive_reload(tmp_path):
    f = tmp_path / "state.json"
    s = State(f)
    s.mark_done("http://keep/1")
    s.save(compact=False)

    s = State(f)
    s.mark_error("http://err/1")
    s.save(compact=True)
    s2 = State(f)
    assert s2.is_error("http://err/1") and s2.is_done("http://keep/1")

    s2.clear_errors()
    s2.save(compact=True)
    s3 = State(f)
    assert s3.error_count == 0 and s3.done_count == 1, "clear_errors не дожил до перезагрузки"

    s3.reset()
    s3.save(compact=True)
    s4 = State(f)
    assert s4.done_count == 0 and s4.error_count == 0


def test_torn_journal_tail_is_dropped_not_applied(tmp_path):
    """Уронили процесс посреди записи строки — полусобытие применять нельзя."""
    f = tmp_path / "state.json"
    s = State(f)
    s.mark_done("http://good/1")
    s.save(compact=True)
    s.journal_file.open("ab").write(b'{"d": "http://torn')

    r = State(f)
    assert r.is_done("http://good/1")
    assert not r.is_done("http://torn")
    assert r.journal_file.read_bytes().endswith(b"\n"), "хвост не обрезан"
    # и дальше журнал пригоден к записи
    r.mark_done("http://good/2")
    r.save(compact=True)
    assert State(f).done_count == 2


def test_garbage_line_stops_replay_at_that_point(tmp_path):
    f = tmp_path / "state.json"
    s = State(f)
    s.save(compact=False)
    s.journal_file.write_bytes('{"d":"http://a"}\nэто не json\n{"d":"http://b"}\n'
                               .encode("utf-8"))
    r = State(f)
    assert r.is_done("http://a")
    assert not r.is_done("http://b")


def test_old_snapshot_format_loads_without_journal(tmp_path):
    """Обратная совместимость: state.json из версии до журнала."""
    f = tmp_path / "state.json"
    f.write_text('{"done": ["http://old/1"], "errors": ["http://old/e"], "sorted": true}',
                 encoding="utf-8")
    s = State(f)
    assert s.is_done("http://old/1") and s.is_error("http://old/e")
    s.mark_done("http://new/1")
    s.save(compact=True)
    assert State(f).done_count == 2


def test_concurrent_marks_and_checkpoints_lose_nothing(tmp_path):
    import threading

    f = tmp_path / "state.json"
    s = State(f, compact_after=300)
    failures: list[BaseException] = []

    def worker(tag):
        try:
            for i in range(600):
                s.mark_done(f"http://p/{tag}/{i}")
                if i % 40 == 0:
                    s.save(compact=True)
            s.save(compact=False)
        except BaseException as e:              # noqa: BLE001
            failures.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in "abcd"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failures, failures
    r = State(f)
    assert r.done_count == 4 * 600, r.done_count
    assert len(set(r._done)) == r.done_count, "дубли событий"


def test_done_removes_url_from_errors(tmp_path):
    """Успех после прошлой ошибки не должен оставлять URL в «не трогать»."""
    s = State(tmp_path / "state.json")
    s.mark_error("http://x")
    assert s.is_error("http://x")
    s.mark_done("http://x")
    assert s.is_done("http://x") and not s.is_error("http://x")
    assert s.error_count == 0

    # то же самое через журнал: перечитали и вижу consistent-состояние
    s.save(compact=True)
    r = State(tmp_path / "state.json")
    assert r.is_done("http://x") and not r.is_error("http://x")
