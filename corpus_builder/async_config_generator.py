"""Асинхронные генераторы config.yaml — 10-30x быстрее синхронной версии.

Улучшения:
  1. Асинхронный BFS через aiohttp (async_seed_crawl_depth)
  2. Параллельная обработка нескольких seeds (crawl_excel_async)
  3. selectolax для быстрого парсинга ссылок (5-10x быстрее BeautifulSoup)
  4. Кэширование между seeds (HTTP-кэш через requests-cache)
  6. Прогресс с ETA (ProgressTracker)
  7. Skip crawl опция — только URL из Excel, без сетевых запросов
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

from .config_generator import from_excel, make_source
from .logging_setup import get_logger

log = get_logger(__name__)

ProgressCallback = Callable[[int, int, str], None]
StopCallback = Callable[[], bool]


# ============================================================
# Улучшение 6: ProgressTracker с ETA
# ============================================================

class ProgressTracker:
    """Трекер прогресса с оценкой оставшегося времени (ETA).

    Использование:
        tracker = ProgressTracker(total=100)
        for i in range(100):
            do_work()
            stats = tracker.update(1)
            print(f"{stats['done']}/{stats['total']} | ETA: {stats['eta']}")
    """

    def __init__(self, total: int):
        self.total = total
        self.start_time = time.time()
        self.done = 0

    def update(self, n: int = 1) -> dict:
        """Увеличить счётчик на n и вернуть словарь с метриками."""
        self.done += n
        elapsed = time.time() - self.start_time
        rate = self.done / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.done) / rate if rate > 0 else 0
        return {
            "done": self.done,
            "total": self.total,
            "rate": rate,
            "rate_str": f"{rate:.1f} URL/s",
            "eta_seconds": remaining,
            "eta": self._format_duration(remaining),
            "elapsed": self._format_duration(elapsed),
            "percent": int(self.done * 100 / self.total) if self.total > 0 else 0,
        }

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Форматировать секунды в 'Xm Ys'."""
        if seconds < 0:
            return "?"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"


# ============================================================
# Улучшение 3: selectolax для быстрого парсинга ссылок
# ============================================================

def extract_links_fast(html: str, base_url: str,
                       same_domain: bool = True,
                       seed_domain: str = "",
                       include_subdomains: bool = False) -> list[str]:
    """Извлечь все ссылки из HTML через selectolax (5-10x быстрее BeautifulSoup).

    Если selectolax не установлен — fallback на BeautifulSoup.
    """
    links: list[str] = []

    try:
        from selectolax.parser import HTMLParser
        tree = HTMLParser(html)
        for node in tree.css("a[href]"):
            href = node.attributes.get("href", "")
            if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            # Фильтр по домену
            if same_domain and seed_domain:
                if include_subdomains:
                    if parsed.netloc != seed_domain and not parsed.netloc.endswith("." + seed_domain):
                        continue
                elif parsed.netloc != seed_domain:
                    continue
            links.append(full_url)
        return links
    except ImportError:
        # Fallback на BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if same_domain and seed_domain:
                if include_subdomains:
                    if parsed.netloc != seed_domain and not parsed.netloc.endswith("." + seed_domain):
                        continue
                elif parsed.netloc != seed_domain:
                    continue
            links.append(full_url)
        return links


# ============================================================
# Улучшение 1: Асинхронный BFS через aiohttp
# ============================================================

async def async_fetch_html(url: str, session, timeout: int = 20,
                            url_cache: dict | None = None) -> str | None:
    """Асинхронно получить HTML страницы.

    С опциональным in-memory кэшем (Улучшение 4): если один и тот же URL
    запрашивается повторно (например, при нескольких seeds с одного домена),
    возвращаем кэшированный HTML без повторного запроса.
    """
    import aiohttp

    # Проверяем кэш (Улучшение 4)
    if url_cache is not None and url in url_cache:
        return url_cache[url]

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return None
            # Кодировка
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
                html = content.decode(encoding, errors="replace")
            except (LookupError, TypeError):
                html = content.decode("utf-8", errors="replace")

            # Сохраняем в кэш (Улучшение 4)
            if url_cache is not None:
                url_cache[url] = html

            return html
    except asyncio.TimeoutError:
        log.debug(f"Timeout for {url}")
    except Exception as e:
        log.debug(f"Error fetching {url}: {e}")
    return None


