"""Оркестратор: загрузка → краулинг → запись → пост-обработка.

Поддерживает hook-функции для GUI (progress callback, on_record, on_error,
should_stop). Это позволяет запускать краулинг в QThread и обновлять UI без
блокировки.
"""
from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


class CrawlWorkerPool:
    """Пул из K daemon-потоков вместо «потока на каждый URL» (A5).

    Проблема прежней схемы: на каждый URL создавался поток, а зависший по
    per-URL таймауту поток оставался живым (requests держит сокет до своих
    connect/read таймаутов). На корпусе из тысяч URL это сотни живых тредов и
    сокетов. Теперь потоков ровно K: зависшая задача занимает один слот, и их
    количество ограничено, а не растёт лавинообразно.

    K=1 был бы хуже прежнего (зависший URL блокировал бы весь ран), поэтому
    размер считается от `pipeline.max_concurrent_total`, и при полном заполнении
    мы ждём, осознанно применяя backpressure: машине не надо долбить сайт,
    пока K соединений висят.
    """

    def __init__(self, max_workers: int = 4) -> None:
        import queue
        self._workers = max(1, int(max_workers))
        self._q: "queue.Queue" = queue.Queue()
        self._outstanding = 0
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        for n in range(self._workers):
            t = threading.Thread(target=self._worker, daemon=True,
                                 name=f"crawl-pool-{n}")
            t.start()
            self._threads.append(t)

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            fn, args, kwargs, box, ev = job
            try:
                box["value"] = fn(*args, **kwargs)
            except BaseException as e:                # noqa: BLE001
                box["error"] = e
            finally:
                box["done"] = True
                with self._lock:
                    self._outstanding -= 1
                ev.set()

    def submit(self, fn, *args, **kwargs):
        """Вернуть (box, event): box["value"] / box["error"] / box["done"]."""
        box: dict = {"done": False}
        ev = threading.Event()
        with self._lock:
            self._outstanding += 1
        self._q.put((fn, args, kwargs, box, ev))
        return box, ev

    @property
    def queued(self) -> int:
        """Незавершённые задачи (включая зависшие)."""
        with self._lock:
            return self._outstanding

    @property
    def workers(self) -> int:
        return self._workers

    def shutdown(self) -> None:
        for _ in self._threads:
            self._q.put(None)

def pool_size(config: AppConfig) -> int:
    """Сколько рабочих потоков в пуле краула (A5).

    Не 1 (иначе один зависший URL блокирует весь ран) и не «по числу URL»
    (иначе вернёмся к лавине тредов).
    """
    return max(2, min(8, int(getattr(config.pipeline, "max_concurrent_total", 8) or 4)))


def _crawl_with_timeout(
    crawler: BaseCrawler,
    url: str,
    categories: list[str],
    timeout_seconds: int = 600,
    source: SourceItem | None = None,
    on_abandon: Callable[[Any], None] | None = None,
    pool: CrawlWorkerPool | None = None,
) -> CorpusRecord | None:
    """Выполнить crawler.crawl() с ограничением по времени.

    Если crawl не завершается за timeout_seconds — поднимает
    _CrawlTimeoutError. Остановить чужой поток из Python нельзя, поэтому
    зависшая задача остаётся живой до собственных HTTP-таймаутов
    (`output.request_timeout` connect+read); сессию мы НЕ закрываем — она
    общая на весь ран.

    `pool` (A5) — общий одноворкерный пул: вместо «поток на URL» одна очередь,
    так что зависшие задачи не плодят сотни живых тредов. `on_abandon`
    вызывается на зависшую задачу/поток, чтобы ран мог сообщить о них в
    статистике.
    """
    def _job():
        return crawler.crawl(url, categories=categories, source=source)

    if pool is not None:
        box, ev = pool.submit(_job)
        if not ev.wait(timeout=timeout_seconds):
            log.warning(
                f"Crawl task did not finish within {timeout_seconds}s; the pool "
                f"worker stays busy until the crawler's HTTP timeouts fire: {url[:80]}")
            if on_abandon:
                on_abandon(box)
            raise _CrawlTimeoutError(f"Timeout after {timeout_seconds}s on {url}")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    # совместимый путь (тесты/вызовы без пула): поток на задачу
    result: list = []
    exception: list = []
    done = threading.Event()

    def _do_crawl():
        try:
            result.append(_job())
        except Exception as e:                       # noqa: BLE001
            exception.append(e)
        finally:
            done.set()

    thread = threading.Thread(target=_do_crawl, daemon=True)
    thread.start()

    if not done.wait(timeout=timeout_seconds):
        log.warning(
            f"Crawl thread did not finish within {timeout_seconds}s (it stays alive "
            f"until its own HTTP timeouts fire): {url[:80]}")
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


