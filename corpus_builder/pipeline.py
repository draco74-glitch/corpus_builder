"""Оркестратор: загрузка → краулинг → запись → пост-обработка.

Поддерживает hook-функции для GUI (progress callback, on_record, on_error,
should_stop). Это позволяет запускать краулинг в QThread и обновлять UI без блокировки.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tqdm import tqdm

from .config import ensure_output_dirs, load_config
from .crawlers import get_crawler
from .logging_setup import get_logger, setup_logging
from .models import AppConfig, CorpusRecord, ErrorRecord
from .robots import RateLimiter, RobotsCache, make_session
from .state import State

log = get_logger(__name__)

# Типы callback-ов для GUI
ProgressCallback = Callable[[int, int, str], None]   # (current, total, message)
RecordCallback = Callable[[dict], None]                 # last_record_preview
LogCallback = Callable[[str, str], None]                 # (level, message)
StopCallback = Callable[[], bool]                        # возвращает True, если нужно остановиться


class _CrawlTimeoutError(Exception):
    """Исключение при превышении таймаута на один URL."""
    pass


def _crawl_with_timeout(
    crawler: BaseCrawler,
    url: str,
    categories: list[str],
    timeout_seconds: int = 600,
) -> CorpusRecord | None:
    """Выполнить crawler.crawl() с ограничением по времени.

    Если crawl не завершается за timeout_seconds — поднимает _CrawlTimeoutError.
    Использует threading.Timer для принудительного прерывания.
    """
    import threading

    result: list = []  # [record]
    exception: list = []  # [exception]
    done = threading.Event()

    def _do_crawl():
        try:
            rec = crawler.crawl(url, categories=categories)
            result.append(rec)
        except Exception as e:
            exception.append(e)
        finally:
            done.set()

    thread = threading.Thread(target=_do_crawl, daemon=True)
    thread.start()

    # Ждём завершения с таймаутом
    if not done.wait(timeout=timeout_seconds):
        # Таймаут — поток всё ещё работает, но мы его бросаем
        # (daemon=True означает, что он не помешает выходу программы)
        raise _CrawlTimeoutError(f"Timeout after {timeout_seconds}s on {url}")

    # Если было исключение — поднимаем
    if exception:
        raise exception[0]

    return result[0] if result else None


def append_record(record: CorpusRecord, corpus_file: str | Path) -> None:
    """Атомарно добавить запись в JSONL."""
    corpus_file = Path(corpus_file)
    corpus_file.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump_json(exclude_none=False) + "\n"
    with open(corpus_file, "a", encoding="utf-8") as f:
        f.write(line)


def append_error(rec: ErrorRecord, error_log: str | Path) -> None:
    """Записать ошибку в отдельный лог."""
    error_log = Path(error_log)
    error_log.parent.mkdir(parents=True, exist_ok=True)
    with open(error_log, "a", encoding="utf-8") as f:
        f.write(rec.model_dump_json() + "\n")


def run_crawl(
    config: AppConfig,
    resume: bool = True,
    limit: int | None = None,
    source_type: str | None = None,
    dry_run: bool = False,
    retry_errors: bool = False,
    on_progress: ProgressCallback | None = None,
    on_record: RecordCallback | None = None,
    on_log: LogCallback | None = None,
    should_stop: StopCallback | None = None,
) -> dict:
    """Основной цикл краулинга с поддержкой GUI-callback'ов.

    - on_progress(i, total, msg): вызывается на каждом шаге
    - on_record(record_dict): вызывается после успешной записи
    - on_log(level, message): для прокидывания логов в GUI
    - should_stop() -> bool: если True, корректно останавливаемся после текущего URL
    - retry_errors: если True, повторно обрабатываем URL из state.errors
    """
    setup_logging(config.output.log_file, verbose=False)
    ensure_output_dirs(config)

    state = State(config.output.state_file)
    if not resume:
        state._done.clear()
        state._errors.clear()

    if retry_errors:
        state._errors.clear()

    session = make_session(config)
    robots = RobotsCache(user_agent=config.output.user_agent, timeout=10)
    rate_limiter = RateLimiter(default_delay=config.output.request_delay)

    sources = config.sources
    if source_type:
        sources = [s for s in sources if s.type == source_type]
    if limit:
        sources = sources[:limit]

    if dry_run:
        log.info(f"Dry run: would crawl {len(sources)} sources")
        for s in sources:
            log.info(f"  - {s.url} ({s.type})")
        return {"total": len(sources), "skipped": 0, "errors": 0}

    log.info(f"Starting crawl: {len(sources)} sources, resume={resume}")
    if config.pipeline.progress_bar and on_progress is None:
        pbar = tqdm(sources, desc="crawling", unit="src")
    else:
        pbar = sources

    crawler_cache: dict[str, Any] = {}
    total = len(sources)
    errors = 0
    skipped = 0
    processed = 0
    stopped = False

    def emit_log(level: str, msg: str) -> None:
        if on_log:
            on_log(level, msg)
        getattr(log, level.lower() if level != "WARNING" else "warning", log.info)(msg)

    for i, src in enumerate(pbar):
        # Проверка остановки
        if should_stop and should_stop():
            log.info("Crawl stopped by user request")
            stopped = True
            break

        if on_progress:
            on_progress(i + 1, total, f"{src.url[:80]}")

        url = src.url
        if state.is_done(url):
            skipped += 1
            continue
        if state.is_error(url):
            skipped += 1
            continue

        # robots.txt
        if not robots.is_allowed(url):
            emit_log("INFO", f"Disallowed by robots.txt: {url}")
            skipped += 1
            state.mark_done(url)
            continue

        # rate-limit
        rate_limiter.wait(url)

        # Найти или создать краулер
        try:
            crawler = crawler_cache.get(src.type)
            if crawler is None:
                crawler = get_crawler(src.type, config)
                crawler.session = session
                crawler_cache[src.type] = crawler
        except ValueError as e:
            log.error(f"Unknown source type {src.type!r}: {e}")
            errors += 1
            continue

        # Краулим с timeout — если зависает более N минут, пропускаем URL
        per_url_timeout = getattr(config.pipeline, "per_url_timeout_minutes", 10)
        record = None
        try:
            record = _crawl_with_timeout(
                crawler, url, src.categories or [],
                timeout_seconds=per_url_timeout * 60,
            )
        except _CrawlTimeoutError:
            log.warning(f"URL timed out after {per_url_timeout} min, skipping: {url}")
            if on_log:
                on_log("WARNING", f"\u23f1 Превышен таймаут {per_url_timeout} мин, пропускаю: {url[:80]}")
            record = CorpusRecord(
                source_url=url,
                source_type=src.type,
                content="",
                status="error",
                metadata={"error": f"timeout after {per_url_timeout} minutes"},
                categories=src.categories or [],
            )
        except Exception as e:
            log.exception(f"Crawler crashed on {url}")
            record = CorpusRecord(
                source_url=url,
                source_type=src.type,
                content="",
                status="error",
                metadata={"error": str(e)},
                categories=src.categories or [],
            )

        if record and record.status == "ok" and record.content:
            append_record(record, config.output.corpus_file)
            state.mark_done(url)
            processed += 1
            if on_record:
                on_record(record.model_dump())
        else:
            reason = (record.metadata or {}).get("error", "empty content") if record else "no record"
            state.mark_error(url)
            append_error(ErrorRecord(
                source_url=url, source_type=src.type, reason=str(reason)
            ), config.output.error_log)
            errors += 1

        # Чекпойнт
        if (i + 1) % config.pipeline.save_checkpoint_every == 0:
            state.save()

    state.save()

    stats = {
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "done_total": state.done_count,
        "errors_total": state.error_count,
        "stopped": stopped,
    }
    log.info(f"Crawl finished: {stats}")
    return stats


def run_postprocess(
    config: AppConfig,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
) -> dict:
    """Полный пост-процессинг: дедупликация → фильтр качества → нормализация → пары.

    С опциональными callback'ами для GUI.
    """
    from .postproc.dedup import run_dedup
    from .postproc.quality import run_quality_filter
    from .postproc.normalize import run_normalize
    from .postproc.extract_pairs import run_extract_pairs

    out_dir = Path(config.output.corpus_file).parent
    raw_file = Path(config.output.corpus_file)
    deduped_file = out_dir / "deduped.jsonl"
    filtered_file = out_dir / "filtered.jsonl"
    normalized_file = out_dir / "corpus_final.jsonl"
    pairs_file = out_dir / "instruction_pairs.jsonl"

    def stage(n: int, total: int, msg: str) -> None:
        log.info(msg)
        if on_progress:
            on_progress(n, total, msg)
        if on_log:
            on_log("INFO", msg)

    stage(1, 4, "Deduplication")
    dedup_stats = run_dedup(raw_file, deduped_file, config.dedup)

    stage(2, 4, "Quality filter")
    quality_stats = run_quality_filter(deduped_file, filtered_file, config.quality)

    stage(3, 4, "Final normalization")
    norm_stats = run_normalize(filtered_file, normalized_file)

    stage(4, 4, "Instruction-tuning pairs")
    pairs_stats = run_extract_pairs(normalized_file, pairs_file)

    return {
        "dedup": dedup_stats,
        "quality": quality_stats,
        "normalize": norm_stats,
        "pairs": pairs_stats,
        "final_corpus": str(normalized_file),
        "pairs_file": str(pairs_file),
    }
