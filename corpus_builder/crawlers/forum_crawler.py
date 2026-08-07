"""Форум-краулер для StackExchange через официальный API.

Закрывает проблемы:
  - регулярные изменения HTML верстки → используем стабильный API
  - нет принятого ответа → поле accepted_answer_id сохраняем в metadata
  - пагинация ответов → API отдаёт все ответы на /questions/{id}/answers
  - BBCode/HTML в теле ответа → используем фильтр withbody, чистим BS4
"""
from __future__ import annotations

import html
import re
from typing import Any

from bs4 import BeautifulSoup

from ..http import download_file
from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)

# SE sites — берём из URL, не из конфига, чтобы поддержать все подсайты
_SE_API_BASE = "https://api.stackexchange.com/2.3"


def _clean_html_body(s: str) -> str:
    """Извлечь чистый текст из HTML-тела ответа SE API."""
    if not s:
        return ""
    soup = BeautifulSoup(s, "html.parser")
    # Удалить код-блоки как отдельные маркеры
    for code in soup.find_all("code"):
        code.replace_with(f"\n```\n{code.get_text()}\n```\n")
    return soup.get_text(separator="\n")


class StackExchangeCrawler(BaseCrawler):
    source_type = "stackexchange"

    def _crawl(self, url: str) -> CorpusRecord | None:
        cfg = self.config.crawlers.stackexchange
        api_key = (
            __import__("os").environ.get(cfg.api_key_env, "")
            if cfg.api_key_env
            else ""
        )

        # 1. Распарсить URL → site + question_id
        site, question_id = self._parse_url(url)
        if not question_id:
            log.warning(f"Cannot parse StackExchange URL: {url}")
            return None

        # 2. Получить вопрос с телом
        params: dict[str, Any] = {
            "site": site,
            "filter": "withbody",
            "pagesize": 100,
        }
        if api_key:
            params["key"] = api_key

        try:
            r = self.session.get(
                f"{_SE_API_BASE}/questions/{question_id}",
                params=params,
                timeout=self.config.output.request_timeout,
            )
            r.raise_for_status()
            q_data = r.json()
        except Exception as e:
            log.warning(f"SE question fetch failed {url}: {e}")
            return None

        items = q_data.get("items") or []
        if not items:
            log.warning(f"Empty SE response for {url}")
            return None
        question = items[0]

        # 3. Получить ответы
        try:
            r2 = self.session.get(
                f"{_SE_API_BASE}/questions/{question_id}/answers",
                params=params,
                timeout=self.config.output.request_timeout,
            )
            r2.raise_for_status()
            answers_data = r2.json()
        except Exception as e:
            log.warning(f"SE answers fetch failed {url}: {e}")
            answers_data = {"items": []}

        answers = sorted(
            answers_data.get("items") or [],
            key=lambda a: (a.get("is_accepted", False), a.get("score", 0)),
            reverse=True,
        )

        # 4. Собрать текст треда
        q_title = html.unescape(question.get("title", "") or "")
        q_body = _clean_html_body(question.get("body", "") or "")
        q_tags = question.get("tags") or []

        parts: list[str] = []
        parts.append(f"# {q_title}\n")
        parts.append(f"## Вопрос\n\n{q_body}\n")
        if q_tags:
            parts.append(f"\nТеги: {', '.join(q_tags)}\n")

        accepted_id = question.get("accepted_answer_id")
        ans_records = []
        for a in answers:
            is_acc = a.get("is_accepted", False)
            score = a.get("score", 0)
            body = _clean_html_body(a.get("body", "") or "")
            mark = "[ПРИНЯТ]" if is_acc else ""
            parts.append(f"\n## Ответ (score={score}) {mark}\n\n{body}\n")
            ans_records.append({
                "answer_id": a.get("answer_id"),
                "score": score,
                "is_accepted": is_acc,
                "body_chars": len(body),
            })

        content = "\n".join(parts)

        # 5. Найти ссылки на вложения в вопросе и ответах (схемы, PDF)
        downloaded: list[DownloadedFile] = []
        full_html = (
            question.get("body", "") + " " +
            " ".join(a.get("body", "") for a in answers)
        )
        soup = BeautifulSoup(full_html, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            if not href:
                continue
            ext = href.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in ("pdf", "png", "jpg", "jpeg", "svg", "zip", "sch", "kicad_sch"):
                continue
            from urllib.parse import urljoin
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
                    type="attachment",
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
            metadata={
                "title": q_title,
                "tags": q_tags,
                "score": question.get("score"),
                "view_count": question.get("view_count"),
                "answer_count": question.get("answer_count"),
                "accepted_answer_id": accepted_id,
                "answers": ans_records,
                "site": site,
                "question_id": question_id,
                "creation_date": question.get("creation_date"),
                "platform": f"stackexchange_{site}",
            },
            license="CC BY-SA 4.0" if question.get("creation_date", 0) >= 1577836800 else "CC BY-SA 3.0",
        )

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        """Извлечь site и question_id из URL StackExchange."""
        # https://electronics.stackexchange.com/questions/322180/title-slug
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc
        # site = первая часть хоста до .stackexchange.com
        if host.endswith(".stackexchange.com"):
            site = host.split(".")[0]
        elif host == "stackoverflow.com":
            site = "stackoverflow"
        else:
            # поддержка stackoverflow на других языках
            site = host.split(".")[0]
        path_parts = [p for p in parsed.path.split("/") if p]
        # /questions/{id}/...
        if "questions" in path_parts:
            idx = path_parts.index("questions")
            if idx + 1 < len(path_parts):
                qid = path_parts[idx + 1]
                if qid.isdigit():
                    return site, qid
        return "", ""
