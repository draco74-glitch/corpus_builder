"""Тесты на дедупликацию."""
import json

from corpus_builder.models import DedupConfig
from corpus_builder.postproc.dedup import dedup_exact, dedup_by_url, run_dedup


def make_records():
    return [
        {"source_url": "https://a.com/1", "content": "Hello world this is a test",
         "status": "ok", "downloaded_files": []},
        {"source_url": "https://a.com/2", "content": "Hello world this is a test",  # точный дубль
         "status": "ok", "downloaded_files": []},
        {"source_url": "https://b.com/3", "content": "Completely different content here",
         "status": "ok", "downloaded_files": []},
    ]


def test_exact_dedup():
    records = make_records()
    dups = dedup_exact(records)
    assert "https://a.com/2" in dups
    assert dups["https://a.com/2"] == "https://a.com/1"


def test_url_dedup():
    records = [
        {"source_url": "https://example.com/page?utm_source=email&id=1", "status": "ok", "content": "a"},
        {"source_url": "https://example.com/page?id=1", "status": "ok", "content": "b"},  # тот же URL после canon
    ]
    dups = dedup_by_url(records)
    # Один из них должен быть помечен как дубль
    assert len(dups) == 1


def test_run_dedup_writes_output(tmp_path):
    records = make_records()
    in_file = tmp_path / "in.jsonl"
    out_file = tmp_path / "out.jsonl"
    with open(in_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    config = DedupConfig(exact=True, minhash=False, dedup_images=False)
    stats = run_dedup(in_file, out_file, config)

    assert stats["total"] == 3
    assert stats["removed"] == 1
    assert stats["kept"] == 2

    # Проверить, что в output есть флаг is_duplicate
    out_records = [json.loads(l) for l in out_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    dup = [r for r in out_records if r.get("is_duplicate")]
    assert len(dup) == 1
