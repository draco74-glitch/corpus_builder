"""Кэширование HTTP-ответов через requests-cache с SQLite WAL (Улучшение 5)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def _optimize_sqlite_cache(cache_path: Path) -> None:
    sqlite_file = Path(str(cache_path) + ".sqlite")
    if not sqlite_file.exists():
        return
    try:
        conn = sqlite3.connect(str(sqlite_file))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Failed to optimize SQLite cache: {e}")


def make_cached_session(config: AppConfig, ttl_hours: int = 24 * 7, use_cache: bool = True):
    from .robots import make_session

    if not use_cache:
        return make_session(config)

    try:
        import requests_cache
    except ImportError:
        log.warning("requests-cache not installed, falling back to plain requests.Session")
        return make_session(config)

    cache_dir = Path(config.output.download_dir).parent / "responses_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "http_cache"

    session = requests_cache.CachedSession(
        cache_name=str(cache_path),
        backend="sqlite",
        expire_after=3600 * ttl_hours,
        allowable_codes=(200,),
        stale_if_error=True,
        cache_control=True,
    )
    session.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })

    _optimize_sqlite_cache(cache_path)

    log.info(f"HTTP cache enabled: {cache_path}.sqlite (TTL={ttl_hours}h, WAL=on)")
    return session
