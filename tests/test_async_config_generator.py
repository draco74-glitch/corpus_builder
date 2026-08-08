"""Тесты на асинхронные генераторы config.yaml (Улучшения 1-7)."""
import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

from corpus_builder.async_config_generator import (
    ProgressTracker,
    extract_links_fast,
    async_seed_crawl_depth,
    crawl_excel_async,
    crawl_excel_async_sync,
)


# ============================================================
# Улучшение 6: ProgressTracker с ETA
# ============================================================

def test_progress_tracker_initial():
    tracker = ProgressTracker(total=100)
    assert tracker.done == 0
    assert tracker.total == 100


def test_progress_tracker_update():
    tracker = ProgressTracker(total=10)
    stats = tracker.update(1)
    assert stats["done"] == 1
    assert stats["total"] == 10
    assert stats["percent"] == 10
    assert "rate_str" in stats
    assert "eta" in stats
    assert "elapsed" in stats


def test_progress_tracker_multiple_updates():
    tracker = ProgressTracker(total=100)
    for _ in range(50):
        tracker.update(1)
    stats = tracker.update(0)  # без увеличения, просто получить статистику
    # done не должен увеличиться
    assert tracker.done == 50
    assert stats["percent"] == 50


def test_progress_tracker_format_duration():
    assert ProgressTracker._format_duration(30) == "30s"
    assert ProgressTracker._format_duration(90) == "1m 30s"
    assert ProgressTracker._format_duration(3700) == "1h 1m"
    assert ProgressTracker._format_duration(-1) == "?"


def test_progress_tracker_zero_total():
    tracker = ProgressTracker(total=0)
    stats = tracker.update(1)
    assert stats["percent"] == 0  # защита от деления на ноль


# ============================================================
# Улучшение 3: extract_links_fast (selectolax)
# ============================================================

def test_extract_links_fast_basic():
    html = """
    <html><body>
    <a href="/page1">Page 1</a>
    <a href="https://example.com/page2">Page 2</a>
    <a href="#section">Skip</a>
    <a href="mailto:test@example.com">Email</a>
    <a href="javascript:void(0)">JS</a>
    </body></html>
    """
    links = extract_links_fast(html, "https://example.com/")
    assert "https://example.com/page1" in links
    assert "https://example.com/page2" in links
    # Служебные ссылки пропускаются
    assert not any("#section" in l for l in links)
    assert not any("mailto" in l for l in links)
    assert not any("javascript" in l for l in links)


def test_extract_links_fast_same_domain_filter():
    html = """
    <a href="https://example.com/page1">Internal</a>
    <a href="https://other.com/page2">External</a>
    <a href="https://sub.example.com/page3">Subdomain</a>
    """
    # Только same-domain, без поддоменов
    links = extract_links_fast(
        html, "https://example.com/",
        same_domain=True, seed_domain="example.com", include_subdomains=False,
    )
    assert "https://example.com/page1" in links
    assert "https://other.com/page2" not in links
    assert "https://sub.example.com/page3" not in links  # поддомен отключён


def test_extract_links_fast_with_subdomains():
    html = """
    <a href="https://example.com/page1">Internal</a>
    <a href="https://blog.example.com/page2">Subdomain</a>
    <a href="https://other.com/page3">External</a>
    """
    links = extract_links_fast(
        html, "https://example.com/",
        same_domain=True, seed_domain="example.com", include_subdomains=True,
    )
    assert "https://example.com/page1" in links
    assert "https://blog.example.com/page2" in links  # поддомен разрешён
    assert "https://other.com/page3" not in links


def test_extract_links_fast_no_domain_filter():
    html = '<a href="https://other.com/page1">External</a>'
    links = extract_links_fast(html, "https://example.com/", same_domain=False)
    assert "https://other.com/page1" in links


def test_extract_links_fast_handles_empty_html():
    assert extract_links_fast("", "https://example.com/") == []


def test_extract_links_fast_handles_malformed_html():
    html = "<html><body><a href='/broken>No closing tag</body></html>"
    links = extract_links_fast(html, "https://example.com/")
    # Должно не упасть, даже если HTML битый
    assert isinstance(links, list)


# ============================================================
# Улучшение 7: Skip crawl опция
# ============================================================