def crawl_dispatch_hint(sources: list[SourceItem], config: AppConfig) -> str:
    """A5: подсказка, если серийный путь тормозит там, где есть асинхронный.

    Скрытое переключение пути не делаем (это меняло бы поведение unexpectedly),
    но пользователь должен видеть, что 500 источников на 50 доменах при
    request_delay=2s дадут ~16 минут только сна.
    """
    from urllib.parse import urlparse
    if config.pipeline.use_async:
        return ""
    domains = {urlparse(s.url).netloc for s in sources}
    eta = estimate_crawl_minutes(sources, config.output.request_delay)
    if len(sources) >= 50 and len(domains) >= 3 and eta >= 5:
        return (f"Синхронный крауллинг: ≥{eta:.0f} мин на вежливые задержки при "
                f"{len(sources)} источниках на {len(domains)} доменах. "
                f"Для ускорения включите pipeline.use_async (тот же session, "
                f"robots, rate-limit и per-URL таймаут): CorpusBuilder → "
                f"Настройки → Crawling → «Async crawl»")
    return ""


def _is_cached(url: str, session: Any) -> bool:
    """Есть ли ответ на URL в HTTP-кэше (A6).

    Polite-задержка нужна, чтобы не долбить СЕРВЕР. Если ответ отдаётся локальным
    кэшем, запроса к серверу нет и спать незачем — иначе повторный прогон по
    1000 URL тратил 33 минуты только на sleep.
    """
    cache = getattr(session, "cache", None)
    if cache is None or not hasattr(cache, "has_url"):
        return False
    try:
        return bool(cache.has_url(url))
    except Exception:                            # noqa: BLE001 — кэш не критичен
        return False


def estimate_crawl_minutes(sources: list[SourceItem], request_delay: float) -> float:
    """Нижняя оценка времени рана по одной только вежливой задержке (A6).

    Задержка копится на домене, у которого больше одного URL: первый запрос к
    домену не спит.
    """
    from collections import Counter
    from urllib.parse import urlparse
    per_domain = Counter(urlparse(s.url).netloc for s in sources)
    sleeps = sum(max(0, n - 1) for n in per_domain.values())
    return sleeps * float(request_delay or 0) / 60.0


