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
from ..text_utils import (canonical_url, normalize_text, shingles_bytes, text_sha1)

log = get_logger(__name__)


def _make_minhash(token_bytes: list[bytes], num_perm: int) -> MinHash:
    """MinHash из списка байтов, пакетом если datasketch это умеет (A1, ×9)."""
    mh = MinHash(num_perm=num_perm)
    update_batch = getattr(mh, "update_batch", None)
    if update_batch is not None and token_bytes:
        try:
            update_batch(token_bytes)
            return mh
        except Exception:                     # noqa: BLE001 — старый API/пустой вход
            pass
    for item in token_bytes:
        mh.update(item)
    return mh


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
    """Нормализованный текст записи.

    Краулер (`crawlers/base.py`) и стадия normalize уже нормализуют контент и
    пишут `content_sha1` от нормализованного текста. Повторный normalize_text
    на каждой стадии стоил ~1.3 с на 2000 записей ×3 прохода (A2), поэтому:
      * если запис помечена `content_normalized` — текст используем как есть;
      * иначе нормализуем, но результат кэшируем в самой записи.
    """
    if r.get("content_normalized"):
        return r.get("content") or ""
    text = normalize_text(r.get("content") or "")
    r["content_normalized"] = True
    return text


def _content_sha1(r: dict, text: str) -> str:
    """Хэш нормализованного текста: переиспользуем тот, что посчитал краулер."""
    stored = r.get("content_sha1")
    if stored and r.get("content_normalized"):
        return stored
    return text_sha1(text)


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
        sha = _content_sha1(r, text)
        if sha in seen:
            duplicates[i] = seen[sha]
        else:
            seen[sha] = i
    log.info(f"Exact dedup: {len(duplicates)} duplicates out of {len(records)} records")
    return duplicates


def dedup_minhash(records: list[dict], num_perm: int = 128,
                  threshold: float = 0.85,
                  on_progress: Callable[[int, int], None] | None = None) -> dict[int, int]:
    """Нечёткие дубли (MinHash LSH). Индекс записи → индекс оригинала.

    LSH ключуется стабильным `str(i)`, поэтому повторяющиеся или пустые URL не
    влияют на работу индекса (C3).
    """
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    duplicates: dict[int, int] = {}
    total = len(records)
    for i, r in enumerate(records):
        if r.get("status") != "ok":
            continue
        text = _content_of(r)
        if not text:
            continue
        mh = _make_minhash(shingles_bytes(text, k=5), num_perm)
        matches = lsh.query(mh)
        if matches:
            duplicates[i] = int(matches[0])
        else:
            lsh.insert(str(i), mh)
        if on_progress and i % 500 == 0:
            on_progress(i, total)                       # A4: прогресс стадии
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

            batch.append((idx, _make_minhash(shingles_bytes(text, k=5), num_perm)))
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
              config: DedupConfig,
              on_progress: Callable[[int, int], None] | None = None) -> dict:
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
            on_progress=on_progress,
        )
        # локальные индексы minhash-прохода → индексы полного корпуса
        duplicate_maps.append(
            {remaining_idx[d]: remaining_idx[o] for d, o in rel.items()}
        )

    duplicate_maps.append(dedup_by_url(records))
    return _write_dedup_output(records, _merge(*duplicate_maps), output_file, config)


def count_records(corpus_file: str | Path) -> int:
    """Число записей, которые увидит `iter_records` (для шкалы прогресса).

    Считаем по строкам без разбора JSON — это быстрый грубый верхний предел,
    и он честно смещает шкалу, если в файле битые строки.
    """
    from ..writer import open_corpus_reader
    with open_corpus_reader(corpus_file) as f:
        return sum(1 for line in f if line.strip())


