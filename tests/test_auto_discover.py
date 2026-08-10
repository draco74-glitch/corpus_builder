"""Тесты на auto_discover и from_wikipedia."""
import json
from pathlib import Path
from unittest import mock

import pytest


# ============================================================
# AutoDiscover — тесты логики (без сети)
# ============================================================

def test_auto_discover_init():
    """Инициализация AutoDiscover."""
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    assert discover._all_sources == []
    assert discover._seen_urls == set()
    assert discover._stats == {}


def test_auto_discover_add_source_dedup():
    """Дедупликация при добавлении источников."""
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    discover._add_source("https://example.com/1", ["test"])
    discover._add_source("https://example.com/1", ["test"])  # дубликат
    assert len(discover._all_sources) == 1


def test_auto_discover_get_stats():
    """Получение статистики."""
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    discover._add_source("https://example.com/1")
    discover._add_source("https://example.com/2")
    stats = discover.get_stats()
    assert stats["total"] == 2
    assert stats["unique_urls"] == 2


def test_auto_discover_presets():
    """Предустановленные наборы тем."""
    from corpus_builder.auto_discover import AutoDiscover
    presets = AutoDiscover.get_preset_topics()
    assert "electronics_general" in presets
    assert "analog_design" in presets
    assert "microcontrollers" in presets
    assert "power_electronics" in presets
    assert "rf_microwave" in presets
    assert "russian_electronics" in presets

    # Проверить структуру пресета
    preset = presets["electronics_general"]
    assert "github_topics" in preset
    assert "se_tags" in preset
    assert "wiki_categories" in preset
    assert "wiki_lang" in preset


def test_auto_discover_save_config(tmp_path):
    """Сохранение config.yaml."""
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    discover._add_source("https://example.com/1", ["test"])
    discover._add_source("https://example.com/2", ["test"])

    output = tmp_path / "config.auto.yaml"
    result = discover.save_config(discover._all_sources, output)

    assert Path(result).exists()
    import yaml
    with open(output, encoding="utf-8") as f:
        content = "\n".join(l for l in f.read().splitlines() if not l.startswith("#"))
    cfg = yaml.safe_load(content)
    assert len(cfg["sources"]) == 2


def test_auto_discover_discover_empty():
    """Пустой discover() не должен падать."""
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    sources = discover.discover(
        topics=None, se_tags=None, wiki_categories=None,
    )
    assert sources == []
    assert discover.get_stats()["total"] == 0


# ============================================================
# from_wikipedia — тесты (без сети)
# ============================================================

def test_from_wikipedia_empty_categories():
    """Пустой список категорий — пустой результат."""
    from corpus_builder.config_generator import from_wikipedia
    sources = from_wikipedia([], lang="en")
    assert sources == []


def test_from_wikipedia_with_mock(tmp_path):
    """Тест with mocked Wikipedia API."""
    from corpus_builder.config_generator import from_wikipedia

    # Мокируем requests.get для Wikipedia API
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {
            "categorymembers": [
                {"title": "Operational amplifier"},
                {"title": "Printed circuit board"},
                {"title": "Category:Subcategory"},  # должно пропуститься
                {"title": "File:something.png"},    # должно пропуститься
            ]
        }
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia(
            categories=["Electronics"],
            lang="en",
            max_articles=10,
        )

    # Должны получить 2 статьи (Operational amplifier, Printed circuit board)
    assert len(sources) == 2
    urls = [s["url"] for s in sources]
    assert any("Operational_amplifier" in u for u in urls)
    assert any("Printed_circuit_board" in u for u in urls)


def test_from_wikipedia_api_error():
    """При ошибке API — возвращается пустой список (не падает)."""
    from corpus_builder.config_generator import from_wikipedia

    mock_response = mock.MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = Exception("Server error")

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia(
            categories=["Electronics"],
            lang="en",
        )

    assert sources == []


def test_from_wikipedia_dedup_across_categories():
    """Дедупликация статей между категориями."""
    from corpus_builder.config_generator import from_wikipedia

    # Две категории возвращают одни и те же статьи
    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {
            "categorymembers": [
                {"title": "Operational amplifier"},
            ]
        }
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia(
            categories=["Electronics", "Circuits"],
            lang="en",
            max_articles=10,
        )

    # Должна быть только 1 статья (дубликат удалён)
    assert len(sources) == 1


def test_from_wikipedia_russian():
    """Тест с русским Wikipedia."""
    from corpus_builder.config_generator import from_wikipedia

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {
            "categorymembers": [
                {"title": "Электроника"},
                {"title": "Печатная плата"},
            ]
        }
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia(
            categories=["Электроника"],
            lang="ru",
            max_articles=10,
        )

    assert len(sources) == 2
    urls = [s["url"] for s in sources]
    assert any("ru.wikipedia.org" in u for u in urls)


def test_from_wikipedia_categories_in_result():
    """В результате есть категории wikipedia."""
    from corpus_builder.config_generator import from_wikipedia

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {
            "categorymembers": [
                {"title": "Operational amplifier"},
            ]
        }
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia(
            categories=["Electronics"],
            lang="en",
        )

    assert len(sources) == 1
    cats = sources[0].get("categories", [])
    assert any("wikipedia" in c for c in cats)
    assert any("category:electronics" in c.lower() for c in cats)
