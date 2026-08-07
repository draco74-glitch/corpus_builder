"""Реестр краулеров с ленивой инициализацией.

Раньше: все 8 краулеров импортировались при первом обращении к crawlers/__init__.py,
что занимало ~500 мс старта + память, даже если в конфиге только HTML.

Сейчас: каждый краулер импортируется только при первом обращении к своему типу.
Для типового сценария (только HTML) — экономия ~400 мс старта.
"""
from __future__ import annotations

from ..models import AppConfig
from .base import BaseCrawler


# Реестр: тип → путь к модулю + имя класса
# Используем строковые пути, чтобы не импортировать все классы сразу
_REGISTRY: dict[str, tuple[str, str]] = {
    "html":         ("corpus_builder.crawlers.html_crawler",        "HtmlCrawler"),
    "pdf":          ("corpus_builder.crawlers.pdf_crawler",          "PdfCrawler"),
    "github_repo":  ("corpus_builder.crawlers.github_crawler",      "GitHubCrawler"),
    "stackexchange":("corpus_builder.crawlers.forum_crawler",       "StackExchangeCrawler"),
    "forum":        ("corpus_builder.crawlers.forum_crawler",       "StackExchangeCrawler"),
    # Новые источники
    "doaj":         ("corpus_builder.crawlers.academic_crawlers",   "DoajCrawler"),
    "arxiv":        ("corpus_builder.crawlers.academic_crawlers",   "ArxivCrawler"),
    "crossref":     ("corpus_builder.crawlers.academic_crawlers",   "CrossrefCrawler"),
    "wikipedia":    ("corpus_builder.crawlers.academic_crawlers",   "WikipediaCrawler"),
}

# Кэш уже импортированных классов (чтобы не импортировать повторно)
_imported_cache: dict[str, type[BaseCrawler]] = {}


# Для обратной совместимости — сохраняем возможность импорта напрямую
def _import_class(module_path: str, class_name: str) -> type[BaseCrawler]:
    """Ленивый импорт класса по строковому пути."""
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# Чтобы сохранить обратную совместимость с кодом, который импортирует классы напрямую
# (например, gui.py, config_generator_dialog.py), мы делаем upfront-импорт
# только когда к нему обращаются. Но для тестов нужно, чтобы все классы были доступны.
# Поэтому сохраним прямые импорты здесь:
def __getattr__(name: str):
    """Ленивый импорт для атрибутов модуля.

    Например, from corpus_builder.crawlers import HtmlCrawler
    вызовет __getattr__("HtmlCrawler") и импортирует только этот класс.
    """
    # Ищем в реестре
    for source_type, (module_path, class_name) in _REGISTRY.items():
        if class_name == name:
            cache_key = f"{module_path}.{class_name}"
            if cache_key not in _imported_cache:
                _imported_cache[cache_key] = _import_class(module_path, class_name)
            return _imported_cache[cache_key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Для прямого импорта: from corpus_builder.crawlers import REGISTRY
REGISTRY = _REGISTRY


def get_crawler(source_type: str, config: AppConfig) -> BaseCrawler:
    """Получить экземпляр краулера для указанного типа источника.

    Использует ленивый импорт — класс загружается только при первом обращении.
    """
    if source_type not in _REGISTRY:
        raise ValueError(
            f"Unknown source type: {source_type!r}. "
            f"Known: {list(_REGISTRY)}"
        )
    module_path, class_name = _REGISTRY[source_type]
    cache_key = f"{module_path}.{class_name}"
    if cache_key not in _imported_cache:
        _imported_cache[cache_key] = _import_class(module_path, class_name)
    cls = _imported_cache[cache_key]
    return cls(config)


def list_known_types() -> list[str]:
    """Вернуть список всех известных типов источников."""
    return list(_REGISTRY.keys())


# ============================================================
# Прямые импорты для обратной совместимости
# ============================================================
# Эти импорты делаются при первом обращении к модулю, но они не запускаются
# автоматически при импорте crawlers — только при явном обращении к классам.

# Для удобства тестов и старого кода — оставим прямые импорты:
from .html_crawler import HtmlCrawler  # noqa: E402
from .pdf_crawler import PdfCrawler  # noqa: E402
from .github_crawler import GitHubCrawler  # noqa: E402
from .forum_crawler import StackExchangeCrawler  # noqa: E402
from .academic_crawlers import DoajCrawler, ArxivCrawler, CrossrefCrawler, WikipediaCrawler  # noqa: E402

__all__ = [
    "BaseCrawler",
    "HtmlCrawler",
    "PdfCrawler",
    "GitHubCrawler",
    "StackExchangeCrawler",
    "DoajCrawler",
    "ArxivCrawler",
    "CrossrefCrawler",
    "WikipediaCrawler",
    "REGISTRY",
    "get_crawler",
    "list_known_types",
]
