"""Тесты на дедупликацию.

Ключ дедупликации — индекс записи, а не `source_url` (см. C3/C4 в
`corpus_builder/postproc/dedup.py`): повторяющийся или пустой URL не должен
ни ронять пост-процесс, ни молча съедать контент.
"""
import json

from corpus_builder.models import DedupConfig
from corpus_builder.postproc.dedup import (
    dedup_by_url,
    dedup_exact,
    dedup_minhash,
    run_dedup,
)


def make_records():
    return [
        {"source_url": "https://a.com/1", "content": "Hello world this is a test",
         "status": "ok", "downloaded_files": []},
        {"source_url": "https://a.com/2", "content": "Hello world this is a test",  # точный дубль
         "status": "ok", "downloaded_files": []},
        {"source_url": "https://b.com/3", "content": "Completely different content here",
         "status": "ok", "downloaded_files": []},
    ]


def _write(tmp_path, records, name="in.jsonl"):
    in_file = tmp_path / name
    with open(in_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    return in_file


def test_exact_dedup():
    records = make_records()
    dups = dedup_exact(records)
    assert dups == {1: 0}          # вторая запись — дубль первой
    assert records[2]["content"] not in [records[i]["content"] for i in dups]


def test_url_dedup():
    records = [
        {"source_url": "https://example.com/page?utm_source=email&id=1",
         "status": "ok", "content": "a" * 50},
        {"source_url": "https://example.com/page?id=1",          # тот же URL после canon
         "status": "ok", "content": "b" * 10},
    ]
    dups = dedup_by_url(records)
    assert len(dups) == 1
    # дубль ссылается на индекс оригинала, а не на URL
    assert dups == {1: 0}


def test_url_dedup_keeps_longer_content_as_original():
    """C4: с одного URL страница отдала сначала обрезанный, потом полный текст."""
    records = [
        {"source_url": "https://example.com/p", "status": "ok", "content": "short"},
        {"source_url": "https://example.com/p?utm_source=rss", "status": "ok", "content": "long text " * 20},
    ]
    dups = dedup_by_url(records)
    assert dups == {0: 1}          # короткая запись помечена дублем длинной


def test_no_crash_on_duplicate_source_url_with_minhash():
    """C3: два разных текста под одним URL роняли весь пост-процесс
    (ValueError: The given key already exists в MinHashLSH.insert)."""
    records = [
        {"source_url": "http://a/1", "status": "ok",
         "content": "photosynthesis converts light into chemical energy " * 8},
        {"source_url": "http://a/1", "status": "ok",
         "content": "suspension bridges carry deck loads through cables and towers " * 8},
    ]
    dups = dedup_minhash(records, threshold=0.85)   # не должно бросать исключение
    assert dups == {}               # разные тексты — не дубли


def test_records_without_url_are_not_collapsed():
    """C4: записи с пустым source_url раньше помечались дублями несуществующей
    записи и молча удалялись на следующем шаге."""
    records = [
        {"source_url": "", "status": "ok", "content": "alpha text about amplifiers " * 5},
        {"source_url": "", "status": "ok", "content": "beta text about transformers " * 5},
    ]
    assert dedup_by_url(records) == {}
    assert dedup_exact(records) == {}
    assert dedup_minhash(records, threshold=0.85) == {}


def test_run_dedup_writes_output(tmp_path):
    records = make_records()
    in_file = _write(tmp_path, records)
    out_file = tmp_path / "out.jsonl"

    config = DedupConfig(exact=True, minhash=False, dedup_images=False)
    stats = run_dedup(in_file, out_file, config)

    assert stats["total"] == 3
    assert stats["removed"] == 1
    assert stats["kept"] == 2

    out_records = [json.loads(l) for l in out_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    dup = [r for r in out_records if r.get("is_duplicate")]
    assert len(dup) == 1
    # человекочитаемая ссылка на оригинала сохранилась
    assert dup[0]["duplicate_of"] == "https://a.com/1"


def test_run_dedup_minhash_enabled_end_to_end(tmp_path):
    """Раньше minhash-проход в `run_dedup` не покрывался ни одним тестом,
    поэтому C3 жил в ветке `minhash=True` незамеченным."""
    records = [
        {"source_url": "http://x/dup", "status": "ok", "content": "alpha " * 60},
        {"source_url": "http://x/dup", "status": "ok", "content": "bravo " * 60},
        {"source_url": "http://y/1", "status": "ok", "content": "charlie " * 60},
        {"source_url": "http://y/2", "status": "ok", "content": "charlie " * 60},
    ]
    in_file = _write(tmp_path, records)
    stats = run_dedup(in_file, tmp_path / "out.jsonl", DedupConfig(exact=True, minhash=True))
    assert stats["total"] == 4
    # «charlie»×2 — точный дубль, вторая «x/dup» — дубль по URL: обе помечены,
    # но пост-процесс не падает (раньше — ValueError из LSH).
    assert stats["removed"] == 2
    assert stats["kept"] == 2


def test_minhash_detects_near_duplicates(tmp_path):
    base = " ".join(
        f"Sentence number {i} about amplifier biasing and thermal drift in analog design."
        for i in range(40)
    )                                   # Jaccard(base, near) ≈ 0.97
    near = base + " One extra closing sentence about layout parasitics."
    different = " ".join(
        f"Bakery note {i}: sourdough hydration changes the crumb and the fermentation window."
        for i in range(40)
    )
    records = [
        {"source_url": "http://a/1", "status": "ok", "content": base},
        {"source_url": "http://a/2", "status": "ok", "content": near},
        {"source_url": "http://a/3", "status": "ok", "content": different},
    ]
    # дефолтный порог из DedupConfig (0.85) обязан ловить такие пары
    cfg = DedupConfig()
    dups = dedup_minhash(records, num_perm=cfg.minhash_num_perm, threshold=cfg.minhash_threshold)
    assert dups.get(1) == 0
    assert 2 not in dups
