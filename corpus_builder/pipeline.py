"""Оркестратор: загрузка → краулинг → запись → пост-обработка.

Поддерживает hook-функции для GUI (progress callback, on_record, on_error,
should_stop). Это позволяет запускать краулинг в QThread и обновлять UI без
блокировки.
"""
from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .config import ensure_output_dirs
from .crawlers import get_crawler
from .crawlers.base import BaseCrawler
from .logging_setup import get_logger, setup_logging
from .models import AppConfig, CorpusRecord, ErrorRecord, SourceItem
from .robots import RateLimiter, RobotsCache
from .state import State
from .text_utils import canonical_url

log = get_logger(__name__)

# Типы callback-ов для GUI
ProgressCallback = Callable[[int, int, str], None]   # (current, total, message)
RecordCallback = Callable[[dict], None]              # last_record_preview
LogCallback = Callable[[str, str], None]             # (level, message)
StopCallback = Callable[[], bool]                    # True → остановиться


class _CrawlTimeoutError(Exception):
    """Исключение при превышении таймаута на один URL."""


def _crawl_with_timeout(
    crawler: BaseCrawler,
    url: str,
    categories: list[str],
    timeout_seconds: int = 600,
    source: SourceItem | None = None,
    on_abandon: Callable[[threading.Thread], None] | None = None,
) -> CorpusRecord | None:
    """Выполнить crawler.crawl() с ограничением по времени.

    Если crawl не завершается за timeout_seconds — поднимает _CrawlTimeoutError.

    Ограничение метода (I7): остановить чужой поток из Python нельзя, поэтому
    поток-«зомби» остаётся живым до срабатывания собственных HTTP-таймаутов
    (`output.request_timeout` connect+read). Сессию мы НЕ закрываем — она общая
    на весь ран, и её закрытие обрубило бы остальные URL. Поэтому per-URL
    таймаут — «страховка» патологических зависаний, а основной механизм — таймауты
    requests. Брошенные потоки учитываются через `on_abandon`, чтобы ран мог
    честно сообщить об них в статистике.
    """
    result: list = []        # [record]
    exception: list = []     # [exception]
    done = threading.Event()

    def _do_crawl():
        try:
            rec = crawler.crawl(url, categories=categories, source=source)
            result.append(rec)
        except Exception as e:            # noqa: BLE001 — пробрасываем наружу
            exception.append(e)
        finally:
            done.set()

    thread = threading.Thread(target=_do_crawl, daemon=True)
    thread.start()

    if not done.wait(timeout=timeout_seconds):
        log.warning(
            f"Crawl thread did not finish within {timeout_seconds}s (it stays alive "
            f"until its own HTTP timeouts fire): {url[:80]}"
        )
        if on_abandon:
            on_abandon(thread)
        raise _CrawlTimeoutError(f"Timeout after {timeout_seconds}s on {url}")

    if exception:
        raise exception[0]

    return result[0] if result else None


def append_record(record: CorpusRecord, corpus_file: str | Path) -> None:
    """Дописать запись в JSONL.

    Запись идёт одним `write()` в файл, открытый на добавление: на POSIX такой
    append атомен, но межпотоковой гарантии нет — вызывающий код обязан
    держать блокировку (см. `run_crawl`), иначе возможна интерливинг-запись.
    """
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