class CrawlThrottle:
    """Вежливость краулинга: request_delay + Crawl-delay из robots + cache-skip (A5/A6).

    Правила:
      * доменное `Crawl-delay`/`Request-rate` из robots.txt имеет приоритет над
        глобальным `output.request_delay` (раньше игнорировалось);
      * если ответ уже в HTTP-кэше — спать незачем: запроса к серверу нет (A6).
    """

    def __init__(self, rate_limiter: RateLimiter, robots: RobotsCache,
                 session: Any, respect_robots: bool = True):
        self.rate_limiter = rate_limiter
        self.robots = robots
        self.session = session
        self.respect_robots = respect_robots
        self._configured: set[str] = set()

    def wait(self, url: str) -> bool:
        """Вернуть True, если реально ждали (для логов/оценок)."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if self.respect_robots and domain not in self._configured:
            self._configured.add(domain)
            try:
                delay = self.robots.crawl_delay(url)
            except Exception:                       # noqa: BLE001
                delay = None
            if delay:
                self.rate_limiter.set_domain_delay(domain, float(delay))
                log.info(f"robots.txt {domain}: Crawl-delay {delay}s "
                         f"(глобальный request_delay={self.rate_limiter.default_delay}s)")
        if _is_cached(url, self.session):
            log.debug(f"cache hit → задержка пропущена: {url[:70]}")
            return False
        self.rate_limiter.wait(url)
        return True


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
        eta = estimate_crawl_minutes(sources, config.output.request_delay)
        return {"total": len(sources), "skipped": 0, "errors": 0, "dry_run": True,
                "min_wait_minutes": round(eta, 1),
                "parallel_hint": crawl_dispatch_hint(sources, config)}

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

    eta = estimate_crawl_minutes(sources, config.output.request_delay)
    if eta >= 1:
        log.info(f"Ожидание: ≥{eta:.0f} мин только на вежливую задержку "
                 f"(request_delay={config.output.request_delay} c/домен) — "
                 f"{len(sources)} источников на "
                 f"{len({urlparse(s.url).netloc for s in sources})} доменах")
    hint = crawl_dispatch_hint(sources, config)
    if hint:
        log.info(hint)
    throttle = CrawlThrottle(rate_limiter, robots, session,
                             respect_robots=robots.respect)
    if config.pipeline.progress_bar and on_progress is None:
        pbar = tqdm(sources, desc="crawling", unit="src")
    else:
        pbar = sources

    file_lock = threading.Lock()      # общие corpus_file/errors.jsonl (I2)
    state_lock = threading.Lock()     # общий state (I2)
    crawl_pool = CrawlWorkerPool(pool_size(config))   # A5: K потоков вместо «потока на URL»
    abandoned: list[Any] = []                      # учёт зависших задач

    total = len(sources) + skipped_by_robots
    errors = 0
    # A: «skipped» раньше смешивал три разные причины (уже сделано / ошибка
    # прошлого рана / запрещено robots), из-за чего нельзя было понять, почему
    # корпус меньше ожидаемого.
    skipped_done = 0
    skipped_failed_before = 0
    skipped_robots = skipped_by_robots
    skipped_duplicate = 0
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

    last_save = [0.0]

    def save_state(final: bool = False) -> None:
        """Чекпойнт (A5): промежуточные — компактные и не чаще раза в N секунд,
        финальный — полный и отсортированный."""
        now = time.monotonic()
        if not final and (now - last_save[0]) < config.pipeline.min_checkpoint_seconds:
            return
        with state_lock:
            state.save(compact=not final)
        last_save[0] = now

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
        if is_error(url):
            skipped_failed_before += 1
            continue
        if is_done(url):
            skipped_done += 1
            continue
        if is_done(canonical_url(url)):
            skipped_duplicate += 1
            continue

        # robots.txt
        if not src.ignore_robots and not robots.is_allowed(url):
            skipped_robots += 1
            emit_log("WARNING",
                     f"robots.txt не разрешает {url[:70]} — пропускаю. Если краулер "
                     f"ходит в API другого домена, для этого источника можно поставить "
                     f"`ignore_robots: true`")
            # ВАЖНО: НЕ mark_done(url): запрет robots.txt — не «успех», и после
            # изменения robots.txt/флага источник обязан снова попасть в выборку
            continue

        # rate-limit + Crawl-delay, с пропуском на cache-hit (A5/A6)
        throttle.wait(url)

        # Краулим с timeout — если зависает более N минут, пропускаем URL
        per_url_timeout = config.pipeline.per_url_timeout_minutes
        record = None
        try:
            crawler = make_crawler(src.type, config, session)
            record = _crawl_with_timeout(
                crawler, url, src.categories or [],
                timeout_seconds=per_url_timeout * 60,
                source=src,
                on_abandon=abandoned.append,
                pool=crawl_pool,
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
                    skipped_duplicate += 1
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

    # A5/I7: зависшие задачи всё ещё держат сокеты — честно в статистику,
    # после чего закрываем пул (иначе ненулевой пул держит процесс).
    # зависшие задачи (box'ы, которые так и не завершились) — честно в статистику
    still_alive = [b for b in abandoned if not b.get("done")]
    if still_alive:
        log.warning(f"{len(still_alive)} timed-out crawl tasks are still running; "
                    f"они завершатся, когда сработают HTTP-таймауты requests")
    if crawl_pool.queued:
        log.warning(f"в пуле краула ({crawl_pool.workers} потоков) осталось "
                    f"{crawl_pool.queued} незавершённых задач")
    crawl_pool.shutdown()

    save_state(final=True)

    breakdown = {"already_done": skipped_done,
                 "previously_failed": skipped_failed_before,
                 "robots_disallowed": skipped_robots,
                 "duplicate_url_in_config": skipped_duplicate}
    return _crawl_stats(total, processed, skipped_done + skipped_failed_before
                        + skipped_robots + skipped_duplicate, errors, state, stopped,
                        len(abandoned), len(still_alive), breakdown)


def _crawl_stats(total: int, processed: int, skipped: int, errors: int,
                 state: State, stopped: bool, abandoned: int = 0,
                 still_running: int = 0, skip_breakdown: dict | None = None) -> dict:
    stats = {
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "skipped_breakdown": dict(skip_breakdown or {}),
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

    # A4: сквозной прогресс. Стадии имеют «вес», внутри стадии прогресс
    # конвертируется в проценты всего поста — шкала в GUI перестала стоять
    # на 25/50/75% по 10 минут.
    weights = {"dedup": 0.45, "quality": 0.30, "normalize": 0.10, "pairs": 0.15}
    state_pct = {"v": 0.0}

    def emit_stage(stage_no: int, msg: str) -> None:
        stage(stage_no, 4, msg)

    def subdiv(name: str):
        base = sum(weights[k] for k in ("dedup", "quality", "normalize", "pairs")
                   if list(weights).index(k) < list(weights).index(name))
        def cb(done: int, total: int) -> None:
            frac = (done / total) if total else 0.0
            pct = int((base + frac * weights[name]) * 100)
            state_pct["v"] = max(state_pct["v"], pct)
            if on_progress:
                on_progress(state_pct["v"], 100, f"{name}: {done}/{total}")
            if on_log and done and total and done % 5000 == 0:
                on_log("INFO", f"{name}: {done}/{total}")
        return cb

    emit_stage(1, "Deduplication")
    # стратегия выбирается конфигурацией: streaming/incremental раньше никуда
    # не передавались из настроек (I3/I4)
    dedup_stats = run_dedup_adaptive(raw_file, deduped_file, config.dedup,
                                     on_progress=subdiv("dedup"))

    if stopped():
        return {"stopped": True, "stage": "quality", "dedup": dedup_stats}

    emit_stage(2, "Quality filter")
    if config.pipeline.parallel_postproc:
        from .parallel_postproc import run_quality_filter_parallel
        quality_stats = run_quality_filter_parallel(
            deduped_file, filtered_file, config.quality,
            workers=config.pipeline.parallel_workers or None)
    else:
        quality_stats = run_quality_filter(deduped_file, filtered_file, config.quality,
                                           on_progress=subdiv("quality"))

    if stopped():
        return {"stopped": True, "stage": "normalize",
                "dedup": dedup_stats, "quality": quality_stats}

    emit_stage(3, "Final normalization")
    norm_stats = run_normalize(filtered_file, normalized_file,
                               on_progress=subdiv("normalize"))

    if config.export.write_gzip:
        gz_path = Path(str(normalized_file) + ".gz")
        norm_stats["gzip_file"] = str(gz_path)
        norm_stats["gzip_bytes"] = _gzip_file(normalized_file, gz_path)

    if stopped():
        return {"stopped": True, "stage": "pairs", "dedup": dedup_stats,
                "quality": quality_stats, "normalize": norm_stats}

    emit_stage(4, "Instruction-tuning pairs")
    pairs_stats = run_extract_pairs(normalized_file, pairs_file,
                                    on_progress=subdiv("pairs"))

    # A4: финальный clamp — стадии могут не сообщить промежуточные точки на
    # маленьких корпусах (шаг отчёта 200–500 записей), шкала обязана дойти до 100
    if on_progress:
        on_progress(100, 100, "postprocess: done")

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
