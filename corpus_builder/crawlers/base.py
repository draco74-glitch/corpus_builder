"""Базовый класс краулера."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import requests

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile, SourceItem
from ..text_utils import detect_language, normalize_text

log = get_logger(__name__)


class BaseCrawler(ABC):
    """Абстрактный краулер.

    Контракт: на вход URL (+ категории и, опционально, элемент config.yaml),
    на выход — CorpusRecord или None. Подкласс реализует только `_crawl()`,
    остальное делает базовый класс: обёртка в try/except, заполнение общих
    полей, нормализация контента.

    Поля конкретного источника (`include_files`, `download_files`) приходят в
    `crawl(..., source=...)` и доступны в `self.source` — раньше per-source
    настройки принимались конфигурацией и молча игнорировались (I7).
    """

    source_type: str = "base"

    def __init__(self, config: AppConfig, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        if not self.session.headers.get("User-Agent"):
            self.session.headers.update({"User-Agent": config.output.user_agent})
        #: элемент `sources:` из config.yaml, для которого идёт текущий вызов
        self.source: SourceItem | None = None

    #: per-source override: какие файлы качать (None = взять из crawlers.*)
    @property
    def include_files(self) -> list[str] | None:
        return self.source.include_files if self.source else None

    @property
    def download_files(self) -> bool:
        return True if self.source is None else self.source.download_files

    def crawl(self, url: str, categories: list[str] | None = None,
              source: SourceItem | None = None) -> CorpusRecord | None:
        """Точка входа. Декорирует _crawl обработкой ошибок и нормализацией."""
        self.source = source
        try:
            record = self._crawl(url)
            if record is None:
                return None
            # Заполнить обязательные поля
            record.source_url = url
            record.source_type = self.source_type
            record.categories = categories or []
            record.date_accessed = datetime.now(timezone.utc).isoformat()
            # Нормализация контента
            if record.content:
                record.content = normalize_text(record.content)
                record.content_sha1 = self._content_hash(record.content)
                record.language = detect_language(record.content)
                # маркируем ПОЛЕМ (A2): пост-обработка иначе не нормализует заново.
                # В metadata метку класть нельзя — дедуп читает верхнеуровневый ключ.
                record.content_normalized = True
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
    def _url_extension(url: str) -> str:
        """Расширение файла из URL, без query и fragment.

        `a.pdf?x=1` и `a.pdf#p=3` → 'pdf'. Прежний разбор
        (`href.rsplit('.', 1)[-1].split('?')[0]`) не резал fragment, поэтому
        ссылки вида `report.pdf#page=2` молча не скачивались.
        """
        from urllib.parse import urlparse
        path = urlparse(url).path or url
        return path.rsplit(".", 1)[-1].lower() if "." in path else ""

    def _download_attachments(self, items: list[tuple[str, str]]) -> list[DownloadedFile]:
        """Скачать вложения: [(kind, absolute_url), ...] → [DownloadedFile].

        Блоклист видео/стриминга и защита от одинаковых URL — здесь; отбор по
        расширениям/patterns остаётся вызывающему коду (он знает свой cfg).
        """
        from ..http import download_file, is_blocked_url
        from ..text_utils import extension_of

        out: list[DownloadedFile] = []
        seen: set[str] = set()
        for kind, file_url in items:
            if not file_url or file_url in seen:
                continue
            seen.add(file_url)
            if is_blocked_url(file_url):
                continue
            result = download_file(
                file_url,
                self.config.output.download_dir,
                self.config.output.max_file_size_mb,
                self.config.output.request_timeout,
                session=self.session,
                revalidate=self.config.output.revalidate_cached_files,
                revalidate_after_hours=float(
                    getattr(self.config.output, "cache_ttl_hours", 168)),
            )
            if not result:
                continue
            path, sha, size = result
            ext = extension_of(file_url)
            if kind == "auto":
                kind = "image" if ext in ("png", "jpg", "jpeg", "gif", "webp", "svg", "bmp") \
                    else ext if ext in ("pdf", "kicad_sch", "kicad_pcb") else "file"
            out.append(DownloadedFile(
                type=kind,
                original_url=file_url,
                local_path=path,
                sha1=sha,
                size_bytes=size,
            ))
        return out

    @staticmethod
    def _content_hash(text: str) -> str:
        import hashlib
        return hashlib.sha1(text.encode("utf-8")).hexdigest()
