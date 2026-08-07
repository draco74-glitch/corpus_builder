"""Кэширование HTTP-ответов через requests-cache.

Решает проблему: при повторном запуске после сбоя повторно качаются все страницы.
Кэш хранит ответы в SQLite (responses_cache/http_cache.sqlite), с TTL 7 дней.

Поддерживает условные запросы If-Modified-Since / If-None-Match — при истечении
TTL сервер вернёт 304 Not Modified, и мы переиспользуем тело ответа.

Использование:
    session = make_cached_session(config, ttl_hours=24*7)

    # далее как обычная requests.Session:
    resp = session.get(url)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def _optimize_sqlite_cache(cache_path: Path) -> None:
    """Применить WAL-mode и другие оптимизации SQLite для кэша.

    WAL (Write-Ahead Logging) позволяет:
      - Параллельные чтения без блокировок
      - Пакетная запись (вместо коммита на каждый INSERT)
      - До 2x ускорения на операциях записи
    """
    sqlite_file = Path(str(cache_path) + ".sqlite")
    if not sqlite_file.exists():
        return  # БД ещё не создана — requests-cache создаст её сам
    try:
        conn = sqlite3.connect(str(sqlite_file))
        # WAL: параллельные чтения, неблокирующая запись
        conn.execute("PRAGMA journal_mode=WAL;")
        # NORMAL: не дожидаемся fsync на каждый коммит (быстрее, но чуть безопаснее OFF)
        conn.execute("PRAGMA synchronous=NORMAL;")
        # Больший кэш страниц в памяти (по умолчанию 2MB)
        conn.execute("PRAGMA cache_size=-64000;")  # 64 MB
        # temp_store=MEMORY: временные таблицы и индексы в RAM
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.commit()
        conn.close()
        log.debug(f"SQLite WAL enabled for {sqlite_file}")
    except Exception as e:
        log.warning(f"Failed to optimize SQLite cache: {e}")


def make_cached_session(
    config: AppConfig,
    ttl_hours: int = 24 * 7,
    use_cache: bool = True,
):
    """Создать requests.Session с кэшированием (если use_cache=True) или обычную.

    Если use_cache=False — возвращает обычную requests.Session.

    С WAL-mode для параллельных чтений и неблокирующей записи.
    """
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
        expire_after=3600 * ttl_hours,  # TTL в часах
        allowable_codes=(200,),         # кэшируем только успешные ответы
        stale_if_error=True,            # при ошибке сети отдаём старый кэш
        cache_control=True,             # уважаем Cache-Control headers
    )
    session.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })

    # Применяем оптимизации SQLite (WAL и др.)
    _optimize_sqlite_cache(cache_path)

    log.info(f"HTTP cache enabled: {cache_path}.sqlite (TTL={ttl_hours}h, WAL=on)")
    return session
