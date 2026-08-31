"""Асинхронный пайплайн краулинга для ускорения сбора корпуса.

В отличие от синхронного `pipeline.run_crawl`, обрабатывает несколько URL
параллельно — при этом соблюдая per-domain rate-limit через
`asyncio.Semaphore` + общий `RateLimiter`.

Ускорение: 4-8 раз для смешанных доменов (без нарушения вежливости).

Что гарантирует общая сборка контекста (I1):
  * одна Session из `build_crawl_context` → настроенный User-Agent, пул
    соединений, повторы на 429/5xx, HTTP-кэш и прокси-ротация;
  * `RateLimiter` (задержка между запросами к домену), а не только семафор;
  * `per_url_timeout_minutes` — зависший URL не блокирует ран;
  * `save_checkpoint_every` — периодический save состояния;
  * `should_stop()` — остановка в mid-run, а не только «не начинать новые».
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .logging_setup import get_logger
from .models import AppConfig, CorpusRecord, ErrorRecord, SourceItem
from .pipeline import CrawlThrottle, build_crawl_context, crawl_dispatch_hint
from .state import State
from .text_utils import canonical_url

log = get_logger(__name__)

ProgressCallback = Callable[[int, int, str], None]
RecordCallback = Callable[[dict], None]
LogCallback = Callable[[str, str], None]
StopCallback = Callable[[], bool]


def _crawler_worker(crawler: Any, url: str, source: SourceItem) -> CorpusRecord | None:
    """Синхронный вызов краулера — выполняется в executor'е."""
    return crawler.crawl(url, categories=source.categories or [], source=source)


