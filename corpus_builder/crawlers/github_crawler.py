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
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from ..logging_setup import get_logger
from ..models import CorpusRecord, DownloadedFile
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

        # 2. Скачать ZIP-архив выбранной ветки (на диск, не в память — I9:
        #    ранние версии держали в RAM два полных архива репозитория)
        zip_path = self._download_archive(owner, repo, branch, headers)
        if zip_path is None:
            return None

        # 3. Прочитать архив, безопасно выбрать файлы
        content_parts: list[str] = []
        downloaded: list[DownloadedFile] = []
        project_tree: list[str] = []
        lfs_files: list[str] = []

        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile as e:
            log.warning(f"Bad ZIP for {owner}/{repo}: {e}")
            zip_path.unlink(missing_ok=True)
            return None

        try:
            docs_texts, included_rel_paths = self._collect_content(
                zf, owner, repo, content_parts, downloaded, project_tree, lfs_files,
            )
        finally:
            zf.close()
            zip_path.unlink(missing_ok=True)

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
                    comments_per_issue=cfg.issues_comments_max,
                )
                if issues_texts:
                    content_parts.append(f"\n\n=== ISSUES & PR ({issues_count} entries) ===")
                    content_parts.extend(issues_texts)
                    log.info(f"Fetched {issues_count} issues for {owner}/{repo}")
            except Exception as e:
                log.debug(f"Issues fetch failed for {owner}/{repo}: {e}")

        # Docs-директория — из уже скачанного архива (сбор идёт тем же проходом)
        if cfg.crawl_docs_dir and docs_texts:
            docs_files_count = len(docs_texts)
            content_parts.append(f"\n\n=== DOCUMENTATION ({docs_files_count} files) ===")
            content_parts.extend(docs_texts)
            log.info(f"Found {docs_files_count} docs files in {owner}/{repo}")

        # Wiki — ОСТОРОЖНО: wiki lives в ОТДЕЛЬНОМ репозитории {repo}.wiki (I8)
        if cfg.crawl_wiki:
            try:
                wiki_texts, wiki_ok = self._fetch_wiki(
                    owner, repo, headers, list(cfg.docs_extensions),
                    skip_paths=included_rel_paths,
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

    # ----------------------- archive -----------------------

    def _download_archive(self, owner: str, repo: str, branch: str,
                          headers: dict) -> Path | None:
        """Скачать ZIP ветки на временный файл с лимитом по размеру (I9).

        `stream=True` + `Content-Length` + счётчик по чанкам: раньше архив
        целиком попадал в память дважды (основной проход и проход docs/).
        """
        cfg = self.config.crawlers.github
        max_bytes = cfg.max_archive_mb * 1024 * 1024
        candidates = [f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"]
        if branch != "master":
            candidates.append(f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip")
        if branch != "main":
            candidates.append(f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip")

        last_error: str | None = None
        for zip_url in candidates:
            try:
                r = self.session.get(zip_url, timeout=120, headers=headers, stream=True)
                if r.status_code == 404:
                    last_error = "404 (branch not found)"
                    continue
                r.raise_for_status()
                declared = int(r.headers.get("Content-Length", 0) or 0)
                if declared and declared > max_bytes:
                    log.warning(f"Repo archive too large: {zip_url} "
                                f"({declared / 1024 / 1024:.0f} MB > {cfg.max_archive_mb} MB)")
                    r.close()
                    return None
                tmp = tempfile.NamedTemporaryFile(prefix="cb_repo_", suffix=".zip", delete=False)
                written = 0
                try:
                    with tmp as f:
                        for chunk in r.iter_content(65536):
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > max_bytes:
                                log.warning(f"Repo archive exceeded {cfg.max_archive_mb} MB: {zip_url}")
                                return None
                            f.write(chunk)
                except BaseException:
                    Path(tmp.name).unlink(missing_ok=True)
                    raise
                return Path(tmp.name)
            except Exception as e:
                last_error = str(e)
                log.debug(f"archive fetch failed {zip_url}: {e}")
        log.warning(f"Failed to download {owner}/{repo} archive: {last_error}")
        return None

    def _collect_content(self, zf: zipfile.ZipFile, owner: str, repo: str,
                         content_parts: list[str], downloaded: list[DownloadedFile],
                         project_tree: list[str], lfs_files: list[str]
                         ) -> tuple[list[str], set[str]]:
        """Один проход по архиву.

        Правила:
          * файлы, подходящие под `include_files` (per-source важнее
            глобального — I7), идут в `content_parts`;
          * файлы из `docs|doc|documentation/` с разрешённым расширением идут
            в отдельный блок DOCUMENTATION и НЕ дублируются в content (I8);
          * LFS-указатели пропускаются целиком (это 130 байт вместо файла);
          * KiCad-файлы сохраняются на диск как `downloaded_files`.

        Возвращает (docs_texts, собранные_относительные_пути).
        """
        cfg = self.config.crawlers.github
        include_patterns = self.include_files or cfg.include_files
        docs_dirs = ("docs/", "doc/", "documentation/")
        doc_exts = tuple(e.lower() if e.startswith(".") else f".{e.lower()}"
                         for e in cfg.docs_extensions)

        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            return [], set()
        root_prefix = names[0].split("/", 1)[0]

        docs_texts: list[str] = []
        included: set[str] = set()

        for name in names:
            if not name.startswith(root_prefix + "/"):
                continue
            rel_path = name[len(root_prefix) + 1:]
            project_tree.append(rel_path)
            lowered = rel_path.lower()

            in_docs_dir = lowered.startswith(docs_dirs)
            matched_include = any(fnmatch.fnmatch(lowered, p.lower())
                                  for p in include_patterns)
            matched_docs = in_docs_dir and lowered.endswith(doc_exts)
            if not (matched_include or matched_docs):
                continue

            try:
                raw = zf.read(name)
            except Exception as e:
                log.debug(f"Cannot read {name} from {owner}/{repo}: {e}")
                continue

            if raw.startswith(b"version https://git-lfs"):
                lfs_files.append(rel_path)
                continue

            # KiCad/схемы сохраняем файлом (для пары README ↔ KiCad)
            if self._is_kicad(rel_path):
                dest_path = self._safe_save(rel_path, raw)
                if dest_path:
                    downloaded.append(DownloadedFile(
                        type="kicad",
                        original_file=rel_path,
                        local_path=dest_path,
                        sha1=hashlib.sha1(raw).hexdigest()[:12],
                        size_bytes=len(raw),
                    ))
                included.add(rel_path)
                continue

            text = self._decode(raw)
            included.add(rel_path)
            if in_docs_dir:
                docs_texts.append(f"=== {rel_path} ===\n{text}")
            else:
                content_parts.append(f"=== {rel_path} ===\n{text}")

        return docs_texts, included

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
        comments_per_issue: int = 20,
    ) -> tuple[list[str], int]:
        """Извлечь обсуждения из Issues/PR через GitHub API (с пагинацией).

        Возвращает (тексты для content_parts, количество собранных обсуждений).

        Раньше бралась ровно ОДНА страница по `per_page=min(max_issues,100)`,
        поэтому `crawl_issues_max: 500` молча давал 100 записей (I9). Теперь
        страница за страницей до `max_issues`, и к каждому треду добавляются
        комментарии (именно в них обычно и содержит объяснение).
        """
        api = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {
            "state": state,
            "per_page": min(max(1, max_issues), 100),
            "filter": "all",
            "sort": "updated",
            "direction": "desc",
        }

        items: list[dict] = []
        page = 1
        while len(items) < max_issues:
            try:
                r = self.session.get(api, params={**params, "page": page},
                                     headers=headers, timeout=20)
                if r.status_code in (403, 429):
                    log.warning(f"GitHub rate limit on /issues for {owner}/{repo} "
                                f"(page {page}); собрано {len(items)}")
                    break
                r.raise_for_status()
                batch = r.json()
            except Exception as e:
                log.warning(f"Failed to fetch issues for {owner}/{repo}: {e}")
                break
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < params["per_page"]:
                break               # последняя страница
            page += 1
            if page > 10:           # страховка от зацикливания на 1000 issues
                log.info("Issues pagination capped at 10 pages")
                break

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
                + (f"{body}\n" if body else "")
            )
            if comments_per_issue > 0 and number != "?":
                comments = self._fetch_issue_comments(
                    owner, repo, number, headers, comments_per_issue)
                if comments:
                    section += "\n--- Комментарии ---\n" + "\n".join(comments)
            texts.append(section)

        return texts, len(texts)

    def _fetch_issue_comments(self, owner: str, repo: str, number: int | str,
                              headers: dict, max_comments: int) -> list[str]:
        """Комментарии под issue/PR — по 100 на страницу, не больше max_comments."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
        out: list[str] = []
        page = 1
        while len(out) < max_comments:
            try:
                r = self.session.get(url, params={"per_page": min(max_comments, 100),
                                                  "page": page},
                                     headers=headers, timeout=20)
                if r.status_code != 200:
                    break
                batch = r.json()
            except Exception as e:
                log.debug(f"comments fetch failed for {owner}/{repo}#{number}: {e}")
                break
            if not isinstance(batch, list) or not batch:
                break
            for c in batch:
                if len(out) >= max_comments:
                    break
                author = (c.get("user") or {}).get("login") or "anonymous"
                body = (c.get("body") or "").strip()
                if body:
                    out.append(f"[{author}]: {body}")
            if len(batch) < min(max_comments, 100):
                break
            page += 1
        return out

    def _fetch_wiki(
        self, owner: str, repo: str, headers: dict,
        docs_extensions: list[str],
        skip_paths: set[str] | None = None,
    ) -> tuple[list[str], bool]:
        """Скачать Wiki репозитория: {owner}/{repo}.wiki (I8).

        GitHub хранит wiki в ОТДЕЛЬНОМ репозитории `{repo}.wiki`; прежний код
        качал архив самого репозитория, из-за чего «WIKI» в контенте была
        копией кода/доков того же репо.

        Возвращает (texts, success). `skip_paths` — файлы, уже попавшие в
        content (дедупликация с основным проходом по архиву).
        """
        skip_paths = skip_paths or set()
        base = f"https://github.com/{owner}/{repo}.wiki/archive/refs/heads"
        texts: list[str] = []
        archive_path: Path | None = None

        for branch in ("master", "main"):
            try:
                r = self.session.get(f"{base}/{branch}.zip", timeout=60,
                                     headers=headers, stream=True)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                tmp = tempfile.NamedTemporaryFile(prefix="cb_wiki_", suffix=".zip",
                                                  delete=False)
                max_bytes = self.config.crawlers.github.max_archive_mb * 1024 * 1024
                written = 0
                with tmp as f:
                    for chunk in r.iter_content(65536):
                        written += len(chunk or b"")
                        if written > max_bytes:
                            log.warning("Wiki archive too large, skipped")
                            Path(tmp.name).unlink(missing_ok=True)
                            return [], False
                        f.write(chunk)
                archive_path = Path(tmp.name)
                break
            except Exception as e:
                log.debug(f"wiki branch {branch} unavailable for {owner}/{repo}: {e}")
                continue

        if archive_path is None:
            return [], False

        try:
            with zipfile.ZipFile(archive_path) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                if not names:
                    return [], False
                root_prefix = names[0].split("/", 1)[0]
                for name in names:
                    if not name.startswith(root_prefix + "/"):
                        continue
                    rel_path = name[len(root_prefix) + 1:]
                    if rel_path in skip_paths:
                        continue
                    if not rel_path.lower().endswith(".md"):
                        continue          # wiki репозиторий — это .md страницы
                    try:
                        raw = zf.read(name)
                    except Exception:
                        continue
                    text = self._decode(raw)
                    title = rel_path[:-3].replace("_", " ").replace("/", " › ")
                    texts.append(f"=== Wiki: {title} ===\n{text}")
        except zipfile.BadZipFile:
            return [], False
        finally:
            archive_path.unlink(missing_ok=True)

        return texts, True

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

    @staticmethod
    def _is_kicad(rel_path: str) -> bool:
        """KiCad/ECAD файлы по расширению пути (не «расширение — часть слова»)."""
        lowered = rel_path.lower()
        return lowered.endswith((".kicad_sch", ".kicad_pcb", ".kicad_pro",
                                 ".dcm", ".lib", ".brd", ".sch"))

    @staticmethod
    def _decode(raw: bytes) -> str:
        """UTF-8 → чарсдет → replace (то, что раньше было инлайном в two местах)."""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                import charset_normalizer
                guess = charset_normalizer.detect(raw)
                enc = guess.get("encoding") or "utf-8"
                return raw.decode(enc, errors="replace")
            except Exception:
                return raw.decode("utf-8", errors="replace")

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
