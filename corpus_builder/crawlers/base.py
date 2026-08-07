"""Базовый класс краулера."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import requests

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord
from ..text_utils import detect_language, normalize_text

log = get_logger(__name__)


class BaseCrawler(ABC):
    """Абстрактный краулер.

    Контракт: на вход URL + config, на выход — CorpusRecord или None.
    Подкласс реализует только _crawl(), остальное делает базовый класс:
    обёртка в try/except, заполнение общих полей, нормализация контента.
    """

    source_type: str = "base"

    def __init__(self, config: AppConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        if not self.session.headers.get("User-Agent"):
            self.session.headers.update({"User-Agent": config.output.user_agent})

    def crawl(self, url: str, categories: list[str] | None = None) -> CorpusRecord | None:
        """Точка входа. Декорирует _crawl обработкой ошибок и нормализацией."""
        try:
            record = self._crawl(url)
            if record is None:
                return None
            # Заполнить обязательные поля
            record.source_url = url
            record.source_type = self.source_type
            record.categories = categories or []
            record.date_accessed = datetime.utcnow().isoformat()
            # Нормализация контента
            if record.content:
                record.content = normalize_text(record.content)
                record.content_sha1 = self._content_hash(record.content)
                record.language = detect_language(record.content)
            return record
        except Exception as e:
            log.exception(f"Crawl failed: {url}")
            return CorpusRecord(
                source_url=url,
                source_type=self.source_type,
                content="",
                status="error",
                metadata={"error": str(e)},
                categories=categories or [],
            )

    @abstractmethod
    def _crawl(self, url: str) -> CorpusRecord | None:
        """Реальная логика краулинга. Без try/except — он в базовом классе."""
        ...

    @staticmethod
    def _content_hash(text: str) -> str:
        import hashlib
        return hashlib.sha1(text.encode("utf-8")).hexdigest()
