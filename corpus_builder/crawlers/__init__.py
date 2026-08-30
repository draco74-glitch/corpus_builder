"""Реестр краулеров с ленивой инициализацией (Улучшение 8).

Реестр объявляет 9 типов источников; валидные значения перечислены в
`models.SOURCE_TYPES` (проверяются тестом на совпадение — иначе config.yaml с
таким типом невозможно загрузить, но реестр его «знает»).

Леность настоящая: классы импортируются через `__getattr__` по первому
обращению, а не все сразу на уровне модуля — иначе `import
corpus_builder.crawlers` тянет PyMuPDF/trafil/httpx ради одного HTML-краулера.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import AppConfig

if TYPE_CHECKING:                      # только для type-checker'а
    from .base import BaseCrawler

_REGISTRY: dict[str, tuple[str, str]] = {
    "html":          ("corpus_builder.crawlers.html_crawler",      "HtmlCrawler"),
    "pdf":           ("corpus_builder.crawlers.pdf_crawler",       "PdfCrawler"),
    "github_repo":   ("corpus_builder.crawlers.github_crawler",    "GitHubCrawler"),
    "stackexchange": ("corpus_builder.crawlers.forum_crawler",     "StackExchangeCrawler"),
    "forum":         ("corpus_builder.crawlers.forum_crawler",     "StackExchangeCrawler"),
    "doaj":          ("corpus_builder.crawlers.academic_crawlers", "DoajCrawler"),
    "arxiv":         ("corpus_builder.crawlers.academic_crawlers", "ArxivCrawler"),
    "crossref":      ("corpus_builder.crawlers.academic_crawlers", "CrossrefCrawler"),
    "wikipedia":     ("corpus_builder.crawlers.academic_crawlers", "WikipediaCrawler"),
}

_imported_cache: dict[str, type] = {}


def _import_class(module_path: str, class_name: str) -> type:
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _resolve(class_name: str) -> type:
    """Импорт класса краулера по имени (кэшируется)."""
    for _type, (module_path, name) in _REGISTRY.items():
        if name != class_name:
            continue
        cache_key = f"{module_path}.{name}"
        if cache_key not in _imported_cache:
            _imported_cache[cache_key] = _import_class(module_path, name)
        return _imported_cache[cache_key]
    raise AttributeError(class_name)


def __getattr__(name: str):
    """Ленивый доступ к классам краулеров: `crawlers.HtmlCrawler`."""
    try:
        return _resolve(name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


REGISTRY = _REGISTRY


def get_crawler(source_type: str, config: AppConfig):
    """Экземпляр краулера для `source_type` (без общей Session — см. pipeline)."""
    if source_type not in _REGISTRY:
        raise ValueError(
            f"Unknown source type: {source_type!r}. Known: {sorted(_REGISTRY)}")
    _module_path, class_name = _REGISTRY[source_type]
    cls = _resolve(class_name)
    return cls(config)


def list_known_types() -> list[str]:
    return list(_REGISTRY)


__all__ = [
    "BaseCrawler", "HtmlCrawler", "PdfCrawler", "GitHubCrawler",
    "StackExchangeCrawler", "DoajCrawler", "ArxivCrawler",
    "CrossrefCrawler", "WikipediaCrawler",
    "REGISTRY", "get_crawler", "list_known_types",
]
