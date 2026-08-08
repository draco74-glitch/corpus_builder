"""Кэш robots.txt и вежливая проверка before_request.

С поддержкой:
  - Connection pooling в make_session (Улучшение 4)
  - Prefetch robots.txt параллельно для всех доменов (Улучшение 11)
  - Pre-filter по robots.txt до запуска краулинга (Улучшение 9)
"""
from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Any, Callable

import time
import threading
from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


class RobotsCache:
    """Кэш robots.txt per-domain с lazy-загрузкой."""

    def __init__(self, user_agent: str, timeout: int = 10):
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

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
            )
            if r.status_code == 200:
                parser.parse(r.text.splitlines())
            elif r.status_code in (401, 403):
                parser.disallow_all = True
            else:
                parser.allow_all = True
        except Exception as e:
            log.warning(f"Failed to fetch robots.txt for {domain}: {e}")
            parser.allow_all = True
        with self._lock:
            self._cache[domain] = parser
        return parser

    def is_allowed(self, url: str) -> bool:
        parser = self._get_parser(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return True


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
        parsed = urlparse(url)
        domain = parsed.netloc
        with self._lock:
            delay = self._domain_delay.get(domain, self.default_delay)
            last = self._last_request.get(domain, 0.0)
            now = time.monotonic()
            wait_for = (last + delay) - now
        if wait_for > 0:
            time.sleep(wait_for)
        with self._lock:
            self._last_request[domain] = time.monotonic()


def make_session(config: AppConfig) -> Any:
    """Создать requests.Session с connection pooling (Улучшение 4)."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })

    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=50,
        max_retries=retry_strategy,
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def prefetch_robots(robots_cache: "RobotsCache", urls: list[str],
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


def pre_filter_by_robots(sources: list, robots_cache: "RobotsCache",
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
