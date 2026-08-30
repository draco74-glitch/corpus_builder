"""Кэш robots.txt и вежливая проверка before_request.

С поддержкой:
  - Connection pooling в make_session (Улучшение 4)
  - Prefetch robots.txt параллельно для всех доменов (Улучшение 11)
  - Pre-filter по robots.txt до запуска краулинга (Улучшение 9)
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


class RobotsCache:
    """Кэш robots.txt per-domain с lazy-загрузкой.

    `respect=False` отключает проверку полностью — настройка
    `crawl.respect_robots_txt` из GUI раньше не имела потребителя (I4).
    `fail_open=False` (по умолчанию): сбой получения robots.txt трактуется
    как «не лезем», а не как «можно всё».
    """

    #: RFC 9309 §2.3: 401/403 и 5xx при запросе robots.txt означают «доступ
    #: ко всему ресурсу запрещён» (4xx без авторизации — «файла нет»,allow).
    _DISALLOW_STATUS = (401, 403, 429, 500, 502, 503, 504)

    def __init__(self, user_agent: str, timeout: int = 10, respect: bool = True,
                 fail_open: bool = False):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect = respect
        self.fail_open = fail_open
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()     # один на кэш: guard + запись

    def _get_parser(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            if domain in self._cache:
                return self._cache[domain]

        robots_url = f"{domain}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            import requests
            r = requests.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                allow_redirects=True,
            )
            if r.status_code == 200:
                parser.parse(r.text.splitlines())
            elif r.status_code in self._DISALLOW_STATUS:
                parser.disallow_all = True
                parser.allow_all = False
            else:
                # 404 и т.п. — robots нет, доступ разрешён (стандартное поведение)
                parser.allow_all = True
                parser.disallow_all = False
        except Exception as e:
            log.warning(f"Failed to fetch robots.txt for {domain}: {e}")
            # fail-closed (по умолчанию): недоступный robots.txt НЕ равен
            # «разрешено всё» — иначе сетевой сбой превращается в обход
            # запрета. fail_open=True восстанавливает прежнюю толерантность.
            parser.allow_all = self.fail_open
            parser.disallow_all = not self.fail_open
        with self._lock:
            self._cache[domain] = parser
        return parser

    def is_allowed(self, url: str) -> bool:
        if not self.respect:
            return True
        parser = self._get_parser(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception as e:
            log.debug(f"robots can_fetch failed for {url}: {e}")
            return self.fail_open


class RateLimiter:
    """Per-domain rate limiter."""

    def __init__(self, default_delay: float = 2.0):
        self.default_delay = default_delay
        self._last_request: dict[str, float] = {}
        self._domain_delay: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_domain_delay(self, domain: str, delay: float) -> None:
        with self._lock:
            self._domain_delay[domain] = delay

    def wait(self, url: str) -> None:
        """Подождать, чтобы между запросами к домену прошёл delay.

        Заносим «время последнего запроса» ПОД тем же замком, что и читаем
        предыдущее: раньше между check и stamp был зазор, и два потока
        (async-путь) успевали запросить один домен одновременно, обходя
        rate-limit (I1).
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        with self._lock:
            delay = self._domain_delay.get(domain, self.default_delay)
            last = self._last_request.get(domain, 0.0)
            now = time.monotonic()
            wait_for = (last + delay) - now
            # резервируем слот сразу: следующий поток увидит новое last
            self._last_request[domain] = now + max(wait_for, 0.0)
        if wait_for > 0:
            time.sleep(wait_for)


#: единая политика повторов для всех HTTP-сессий проекта
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 0.5
POOL_CONNECTIONS = 20
POOL_MAXSIZE = 50


def make_retry_adapter() -> Any:
    """HTTPAdapter с pool-соединений и повторами на 429/5xx."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry_strategy = Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=list(RETRY_STATUS_FORCELIST),
        allowed_methods=["GET", "HEAD"],
    )
    return HTTPAdapter(
        pool_connections=POOL_CONNECTIONS,
        pool_maxsize=POOL_MAXSIZE,
        max_retries=retry_strategy,
    )


def make_session(config: AppConfig) -> Any:
    """Создать requests.Session с connection pooling (Улучшение 4).

    Единственная реализация: `http.make_session` дублировал её код 1-в-1
    (расхождение только в том, какой из них вызывает вызывающий код).
    """
    import requests

    s = requests.Session()
    s.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })
    adapter = make_retry_adapter()
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def prefetch_robots(robots_cache: RobotsCache, urls: list[str],
                     max_workers: int = 10) -> dict[str, bool]:
    """Параллельно prefetch robots.txt для всех уникальных доменов (Улучшение 11)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    by_domain: dict[str, str] = {}
    for url in urls:
        domain = urlparse(url).netloc
        if domain and domain not in by_domain:
            by_domain[domain] = url

    if not by_domain:
        return {}

    results: dict[str, bool] = {}

    def fetch_one(domain: str, sample_url: str) -> tuple[str, bool]:
        return domain, robots_cache.is_allowed(sample_url)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, d, u): d for d, u in by_domain.items()}
        for future in as_completed(futures):
            try:
                domain, allowed = future.result()
                results[domain] = allowed
            except Exception as e:
                log.warning(f"robots prefetch failed for {futures[future]}: {e}")
                results[futures[future]] = True

    log.info(f"Prefetched robots.txt for {len(results)} domains")
    return results


def pre_filter_by_robots(sources: list, robots_cache: RobotsCache,
                         on_skip: Callable[[str], None] | None = None
                         ) -> tuple[list, dict[str, bool]]:
    """Отфильтровать источники по robots.txt до запуска краулинга (Улучшение 9)."""
    all_urls = [s.url for s in sources]
    prefetch_robots(robots_cache, all_urls)

    allowed: list = []
    disallowed_by_domain: dict[str, bool] = {}

    for src in sources:
        domain = urlparse(src.url).netloc
        if domain not in disallowed_by_domain:
            disallowed_by_domain[domain] = not robots_cache.is_allowed(src.url)
        if disallowed_by_domain[domain]:
            if on_skip:
                on_skip(src.url)
            continue
        allowed.append(src)

    return allowed, disallowed_by_domain