def _iter_records_indexed(corpus_file: str | Path):
    """(index, record) ровно с той же нумерацией, что и `iter_records`.

    Индексы во всех проходах стримингового дедупа обязаны совпадать, иначе
    `duplicate_of` укажет не на ту запись.
    """
    from ..writer import open_corpus_reader
    idx = -1
    with open_corpus_reader(corpus_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Bad JSON line skipped (streaming dedup)")
                continue
            idx += 1
            yield idx, rec


def run_dedup_streaming(corpus_file: str | Path, output_file: str | Path,
                        config: DedupConfig,
                        on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Дедупликация больших корпусов: решений в RAM — O(dup), записей — 0 (A4).

    Обычно `run_dedup` materializует весь список записей (для корпуса из 1M
    стотысячесимвольных страниц — десятки ГБ RAM). Здесь три прохода по файлу:

      1. проход-скан: точные sha1, канонические URL, LSH MinHash, sha1 картинок
         → только отображения «индекс дубля → индекс оригинала»;
      2. проход-разрешение: вытаскиваем URL только тех записей, на которые
         кто-то сослался как на оригинал (память = число дублей, не корпус);
      3. проход-запись: пишем строки с флагами.

    Два первых прохода вместо одного — сознательно: правило «при коллизии URL
    оригиналом становится более длинная запись» требует знания всех кандидатов
    заранее, иначе streaming-режим давал бы другой результат, чем обычный
    (см. test_dedup_strategies_agree).
    """
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    exact_map: dict[str, int] = {}            # sha1(norm) → index оригинала
    url_map: dict[str, int] = {}              # canonical url → index оригинала
    url_len: dict[int, int] = {}              # index → длина текста оригинала
    img_seen: dict[str, str] = {}             # sha1 файла → local_path
    d_exact: dict[int, int] = {}
    d_url: dict[int, int] = {}
    d_hash: dict[int, int] = {}
    img_dups: dict[str, str] = {}
    lsh = (MinHashLSH(threshold=config.minhash_threshold,
                      num_perm=config.minhash_num_perm) if config.minhash else None)

    total_expected = count_records(corpus_file) if on_progress else 0
    total = 0
    for i, r in _iter_records_indexed(corpus_file):
        total = i + 1
        ok = r.get("status") == "ok"
        text = _content_of(r)
        url = r.get("source_url") or ""

        if config.exact and ok and text:
            sha = _content_sha1(r, text)
            prev = exact_map.get(sha)
            if prev is not None:
                d_exact[i] = prev
            else:
                exact_map[sha] = i

        if ok and url:
            canon = canonical_url(url)
            prev = url_map.get(canon)
            if prev is None:
                url_map[canon] = i
                url_len[i] = len(text)
            elif len(text) > url_len.get(prev, -1):
                d_url[prev] = i               # прежний «оригинал» стал дублем
                url_map[canon] = i
                url_len[i] = len(text)
            else:
                d_url[i] = prev

        if lsh is not None and ok and text:
            mh = _make_minhash(shingles_bytes(text, k=5), config.minhash_num_perm)
            matches = lsh.query(mh)
            if matches:
                d_hash[i] = int(matches[0])
            else:
                lsh.insert(str(i), mh)

        if config.dedup_images:
            for df in r.get("downloaded_files", []) or []:
                sha_f, path = df.get("sha1"), df.get("local_path")
                if not sha_f or not path:
                    continue
                if sha_f in img_seen and img_seen[sha_f] != path:
                    img_dups[path] = img_seen[sha_f]
                else:
                    img_seen.setdefault(sha_f, path)

        if on_progress and total % 500 == 0:
            on_progress(total, max(total_expected, total))

    duplicates = _merge(d_exact, d_url, d_hash)

    # освобожаем индексы скана — во втором/третьем проходе они не нужны
    del exact_map, url_map, url_len, d_exact, d_url, d_hash, img_seen
    if lsh is not None:
        del lsh

    # проход 2: URL только тех индексов, которые оказались «оригиналами»
    wanted = set(duplicates.values())
    orig_url: dict[int, str] = {}
    if wanted:
        for i, r in _iter_records_indexed(corpus_file):
            if i in wanted:
                orig_url[i] = r.get("source_url") or ""
            if len(orig_url) == len(wanted):
                break

    # проход 3: запись
    kept = removed = 0
    with open(output_file, "w", encoding="utf-8") as fout:
        for i, r in _iter_records_indexed(corpus_file):
            if i in duplicates:
                r["is_duplicate"] = True
                r["duplicate_of"] = orig_url.get(duplicates[i]) or None
                removed += 1
            else:
                r["is_duplicate"] = False
                r["duplicate_of"] = None
                kept += 1
            for df in r.get("downloaded_files", []) or []:
                p = df.get("local_path")
                if p and p in img_dups:
                    df["is_duplicate"] = True
                    df["duplicate_of"] = img_dups[p]
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {"total": total, "kept": kept, "removed": removed,
             "image_duplicates": len(img_dups), "strategy": "streaming"}
    log.info(f"Dedup done: {stats}")
    return stats


def _streaming_threshold_bytes(config: DedupConfig) -> int:
    return max(1, int(config.auto_streaming_threshold_mb)) * 1024 * 1024


def run_dedup_adaptive(corpus_file: str | Path, output_file: str | Path,
                       config: DedupConfig,
                       on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Дедупликация: выбор стратегии по конфигу (I3/I4) + авто-выбор (A4).

    `incremental` → персистентный индекс между запусками;
    `streaming`   → MinHash по частям, корпус целиком в RAM не грузится;
    иначе         → обычный `run_dedup`, КРОМЕ случая, когда файл корпуса больше
    `auto_streaming_threshold_mb` (по умолчанию 256 МБ) и `auto_streaming` не
    выключен: тогда streaming включается автоматически — иначе большой корпус
    просто не помещается в память.
    """
    if config.incremental:
        from ..incremental_dedup import IncrementalDedup

        index = config.incremental_index_file
        inc = IncrementalDedup(index, threshold=config.minhash_threshold,
                               num_perm=config.minhash_num_perm)
        url_dups = inc.process_new_corpus(corpus_file, on_progress=on_progress)
        records = list(iter_records(corpus_file))
        by_url = {r.get("source_url"): i for i, r in enumerate(records)
                  if r.get("source_url")}
        duplicates = {by_url[u]: by_url.get(orig) for u, orig in url_dups.items()
                      if u in by_url}
        return _write_dedup_output(records, duplicates, output_file, config,
                                   extra={"strategy": "incremental",
                                          "index_file": str(index),
                                          "index_size": len(inc.processed_urls)})

    path = Path(corpus_file)
    size = path.stat().st_size if path.exists() else 0
    auto = (not config.streaming and config.auto_streaming != "off"
            and (config.auto_streaming == "force"
                 or size >= _streaming_threshold_bytes(config)))
    if auto:
        log.info(f"Корпус {size / 1e6:.0f} МБ ≥ {config.auto_streaming_threshold_mb} МБ — "
                 f"автоматически включён streaming-дедуп (A4; отключить: "
                 f"dedup.auto_streaming: off)")

    if config.streaming or auto:
        # A4: три прохода по файлу; корпус в RAM не materialизуется
        stats = run_dedup_streaming(corpus_file, output_file, config,
                                    on_progress=on_progress)
        stats["strategy_auto"] = auto
        return stats

    result = run_dedup(corpus_file, output_file, config, on_progress=on_progress)
    result["strategy"] = "memory"
    return result