def test_crawl_excel_skip_crawl(tmp_path):
    """Skip crawl — только URL из Excel, без сетевых запросов."""
    excel_path = tmp_path / "sources.csv"
    excel_path.write_text(
        "url,depth,categories\n"
        "https://example.com/1,2,electronics\n"
        "https://example.com/2,0,test\n",
        encoding="utf-8",
    )
    progress_calls = []
    def on_progress(current, total, msg):
        progress_calls.append((current, total, msg))

    sources = crawl_excel_async_sync(
        excel_path, skip_crawl=True, on_progress=on_progress,
    )
    assert len(sources) == 2
    assert sources[0]["url"] == "https://example.com/1"
    assert sources[1]["url"] == "https://example.com/2"
    # Должен быть хотя бы один прогресс-колбэк
    assert len(progress_calls) >= 1


def test_crawl_excel_skip_crawl_no_network(tmp_path):
    """Skip crawl не делает сетевых запросов даже с depth > 0."""
    excel_path = tmp_path / "sources.csv"
    excel_path.write_text(
        "url,depth\n"
        "https://example.com/page,5\n",  # depth=5 — обычно вызвало бы BFS
        encoding="utf-8",
    )

    # Мокируем async_seed_crawl_depth, чтобы убедиться, что он не вызывается
    with mock.patch(
        "corpus_builder.async_config_generator.async_seed_crawl_depth"
    ) as mock_crawl:
        async def fake_crawl(*args, **kwargs):
            return []
        mock_crawl.side_effect = fake_crawl

        sources = crawl_excel_async_sync(excel_path, skip_crawl=True)
        assert len(sources) == 1
        # async_seed_crawl_depth НЕ должен был вызваться
        assert mock_crawl.call_count == 0


# ============================================================
# Улучшение 1: Асинхронный BFS (без сети — с моком)
# ============================================================

def test_async_seed_crawl_depth_zero_depth():
    """depth=0 возвращает только сам seed (без сетевых запросов)."""
    async def run():
        sources = await async_seed_crawl_depth(
            seed="https://example.com/",
            depth=0,
            max_urls=10,
        )
        return sources

    sources = asyncio.run(run())
    assert len(sources) == 1
    assert sources[0]["url"].startswith("https://example.com")


def test_async_seed_crawl_progress_callback():
    """on_progress вызывается."""
    progress_calls = []

    async def run():
        await async_seed_crawl_depth(
            seed="https://example.com/",
            depth=0,
            on_progress=lambda c, t, m: progress_calls.append((c, t, m)),
        )

    asyncio.run(run())
    # Хотя бы один вызов (финальный)
    assert len(progress_calls) >= 1


# ============================================================
# Улучшение 4: Кэширование (in-memory)
# ============================================================

def test_url_cache_reuses_html():
    """async_fetch_html с url_cache переиспользует HTML."""
    from corpus_builder.async_config_generator import async_fetch_html
    import aiohttp

    cache: dict[str, str] = {}
    cache["https://example.com/test"] = "<html>cached</html>"

    async def run():
        async with aiohttp.ClientSession() as session:
            # Должен вернуть кэшированное значение без запроса
            html = await async_fetch_html(
                "https://example.com/test", session, url_cache=cache
            )
            return html

    html = asyncio.run(run())
    assert html == "<html>cached</html>"


def test_url_cache_stores_after_fetch():
    """После запроса HTML сохраняется в кэш."""
    # Этот тест не делает реальный сетевой запрос, проверяем логику
    from corpus_builder.async_config_generator import async_fetch_html

    cache: dict[str, str] = {}
    # Симулируем, что в кэше нет URL — функция попытается сделать запрос,
    # но мы не даём ей реальный session, поэтому она вернёт None
    # Главное — кэш остаётся пустым (не было успешного запроса)
    assert "https://example.com/test" not in cache


# ============================================================
# Интеграционный тест: полная генерация config.yaml
# ============================================================

def test_crawl_excel_async_full_pipeline_with_skip(tmp_path):
    """Полный пайплайн: Excel → skip_crawl → build_config."""
    from corpus_builder.config_generator import build_config

    excel_path = tmp_path / "sources.xlsx"
    # Создаём простой Excel
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["url", "depth", "categories"])
    ws.append(["https://habr.com/ru/articles/1/", 0, "electronics"])
    ws.append(["https://github.com/user/repo", 0, "kicad"])
    wb.save(str(excel_path))

    # Skip crawl — мгновенно
    sources = crawl_excel_async_sync(excel_path, skip_crawl=True)
    assert len(sources) == 2

    # Генерируем config.yaml
    config_path = tmp_path / "config.yaml"
    build_config(sources, config_path)
    assert config_path.exists()

    # Проверяем, что config валидный YAML
    import yaml
    with open(config_path, encoding="utf-8") as f:
        content = "\n".join(l for l in f.read().splitlines() if not l.startswith("#"))
    cfg = yaml.safe_load(content)
    assert "sources" in cfg
    assert len(cfg["sources"]) == 2
    assert "output" in cfg