def build_crawl_context(config: AppConfig) -> dict:
    """Собрать общие компоненты краулинга: сессия, robots, rate limiter, state.

    Используется и синхронным `run_crawl`, и `async_pipeline.run_async_crawl`
    (I1): раньше асинхронный путь создавал краулеры с голой `requests.Session`
    (UA `python-requests` вместо настроенного) и без ретраев, пула соединений,
    rate-limit'а, кэша и per-URL таймаута.

    Здесь же расходуются настройки GUI (I4): HTTP-кэш и его TTL, уважение
    robots.txt, browser-подобные заголовки и прокси-ротация.
    """
    from .http_cache import make_cached_session

    out = config.output
    use_cache = out.use_http_cache
    want_headers = out.use_browser_headers
    want_proxy = out.use_proxy

    if want_headers or want_proxy:
        from .proxy_rotator import RotatingProxySession, make_session_with_proxy
        base, rotator = make_session_with_proxy(
            config, use_browser_headers=want_headers, use_proxy=want_proxy)
        if use_cache:
            cached = make_cached_session(config, ttl_hours=float(out.cache_ttl_hours))
            # заголовки/UA из base переносим в кэшированную сессию
            cached.headers.update(dict(base.headers))
            session = RotatingProxySession(cached, rotator) if rotator else cached
        else:
            session = base
    else:
        session = make_cached_session(config, ttl_hours=float(out.cache_ttl_hours),
                                      use_cache=use_cache)

    return {
        "session": session,
        "robots": RobotsCache(
            user_agent=out.user_agent,
            timeout=10,
            respect=out.respect_robots_txt,
            fail_open=out.robots_fail_open,
        ),
        "rate_limiter": RateLimiter(default_delay=out.request_delay),
        "state": State(out.state_file),
    }


def make_crawler(source_type: str, config: AppConfig, session: Any) -> BaseCrawler:
    """Экземпляр краулера с ОБЩЕЙ сессией (пул соединений, кэш, ретраи в ней).

    Экземпляр создаётся на каждый URL: краулер хранит `self.source`
    (per-source include_files/download_files), и общий на несколько задач
    экземпляр перезаписывал бы это состояние (I7/I8).
    """
    crawler = get_crawler(source_type, config)
    crawler.session = session
    return crawler


def truncate_run_outputs(config: AppConfig) -> None:
    """Публичная обёртка (используется CLI/GUI/async-путём)."""
    _truncate_run_outputs(config)


