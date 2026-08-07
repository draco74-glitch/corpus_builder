"""HTML-краулер на базе trafilatura.

trafilatura специализируется на извлечении главного текста из статей и блогов,
сам удаляет навигацию, рекламу, комментарии. Это надёжнее ручной эвристики с
BeautifulSoup.
"""
from __future__ import annotations

import json
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import trafilatura
import trafilatura.metadata

from ..http import download_file
from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)


class HtmlCrawler(BaseCrawler):
    source_type = "html"

    def _crawl(self, url: str) -> CorpusRecord | None:
        cfg = self.config.crawlers.html
        try:
            resp = self.session.get(url, timeout=self.config.output.request_timeout)
            resp.raise_for_status()
        except Exception as e:
            log.warning(f"HTML fetch failed {url}: {e}")
            return None

        # Кодировка
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            try:
                import charset_normalizer
                guess = charset_normalizer.detect(resp.content)
                if guess and guess.get("encoding"):
                    resp.encoding = guess["encoding"]
            except Exception:
                pass

        html_text = resp.text
        content = ""
        metadata: dict = {}
        downloaded: list[DownloadedFile] = []

        if cfg.extract_mode == "trafilatura":
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
                    content = data.get("text", "") or ""
                    metadata = {
                        "title": data.get("title"),
                        "author": data.get("author"),
                        "date": data.get("date"),
                        "url": data.get("url", url),
                        "tags": data.get("tags") or [],
                    }
            except Exception as e:
                log.warning(f"trafilatura failed for {url}: {e}, falling back to bs4")

        if not content:
            # Fallback на BeautifulSoup
            soup = BeautifulSoup(html_text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                tag.decompose()
            article = soup.find("article") or soup.find("main") or soup.body
            content = article.get_text(separator="\n") if article else soup.get_text(separator="\n")
            metadata["title"] = (soup.title.string if soup.title else "") or ""

        if self.config.sources and (self.config.sources[0].download_files):
            pass  # не используется напрямую — берём из cfg ниже
        # Скачивание связанных файлов
        soup = BeautifulSoup(html_text, "html.parser")
        if cfg.download_images:
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if not src:
                    continue
                # Фильтр по расширению
                ext = src.rsplit(".", 1)[-1].split("?")[0].lower()
                if ext not in cfg.image_extensions:
                    continue
                img_url = urljoin(url, src)
                result = download_file(
                    img_url,
                    self.config.output.download_dir,
                    self.config.output.max_file_size_mb,
                    self.config.output.request_timeout,
                    session=self.session,
                )
                if result:
                    path, sha, size = result
                    downloaded.append(DownloadedFile(
                        type="image",
                        original_url=img_url,
                        local_path=path,
                        sha1=sha,
                        size_bytes=size,
                    ))

        # Ссылки на PDF / KiCad / архивы
        for a in soup.find_all("a"):
            href = a.get("href")
            if not href:
                continue
            ext = href.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in cfg.download_files_ext:
                continue
            file_url = urljoin(url, href)
            result = download_file(
                file_url,
                self.config.output.download_dir,
                self.config.output.max_file_size_mb,
                self.config.output.request_timeout,
                session=self.session,
            )
            if result:
                path, sha, size = result
                downloaded.append(DownloadedFile(
                    type=ext if ext in ("pdf", "kicad_sch", "kicad_pcb") else "file",
                    original_url=file_url,
                    local_path=path,
                    sha1=sha,
                    size_bytes=size,
                ))

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content=content,
            downloaded_files=downloaded,
            metadata=metadata,
        )
