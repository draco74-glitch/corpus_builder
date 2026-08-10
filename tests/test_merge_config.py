"""Тесты на умное объединение config.yaml и мультиязычный Wikipedia."""
import json
import yaml
from pathlib import Path
from unittest import mock

import pytest


# ============================================================
# merge_sources — умное объединение с дедупликацией
# ============================================================

def test_merge_exact_duplicates(tmp_path):
    """Точное совпадение URL — дубликат удаляется."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page", "type": "html"}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page", "type": "html"}]
    }), encoding="utf-8")

    merged = merge_sources([str(f1), str(f2)])
    assert len(merged) == 1


def test_merge_canonical_duplicates(tmp_path):
    """Канонизированные URL-ы — дубликат удаляется (utm-params, trailing slash)."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page?id=1&utm_source=email", "type": "html"}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page?id=1", "type": "html"}]
    }), encoding="utf-8")

    merged = merge_sources([str(f1), str(f2)])
    assert len(merged) == 1  # дубликат по канонизированному URL


def test_merge_trailing_slash(tmp_path):
    """URL с/без trailing slash — дубликат."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page/", "type": "html"}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page", "type": "html"}]
    }), encoding="utf-8")

    merged = merge_sources([str(f1), str(f2)])
    assert len(merged) == 1


def test_merge_categories_merged(tmp_path):
    """Категории из дубликатов сливаются."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page", "type": "html", "categories": ["electronics"]}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/page", "type": "html", "categories": ["kicad"]}]
    }), encoding="utf-8")

    merged = merge_sources([str(f1), str(f2)])
    assert len(merged) == 1
    cats = merged[0].get("categories", [])
    assert "electronics" in cats
    assert "kicad" in cats


def test_merge_no_duplicates(tmp_path):
    """Нет дубликатов — все записи сохраняются."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/1", "type": "html"}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/2", "type": "html"}]
    }), encoding="utf-8")

    merged = merge_sources([str(f1), str(f2)])
    assert len(merged) == 2


def test_merge_with_stats(tmp_path):
    """merge_sources_with_stats возвращает статистику."""
    from corpus_builder.config_generator import merge_sources_with_stats

    f1 = tmp_path / "c1.yaml"
    f2 = tmp_path / "c2.yaml"
    f1.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/1", "type": "html"}]
    }), encoding="utf-8")
    f2.write_text(yaml.dump({
        "sources": [{"url": "https://example.com/1", "type": "html"},
                   {"url": "https://example.com/2", "type": "html"}]
    }), encoding="utf-8")

    merged, stats = merge_sources_with_stats([str(f1), str(f2)])
    assert stats["total_input"] == 3
    assert stats["total_output"] == 2
    assert stats["duplicates_removed"] == 1
    assert "c1.yaml" in stats["by_file"]
    assert "c2.yaml" in stats["by_file"]


def test_merge_empty_files(tmp_path):
    """Пустые файлы — пустой результат."""
    from corpus_builder.config_generator import merge_sources

    f1 = tmp_path / "c1.yaml"
    f1.write_text("sources: []", encoding="utf-8")

    merged = merge_sources([str(f1)])
    assert merged == []


# ============================================================
# from_wikipedia_multi — мультиязычный поиск
# ============================================================

def test_wikipedia_multi_lang_with_mock():
    """Мультиязычный поиск — mocked API для двух языков."""
    from corpus_builder.config_generator import from_wikipedia_multi

    mock_response = mock.MagicMock()
    mock_response.status_code = 200

    def side_effect(*args, **kwargs):
        # Разные ответы для разных языков
        url = args[0] if args else kwargs.get("url", "")
        if "en.wikipedia.org" in url:
            mock_response.json.return_value = {
                "query": {"categorymembers": [{"title": "Electronics"}]}
            }
        elif "ru.wikipedia.org" in url:
            mock_response.json.return_value = {
                "query": {"categorymembers": [{"title": "Электроника"}]}
            }
        return mock_response

    with mock.patch("requests.get", side_effect=side_effect):
        sources = from_wikipedia_multi(
            categories=["Electronics"],
            languages=["en", "ru"],
            max_articles=5,
        )

    # Должны получить статьи с обоих языков
    assert len(sources) >= 2
    urls = [s["url"] for s in sources]
    assert any("en.wikipedia.org" in u for u in urls)
    assert any("ru.wikipedia.org" in u for u in urls)


def test_wikipedia_multi_lang_dedup():
    """Дедупликация между языками (разные языки = разные URL)."""
    from corpus_builder.config_generator import from_wikipedia_multi

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {"categorymembers": [{"title": "Electronics"}]}
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia_multi(
            categories=["Electronics"],
            languages=["en", "ru", "de"],
            max_articles=5,
        )

    # Каждый язык даёт 1 статью = 3 URL (разные языки = разные URL)
    assert len(sources) == 3
    langs_found = set()
    for s in sources:
        if "en.wikipedia.org" in s["url"]:
            langs_found.add("en")
        elif "ru.wikipedia.org" in s["url"]:
            langs_found.add("ru")
        elif "de.wikipedia.org" in s["url"]:
            langs_found.add("de")
    assert langs_found == {"en", "ru", "de"}


def test_wikipedia_multi_lang_empty():
    """Пустой список языков — default на en."""
    from corpus_builder.config_generator import from_wikipedia_multi

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "query": {"categorymembers": [{"title": "Electronics"}]}
    }

    with mock.patch("requests.get", return_value=mock_response):
        sources = from_wikipedia_multi(
            categories=["Electronics"],
            languages=None,  # default
        )

    assert len(sources) >= 1