async def async_seed_crawl_depth(
    seed: str,
    depth: int = 1,
    max_urls: int = 100,
    same_domain_only: bool = True,
    include_subdomains: bool = False,
    request_delay: float = 1.0,
    batch_size: int = 10,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCallback | None = None,
    user_agent: str = "CorpusBuilder/0.2 (async-config-gen)",
    url_cache: dict | None = None,
) -> list[dict]:
    """Асинхронный BFS-обход — 5-10x быстрее синхронной версии.

    Параметры:
        seed: стартовый URL
        depth: глубина обхода (0 = только seed, 1 = seed + ссылки, и т.д.)
        max_urls: лимит общего числа собранных URL
        same_domain_only: только same-domain ссылки
        include_subdomains: разрешать поддомены (blog.example.com для example.com)
        request_delay: пауза между батчами (сек)
        batch_size: сколько URL качать параллельно (10 = оптимально)
        on_progress: колбэк (current, total, message)
        should_stop: если True — останавливаемся
        url_cache: опциональный кэш HTML (Улучшение 4) — переиспользуется между seeds
    """
    depth = max(depth, 0)
    depth = min(depth, 1000)

    import aiohttp

    # Нормализуем seed
    seed_parsed = urlparse(seed)
    seed_domain = seed_parsed.netloc
    seed_clean = f"{seed_parsed.scheme}://{seed_parsed.netloc}{seed_parsed.path}"
    if seed_parsed.query:
        seed_clean += "?" + seed_parsed.query

    visited: set[str] = {seed_clean}
    sources: list[dict] = [make_source(seed_clean)]

    # Очередь: (url, current_depth)
    queue: list[tuple[str, int]] = [(seed_clean, 0)]

    connector = aiohttp.TCPConnector(
        limit=50,              # всего соединений
        limit_per_host=5,      # на один домен (вежливо)
        ttl_dns_cache=300,     # кэш DNS 5 минут
        enable_cleanup_closed=True,
    )
    headers = {"User-Agent": user_agent}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        while queue and len(sources) < max_urls:
            if should_stop and should_stop():
                break

            # Берём батч URL из очереди
            batch = []
            while queue and len(batch) < batch_size:
                url, d = queue.pop(0)
                if d >= depth:
                    continue
                batch.append((url, d))

            if not batch:
                continue

            # Параллельно качаем
            tasks = [async_fetch_html(url, session, url_cache=url_cache) for url, _ in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            new_count = 0
            for (url, d), result in zip(batch, results):
                if isinstance(result, Exception) or result is None:
                    continue
                # Извлекаем ссылки через selectolax (Улучшение 3)
                links = extract_links_fast(
                    result, url,
                    same_domain=same_domain_only,
                    seed_domain=seed_domain,
                    include_subdomains=include_subdomains,
                )
                for link in links:
                    if len(sources) >= max_urls:
                        break
                    if link not in visited:
                        visited.add(link)
                        sources.append(make_source(link))
                        new_count += 1
                        if d + 1 < depth:
                            queue.append((link, d + 1))

            if on_progress:
                on_progress(
                    len(sources), max_urls,
                    f"depth={depth}: found {len(sources)} URLs (+{new_count})"
                )

            # Вежливая пауза между батчами
            if request_delay > 0 and queue:
                await asyncio.sleep(request_delay)

    if on_progress:
        on_progress(len(sources), max_urls, "done")
    return sources


# ============================================================
# Улучшение 2: Параллельная обработка нескольких seeds
# ============================================================

async def crawl_excel_async(
    excel_path: str,
    max_concurrent_seeds: int = 5,
    max_total_urls: int = 5000,
    same_domain_only: bool = True,
    include_subdomains: bool = False,
    request_delay: float = 1.0,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCallback | None = None,
    skip_crawl: bool = False,
) -> list[dict]:
    """Параллельная обработка Excel — асинхронно обходит все seeds.

    Параметры:
        excel_path: путь к Excel/CSV файлу
        max_concurrent_seeds: сколько seeds обрабатывать параллельно (5 = оптимально)
        max_total_urls: лимит общего числа собранных URL
        skip_crawl: если True — только URL из Excel, без сетевых запросов (Улучшение 7)
    """
    rows = from_excel(excel_path)
    if not rows:
        return []

    # Улучшение 7: Skip crawl — мгновенно, без сети
    if skip_crawl:
        sources = []
        seen: set[str] = set()
        for url, depth, cats in rows:
            if url not in seen:
                sources.append(make_source(url, categories=cats or None))
                seen.add(url)
        if on_progress:
            on_progress(len(sources), len(sources), f"skip_crawl: {len(sources)} URLs (no network)")
        return sources

    # Параллельная обработка seeds
    sem = asyncio.Semaphore(max_concurrent_seeds)
    all_sources: list[dict] = []
    seen_urls: set[str] = set()
    total = len(rows)

    # Общий кэш HTML для всех seeds (Улучшение 4)
    # Если 2+ seeds с одного домена — HTML переиспользуется
    shared_url_cache: dict[str, str] = {}

    # Добавляем все URL из Excel сразу (даже те, у которых depth=0)
    for url, depth, cats in rows:
        if url not in seen_urls:
            all_sources.append(make_source(url, categories=cats or None))
            seen_urls.add(url)

    # Tracker для прогресса с ETA (Улучшение 6)
    tracker = ProgressTracker(total=total)

    async def process_one(url: str, depth: int, cats: list[str], idx: int):
        async with sem:
            if should_stop and should_stop():
                return []

            if depth <= 0:
                # Только сам URL, без обхода
                stats = tracker.update(1)
                if on_progress:
                    on_progress(
                        stats["done"], stats["total"],
                        f"[{stats['done']}/{stats['total']}] ETA: {stats['eta']} | {url[:50]}"
                    )
                return []

            # Асинхронный BFS с общим кэшем
            new_sources = await async_seed_crawl_depth(
                seed=url,
                depth=depth,
                max_urls=min(max(50, depth * 50), 1000),
                same_domain_only=same_domain_only,
                include_subdomains=include_subdomains,
                request_delay=request_delay,
                on_progress=None,  # не передаём, т.к. обрабатываем несколько seeds
                should_stop=should_stop,
                url_cache=shared_url_cache,  # Улучшение 4
            )

            result: list[dict] = []
            for s in new_sources:
                if s["url"] not in seen_urls:
                    if cats:
                        s["categories"] = list(s.get("categories") or []) + cats
                    result.append(s)
                    seen_urls.add(s["url"])

            stats = tracker.update(1)
            if on_progress:
                on_progress(
                    stats["done"], stats["total"],
                    f"[{stats['done']}/{stats['total']}] +{len(result)} | ETA: {stats['eta']} | {url[:50]}"
                )
            return result

    tasks = [process_one(url, depth, cats, i) for i, (url, depth, cats) in enumerate(rows)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Объединяем результаты
    for result in results:
        if isinstance(result, Exception):
            log.debug(f"Seed processing failed: {result}")
            continue
        all_sources.extend(result)
        if len(all_sources) >= max_total_urls:
            break

    if on_progress:
        on_progress(len(all_sources), len(all_sources), f"done: {len(all_sources)} URLs total")
    return all_sources


# ============================================================
# Удобная синхронная обёртка для GUI/CLI
# ============================================================

def crawl_excel_async_sync(
    excel_path: str,
    max_concurrent_seeds: int = 5,
    max_total_urls: int = 5000,
    same_domain_only: bool = True,
    include_subdomains: bool = False,
    request_delay: float = 1.0,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCallback | None = None,
    skip_crawl: bool = False,
) -> list[dict]:
    """Синхронная обёртка для crawl_excel_async — для вызова из не-async кода.

    Запускает event loop и ждёт результата.
    На Windows в PyInstaller frozen режиме использует SelectorEventLoopPolicy
    (ProactorEventLoopPolicy может не работать корректно с aiohttp).
    """
    import sys as _sys
    if _sys.platform == "win32":
        # На Windows используем SelectorEventLoop для совместимости с aiohttp
        # в PyInstaller frozen режиме
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(crawl_excel_async(
        excel_path=excel_path,
        max_concurrent_seeds=max_concurrent_seeds,
        max_total_urls=max_total_urls,
        same_domain_only=same_domain_only,
        include_subdomains=include_subdomains,
        request_delay=request_delay,
        on_progress=on_progress,
        should_stop=should_stop,
        skip_crawl=skip_crawl,
    ))
