"""Дедупликация корпуса.

ИДЕНТИЧНОСТЬ ЗАПИСИ (C3/C4). Все функции дедупликации работают с индексами
записей во входном списке (`dict[int, int]`: индекс-дубль → индекс оригинала),
а НЕ с `source_url` как ключом. Ключ по URL давал две ошибки:

  * `MinHashLSH.insert(key)` бросал `ValueError: The given key already exists`
    для двух записей с одинаковым URL — падал весь пост-процесс (C3);
  * записи без `source_url` (или с одинаковым URL и разным содержимым)
    схлопывались в «дубликаты» друг друга, молча теряя контент (C4).

`duplicate_of` в выходном JSONL по-прежнему человекочитаемый URL оригинала.

Стратегии (I3/I4): `run_dedup` (весь корпус в RAM), `streaming` (MinHash по
частям) и `incremental` (персистентный индекс между запусками) — выбираются
через `run_dedup_adaptive`.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

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


def _content_of(r: dict) -> str:
    return normalize_text(r.get("content") or "")


def dedup_exact(records: list[dict]) -> dict[int, int]:
    """Полные дубли по sha1 нормализованного текста. index → index оригинала."""
    seen: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for i, r in enumerate(records):
        if r.get("status") != "ok":
            continue
        text = _content_of(r)
        if not text:
            continue
        sha = text_sha1(text)
        if sha in seen:
            duplicates[i] = seen[sha]
        else:
            seen[sha] = i
    log.info(f"Exact dedup: {len(duplicates)} duplicates out of {len(records)} records")
    return duplicates


def dedup_minhash(records: list[dict], num_perm: int = 128,
                  threshold: float = 0.85) -> dict[int, int]:
    """Нечёткие дубли (MinHash LSH). Индекс записи → индекс оригинала.

    LSH ключуется стабильным `str(i)`, поэтому повторяющиеся или пустые URL не
    влияют на работу индекса (C3).
    """
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    duplicates: dict[int, int] = {}
    for i, r in enumerate(records):
        if r.get("status") != "ok":
            continue
        text = _content_of(r)
        if not text:
            continue
        mh = MinHash(num_perm=num_perm)
        for s in shingles(text, k=5):
            mh.update(s.encode("utf-8"))
        matches = lsh.query(mh)
        if matches:
            duplicates[i] = int(matches[0])
        else:
            lsh.insert(str(i), mh)
    log.info(f"MinHash dedup (threshold={threshold}): {len(duplicates)} near-duplicates")
    return duplicates


def dedup_minhash_streaming(corpus_file: str | Path, num_perm: int = 128,
                            threshold: float = 0.85, batch_size: int = 1000,
                            on_progress: Callable[[int, int], None] | None = None
                            ) -> dict[int, int]:
    """Streaming MinHash дедупликация для больших корпусов (Улучшение 7).

    Возвращает индекс-дубль → индекс оригинала; в RAM держит только LSH.
    """
    from ..writer import open_corpus_reader

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    duplicates: dict[int, int] = {}
    processed = 0
    total = 0

    with open_corpus_reader(corpus_file) as f:
        total = sum(1 for _ in f)

    batch: list[tuple[int, MinHash]] = []

    def flush(batch: list[tuple[int, MinHash]]) -> None:
        for local_idx, mh in batch:
            matches = lsh.query(mh)
            if matches:
                duplicates[local_idx] = int(matches[0])
            else:
                lsh.insert(str(local_idx), mh)
        batch.clear()

    # Индекс считаем ровно так же, как `iter_records` (который позже строит
    # список records): +1 на каждую успешно разобранную непустую строку,
    # независимо от status/содержимого — иначе индексы разъедутся.
    idx = -1
    with open_corpus_reader(corpus_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            idx += 1
            if r.get("status") != "ok":
                continue
            text = _content_of(r)
            if not text:
                continue

            mh = MinHash(num_perm=num_perm)
            for s in shingles(text, k=5):
                mh.update(s.encode("utf-8"))
            batch.append((idx, mh))
            processed += 1

            if len(batch) >= batch_size:
                flush(batch)
                if on_progress:
                    on_progress(processed, total)

    flush(batch)

    log.info(f"Streaming MinHash dedup: {len(duplicates)} duplicates "
             f"out of {processed} records")
    return duplicates


def dedup_by_url(records: list[dict]) -> dict[int, int]:
    """Дубли по канонизированному URL (utm_*, порядок query, без fragment).

    Правила (C4):
      * записи без `source_url` НИКОГДА не участвуют в URL-дедупликации —
        у независимых страниц (локальные PDF, файлы из ZIP) URL может быть
        пустым, и раньше они схлопывались в дубли друг друга;
      * «оригиналом» считается запись с БОЛЕЕ ДЛИННЫМ содержимым, чтобы при
        склейке не потерять текст (один URL мог отдать разный контент);
      * разные схема/хост с одинаковым путём не схлопываются — канонизация их
        не выбрасывает.
    """
    seen: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for i, r in enumerate(records):
        url = r.get("source_url") or ""
        if not url:
            continue
        canon = canonical_url(url)
        if canon in seen:
            original = seen[canon]
            if len(_content_of(r)) > len(_content_of(records[original])):
                duplicates[original] = i      # богатая запись — новый оригинал
                seen[canon] = i
            else:
                duplicates[i] = original
        else:
            seen[canon] = i
    log.info(f"URL dedup: {len(duplicates)} duplicates")
    return duplicates


def dedup_images(records: list[dict]) -> dict[str, str]:
    """Дубли скачанных файлов по sha1. local_path → local_path оригинала.

    Файлы на диске уникальны по пути, поэтому ключ — путь, а не индекс.
    """
    seen: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    for r in records:
        for f in r.get("downloaded_files", []) or []:
            path = f.get("local_path")
            sha = f.get("sha1")
            if not path or not sha:
                continue
            if sha in seen and seen[sha] != path:
                duplicates[path] = seen[sha]
            else:
                seen.setdefault(sha, path)
    log.info(f"Image dedup: {len(duplicates)} duplicates "
             f"out of {len(seen)} unique images")
    return duplicates


def _merge(*maps: dict[int, int]) -> dict[int, int]:
    """Объединить отображения дублей и схлопнуть цепочки в плоские.

    Порядок карт важен: первая (exact) достовернее, и её решение не
    перезаписывается. Каждый дубль в результате указывает на запись, которая
    сама не является дублем; зациклённые цепочки обрываются.
    """
    raw: dict[int, int] = {}
    for m in maps:
        for dup, orig in m.items():
            raw.setdefault(dup, orig)

    flat: dict[int, int] = {}
    limit = len(raw) + 1
    for dup in raw:
        node = raw[dup]
        steps = 0
        while node in raw and node != dup and steps < limit:
            node = raw[node]
            steps += 1
        flat[dup] = node
    return flat


def _write_dedup_output(records: list[dict], duplicates: dict[int, int | None],
                        output_file: str | Path, config: DedupConfig,
                        extra: dict | None = None) -> dict:
    """Единая запись результата дедупликации (используется всеми стратегиями)."""
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    image_dups: dict[str, str] = {}
    if config.dedup_images:
        image_dups = dedup_images(records)

    kept = removed = 0
    with open(output_file, "w", encoding="utf-8") as f:
        for i, r in enumerate(records):
            if i in duplicates:
                orig = duplicates[i]
                r["is_duplicate"] = True
                r["duplicate_of"] = (records[orig].get("source_url") or None) \
                    if orig is not None else None
                removed += 1
            else:
                r["is_duplicate"] = False
                r["duplicate_of"] = None
                kept += 1

            if image_dups:
                for df in r.get("downloaded_files", []) or []:
                    p = df.get("local_path")
                    if p and p in image_dups:
                        df["is_duplicate"] = True
                        df["duplicate_of"] = image_dups[p]

            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {"total": len(records), "kept": kept, "removed": removed,
             "image_duplicates": len(image_dups)}
    if extra:
        stats.update(extra)
    log.info(f"Dedup done: {stats}")
    return stats


def run_dedup(corpus_file: str | Path, output_file: str | Path,
              config: DedupConfig) -> dict:
    """Классическая дедупликация: весь корпус в RAM."""
    corpus_file = Path(corpus_file)
    records = list(iter_records(corpus_file))
    log.info(f"Loaded {len(records)} records from {corpus_file}")

    duplicate_maps: list[dict[int, int]] = []

    if config.exact:
        duplicate_maps.append(dedup_exact(records))

    if config.minhash:
        already_dup: set[int] = set()
        for m in duplicate_maps:
            already_dup |= set(m)
        remaining_idx = [i for i in range(len(records)) if i not in already_dup]
        remaining = [records[i] for i in remaining_idx]
        rel = dedup_minhash(
            remaining,
            num_perm=config.minhash_num_perm,
            threshold=config.minhash_threshold,
        )
        # локальные индексы minhash-прохода → индексы полного корпуса
        duplicate_maps.append(
            {remaining_idx[d]: remaining_idx[o] for d, o in rel.items()}
        )

    duplicate_maps.append(dedup_by_url(records))
    return _write_dedup_output(records, _merge(*duplicate_maps), output_file, config)


def run_dedup_adaptive(corpus_file: str | Path, output_file: str | Path,
                       config: DedupConfig) -> dict:
    """Дедупликация с выбором стратегии по конфигу (I3/I4: настройки-потребители).

    `streaming`   → MinHash по частям, без загрузки всего корпуса в RAM;
    `incremental` → персистентный индекс `IncrementalDedup` (повторные запуски
                    дёшевы, уже виденные URL не пересчитываются);
    иначе         → обычный `run_dedup`.
    """
    if config.incremental:
        from ..incremental_dedup import IncrementalDedup

        index = config.incremental_index_file
        inc = IncrementalDedup(index, threshold=config.minhash_threshold,
                               num_perm=config.minhash_num_perm)
        url_dups = inc.process_new_corpus(corpus_file)
        records = list(iter_records(corpus_file))
        by_url = {r.get("source_url"): i for i, r in enumerate(records)
                  if r.get("source_url")}
        duplicates = {by_url[u]: by_url.get(orig) for u, orig in url_dups.items()
                      if u in by_url}
        return _write_dedup_output(records, duplicates, output_file, config,
                                   extra={"index_file": str(index),
                                          "index_size": len(inc.processed_urls)})

    if config.streaming and config.minhash:
        idx_dups = dedup_minhash_streaming(
            corpus_file, num_perm=config.minhash_num_perm,
            threshold=config.minhash_threshold)
        records = list(iter_records(corpus_file))
        other: dict[int, int] = dedup_exact(records) if config.exact else {}
        other.update(dedup_by_url(records))
        return _write_dedup_output(records, _merge(other, idx_dups), output_file, config)

    return run_dedup(corpus_file, output_file, config)
