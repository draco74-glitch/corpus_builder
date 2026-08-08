"""Кэш robots.txt и вежливая проверка before_request."""
from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from typing import Any

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
            # RobotFileParser.read() использует urllib — нет timeout, поэтому
            # подгружаем сами и парсим из строки.
            import requests
            r = requests.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            if r.status_code == 200:
                parser.parse(r.text.splitlines())
            elif r.status_code in (401, 403):
                # Доступ закрыт = всё запрещено
                parser.disallow_all = True
                log.warning(f"robots.txt returned {r.status_code} for {domain} — assuming disallow")
            else:
                # 404 и пр. = разрешено всё
                parser.allow_all = True
                log.debug(f"robots.txt returned {r.status_code} for {domain} — assuming allow")
        except Exception as e:
            log.warning(f"Failed to fetch robots.txt for {domain}: {e} — assuming allow")
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
    """Per-domain rate limiter: не чаще чем delay секунд между запросами на один домен."""

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
    """Создать requests.Session с правильными заголовками."""
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })
    return s
