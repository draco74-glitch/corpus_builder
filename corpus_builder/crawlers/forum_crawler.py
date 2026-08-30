"""Форум-краулер для StackExchange через официальный API.

Закрывает проблемы:
  - регулярные изменения HTML верстки → используем стабильный API
  - нет принятого ответа → поле accepted_answer_id сохраняем в metadata
  - пагинация ответов → API отдаёт все ответы на /questions/{id}/answers
  - BBCode/HTML в теле ответа → используем фильтр withbody, чистим BS4
"""
from __future__ import annotations

import html
import os
from typing import Any

from bs4 import BeautifulSoup

from ..http import download_file
from ..logging_setup import get_logger
from ..models import CorpusRecord, DownloadedFile
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

        # 1. Распарсить URL → site + question_id (или tag, если это список)
        site, question_id, tag = self.parse_target(url)
        if not site:
            raise ValueError(f"not a StackExchange URL: {url!r}")
        if not question_id:
            # /questions/tagged/<tag> или /questions — это СПИСОК вопросов
            return self._crawl_list(site, tag, url)

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
            # раньше — return None, и в errors.jsonl попадало «no record» без
            # причины; base.crawl() превращает исключение в error-запись с текстом
            raise RuntimeError(f"SE question {question_id} fetch failed: {e}") from e

        items = q_data.get("items") or []
        if not items:
            raise ValueError(
                f"question {question_id} not found on site={site} "
                f"(проверьте URL: id должен существовать)")
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
                # тело ответа сохраняем и в метаданных: выбор Accepted Answer по
                # тексту требует парсить рус/англ маркеры заголовков и может
                # промахнуться, здесь соответствие однозначное (I11).
                **({"body": body} if len(body) <= 20000 else {}),
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

    def _crawl_list(self, site: str, tag: str, url: str) -> CorpusRecord | None:
        """Топ вопросов тега/сайта — «Top questions by tags» из README.

        Прежний `_parse_url` требовал числовой id и на
        `/questions/tagged/<tag>` возвращал ("", "") → источник
        помечался ошибкой «no record» без объяснения причины (I11).
        """
        se_cfg = self.config.crawlers.stackexchange
        params: dict[str, Any] = {
            "site": site, "filter": "withbody", "order": "desc", "sort": "votes",
            "pagesize": max(1, min(100, se_cfg.max_list_questions)),
        }
        # минимальный рейтинг из настроек GUI (раньше `min_score` ни на что не
        # влиял — I4): с ним в корпус не попадают мусорные вопросы
        if getattr(se_cfg, "min_score", 0) > 0:
            params["min"] = se_cfg.min_score
        api_key = (os.environ.get(self.config.crawlers.stackexchange.api_key_env, "")
                   if self.config.crawlers.stackexchange.api_key_env else "")
        if api_key:
            params["key"] = api_key
        if tag:
            params["tagged"] = tag

        try:
            r = self.session.get(f"{_SE_API_BASE}/questions", params=params,
                                 timeout=self.config.output.request_timeout)
            r.raise_for_status()
            items = r.json().get("items") or []
        except Exception as e:
            raise RuntimeError(
                f"SE questions list failed (site={site}, tag={tag or '-'}): {e}") from e
        min_score = getattr(self.config.crawlers.stackexchange, "min_score", 0) or 0
        if min_score > 0:
            items = [i for i in items if int(i.get("score", 0) or 0) >= min_score]
        if not items:
            return None

        head = f"# StackExchange{' / тег ' + tag if tag else ''} — топ вопросов ({site})\n"
        parts = [head]
        questions: list[dict] = []
        for it in items:
            qid = it.get("question_id")
            title = html.unescape(it.get("title", "") or "")
            body = _clean_html_body(it.get("body", "") or "")
            score = it.get("score", 0)
            answers = it.get("answer_count", 0)
            accepted = bool(it.get("accepted_answer_id"))
            parts.append(
                f"## Вопрос {qid} (score={score}, answers={answers}"
                + (", [ПРИНЯТ]" if accepted else "") + f")\n\n{title}\n\n{body}\n")
            questions.append({"question_id": qid, "title": title, "score": score,
                              "answer_count": answers,
                              "accepted_answer_id": it.get("accepted_answer_id"),
                              "link": it.get("link", "")})

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n".join(parts),
            metadata={
                "site": site, "tag": tag, "questions": questions,
                "question_count": len(questions),
                "platform": f"stackexchange_{site}",
                "kind": "list",
            },
            license="CC BY-SA 4.0",
        )

    # локальные домены stackoverflow, где `site` — не первая часть хоста
    _SO_LOCALES = {"es", "pt", "ru", "ja", "fr", "it", "ko", "cs", "pl", "nl", "hi", "id"}

    @classmethod
    def _site_from_host(cls, host: str) -> str:
        """`site` для SE API из хоста.

        Прежний разбор `host.split(".")[0]` давал `ru.stackoverflow.com` →
        "ru" (API не знает такого сайта) и молча возвращал пустышку;
        `meta.stackexchange.com` → "meta". Теперь — корректные имена (I11).
        """
        host = (host or "").lower().strip(".").removeprefix("www.")
        parts = host.split(".")
        # <site>.stackexchange.com — electronics, physics, unix, ...
        if host.endswith(".stackexchange.com") and len(parts) >= 3:
            sub = parts[0]
            return "" if sub in ("meta", "blog", "api", "data", "stacks") else sub
        if host in ("stackoverflow.com",):
            return "stackoverflow"
        if host == "meta.stackoverflow.com":
            return "meta.stackoverflow"
        if len(parts) >= 3 and parts[-2] == "stackoverflow" and parts[0] in cls._SO_LOCALES:
            return f"{parts[0]}.stackoverflow"
        if host == "mathoverflow.net":
            return "mathoverflow"
        known_sites = {"superuser.com": "superuser", "serverfault.com": "serverfault",
                       "askubuntu.com": "askubuntu", "stackapps.com": "stackapps",
                       "unix.stackexchange.com": "unix"}
        if host in known_sites:
            return known_sites[host]
        # Хост не из SE Network — лучше честно вернуть "", чем послать запрос
        # с site=<первый кусок домена> и получить 400 (I11).
        return ""

    @classmethod
    def parse_target(cls, url: str) -> tuple[str, str, str]:
        """(site, question_id, tag) — question_id/tag пустые, если это список.

        Возвращает site="" если URL вообще не похож на StackExchange: вызывающий
        код обязан сообщить об этом в error-запись, а не писать «no record».
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        site = cls._site_from_host(parsed.netloc)
        parts = [p for p in parsed.path.split("/") if p]
        if not site or "questions" not in parts:
            return site, "", ""
        i = parts.index("questions")
        if i + 2 < len(parts) and parts[i + 1] == "tagged":
            return site, "", parts[i + 2]
        if i + 1 < len(parts) and parts[i + 1].isdigit():
            return site, parts[i + 1], ""
        return site, "", ""

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        """(site, question_id) — сохранено для совместимости с тестами."""
        site, qid, _tag = StackExchangeCrawler.parse_target(url)
        return site, qid
