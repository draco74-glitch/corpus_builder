"""Дедупликация корпуса."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from datasketch import MinHash, MinHashLSH

from ..logging_setup import get_logger
from ..models import DedupConfig
from ..text_utils import canonical_url, normalize_text, shingles, text_sha1

log = get_logger(__name__)


def iter_records(corpus_file: str | Path) -> Iterable[dict]:
    from ..writer import open_corpus_reader
    with open_corpus_reader(corpus_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                log.warning(f"Bad JSON line skipped: {e}")


def dedup_exact(records: list[dict]) -> dict[str, str]:
    seen: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    for r in records:
        if r.get("status") != "ok":
            continue
        text = normalize_text(r.get("content") or "")
        if not text:
            continue
        sha = text_sha1(text)
        url = r.get("source_url", "")
        if sha in seen:
            duplicates[url] = seen[sha]
        else:
            seen[sha] = url
    log.info(f"Exact dedup: {len(duplicates)} duplicates out of {len(records)} records")
    return duplicates


def dedup_minhash(records: list[dict], num_perm: int = 128,
                  threshold: float = 0.85) -> dict[str, str]:
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    duplicates: dict[str, str] = {}
    for r in records:
        if r.get("status") != "ok":
            continue
        text = normalize_text(r.get("content") or "")
        if not text:
            continue
        url = r.get("source_url", "")
        mh = MinHash(num_perm=num_perm)
        for s in shingles(text, k=5):
            mh.update(s.encode("utf-8"))
        matches = lsh.query(mh)
        if matches:
            duplicates[url] = matches[0]
        else:
            lsh.insert(url, mh)
    log.info(f"MinHash dedup (threshold={threshold}): {len(duplicates)} near-duplicates")
    return duplicates


def dedup_minhash_streaming(corpus_file: str | Path, num_perm: int = 128,
                            threshold: float = 0.85, batch_size: int = 1000,
                            on_progress: Callable[[int, int], None] | None = None
                            ) -> dict[str, str]:
    """Streaming MinHash дедупликация для больших корпусов (Улучшение 7)."""
    from ..writer import open_corpus_reader

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    duplicates: dict[str, str] = {}
    processed = 0
    total = 0

    with open_corpus_reader(corpus_file) as f:
        total = sum(1 for _ in f)

    batch: list[tuple[str, MinHash]] = []

    with open_corpus_reader(corpus_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok":
                continue
            text = normalize_text(r.get("content") or "")
            if not text:
                continue
            url = r.get("source_url", "")

            mh = MinHash(num_perm=num_perm)
            for s in shingles(text, k=5):
                mh.update(s.encode("utf-8"))
            batch.append((url, mh))
            processed += 1

            if len(batch) >= batch_size:
                for url, mh in batch:
                    matches = lsh.query(mh)
                    if matches:
                        duplicates[url] = matches[0]
                    else:
                        try:
                            lsh.insert(url, mh)
                        except Exception:
                            pass
                batch.clear()
                if on_progress:
                    on_progress(processed, total)

    for url, mh in batch:
        matches = lsh.query(mh)
        if matches:
            duplicates[url] = matches[0]
        else:
            try:
                lsh.insert(url, mh)
            except Exception:
                pass

    log.info(f"Streaming MinHash dedup: {len(duplicates)} duplicates out of {processed} records")
    return duplicates


def dedup_by_url(records: list[dict]) -> dict[str, str]:
    seen: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    for r in records:
        url = r.get("source_url", "")
        if not url:
            continue
        canon = canonical_url(url)
        if canon in seen:
            duplicates[url] = seen[canon]
        else:
            seen[canon] = url
    log.info(f"URL dedup: {len(duplicates)} duplicates")
    return duplicates


def dedup_images(records: list[dict]) -> dict[str, str]:
    seen: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    for r in records:
        for f in r.get("downloaded_files", []):
            path = f.get("local_path")
            sha = f.get("sha1")
            if not path or not sha:
                continue
            if sha in seen:
                duplicates[path] = seen[sha]
            else:
                seen[sha] = path
    log.info(f"Image dedup: {len(duplicates)} duplicates out of {len(seen)} unique images")
    return duplicates


def run_dedup(corpus_file: str | Path, output_file: str | Path, config: DedupConfig) -> dict:
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    records = list(iter_records(corpus_file))
    log.info(f"Loaded {len(records)} records from {corpus_file}")

    duplicates: dict[str, str] = {}

    if config.exact:
        duplicates.update(dedup_exact(records))

    if config.minhash:
        remaining = [r for r in records if r.get("source_url") not in duplicates]
        duplicates.update(dedup_minhash(
            remaining,
            num_perm=config.minhash_num_perm,
            threshold=config.minhash_threshold,
        ))

    url_dups = dedup_by_url(records)
    duplicates.update(url_dups)

    image_dups = {}
    if config.dedup_images:
        image_dups = dedup_images(records)

    kept = 0
    removed = 0
    with open(output_file, "w", encoding="utf-8") as f:
        for r in records:
            url = r.get("source_url", "")
            if url in duplicates:
                r["is_duplicate"] = True
                r["duplicate_of"] = duplicates[url]
                removed += 1
            else:
                r["is_duplicate"] = False
                r["duplicate_of"] = None
                kept += 1

            if image_dups:
                new_files = []
                for df in r.get("downloaded_files", []):
                    p = df.get("local_path")
                    if p and p in image_dups:
                        df["is_duplicate"] = True
                        df["duplicate_of"] = image_dups[p]
                    new_files.append(df)
                r["downloaded_files"] = new_files

            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {
        "total": len(records), "kept": kept, "removed": removed,
        "image_duplicates": len(image_dups),
    }
    log.info(f"Dedup done: {stats}")
    return stats