def _truncate_run_outputs(config: AppConfig) -> None:
    """Очистить файлы корпуса при запуске без resume (C6).

    `corpus_file`/`error_log` всю жизнь открывались только в режиме "a", а
    `resume=False` сбрасывал лишь state.json. В результате каждый новый запуск
    дописывал те же записи сверху: дубли в корпусе + падение пост-обработки.
    """
    for path in (config.output.corpus_file, config.output.error_log):
        p = Path(path)
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            log.warning(f"Cannot truncate {p}: {e}")


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

    context = build_crawl_context(config)
    session = context["session"]
    robots: RobotsCache = context["robots"]
    rate_limiter: RateLimiter = context["rate_limiter"]
    state: State = context["state"]
    fresh_run = not resume

    if retry_errors:
        state.clear_errors()

    sources = config.sources
    if source_type:
        sources = [s for s in sources if s.type == source_type]
    if limit:
        sources = sources[:limit]

    if dry_run:
        log.info(f"Dry run: would crawl {len(sources)} sources")
        for s in sources:
            log.info(f"  - {s.url} ({s.type})")
        return {"total": len(sources), "skipped": 0, "errors": 0, "dry_run": True}

    if fresh_run:
        # C6: сбрасываем state И корпус (раньше state — да, корпус — нет).
        # ВАЖНО: после проверки dry_run, чтобы «посмотреть, что будет» не стирал
        # уже собранный корпус.
        state.reset()
        _truncate_run_outputs(config)

    # Пред-фильтр по robots.txt: параллельный prefetch + отброс доменов, где нам
    # всё запрещено. Функция жила в robots.py «для теста» и не вызывалась (I1).
    skipped_by_robots = 0
    if robots.respect and len(sources) > 1:
        from .robots import pre_filter_by_robots
        # источники с ignore_robots не отдаём пре-фильтру
        checkable = [s for s in sources if not s.ignore_robots]
        kept_explicit = [s for s in sources if s.ignore_robots]
        before = len(sources)
        allowed, disallowed = pre_filter_by_robots(
            checkable, robots,
            on_skip=lambda u: log.warning(f"robots.txt pre-filter: пропущен {u[:80]}"))
        sources = kept_explicit + allowed
        skipped_by_robots = before - len(sources)
        if skipped_by_robots:
            blocked = sorted(d for d, v in disallowed.items() if v)[:8]
            log.info(f"robots.txt pre-filter: {skipped_by_robots} sources skipped "
                     f"(домены: {', '.join(blocked)})")

    log.info(f"Starting crawl: {len(sources)} sources, resume={resume}")
    if config.pipeline.progress_bar and on_progress is None:
        pbar = tqdm(sources, desc="crawling", unit="src")
    else:
        pbar = sources

    file_lock = threading.Lock()      # общие corpus_file/errors.jsonl (I2)
    state_lock = threading.Lock()     # общий state (I2)
    zombie_threads: list[threading.Thread] = []   # учёт брошенных потоков (I7)

    total = len(sources) + skipped_by_robots
    errors = 0
    skipped = skipped_by_robots
    processed = 0
    stopped = False

    def emit_log(level: str, msg: str) -> None:
        if on_log:
            on_log(level, msg)
        method = getattr(log, level.lower(), None)
        (method if callable(method) else log.info)(msg)

    def mark_done(url: str) -> None:
        with state_lock:
            state.mark_done(url)

    def mark_error(url: str) -> None:
        with state_lock:
            state.mark_error(url)

    def is_done(url: str) -> bool:
        with state_lock:
            return state.is_done(url) or state.is_done(canonical_url(url))

    def is_error(url: str) -> bool:
        with state_lock:
            return state.is_error(url)

    def save_state() -> None:
        with state_lock:
            state.save()

    for i, src in enumerate(pbar):
        # Проверка остановки
        if should_stop and should_stop():
            log.info("Crawl stopped by user request")
            stopped = True
            break

        if on_progress:
            on_progress(i + 1, total, f"{src.url[:80]}")

        url = src.url
        # один и тот же URL мог стоять в config дважды (например, с utm_* и без)
        if is_done(url):
            skipped += 1
            continue
        if is_error(url):
            skipped += 1
            continue

        # robots.txt
        if not src.ignore_robots and not robots.is_allowed(url):
            emit_log("WARNING",
                     f"robots.txt не разрешает {url[:70]} — пропускаю. Если краулер "
                     f"ходит в API другого домена, для этого источника можно поставить "
                     f"`ignore_robots: true`")
            skipped += 1
            # ВАЖНО: НЕ mark_done(url): запрет robots.txt — не «успех», и после
            # изменения robots.txt/флага источник обязан снова попасть в выборку
            continue

        # rate-limit
        rate_limiter.wait(url)

        # Краулим с timeout — если зависает более N минут, пропускаем URL
        per_url_timeout = config.pipeline.per_url_timeout_minutes
        record = None
        try:
            crawler = make_crawler(src.type, config, session)
            record = _crawl_with_timeout(
                crawler, url, src.categories or [],
                timeout_seconds=per_url_timeout * 60,
                source=src,
                on_abandon=zombie_threads.append,
            )
        except _CrawlTimeoutError:
            emit_log("WARNING", f"⏱ Превышен таймаут {per_url_timeout} мин, "
                                f"пропускаю: {url[:80]}")
            record = CorpusRecord(
                source_url=url, source_type=src.type, content="", status="error",
                metadata={"error": f"timeout after {per_url_timeout} minutes"},
                categories=src.categories or [],
            )
        except ValueError as e:
            # неизвестный тип источника — не «падение краулера», а ошибка конфига
            log.error(f"Unknown source type {src.type!r}: {e}")
            errors += 1
            continue
        except Exception as e:                       # noqa: BLE001
            log.exception(f"Crawler crashed on {url}")
            record = CorpusRecord(
                source_url=url, source_type=src.type, content="", status="error",
                metadata={"error": str(e)}, categories=src.categories or [],
            )

        # Запись под общим замком: тот же URL могли успеть обработать параллельно
        acquired = False
        with file_lock:
            if record and record.status == "ok" and record.content:
                if is_done(url):
                    skipped += 1
                    continue
                append_record(record, config.output.corpus_file)
                mark_done(url)
                processed += 1
                acquired = True
            else:
                reason = ((record.metadata or {}).get("error", "empty content")
                          if record else "no record")
                append_error(ErrorRecord(
                    source_url=url, source_type=src.type, reason=str(reason)),
                    config.output.error_log)
                mark_error(url)
                errors += 1

        if acquired and on_record:
            on_record(record.model_dump())

        # Чекпойнт
        if (i + 1) % config.pipeline.save_checkpoint_every == 0:
            save_state()

    # I7: потоки, брошенные по таймауту, всё ещё держат сокеты — честно в логи
    still_alive = [t for t in zombie_threads if t.is_alive()]
    if still_alive:
        log.warning(f"{len(still_alive)} timed-out crawl threads are still running; "
                    f"они завершатся, когда сработают их HTTP-таймауты")

    save_state()

    return _crawl_stats(total, processed, skipped, errors, state, stopped,
                        len(zombie_threads), len(still_alive))