async def run_async_crawl(
    config: AppConfig,
    resume: bool = True,
    retry_errors: bool = False,
    limit: int | None = None,
    source_type: str | None = None,
    on_progress: ProgressCallback | None = None,
    on_record: RecordCallback | None = None,
    on_log: LogCallback | None = None,
    should_stop: StopCallback | None = None,
    max_concurrent_per_domain: int | None = None,
    max_concurrent_total: int | None = None,
) -> dict:
    """Асинхронный цикл краулинга.

    Параметры:
      - max_concurrent_per_domain: максимум одновременных запросов на один домен (1 = вежливо)
      - max_concurrent_total: общий лимит одновременных запросов (8 = ускорение ~8x)

    Возвращает тот же словарь статистики, что и run_crawl.
    """
    from .config import ensure_output_dirs

    ensure_output_dirs(config)
    loop = asyncio.get_running_loop()
    executor = _make_executor()

    context = build_crawl_context(config)
    session = context["session"]
    robots = context["robots"]
    rate_limiter = context["rate_limiter"]
    state: State = context["state"]
    if not resume:
        state.reset()
        from .pipeline import _truncate_run_outputs
        _truncate_run_outputs(config)
    if retry_errors:
        state.clear_errors()

    from .crawlers import get_crawler

    sources = config.sources
    if source_type:
        sources = [s for s in sources if s.type == source_type]
    if limit:
        sources = sources[:limit]
    if max_concurrent_per_domain is None:
        max_concurrent_per_domain = config.pipeline.max_concurrent_per_domain
    if max_concurrent_total is None:
        max_concurrent_total = config.pipeline.max_concurrent_total

    domain_sems: dict[str, asyncio.Semaphore] = {}
    total_sem = asyncio.Semaphore(max_concurrent_total)
    write_lock = asyncio.Lock()          # JSONL/state пишутся по одному

    # параллельный prefetch robots.txt + отброс полностью запрещённых доменов
    skipped_by_prefilter = 0
    if robots.respect and len([s for s in sources if not s.ignore_robots]) > 1:
        from .robots import pre_filter_by_robots
        before = len(sources)
        checkable = [s for s in sources if not s.ignore_robots]
        explicit = [s for s in sources if s.ignore_robots]
        allowed, _disallowed = await loop.run_in_executor(
            None, lambda: pre_filter_by_robots(checkable, robots))  # один раз, до цикла
        sources = explicit + allowed
        skipped_by_prefilter = before - len(sources)
        if skipped_by_prefilter:
            log.info(f"robots.txt pre-filter: {skipped_by_prefilter} sources skipped")

    throttle = CrawlThrottle(rate_limiter, robots, session, respect_robots=robots.respect)
    hint = crawl_dispatch_hint(sources, config)
    if hint:
        log.info(hint.replace(" Настройки", ""))
    total = len(sources) + skipped_by_prefilter
    processed = 0
    errors = 0
    skipped = skipped_by_prefilter
    # счётчики причин «пропущено» — через dict: в замыкании `+=` на int
    # требовал бы nonlocal и молча плодил бы локальную копию
    skip_counts = {"already_done": 0, "previously_failed": 0,
                   "robots_disallowed": skipped_by_prefilter,
                   "duplicate_url_in_config": 0}
    stopped = False
    checkpoint_every = max(1, config.pipeline.save_checkpoint_every)

    async def crawl_one(src: SourceItem, idx: int) -> None:
        nonlocal processed, errors, skipped, stopped
        url = src.url

        if should_stop and should_stop():
            stopped = True
            return

        if state.is_error(url):
            skipped += 1
            skip_counts["previously_failed"] += 1
            return
        if state.is_done(url):
            skipped += 1
            skip_counts["already_done"] += 1
            return
        if state.is_done(canonical_url(url)):
            skipped += 1
            skip_counts["duplicate_url_in_config"] += 1
            return

        # блокирующий HTTP-запрос robots.txt нельзя делать в event loop:
        # он блокирует все корутины до 10 с
        allowed = (src.ignore_robots or
                   await loop.run_in_executor(executor, robots.is_allowed, url))
        if not allowed:
            if on_log:
                on_log("WARNING",
                       f"robots.txt не разрешает {url[:70]} — пропускаю "
                       f"(для API-краулеров можно `ignore_robots: true`)")
            skipped += 1
            skip_counts["robots_disallowed"] += 1
            # НЕ mark_done: «запрещено» ≠ «обработано», иначе источник
            # навсегда выпадает из resume-цикла
            return

        domain = urlparse(url).netloc
        if domain not in domain_sems:
            domain_sems[domain] = asyncio.Semaphore(max(1, max_concurrent_per_domain))

        # ВАЖНО (I7): экземпляр краулера создаётся НА ЗАДАЧУ. `crawler.source`
        # (per-source include_files/download_files) — изменяемое состояние, и
        # общий на все задачи краулер перезаписывал бы его параллельными
        # задачами. Дорогое (Session, пул, кэш) при этом общее.
        async with total_sem, domain_sems[domain]:
            if on_progress:
                on_progress(idx + 1, total, f"async: {url[:60]}")

            try:
                crawler = get_crawler(src.type, config)
                crawler.session = session
            except ValueError as e:
                log.error(f"Unknown source type {src.type!r}: {e}")
                errors += 1
                return

            # вежливость: того же RateLimiter, что и в синхронном пути
            # A5/A6: троттлинг с Crawl-delay из robots и пропуском сна на cache-hit
            await loop.run_in_executor(executor, throttle.wait, url)

            per_url_timeout = config.pipeline.per_url_timeout_minutes * 60
            try:
                record = await asyncio.wait_for(
                    loop.run_in_executor(
                        executor, _crawler_worker, crawler, url, src),
                    timeout=per_url_timeout,
                )
            except asyncio.TimeoutError:
                log.warning(f"URL timed out after {config.pipeline.per_url_timeout_minutes} "
                            f"min, skipping: {url}")
                if on_log:
                    on_log("WARNING", f"Превышен таймаут, пропускаю: {url[:80]}")
                record = CorpusRecord(
                    source_url=url, source_type=src.type, content="", status="error",
                    metadata={"error": f"timeout after "
                                       f"{config.pipeline.per_url_timeout_minutes} minutes"},
                    categories=src.categories or [],
                )
            except Exception as e:
                log.exception(f"Crawler crashed on {url}")
                record = CorpusRecord(
                    source_url=url, source_type=src.type, content="", status="error",
                    metadata={"error": str(e)}, categories=src.categories or [],
                )

            async with write_lock:
                # повторная проверка под замком: параллельные задачи на один URL
                # (например, «example.com/» дважды) иначе пишут дубль (I2)
                if record and record.status == "ok" and record.content:
                    if state.is_done(url) or state.is_done(canonical_url(url)):
                        skipped += 1
                        skip_counts["duplicate_url_in_config"] += 1
                        return
                    from .pipeline import append_record
                    append_record(record, config.output.corpus_file)
                    state.mark_done(url)
                    processed += 1
                    if on_record:
                        on_record(record.model_dump())
                else:
                    from .pipeline import append_error
                    reason = ((record.metadata or {}).get("error", "empty content")
                              if record else "no record")
                    state.mark_error(url)
                    append_error(ErrorRecord(
                        source_url=url, source_type=src.type, reason=str(reason)),
                        config.output.error_log)
                    errors += 1

                if (idx + 1) % checkpoint_every == 0:
                    # А5: промежуточный чекпойнт дописывает журнал, а не
                    # переписывает всё состояние
                    state.save(compact=True)

    tasks = [asyncio.create_task(crawl_one(src, i)) for i, src in enumerate(sources)]
    # возвращаем исключения, чтобы один упавший URL не ронял весь ран
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for src, res in zip(sources, results, strict=False):
        if isinstance(res, Exception):
            log.error(f"async task crashed for {src.url}: {res}")
            errors += 1

    state.save()

    stats = {
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "done_total": state.done_count,
        "errors_total": state.error_count,
        "stopped": stopped,
        "async": True,
        "skipped_breakdown": dict(skip_counts),
    }
    log.info(f"Async crawl finished: {stats}")
    executor.shutdown(wait=False)
    return stats


def _make_executor():
    """Пул потоков для синхронных краулеров."""
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(max_workers=32, thread_name_prefix="crawl")
