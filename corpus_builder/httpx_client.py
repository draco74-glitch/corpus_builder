"""HTTP/2 клиент через httpx для ускорения на современных сайтах.

HTTP/2 позволяет мультиплексировать несколько запросов в одном TCP-соединении,
что даёт 10-20% ускорения на сайтах с HTTP/2 (большинство современных сайтов).

Используется как опциональная замена requests.Session для асинхронного пайплайна.
"""
from __future__ import annotations

from typing import Any

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def is_httpx_available() -> bool:
    """Проверить, установлен ли httpx."""
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


def make_httpx_client(
    config: AppConfig,
    sync: bool = False,
    http2: bool = True,
    follow_redirects: bool = True,
    timeout: float | None = None,
) -> Any:
    """Создать httpx.Client (синхронный) или httpx.AsyncClient (асинхронный).

    Использует HTTP/2, если поддерживается сервером, иначе fallback на HTTP/1.1.
    Пулинг соединений через httpx.Limits.

    Параметры:
        config: AppConfig с настройками
        sync: True для синхронного Client, False для AsyncClient
        http2: использовать HTTP/2 (если установлен h2)
        follow_redirects: следовать редиректам
        timeout: таймаут в секундах (по умолчанию из конфига)
    """
    try:
        import httpx
    except ImportError:
        log.warning("httpx not installed, falling back to requests")
        return None

    timeout = timeout or config.output.request_timeout
    headers = {
        "User-Agent": config.output.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    # Лимиты соединений: 20 хостов, 50 всего соединений
    limits = httpx.Limits(
        max_connections=50,
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    )

    if sync:
        client = httpx.Client(
            http2=http2,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=headers,
            limits=limits,
        )
    else:
        client = httpx.AsyncClient(
            http2=http2,
            follow_redirects=follow_redirects,
            timeout=timeout,
            headers=headers,
            limits=limits,
        )
    return client


async def fetch_with_httpx(
    url: str,
    client: Any,
    timeout: float | None = None,
) -> tuple[str, str] | None:
    """Асинхронно получить HTML через httpx.

    Возвращает (html_text, final_url) или None при ошибке.
    """
    import asyncio
    try:
        if timeout:
            resp = await client.get(url, timeout=timeout)
        else:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.debug(f"HTTP {resp.status_code} for {url}")
            return None
        # Кодировка
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            try:
                import charset_normalizer
                guess = charset_normalizer.detect(resp.content)
                if guess and guess.get("encoding"):
                    return resp.content.decode(guess["encoding"], errors="replace"), str(resp.url)
            except ImportError:
                pass
            return resp.content.decode("utf-8", errors="replace"), str(resp.url)
        return resp.text, str(resp.url)
    except Exception as e:
        log.debug(f"httpx error for {url}: {e}")
        return None
