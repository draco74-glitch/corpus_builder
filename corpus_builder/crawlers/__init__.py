"""Реестр краулеров с ленивой инициализацией (Улучшение 8)."""
from __future__ import annotations

from ..models import AppConfig
from .base import BaseCrawler

_REGISTRY: dict[str, tuple[str, str]] = {
    "html":         ("corpus_builder.crawlers.html_crawler",        "HtmlCrawler"),
    "pdf":          ("corpus_builder.crawlers.pdf_crawler",          "PdfCrawler"),
    "github_repo":  ("corpus_builder.crawlers.github_crawler",      "GitHubCrawler"),
    "stackexchange":("corpus_builder.crawlers.forum_crawler",       "StackExchangeCrawler"),
    "forum":        ("corpus_builder.crawlers.forum_crawler",       "StackExchangeCrawler"),
    "doaj":         ("corpus_builder.crawlers.academic_crawlers",   "DoajCrawler"),
    "arxiv":        ("corpus_builder.crawlers.academic_crawlers",   "ArxivCrawler"),
    "crossref":     ("corpus_builder.crawlers.academic_crawlers",   "CrossrefCrawler"),
    "wikipedia":    ("corpus_builder.crawlers.academic_crawlers",   "WikipediaCrawler"),
}

_imported_cache: dict[str, type[BaseCrawler]] = {}


def _import_class(module_path: str, class_name: str) -> type[BaseCrawler]:
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def __getattr__(name: str):
    for source_type, (module_path, class_name) in _REGISTRY.items():
        if class_name == name:
            cache_key = f"{module_path}.{class_name}"
            if cache_key not in _imported_cache:
                _imported_cache[cache_key] = _import_class(module_path, class_name)
            return _imported_cache[cache_key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


REGISTRY = _REGISTRY


def get_crawler(source_type: str, config: AppConfig) -> BaseCrawler:
    if source_type not in _REGISTRY:
        raise ValueError(f"Unknown source type: {source_type!r}. Known: {list(_REGISTRY)}")
    module_path, class_name = _REGISTRY[source_type]
    cache_key = f"{module_path}.{class_name}"
    if cache_key not in _imported_cache:
        _imported_cache[cache_key] = _import_class(module_path, class_name)
    cls = _imported_cache[cache_key]
    return cls(config)


def list_known_types() -> list[str]:
    return list(_REGISTRY.keys())


from .html_crawler import HtmlCrawler
from .pdf_crawler import PdfCrawler
from .github_crawler import GitHubCrawler
from .forum_crawler import StackExchangeCrawler
from .academic_crawlers import DoajCrawler, ArxivCrawler, CrossrefCrawler, WikipediaCrawler

__all__ = [
    "BaseCrawler", "HtmlCrawler", "PdfCrawler", "GitHubCrawler",
    "StackExchangeCrawler", "DoajCrawler", "ArxivCrawler",
    "CrossrefCrawler", "WikipediaCrawler",
    "REGISTRY", "get_crawler", "list_known_types",
]
