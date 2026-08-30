"""HTML-краулер на базе trafilatura.

trafilatura специализируется на извлечении главного текста из статей и блогов,
сам удаляет навигацию, рекламу, комментарии. Это надёжнее ручной эвристики с
BeautifulSoup.
"""
from __future__ import annotations

import json
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup

from ..logging_setup import get_logger
from ..models import CorpusRecord
from ..text_utils import extension_of, matches_patterns
from .base import BaseCrawler

log = get_logger(__name__)

# Медиа-расширения, которые не должны попадать в вложения
_MEDIA_EXTS = {
    "mp4", "webm", "avi", "mov", "wmv", "flv", "mkv", "m4v", "mp3", "wav",
    "ogg", "flac", "m3u8", "m3u",
}


class HtmlCrawler(BaseCrawler):
    source_type = "html"

    def _crawl(self, url: str) -> CorpusRecord:
        try:
            resp = self.session.get(url, timeout=self.config.output.request_timeout)
            resp.raise_for_status()
        except Exception as e:
            # Раньше возвращали None, и в errors.jsonl писалось "no record" —
            # причину сбоя нельзя было диагностировать.
            raise RuntimeError(f"HTTP fetch failed: {e}") from e

        # Кодировка: requests по умолчанию отдаёт latin-1 для ответов без
        # charset — уступаем детектору.
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            try:
                import charset_normalizer
                guess = charset_normalizer.detect(resp.content)
                if guess and guess.get("encoding"):
                    resp.encoding = guess["encoding"]
            except Exception:
                pass

        html_text = resp.text
        metadata: dict = {}
        content = self._extract_trafilatura(html_text, url, metadata)
        if not content:
            content = self._extract_bs4(html_text, metadata)

        downloaded = self._collect_attachments(url, html_text) if self.download_files else []

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content=content,
            downloaded_files=downloaded,
            metadata=metadata,
        )

    # ---------------- extraction ----------------

    def _extract_trafilatura(self, html_text: str, url: str, metadata: dict) -> str:
        cfg = self.config.crawlers.html
        if cfg.extract_mode != "trafilatura":
            return ""
        try:
            extracted = trafilatura.extract(
                html_text,
                url=url,
                include_tables=True,
                include_links=True,
                output_format="json",
                with_metadata=True,
            )
            if extracted:
                data = json.loads(extracted)
                metadata.update({
                    "title": data.get("title"),
                    "author": data.get("author"),
                    "date": data.get("date"),
                    "url": data.get("url", url),
                    "tags": data.get("tags") or [],
                })
                return data.get("text", "") or ""
        except Exception as e:
            log.warning(f"trafilatura failed for {url}: {e}, falling back to bs4")
        return ""

    @staticmethod
    def _extract_bs4(html_text: str, metadata: dict) -> str:
        soup = BeautifulSoup(html_text, "html.parser")
        # title берём до удаления header/nav — иначе он терялся.
        metadata.setdefault("title", (soup.title.string if soup.title else "") or "")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        article = soup.find("article") or soup.find("main") or soup.body
        return article.get_text(separator="\n") if article else soup.get_text(separator="\n")

    # ---------------- attachments ----------------

    def attachment_allowed(self, url: str, global_exts: list[str]) -> bool:
        """Можно ли скачивать файл по URL.

        Per-source `include_files` (sources[].include_files в config.yaml)
        важнее глобального списка расширений — раньше он вообще не читался (I7).
        """
        ext = extension_of(url)
        if not ext or ext in _MEDIA_EXTS:
            return False
        if self.include_files:
            return matches_patterns(url, self.include_files)
        return ext in {e.lstrip(".").lower() for e in global_exts}

    def _collect_attachments(self, page_url: str, html_text: str) -> list:
        """Собрать вложения страницы с учётом per-source `include_files` (I7)."""
        cfg = self.config.crawlers.html
        soup = BeautifulSoup(html_text, "html.parser")

        items: list[tuple[str, str]] = []
        if cfg.download_images:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if not src:
                    continue
                abs_src = urljoin(page_url, src)
                if self.attachment_allowed(abs_src, cfg.image_extensions):
                    items.append(("image", abs_src))

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            abs_href = urljoin(page_url, href)
            if self.attachment_allowed(abs_href, cfg.download_files_ext):
                items.append(("auto", abs_href))

        return self._download_attachments(items)
