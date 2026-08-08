"""Реестр краулеров."""
from __future__ import annotations

from ..models import AppConfig
from .base import BaseCrawler
from .html_crawler import HtmlCrawler
from .pdf_crawler import PdfCrawler
from .github_crawler import GitHubCrawler
from .forum_crawler import StackExchangeCrawler
from .academic_crawlers import DoajCrawler, ArxivCrawler, CrossrefCrawler, WikipediaCrawler


REGISTRY: dict[str, type[BaseCrawler]] = {
    "html": HtmlCrawler,
    "pdf": PdfCrawler,
    "github_repo": GitHubCrawler,
    "stackexchange": StackExchangeCrawler,
    "forum": StackExchangeCrawler,  # по умолчанию forum = SE; можно расширить
    # Новые источники (Этап 8)
    "doaj": DoajCrawler,
    "arxiv": ArxivCrawler,
    "crossref": CrossrefCrawler,
    "wikipedia": WikipediaCrawler,
}


def get_crawler(source_type: str, config: AppConfig) -> BaseCrawler:
    cls = REGISTRY.get(source_type)
    if not cls:
        raise ValueError(f"Unknown source type: {source_type!r}. Known: {list(REGISTRY)}")
    return cls(config)
