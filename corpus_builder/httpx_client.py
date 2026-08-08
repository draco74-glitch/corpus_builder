"""HTTP/2 клиент через httpx."""
from __future__ import annotations

from typing import Any

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def is_httpx_available() -> bool:
    try:
        import httpx
        return True
    except ImportError:
        return False


def make_httpx_client(config, sync=False, http2=True, follow_redirects=True, timeout=None):
    try:
        import httpx
    except ImportError:
        return None
    timeout = timeout or config.output.request_timeout
    headers = {
        "User-Agent": config.output.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)
    if sync:
        return httpx.Client(http2=http2, follow_redirects=follow_redirects, timeout=timeout,
                            headers=headers, limits=limits)
    return httpx.AsyncClient(http2=http2, follow_redirects=follow_redirects, timeout=timeout,
                              headers=headers, limits=limits)


async def fetch_with_httpx(url, client, timeout=None):
    try:
        resp = await client.get(url, timeout=timeout) if timeout else await client.get(url)
        if resp.status_code != 200:
            return None
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
