"""Нативный асинхронный HTML-краулер на aiohttp."""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile

log = get_logger(__name__)


async def async_fetch_html(url, session, timeout=30):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()
            encoding = resp.charset
            if not encoding or encoding.lower() == "iso-8859-1":
                try:
                    import charset_normalizer
                    guess = charset_normalizer.detect(content)
                    encoding = (guess.get("encoding") if guess else None) or "utf-8"
                except ImportError:
                    encoding = "utf-8"
            try:
                return content.decode(encoding, errors="replace"), str(resp.url)
            except (LookupError, TypeError):
                return content.decode("utf-8", errors="replace"), str(resp.url)
    except asyncio.TimeoutError:
        log.debug(f"Timeout for {url}")
    except Exception as e:
        log.debug(f"Error fetching {url}: {e}")
    return None


def make_aiohttp_session(config):
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=10, ttl_dns_cache=300,
                                       enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=config.output.request_timeout)
    headers = {
        "User-Agent": config.output.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    return aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers)
