"""Дополнительные краулеры для научных источников:
  - DOAJ (Directory of Open Access Journals)
  - arXiv API (статьи из eess.SP, eess.SY, cs.AR и др.)
  - Crossref (метаданные DOI)
  - Wikipedia REST API (статьи напрямую в JSON, без HTML-парсинга)

Все они добавляются как новый source_type в конфигурации.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlparse

import requests

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)


# ============================================================
# DOAJ — Directory of Open Access Journals
# ============================================================

class DoajCrawler(BaseCrawler):
    """Поиск открытых научных статей через DOAJ API.

    Конфиг:
        type: doaj
        query: "electronics"          # поисковый запрос
        max_articles: 50               # ограничение
    """

    source_type = "doaj"

    def _crawl(self, url: str) -> CorpusRecord | None:
        # URL в config.yaml интерпретируется как поисковый запрос
        # Например: url: "electronics" → ищем статьи по слову "electronics"
        query = url
        # Если это полноценный URL — извлекаем из него query
        if query.startswith("http"):
            parsed = urlparse(query)
            query = parsed.path.strip("/").replace("search/articles/", "")

        api = "https://doaj.org/api/search/articles/{query}".format(query=quote(query))
        params = {"page": 1, "pageSize": 50}

        try:
            r = self.session.get(api, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning(f"DOAJ search failed for '{query}': {e}")
            return None

        results = data.get("results") or []
        if not results:
            return None

        parts: list[str] = []
        for i, hit in enumerate(results[:50], 1):
            bibjson = hit.get("bibjson", {})
            title = bibjson.get("title") or "(no title)"
            abstract = bibjson.get("abstract") or ""
            authors = ", ".join(
                (a.get("name") or "") for a in (bibjson.get("author") or [])
            )
            journal = (bibjson.get("journal") or {}).get("title") or ""
            year = (bibjson.get("journal") or {}).get("year") or ""
            keywords = bibjson.get("keywords") or []
            doi = (bibjson.get("identifier") or [{}])[0].get("id") if bibjson.get("identifier") else ""
            link = ""
            for l in bibjson.get("link") or []:
                if l.get("url"):
                    link = l["url"]
                    break

            section = (
                f"=== Article {i}: {title} ===\n"
                f"Authors: {authors}\n"
                f"Journal: {journal} ({year})\n"
                + (f"DOI: {doi}\n" if doi else "")
                + (f"Keywords: {', '.join(keywords)}\n" if keywords else "")
                + (f"Link: {link}\n\n" if link else "\n")
                + (f"Abstract: {abstract}\n" if abstract else "(no abstract)\n")
            )
            parts.append(section)

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n\n".join(parts),
            metadata={
                "query": query,
                "results_count": len(results),
                "platform": "DOAJ",
            },
            license="CC BY",
        )


# ============================================================
# arXiv API
# ============================================================

class ArxivCrawler(BaseCrawler):
    """Поиск статей в arXiv через официальный API.

    Конфиг:
        type: arxiv
        # URL в формате: eess.SP / eess.SY / cs.AR / или конкретный ID
        # Примеры:
        #   url: "cat:eess.SP"  → все статьи из eess.SP
        #   url: "ti:operational amplifier"  → полнотекстовый поиск в заголовке
    """

    source_type = "arxiv"
    ARXIV_API = "http://export.arxiv.org/api/query"

    def _crawl(self, url: str) -> CorpusRecord | None:
        # URL интерпретируется как поисковый запрос arXiv
        query = url
        if query.startswith("http"):
            # Если это URL arxiv.org/list/... — извлекаем категорию
            if "arxiv.org/list/" in query:
                query = query.split("/list/")[-1].rstrip("/")
            elif "arxiv.org/abs/" in query:
                # Конкретная статья
                arxiv_id = query.split("/abs/")[-1].split("/")[0].split("?")[0]
                query = f"id_list:{arxiv_id}"

        params = {
            "search_query": query if not query.startswith("id_list:") else None,
            "id_list": query.split("id_list:")[1] if query.startswith("id_list:") else None,
            "start": 0,
            "max_results": 50,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        params = {k: v for k, v in params.items() if v is not None}

        try:
            r = self.session.get(self.ARXIV_API, params=params, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"arXiv API failed for '{query}': {e}")
            return None

        # Парсим Atom XML ответ
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "xml")
        entries = soup.find_all("entry")

        if not entries:
            return None

        parts: list[str] = []
        for i, entry in enumerate(entries, 1):
            title = (entry.find("title").text if entry.find("title") else "").strip()
            summary = (entry.find("summary").text if entry.find("summary") else "").strip()
            published = (entry.find("published").text if entry.find("published") else "").strip()
            arxiv_id = ""
            id_elem = entry.find("id")
            if id_elem:
                arxiv_id = id_elem.text.strip().split("/abs/")[-1]

            authors = []
            for author in entry.find_all("author"):
                name = author.find("name")
                if name:
                    authors.append(name.text.strip())

            # DOI (если есть)
            doi_elem = entry.find("arxiv:doi")
            doi = doi_elem.text if doi_elem else ""

            # PDF link
            pdf_link = ""
            for link in entry.find_all("link"):
                if link.get("title") == "pdf":
                    pdf_link = link.get("href") or ""
                    break

            # Журнальная ссылка
            journal_ref = ""
            jr_elem = entry.find("arxiv:journal_ref")
            if jr_elem:
                journal_ref = jr_elem.text

            section = (
                f"=== arXiv Article {i}: {title} ===\n"
                f"arXiv ID: {arxiv_id}\n"
                f"Authors: {', '.join(authors)}\n"
                f"Published: {published}\n"
                + (f"DOI: {doi}\n" if doi else "")
                + (f"Journal: {journal_ref}\n" if journal_ref else "")
                + (f"PDF: {pdf_link}\n\n" if pdf_link else "\n")
                + f"Abstract: {summary}\n"
            )
            parts.append(section)

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n\n".join(parts),
            metadata={
                "query": query,
                "results_count": len(entries),
                "platform": "arXiv",
            },
            license="arXiv non-exclusive license",
        )


# ============================================================
# Crossref API
# ============================================================

class CrossrefCrawler(BaseCrawler):
    """Поиск научных публикаций через Crossref API.

    Конфиг:
        type: crossref
        # URL — это поисковый запрос
        # Пример: url: "operational amplifier"
    """

    source_type = "crossref"
    CROSSREF_API = "https://api.crossref.org/works"

    def _crawl(self, url: str) -> CorpusRecord | None:
        query = url
        if query.startswith("http"):
            query = urlparse(query).path.strip("/").replace("works/", "")

        params = {
            "query": query,
            "rows": 50,
            "select": "DOI,title,abstract,author,published,container-title,subject,link",
        }
        headers = {
            "User-Agent": f"{self.config.output.user_agent} (mailto:research@example.com)",
        }

        try:
            r = self.session.get(self.CROSSREF_API, params=params,
                                 headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning(f"Crossref API failed for '{query}': {e}")
            return None

        items = (data.get("message") or {}).get("items") or []
        if not items:
            return None

        parts: list[str] = []
        for i, item in enumerate(items[:50], 1):
            title_list = item.get("title") or []
            title = title_list[0] if title_list else "(no title)"
            abstract = item.get("abstract") or ""
            # Crossref возвращает abstract с JATS-тегами, чистим
            abstract = re.sub(r"<[^>]+>", "", abstract).strip()

            authors = []
            for a in item.get("author") or []:
                name = " ".join(filter(None, [a.get("given"), a.get("family")]))
                if name:
                    authors.append(name)

            container = (item.get("container-title") or [""])[0]
            published = item.get("published") or {}
            year = published.get("date-parts", [[""]])[0][0] if published else ""
            doi = item.get("DOI") or ""
            subjects = item.get("subject") or []
            links = item.get("link") or []
            pdf_url = ""
            for l in links:
                if l.get("content-type") == "application/pdf":
                    pdf_url = l.get("URL") or ""
                    break

            section = (
                f"=== Crossref Article {i}: {title} ===\n"
                f"Authors: {', '.join(authors)}\n"
                f"Journal: {container} ({year})\n"
                + (f"DOI: {doi}\n" if doi else "")
                + (f"Subjects: {', '.join(subjects)}\n" if subjects else "")
                + (f"PDF: {pdf_url}\n\n" if pdf_url else "\n")
                + (f"Abstract: {abstract}\n" if abstract else "(no abstract)\n")
            )
            parts.append(section)

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n\n".join(parts),
            metadata={
                "query": query,
                "results_count": len(items),
                "platform": "Crossref",
            },
            license="various (see per-record)",
        )


# ============================================================
# Wikipedia REST API
# ============================================================

class WikipediaCrawler(BaseCrawler):
    """Получить статьи Wikipedia напрямую через REST API (в JSON, без HTML-парсинга).

    Конфиг:
        type: wikipedia
        # URL — название статьи или ссылка
        # Пример: url: "Operational amplifier"
        #         url: "https://en.wikipedia.org/wiki/Operational_amplifier"
    """

    source_type = "wikipedia"

    def _crawl(self, url: str) -> CorpusRecord | None:
        # Определяем язык и название статьи из URL
        lang = "en"
        title = url

        if url.startswith("http"):
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            if host.endswith(".wikipedia.org"):
                lang = host.split(".")[0]
            path_parts = parsed.path.split("/wiki/")
            if len(path_parts) > 1:
                title = path_parts[1].split("?")[0].split("#")[0]
                # URL-декодирование
                from urllib.parse import unquote
                title = unquote(title)

        if not title:
            return None

        # Используем REST API для plain text
        # https://en.wikipedia.org/api/rest_v1/page/html/{title} — HTML с разметкой
        # https://en.wikipedia.org/w/api.php?action=parse&page=...&format=json — wikitext
        # Лучший вариант для корпуса — REST API с plain text:
        api = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title)}"

        try:
            r = self.session.get(api, timeout=15)
            if r.status_code == 404:
                log.warning(f"Wikipedia article not found: {title}")
                return None
            r.raise_for_status()
            summary_data = r.json()
        except Exception as e:
            log.warning(f"Wikipedia summary failed for '{title}': {e}")
            return None

        # Достаём extract (plain text)
        extract = summary_data.get("extract") or ""
        if not extract:
            return None

        # Достаём метаданные
        title_clean = summary_data.get("title") or title
        description = summary_data.get("description") or ""
        thumbnail = (summary_data.get("thumbnail") or {}).get("source") or ""
        coordinates = None
        if "coordinates" in summary_data:
            coords = summary_data["coordinates"]
            coordinates = [coords.get("lat"), coords.get("lon")]

        # Также получаем полный текст через /page/html
        html_api = f"https://{lang}.wikipedia.org/api/rest_v1/page/html/{quote(title)}"
        full_text = ""
        try:
            r2 = self.session.get(html_api, timeout=20)
            if r2.status_code == 200:
                # Извлекаем текст из HTML
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r2.text, "html.parser")
                # Удаляем служебные секции
                for tag in soup(["script", "style", "table", "figure", "math"]):
                    tag.decompose()
                full_text = soup.get_text(separator="\n")
                # Нормализуем
                full_text = re.sub(r"\n{3,}", "\n\n", full_text).strip()
        except Exception as e:
            log.debug(f"Wikipedia full text failed for '{title}': {e}")
            full_text = extract

        # Загружаем изображение-превью, если есть
        downloaded: list[DownloadedFile] = []
        if thumbnail:
            from ..http import download_file
            result = download_file(
                thumbnail,
                self.config.output.download_dir,
                self.config.output.max_file_size_mb,
                self.config.output.request_timeout,
                session=self.session,
            )
            if result:
                path, sha, size = result
                downloaded.append(DownloadedFile(
                    type="image",
                    original_url=thumbnail,
                    local_path=path,
                    sha1=sha,
                    size_bytes=size,
                ))

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content=full_text or extract,
            downloaded_files=downloaded,
            metadata={
                "title": title_clean,
                "description": description,
                "language": lang,
                "thumbnail_url": thumbnail,
                "coordinates": coordinates,
                "platform": "Wikipedia",
            },
            license="CC BY-SA 4.0",
        )
