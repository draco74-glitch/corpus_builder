"""Дополнительные краулеры для научных источников:
  - DOAJ (Directory of Open Access Journals)
  - arXiv API (статьи из eess.SP, eess.SY, cs.AR и др.)
  - Crossref (метаданные DOI)
  - Wikipedia REST API (статьи напрямую в JSON, без HTML-парсинга)

Все они добавляются как новый source_type в конфигурации.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)


def _polite_user_agent(config: AppConfig) -> str:
    """User-Agent с контактом для API, где это требуют (Crossref, Wikimedia).

    Wikimedia отвечаёт 403 «Too Many Requests» на анонимный агентический UA,
    поэтому контакт важен и для Wikipedia (I14).
    """
    contact = getattr(config.output, "contact_email", "") or ""
    ua = config.output.user_agent or "CorpusBuilder"
    return f"{ua} (mailto:{contact})" if contact else ua


def _api_headers(config: AppConfig, accept: str = "application/json") -> dict:
    return {"User-Agent": _polite_user_agent(config), "Accept": accept}


_DOI_RE = re.compile(r"^(?:doi:)?10\.\d{4,9}/\S+$", re.IGNORECASE)


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
            r = self.session.get(api, params=params, timeout=20,
                                 headers=_api_headers(self.config, "application/json"))
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"DOAJ search failed for '{query}': {e}") from e

        results = data.get("results") or []
        if not results:
            return None

        parts: list[str] = []
        record_licenses: list[str] = []
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
            # лицензация статьи берётся из самой записи DOAJ, а не из «CC BY» по
            # умолчанию (в DOAJ есть и CC BY-NC, и CC BY-SA, и все-right reserved)
            lic_items = [l for l in (bibjson.get("license") or [])
                         if isinstance(l, dict) and (l.get("type") or l.get("url"))]
            for l in lic_items:
                record_licenses.append(str(l.get("type") or l.get("url")))
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
                + ("License: not specified\n" if not lic_items else "")
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
                "licenses": sorted(set(record_licenses)),
            },
            # Сводная лицензия по выдаче: одна строка только если все статьи
            # лицензированы одинаково, иначе «mixed» — выдумывать «CC BY»
            # для всего запроса было бы неправдой.
            license=(sorted(set(record_licenses))[0] if record_licenses
                     and len(set(record_licenses)) == 1 else
                     ("mixed" if record_licenses else None)),
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
    ARXIV_API = "https://export.arxiv.org/api/query"   # был http:// (I14)

    def _crawl(self, url: str) -> CorpusRecord | None:
        # URL интерпретируется как поисковый запрос arXiv
        query = url
        if query.startswith("http"):
            # URL arxiv.org/list/<cat>/<period> — извлекаем именно категорию
            # (раньше "cs.AR/recent" уходило в search_query как есть, и API
            #  возвращал пустую выдачу — источник помечался «empty content»)
            if "arxiv.org/list/" in query:
                tail = query.split("/list/")[-1].split("?")[0].strip("/")
                query = f"cat:{tail.split('/')[0]}" if tail else query
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
            r = self.session.get(self.ARXIV_API, params=params, timeout=30,
                                 headers={"User-Agent": _polite_user_agent(self.config)})
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(f"arXiv API failed for '{query}': {e}") from e

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
        url: "operational amplifier"          # поисковый запрос
        url: "10.1016/j.sna.2004.03.010"      # конкретный DOI
        url: "https://doi.org/10.1016/j.sna.2004.03.010"
    """

    source_type = "crossref"
    CROSSREF_API = "https://api.crossref.org/works"

    def _crawl(self, url: str) -> CorpusRecord | None:
        query = url
        if query.startswith("http"):
            path = urlparse(query).path
            query = path.replace("/works/", "").replace("/doi/", "").strip("/")
            query = query.removeprefix("doi:")

        # DOI ищем НАПРЯМУЮ: как поисковая строка он ранжируется Crossref по
        # своему, и в корпус могла попасть чужая статья вместо запрошенной (I14).
        if _DOI_RE.match(query or ""):
            doi = query.removeprefix("doi:")
            return self._render(url, [self._fetch_doi_work(doi)], query=doi, kind="doi")

        params = {
            "query": query,
            "rows": 50,
            "select": "DOI,title,abstract,author,published,container-title,"
                      "subject,link,license",
        }
        try:
            r = self.session.get(self.CROSSREF_API, params=params,
                                 headers=_api_headers(self.config), timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"Crossref API failed for '{query}': {e}") from e

        items = (data.get("message") or {}).get("items") or []
        if not items:
            log.info(f"Crossref: no results for query {query!r}")
            return None
        return self._render(url, items, query=query, kind="search")

    def _fetch_doi_work(self, doi: str) -> dict:
        """Одна запись Crossref по DOI через /works/{doi}."""
        try:
            r = self.session.get(f"{self.CROSSREF_API}/{quote(doi, safe='')}",
                                 headers=_api_headers(self.config), timeout=20)
            if r.status_code == 404:
                raise ValueError(f"DOI not found in Crossref: {doi}")
            r.raise_for_status()
            message = (r.json() or {}).get("message") or {}
        except ValueError:
            raise
        except Exception as e:
            raise RuntimeError(f"Crossref DOI lookup failed for {doi}: {e}") from e
        if not message:
            raise ValueError(f"Crossref returned an empty record for DOI {doi}")
        return message

    def _render(self, url: str, items: list[dict], query: str,
                kind: str) -> CorpusRecord | None:
        """Форматирование записей (общий код для поиска и одиночного DOI)."""
        parts: list[str] = []
        licenses: list[str] = []
        for i, item in enumerate(items[:50], 1):
            title_list = item.get("title") or []
            title = title_list[0] if title_list else "(no title)"
            # Crossref отдаёт abstract с JATS-тегами — вырезаем теги
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "").strip()

            authors = []
            for a in item.get("author") or []:
                name = " ".join(filter(None, [a.get("given"), a.get("family")]))
                if name:
                    authors.append(name)

            container = (item.get("container-title") or [""])[0]
            published = item.get("published") or item.get("published-print") or {}
            date_parts = (published.get("date-parts") or [[""]])[0] if published else [""]
            year = date_parts[0] if date_parts else ""
            doi = item.get("DOI") or ""
            subjects = item.get("subject") or []

            pdf_url = ""
            for l in item.get("link") or []:
                if "pdf" in (l.get("content-type") or "").lower():
                    pdf_url = l.get("URL") or ""
                    break

            # лиценция берётся из самой записи (см. metadata.licenses)
            lic_names = [str(l.get("URL") or l.get("name") or "")
                         for l in (item.get("license") or []) if isinstance(l, dict)]
            lic_names = [x for x in lic_names if x]
            licenses.extend(lic_names)

            section = (
                f"=== {title} ===\n"
                f"Authors: {', '.join(authors) or 'n/a'}\n"
                f"Journal: {container} ({year})\n"
                + (f"DOI: {doi}\n" if doi else "")
                + (f"Subjects: {', '.join(subjects)}\n" if subjects else "")
                + (f"License: {'; '.join(lic_names[:3])}\n" if lic_names else "")
                + (f"PDF: {pdf_url}\n\n" if pdf_url else "\n")
                + (f"{abstract}\n" if abstract else "(no abstract)\n")
            )
            parts.append(section)

        if not parts:
            return None
        unique_lic = sorted(set(licenses))
        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n\n".join(parts),
            metadata={
                "query": query,
                "kind": kind,
                "results_count": len(items),
                "platform": "Crossref",
                "licenses": unique_lic,
            },
            # раньше стояло «various (see per-record)»; для одиночного DOI это
            # была бы неправда
            license=(unique_lic[0] if len(unique_lic) == 1
                     else ("mixed" if unique_lic else None)),
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
            raise ValueError("empty wikipedia article title")
        title = title.replace(" ", "_")

        # Используем REST API для plain text
        # https://en.wikipedia.org/api/rest_v1/page/html/{title} — HTML с разметкой
        # https://en.wikipedia.org/w/api.php?action=parse&page=...&format=json — wikitext
        # Лучший вариант для корпуса — REST API с plain text:
        api = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title, safe='')}"

        try:
            r = self.session.get(api, timeout=15, headers=_api_headers(self.config))
            if r.status_code == 404:
                raise ValueError(f"wikipedia article not found: {title!r}")
            if r.status_code == 429:                    # Wikimedia rate limit
                wait_s = int(r.headers.get("Retry-After", 0) or 0)
                raise RuntimeError(f"wikipedia rate limited (Retry-After={wait_s}s)")
            r.raise_for_status()
            summary_data = r.json()
        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            # раньше возвращался None, и в errors.jsonl писалось «no record»
            raise RuntimeError(f"Wikipedia summary failed for '{title}': {e}") from e

        # Достаём extract (plain text)
        extract = summary_data.get("extract") or ""
        if not extract:
            raise ValueError(f"wikipedia returned no extract for {title!r}")

        # Достаём метаданные
        title_clean = summary_data.get("title") or title
        description = summary_data.get("description") or ""
        thumbnail = (summary_data.get("thumbnail") or {}).get("source") or ""
        coordinates = None
        if "coordinates" in summary_data:
            coords = summary_data["coordinates"]
            coordinates = [coords.get("lat"), coords.get("lon")]

        # Также получаем полный текст через /page/html
        html_api = f"https://{lang}.wikipedia.org/api/rest_v1/page/html/{quote(title, safe='')}"
        full_text = ""
        try:
            r2 = self.session.get(html_api, timeout=20, headers=_api_headers(self.config))
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
