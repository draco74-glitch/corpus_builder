"""Прокси-ротация и browser-like заголовки для обхода анти-бот защиты.

Используется для сайтов с жёсткими лимитами (IEEE Xplore, ScienceDirect):
при 429/403 автоматически переключается на следующий прокси из списка.

Поддержка:
  - HTTP/HTTPS прокси: http://user:pass@host:port
  - SOCKS5: socks5://user:pass@host:port (нужен requests[socks])
  - Browser-like заголовки: Accept-Language, Sec-Fetch-*, Sec-Ch-Ua
"""
from __future__ import annotations

import itertools
import random
from typing import Any

import requests

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


# Реалистичные User-Agent строки для ротации
# (взяты из топовых браузеров на момент 2026 года)
USER_AGENTS = [
    # Chrome на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Edge на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Firefox на Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    # Chrome на macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Chrome на Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]


def get_browser_headers(user_agent: str | None = None) -> dict[str, str]:
    """Вернуть browser-like заголовки для маскировки под реальный браузер.

    Используется для обхода простой анти-бот защиты, которая проверяет
    наличие Sec-Fetch-* и Accept заголовков.
    """
    ua = user_agent or random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.9,en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Cache-Control": "max-age=0",
    }


class ProxyRotator:
    """Ротация прокси для обхода блокировок.

    Использование:
        rotator = ProxyRotator(config.network)
        proxy = rotator.next()
        try:
            resp = session.get(url, proxies={"http": proxy, "https": proxy})
        except Exception:
            rotator.mark_bad(proxy)
    """

    def __init__(self, proxies: list[str] | None = None):
        self.proxies: list[str] = proxies or []
        self._bad: set[str] = set()
        self._cycle = itertools.cycle(self.proxies) if self.proxies else None

    def next(self) -> str | None:
        """Вернуть следующий рабочий прокси или None, если список пуст."""
        if not self._cycle:
            return None
        # Перебираем, пока не найдём не помеченный как плохой
        for _ in range(len(self.proxies)):
            proxy = next(self._cycle)
            if proxy not in self._bad:
                return proxy
        # Все помечены как плохие — сбрасываем и пытаемся снова
        log.warning("All proxies marked as bad, resetting blacklist")
        self._bad.clear()
        return next(self._cycle, None)

    def mark_bad(self, proxy: str) -> None:
        """Помечает прокси как неработающий (например, после 403/429)."""
        if proxy:
            self._bad.add(proxy)
            log.info(f"Proxy marked as bad: {proxy[:30]}... ({len(self._bad)}/{len(self.proxies)} bad)")

    def __bool__(self) -> bool:
        return bool(self.proxies)


def make_session_with_proxy(
    config: AppConfig,
    use_browser_headers: bool = True,
    use_proxy: bool = True,
) -> Any:
    """Создать requests.Session с browser-like заголовками и опциональным прокси.

    Возвращает tuple (session, rotator). rotator == None, если прокси не заданы.
    """
    from .robots import make_session
    session = make_session(config)

    if use_browser_headers:
        headers = get_browser_headers()
        # Пользовательский UA из конфига уважаем ВСЕГДА: раньше ветка `if ...: pass`
        # ничего не делала, и браузерный UA перезаписывал настроенный; при
        # кастомном UA — наоборот, терялись Sec-Fetch-* заголовки (I4).
        if config.output.user_agent:
            headers["User-Agent"] = config.output.user_agent
        session.headers.update(headers)

    rotator = None
    if use_proxy:
        # Прокси приходят из AppSettings.setup_env_vars() -> CORPUS_BUILDER_PROXIES
        proxy_env = os.environ.get("CORPUS_BUILDER_PROXIES", "")
        proxies = [p.strip() for p in proxy_env.split(",") if p.strip()]
        if proxies:
            rotator = ProxyRotator(proxies)
            log.info(f"Proxy rotator initialized with {len(proxies)} proxies")
            session = RotatingProxySession(session, rotator)

    return session, rotator


class RotatingProxySession(requests.Session):
    """Сессия, выбирающая прокси на КАЖДЫЙ запрос (I4: ротация была декоративной).

    Один `session.proxies` на всё время жизни сессии использует только первый
    прокси; здесь `prepare_request` назначает `req.proxies` из ротатора, а при
    сбое прокси помечается «плохим» и запрос повторяется напрямую.
    """

    def __init__(self, base: requests.Session, rotator: ProxyRotator):
        self.__dict__.update(base.__dict__)
        self._rotator = rotator

    def prepare_request(self, request):
        prepared = super().prepare_request(request)
        proxy = self._rotator.next_proxy()
        if proxy:
            prepared.proxies = {"http": proxy, "https": proxy}
        return prepared

    def send(self, request, **kwargs):
        try:
            return super().send(request, **kwargs)
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError) as e:
            used = dict(getattr(request, "proxies", None) or {})
            if used.get("https") or used.get("http"):
                self._rotator.mark_bad(used.get("https") or used.get("http"))
            log.debug(f"proxy failed ({e}); retrying directly")
            kwargs["proxies"] = {}
            return super().send(request, **kwargs)


import os  # нужен для os.environ в make_session_with_proxy
