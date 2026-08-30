"""Тесты с записанными HTTP-ответами через VCR.py (сетевые, off-by-default).

Кассеты лежат в tests/cassettes/*.yaml и ЗАКоммичены: прогон офлайн использует
их и не трогает интернет. Новые кассеты: удалите файл и запустите тесты с
`--run-network` (доступ к интернету обязателен).

Раньше:
  * `@use_recorded_cassette("x.yaml")` резолвил путь ОТ ТЕКУЩЕЙ ДИРЕКТОРИИ, поэтому
    кассета не находилась (в tests/cassettes был только .gitkeep), и каждый
    прогон молча бил по живым API — а `.gitignore` добавлял `/test_*.yaml`,
    чтобы не светить случайно записанное в корне;
  * тест arXiv зависел от того, выложили ли в этот день papers (RSS пуст в
    выходные) — теперь сравнивается с записанной кассетой.
"""
import os

import pytest

# VCR может не быть установлен в окружении; импортируем лениво
try:
    import vcr
    HAS_VCR = True
except ImportError:
    HAS_VCR = False

# Пропускаем всё, если vcrpy не установлены; и просим явного --run-network
pytestmark = [
    pytest.mark.skipif(not HAS_VCR, reason="vcrpy not installed"),
    pytest.mark.network,
]


CASSETTE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cassettes")

# network-маркер: без --run-network тесты пропускаются (см. conftest.py)
NETWORK_MARK = pytest.mark.network


def use_recorded_cassette(name: str):
    """vcr.use_cassette с абсолютным путём и «рекордом только если нет файла»."""
    path = os.path.join(CASSETTE_DIR, name)
    return vcr.use_cassette(
        path,
        record_mode="once",          # есть файл → играем; нет → пишем (нужна сеть)
        match_on=["method", "scheme", "host", "port", "path", "query"],
        filter_headers=["authorization", ("USER-AGENT", "CorpusBuilder-Test")],
    )


@use_recorded_cassette("test_habr_article.yaml")
def test_crawl_habr_article_smoke():
    """Тест: скачать статью на Habr и проверить, что получаем контент.

    Кассета: tests/cassettes/test_habr_article.yaml
    """
    import requests
    import trafilatura

    url = "https://habr.com/ru/articles/712234/"
    r = requests.get(url, timeout=10)
    assert r.status_code == 200
    assert len(r.text) > 1000

    # trafilatura должен извлечь главный текст
    extracted = trafilatura.extract(r.text, url=url, with_metadata=True)
    assert extracted is not None
    assert len(extracted) > 500


@use_recorded_cassette("test_github_repo_search.yaml")
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


@use_recorded_cassette("test_stackexchange_questions.yaml")
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


@use_recorded_cassette("test_arxiv_rss.yaml")
def test_arxiv_api_smoke():
    """Тест: получить статьи из arXiv eess.SP через RSS/Atom feed.

    Кассета: tests/cassettes/test_arxiv_rss.yaml
    """
    import requests

    # сам API arXiv (Atom), а не RSS-листинг: RSS пуст в выходные, и тест
    # падал без всякой связи с кодом проекта
    url = "http://export.arxiv.org/api/query?search_query=cat:eess.SP&max_results=3"
    r = requests.get(url, timeout=15)
    assert r.status_code == 200
    body = r.text.lower()
    assert "<entry" in body or "<item" in body, "arXiv вернул пустой/не-API ответ"


@use_recorded_cassette("test_doaj_search.yaml")
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
