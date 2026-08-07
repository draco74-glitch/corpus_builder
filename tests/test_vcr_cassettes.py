"""Тесты с записанными HTTP-ответами через VCR.py.

Записанные кассеты хранятся в tests/cassettes/*.yaml. При первом запуске
VCR.py сделает реальные HTTP-запросы и сохранит ответы. При повторных
запусках используются кассеты, что позволяет запускать тесты без сети.

Чтобы перезаписать кассеты, удалите tests/cassettes/ и запустите тесты
с доступом к интернету.
"""
import os
import json
import pytest

# VCR может не быть установлен в окружении; импортируем лениво
try:
    import vcr
    HAS_VCR = True
except ImportError:
    HAS_VCR = False

# Пропускаем все тесты, если vcrpy не установлен
pytestmark = pytest.mark.skipif(not HAS_VCR, reason="vcrpy not installed")


@pytest.fixture
def vcr_cassette_dir():
    """Папка с записанными кассетами."""
    return os.path.join(os.path.dirname(__file__), "cassettes")


@vcr.use_cassette("test_habr_article.yaml")
def test_crawl_habr_article_smoke():
    """Тест: скачать статью на Habr и проверить, что получаем контент.

    Кассета: tests/cassettes/test_habr_article.yaml
    """
    import requests
    from bs4 import BeautifulSoup
    import trafilatura

    url = "https://habr.com/ru/articles/712234/"
    r = requests.get(url, timeout=10)
    assert r.status_code == 200
    assert len(r.text) > 1000

    # trafilatura должен извлечь главный текст
    extracted = trafilatura.extract(r.text, url=url, with_metadata=True)
    assert extracted is not None
    assert len(extracted) > 500


@vcr.use_cassette("test_github_repo_search.yaml")
def test_github_repo_search_smoke():
    """Тест: поиск репозиториев через GitHub Search API.

    Кассета: tests/cassettes/test_github_repo_search.yaml
    """
    import requests

    api = "https://api.github.com/search/repositories"
    params = {"q": "topic:kicad", "sort": "stars", "per_page": 5}
    r = requests.get(api, params=params, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) > 0
    assert "html_url" in data["items"][0]


@vcr.use_cassette("test_stackexchange_questions.yaml")
def test_stackexchange_questions_smoke():
    """Тест: получить топ-вопросы по тегу kicad через SE API.

    Кассета: tests/cassettes/test_stackexchange_questions.yaml
    """
    import requests

    api = "https://api.stackexchange.com/2.3/questions"
    params = {
        "site": "electronics",
        "tagged": "kicad",
        "sort": "votes",
        "order": "desc",
        "pagesize": 5,
        "filter": "default",
    }
    r = requests.get(api, params=params, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) > 0
    assert "link" in data["items"][0]


@vcr.use_cassette("test_arxiv_rss.yaml")
def test_arxiv_api_smoke():
    """Тест: получить статьи из arXiv eess.SP через RSS/Atom feed.

    Кассета: tests/cassettes/test_arxiv_rss.yaml
    """
    import requests

    url = "http://export.arxiv.org/rss/eess.SP"
    r = requests.get(url, timeout=10)
    assert r.status_code == 200
    # В RSS-фиде должны быть элементы <item>
    assert "<item" in r.text.lower()


@vcr.use_cassette("test_doaj_search.yaml")
def test_doaj_api_smoke():
    """Тест: поиск статей по электронике через DOAJ API.

    Кассета: tests/cassettes/test_doaj_search.yaml
    """
    import requests

    api = "https://doaj.org/api/search/articles/electronics"
    params = {"page": 1, "pageSize": 5}
    r = requests.get(api, params=params, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert len(data["results"]) > 0
