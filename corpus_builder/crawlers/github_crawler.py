"""GitHub-краулер через REST API + ZIP-архив.

Закрывает проблемы исходной версии:
  - Zip Slip: используем safe_extract с проверкой realpath
  - ветка по умолчанию: узнаём через API /repos/:owner/:repo
  - лимиты: поддержка GITHUB_TOKEN через env
  - LFS: помечаем LFS-указатели в метаданных (не вытягиваем данные)
  - декодирование: проверяем успешность UTF-8 через chardet
"""
from __future__ import annotations

import fnmatch
import io
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from ..logging_setup import get_logger
from ..models import AppConfig, CorpusRecord, DownloadedFile
from .base import BaseCrawler

log = get_logger(__name__)


class GitHubCrawler(BaseCrawler):
    source_type = "github_repo"

    def _crawl(self, url: str) -> CorpusRecord | None:
        cfg = self.config.crawlers.github
        owner, repo = self._parse_url(url)
        if not owner:
            log.warning(f"Cannot parse GitHub URL: {url}")
            return None

        token = os.environ.get(cfg.token_env, "")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # 1. Определить ветку
        branch = cfg.branch
        if not branch:
            try:
                branch = self._get_default_branch(owner, repo, headers)
            except Exception as e:
                log.warning(f"Cannot get default branch for {owner}/{repo}: {e}, fallback 'main'")
                branch = "main"

        # 2. Скачать ZIP-архив выбранной ветки
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        try:
            r = self.session.get(zip_url, timeout=120, headers=headers, stream=True)
            if r.status_code == 404:
                # Попробовать master
                zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
                r = self.session.get(zip_url, timeout=120, headers=headers, stream=True)
            r.raise_for_status()
        except Exception as e:
            log.warning(f"Failed to download {owner}/{repo} archive: {e}")
            return None

        # 3. Распаковать в памяти, безопасно выбрать файлы
        content_parts: list[str] = []
        downloaded: list[DownloadedFile] = []
        project_tree: list[str] = []
        lfs_files: list[str] = []

        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile as e:
            log.warning(f"Bad ZIP for {owner}/{repo}: {e}")
            return None

        # Корень архива: обычно "{repo}-{hash}/"
        names = zf.namelist()
        if not names:
            return None
        root_prefix = names[0].split("/", 1)[0]

        for name in names:
            if name.endswith("/"):
                continue
            # rel_path без корневого префикса
            if not name.startswith(root_prefix + "/"):
                continue
            rel_path = name[len(root_prefix) + 1 :]
            project_tree.append(rel_path)

            # Фильтр по include_files
            include_patterns = cfg.include_files
            matched = any(
                fnmatch.fnmatch(rel_path.lower(), p.lower()) for p in include_patterns
            )
            if not matched:
                continue

            try:
                raw = zf.read(name)
            except Exception as e:
                log.debug(f"Cannot read {name} from {owner}/{repo}: {e}")
                continue

            # Проверка LFS-указателя
            if raw.startswith(b"version https://git-lfs"):
                lfs_files.append(rel_path)
                # Не пытаемся декодировать как текст — это указатель
                continue

            # Сохраняем бинарник (KiCad/CSV) как файл
            if any(
                ext in rel_path
                for ext in [".kicad_sch", ".kicad_pcb", ".kicad_pro", ".dcm", ".lib"]
            ):
                dest_path = self._safe_save(rel_path, raw)
                if dest_path:
                    import hashlib
                    sha = hashlib.sha1(raw).hexdigest()[:12]
                    downloaded.append(DownloadedFile(
                        type="kicad",
                        original_file=rel_path,
                        local_path=dest_path,
                        sha1=sha,
                        size_bytes=len(raw),
                    ))
                continue

            # Текстовые файлы — добавляем в content
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    import charset_normalizer
                    guess = charset_normalizer.detect(raw)
                    enc = guess.get("encoding") or "utf-8"
                    text = raw.decode(enc, errors="replace")
                except Exception:
                    text = raw.decode("utf-8", errors="replace")

            content_parts.append(f"=== {rel_path} ===\n{text}")

        # === Расширенные опции (Этап 2) ===
        issues_count = 0
        wiki_crawled = False
        docs_files_count = 0

        # Issues/PR — опционально
        if cfg.crawl_issues:
            try:
                issues_texts, issues_count = self._fetch_issues(
                    owner, repo, headers,
                    state=cfg.crawl_issues_state,
                    max_issues=cfg.crawl_issues_max,
                )
                if issues_texts:
                    content_parts.append(f"\n\n=== ISSUES & PR ({issues_count} entries) ===")
                    content_parts.extend(issues_texts)
                    log.info(f"Fetched {issues_count} issues for {owner}/{repo}")
            except Exception as e:
                log.debug(f"Issues fetch failed for {owner}/{repo}: {e}")

        # Docs-директория — ищем в уже распакованном ZIP
        if cfg.crawl_docs_dir:
            try:
                # Передаём ZipFile повторно: открываем ещё раз
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf_docs:
                    docs_texts = self._collect_docs_from_zip(
                        zf_docs, root_prefix, list(cfg.docs_extensions),
                    )
                    if docs_texts:
                        docs_files_count = len(docs_texts)
                        content_parts.append(f"\n\n=== DOCUMENTATION ({docs_files_count} files) ===")
                        content_parts.extend(docs_texts)
                        log.info(f"Found {docs_files_count} docs files in {owner}/{repo}")
            except Exception as e:
                log.debug(f"Docs collection failed for {owner}/{repo}: {e}")

        # Wiki — опционально
        if cfg.crawl_wiki:
            try:
                wiki_texts, wiki_files, wiki_ok = self._fetch_wiki(
                    owner, repo, headers, list(cfg.docs_extensions),
                )
                if wiki_ok and wiki_texts:
                    wiki_crawled = True
                    content_parts.append(f"\n\n=== WIKI ({len(wiki_texts)} files) ===")
                    content_parts.extend(wiki_texts)
                    log.info(f"Crawled {len(wiki_texts)} wiki files for {owner}/{repo}")
            except Exception as e:
                log.debug(f"Wiki crawl failed for {owner}/{repo}: {e}")

        return CorpusRecord(
            source_url=url,
            source_type=self.source_type,
            content="\n\n".join(content_parts),
            downloaded_files=downloaded,
            metadata={
                "repo_owner": owner,
                "repo_name": repo,
                "branch": branch,
                "project_tree": project_tree,
                "lfs_files": lfs_files,
                "file_count": len(project_tree),
                "issues_crawled": issues_count,
                "wiki_crawled": wiki_crawled,
                "docs_files_count": docs_files_count,
            },
            license=self._fetch_license(owner, repo, headers),
        )

    # ----------------------- helpers -----------------------

    @staticmethod
    def _parse_url(url: str) -> tuple[str, str]:
        parsed = urlparse(url)
        path = parsed.path.strip("/").split("/")
        # Может быть /owner/repo, /owner/repo.git, /owner/repo/tree/main
        if len(path) < 2:
            return "", ""
        owner = path[0]
        repo = path[1].removesuffix(".git")
        return owner, repo

    def _get_default_branch(self, owner: str, repo: str, headers: dict) -> str:
        api = f"https://api.github.com/repos/{owner}/{repo}"
        r = self.session.get(api, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()["default_branch"]

    def _fetch_issues(
        self, owner: str, repo: str, headers: dict,
        state: str = "all", max_issues: int = 50,
    ) -> tuple[list[str], int]:
        """Извлечь обсуждения из Issues/PR через GitHub API.

        Возвращает (тексты для content_parts, количество найденных issues).
        """
        api = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {
            "state": state,
            "per_page": min(max_issues, 100),
            "filter": "all",
            "sort": "updated",
            "direction": "desc",
        }
        try:
            r = self.session.get(api, params=params, headers=headers, timeout=20)
            if r.status_code == 403:
                log.warning(f"GitHub rate limit on /issues for {owner}/{repo}")
                return [], 0
            r.raise_for_status()
            items = r.json()
        except Exception as e:
            log.warning(f"Failed to fetch issues for {owner}/{repo}: {e}")
            return [], 0

        texts: list[str] = []
        for it in items[:max_issues]:
            if not isinstance(it, dict):
                continue
            title = it.get("title") or ""
            number = it.get("number") or "?"
            user = (it.get("user") or {}).get("login") or "anonymous"
            state_v = it.get("state") or "unknown"
            body = it.get("body") or ""
            is_pr = "pull_request" in it
            kind = "PR" if is_pr else "Issue"
            labels = [l.get("name", "") for l in it.get("labels") or []]
            label_str = ", ".join(labels) if labels else ""

            section = (
                f"=== {kind} #{number}: {title} ===\n"
                f"Author: {user} | State: {state_v}"
                + (f" | Labels: {label_str}" if label_str else "")
                + "\n\n"
                f"{body}"
            )
            texts.append(section)

        return texts, len(texts)

    def _fetch_wiki(
        self, owner: str, repo: str, headers: dict,
        docs_extensions: list[str],
    ) -> tuple[list[str], list[dict], bool]:
        """Клонировать GitHub Wiki как ZIP-архив.

        Wiki-репозитории имеют вид {owner}/{repo}.wiki.git. Их можно скачать как
        https://github.com/{owner}/{repo}/wiki (HTML) или как ZIP через архив
        refs/heads/master.zip. Здесь используем второй подход, чтобы получить
        исходные .md-файлы.

        Возвращает (texts, downloaded_files, success).
        """
        wiki_zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
        # Попробуем также main, т.к. новые wiki используют main
        wiki_fallback_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip"

        try:
            r = self.session.get(wiki_zip_url, timeout=60,
                                 headers={**headers, "Accept": "application/vnd.github+json"})
            if r.status_code == 404:
                r = self.session.get(wiki_fallback_url, timeout=60, headers=headers)
                if r.status_code == 404:
                    return [], [], False
            r.raise_for_status()
        except Exception as e:
            log.debug(f"Wiki not available for {owner}/{repo}: {e}")
            return [], [], False

        texts: list[dict] = []  # т.е. (path, content)
        downloaded_files: list[dict] = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                rel_path = name.split("/", 1)[-1] if "/" in name else name
                ext = os.path.splitext(rel_path)[1].lower()
                if ext not in docs_extensions and ext not in (".md", ".rst", ".txt"):
                    continue
                try:
                    raw = zf.read(name)
                    text = raw.decode("utf-8", errors="replace")
                    texts.append(f"=== Wiki: {rel_path} ===\n{text}")
                except Exception:
                    continue
        except zipfile.BadZipFile:
            return [], [], False

        return texts, downloaded_files, True

    def _collect_docs_from_zip(
        self,
        zf: zipfile.ZipFile,
        root_prefix: str,
        docs_extensions: list[str],
    ) -> list[str]:
        """Найти файлы в docs/, doc/, documentation/ директориях ZIP-архива.

        Возвращает список строк вида '=== docs/file.md ===\n{content}'.
        """
        docs_dirs = ("docs/", "doc/", "documentation/", "Documentation/", "Docs/")
        texts: list[str] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            rel_path = name[len(root_prefix) + 1:] if name.startswith(root_prefix + "/") else name
            # Проверяем, что путь начинается с одной из docs-директорий
            if not any(rel_path.startswith(d) for d in docs_dirs):
                continue
            ext = os.path.splitext(rel_path)[1].lower()
            if ext not in docs_extensions:
                continue
            try:
                raw = zf.read(info)
                text = raw.decode("utf-8", errors="replace")
                texts.append(f"=== {rel_path} ===\n{text}")
            except Exception:
                continue
        return texts

    def _fetch_license(self, owner: str, repo: str, headers: dict) -> str | None:
        try:
            api = f"https://api.github.com/repos/{owner}/{repo}/license"
            r = self.session.get(api, headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                lic = data.get("license") or {}
                spdx = lic.get("spdx_id")
                return spdx if spdx and spdx != "NOASSERTION" else None
        except Exception:
            pass
        return None

    def _safe_save(self, rel_path: str, raw: bytes) -> str | None:
        """Сохранить файл из архива безопасно (без Zip Slip).

        Имя = basename(rel_path) + префикс хэша от пути.
        """
        import hashlib
        dest_dir = Path(self.config.output.download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Берём basename и хэшируем полный путь, чтобы избежать коллизий
        basename = os.path.basename(rel_path) or "file"
        prefix = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:8]
        safe_name = f"{prefix}_{basename}"
        dest = dest_dir / safe_name
        # Проверка, что dest точно внутри dest_dir
        try:
            real_dest = dest.resolve()
            real_dir = dest_dir.resolve()
            if not str(real_dest).startswith(str(real_dir)):
                log.warning(f"Path traversal detected: {rel_path}")
                return None
        except Exception:
            return None
        try:
            with open(dest, "wb") as f:
                f.write(raw)
            return str(dest)
        except Exception as e:
            log.warning(f"Cannot save {rel_path}: {e}")
            return None