def _crawl_stats(total: int, processed: int, skipped: int, errors: int,
                 state: State, stopped: bool, abandoned: int = 0,
                 still_running: int = 0) -> dict:
    stats = {
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "done_total": state.done_count,
        "errors_total": state.error_count,
        "stopped": stopped,
        "abandoned_threads": abandoned,
        "still_running_threads": still_running,
    }
    log.info(f"Crawl finished: {stats}")
    return stats


def _gzip_file(src: Path, dst: Path) -> int:
    """Сжать файл; вернуть размер результата."""
    import gzip
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, gzip.open(dst, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    return dst.stat().st_size


def run_postprocess(
    config: AppConfig,
    on_progress: ProgressCallback | None = None,
    on_log: LogCallback | None = None,
    should_stop: StopCallback | None = None,
) -> dict:
    """Полный пост-процессинг: дедупликация → фильтр качества → нормализация → пары.

    С опциональными callback'ами для GUI. `should_stop` проверяется между
    стадиями: раньше кнопка «Остановить» в режиме пост-обработки не имела
    эффекта, и можно было открыть диалог настроек поверх работающего рана.
    """
    from .postproc.dedup import run_dedup_adaptive
    from .postproc.extract_pairs import run_extract_pairs
    from .postproc.normalize import run_normalize
    from .postproc.quality import run_quality_filter

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

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    stage(1, 4, "Deduplication")
    # стратегия выбирается конфигурацией: streaming/incremental раньше никуда
    # не передавались из настроек (I3/I4)
    dedup_stats = run_dedup_adaptive(raw_file, deduped_file, config.dedup)

    if stopped():
        return {"stopped": True, "stage": "quality", "dedup": dedup_stats}

    stage(2, 4, "Quality filter")
    if config.pipeline.parallel_postproc:
        from .parallel_postproc import run_quality_filter_parallel
        quality_stats = run_quality_filter_parallel(
            deduped_file, filtered_file, config.quality,
            workers=config.pipeline.parallel_workers or None)
    else:
        quality_stats = run_quality_filter(deduped_file, filtered_file, config.quality)

    if stopped():
        return {"stopped": True, "stage": "normalize",
                "dedup": dedup_stats, "quality": quality_stats}

    stage(3, 4, "Final normalization")
    norm_stats = run_normalize(filtered_file, normalized_file)

    if config.export.write_gzip:
        gz_path = Path(str(normalized_file) + ".gz")
        norm_stats["gzip_file"] = str(gz_path)
        norm_stats["gzip_bytes"] = _gzip_file(normalized_file, gz_path)

    if stopped():
        return {"stopped": True, "stage": "pairs", "dedup": dedup_stats,
                "quality": quality_stats, "normalize": norm_stats}

    stage(4, 4, "Instruction-tuning pairs")
    pairs_stats = run_extract_pairs(normalized_file, pairs_file)

    if not config.export.keep_intermediate:
        for tmp in (deduped_file, filtered_file):
            try:
                tmp.unlink(missing_ok=True)
            except OSError as e:
                log.warning(f"Cannot remove intermediate {tmp}: {e}")

    return {
        "dedup": dedup_stats,
        "quality": quality_stats,
        "normalize": norm_stats,
        "pairs": pairs_stats,
        "final_corpus": str(normalized_file),
        "pairs_file": str(pairs_file),
        "stopped": False,
    }
