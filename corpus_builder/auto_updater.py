"""Авто-обновление CorpusBuilder через GitHub Releases.

Проверяет новую версию на GitHub, скачивает diff (только изменившиеся .py файлы)
и распаковывает в _internal/corpus_builder/.

One-dir архитектура позволяет обновлять отдельные файлы без пересборки .exe:
  - Быстро (скачать 50 КБ вместо 450 МБ)
  - Безопасно (проверка SHA256)
  - Автоматически (проверка при старте)

Использование:
    from corpus_builder.auto_updater import AutoUpdater
    updater = AutoUpdater("draco74-glitch/corpus_builder", "0.2.0")
    if updater.check_for_updates():
        updater.download_and_apply()
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from .logging_setup import get_logger

log = get_logger(__name__)

# GitHub API для получения последнего релиза
GITHUB_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_API_TAGS = "https://api.github.com/repos/{repo}/releases/tags/{tag}"


class UpdateIntegrityError(Exception):
    """Архив обновления не прошёл проверку целостности (C7)."""


class UnsafeArchiveEntry(ValueError):
    """Элемент архива пытается записать файл вне целевого каталога (Zip Slip)."""


def verify_member_path(target_dir: Path, member_name: str,
                       allowed_extensions: tuple[str, ...] = (".py",)) -> Path:
    """Вернуть безопасный путь для элемента архива или поднять UnsafeArchiveEntry.

    Проверяем: не абсолютный, без drive-буквы, без `..`, расширение из
    разрешённого списка, и итоговый resolve()-путь лежит ВНУТРИ target_dir.
    Раньше `_apply_patch` делал `target_dir / rel_path` без любой проверки, и
    член вида `corpus_builder/../../../../evil.py` записывался за пределы
    каталога приложения.
    """
    name = member_name.replace("\\", "/")
    pure = Path(name)
    if pure.is_absolute() or (len(pure.parts) > 1 and pure.parts[0].endswith(":")):
        raise UnsafeArchiveEntry(f"absolute member path: {member_name!r}")
    if ".." in pure.parts:
        raise UnsafeArchiveEntry(f"parent traversal in member path: {member_name!r}")
    if allowed_extensions and pure.suffix.lower() not in allowed_extensions:
        raise UnsafeArchiveEntry(
            f"member {member_name!r} is not an updatable file type "
            f"(allowed: {allowed_extensions})"
        )
    base = target_dir.resolve()
    dest = (base / name).resolve()
    try:
        dest.relative_to(base)
    except ValueError:
        raise UnsafeArchiveEntry(f"member escapes target dir: {member_name!r}") from None
    return dest


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_digest_text(text: str, asset_name: str) -> str | None:
    """Вытащить hex-дайджест из sidecar-файла (.sha256).

    Поддерживаем форматы: `<hex>`, `<hex>  file.zip`, `sha256=<hex>`.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0] if line.split() else ""
        token = token.split("=")[-1].strip()
        if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
            return token.lower()
    log.debug(f"No sha256 digest found in sidecar for {asset_name}")
    return None


class AutoUpdater:
    """Проверка и применение авто-обновлений через GitHub Releases.

    NOTE: GUI использует `CommitUpdater` (файлы из git-дерева); этот класс —
    путь «релизних ассетов», вызывается только явно/из инструментов сборки.

    Атрибуты:
        repo: GitHub репозиторий в формате "owner/repo"
        current_version: текущая версия программы (например, "0.2.0")
        update_channel: "stable" или "pre-release"
        require_digest: проверять sha256 перед установкой (см. C7). Если True
            (по умолчанию) и дайджест недоступен — обновление НЕ применяется.
    """

    def __init__(
        self,
        repo: str = "draco74-glitch/corpus_builder",
        current_version: str = "0.2.0",
        update_channel: str = "stable",
        require_digest: bool = True,
    ):
        self.repo = repo
        self.current_version = current_version
        self.update_channel = update_channel
        self.require_digest = require_digest
        self._latest_release: dict | None = None

    def check_for_updates(self) -> dict | None:
        """Проверить наличие обновлений.

        Возвращает dict с информацией о релизе, если обновление доступно,
        иначе None.
        """
        try:
            url = GITHUB_API_LATEST.format(repo=self.repo)
            headers = {"Accept": "application/vnd.github+json"}
            token = os.environ.get("GITHUB_TOKEN", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"

            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 404:
                log.info("No releases found on GitHub")
                return None
            r.raise_for_status()
            release = r.json()

            latest_version = release.get("tag_name", "").lstrip("v")
            if not latest_version:
                log.warning("Cannot determine latest version")
                return None

            if self._compare_versions(latest_version, self.current_version) <= 0:
                log.info(f"Already up to date: {self.current_version}")
                return None

            self._latest_release = release
            log.info(f"Update available: {self.current_version} -> {latest_version}")
            return {
                "version": latest_version,
                "url": release.get("html_url", ""),
                "body": release.get("body", ""),
                "assets": [
                    {"name": a.get("name"), "url": a.get("browser_download_url"),
                     "size": a.get("size")}
                    for a in release.get("assets", [])
                ],
            }

        except Exception as e:
            log.warning(f"Failed to check for updates: {e}")
            return None

    def download_and_apply(
        self,
        asset_name: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Скачать и применить обновление.

        Параметры:
            asset_name: имя файла-asset на GitHub Releases.
                        Если None — ищет "patch.zip" или "update.zip".
            on_progress: колбэк (downloaded_bytes, total_bytes).

        Возвращает True, если обновление успешно применено.
        """
        if not self._latest_release:
            release_info = self.check_for_updates()
            if not release_info:
                return False
        else:
            release_info = {
                "version": self._latest_release.get("tag_name", "").lstrip("v"),
                "assets": [
                    {"name": a.get("name"), "url": a.get("browser_download_url"),
                     "size": a.get("size")}
                    for a in self._latest_release.get("assets", [])
                ],
            }

        # Найти подходящий asset
        assets = release_info.get("assets", [])
        target_asset = None
        if asset_name:
            target_asset = next((a for a in assets if a["name"] == asset_name), None)
        else:
            # Авто-поиск: patch.zip, update.zip, или полный ZIP-дистрибутив
            for pattern in ["patch.zip", "update.zip", "CorpusBuilder.zip"]:
                target_asset = next((a for a in assets if a["name"] == pattern), None)
                if target_asset:
                    break

        if not target_asset:
            log.warning(f"No update asset found in release {release_info['version']}")
            log.info(f"Available assets: {[a['name'] for a in assets]}")
            return False

        # Скачать
        download_url = target_asset["url"]
        total_size = target_asset.get("size", 0)
        log.info(f"Downloading {target_asset['name']} ({total_size} bytes)...")

        tmp_dir: Path | None = None      # cleanup должен работать и при раннем сбое
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="corpus_builder_update_"))
            zip_path = tmp_dir / target_asset["name"]

            r = requests.get(download_url, stream=True, timeout=120)
            r.raise_for_status()

            digest = self._fetch_digest_for(asset_name=target_asset["name"],
                                            browser_url=download_url)

            hasher = hashlib.sha256()
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total_size)

            log.info(f"Downloaded {downloaded} bytes to {zip_path}")

            # C7: проверка целостности ДО записи каких-либо файлов на диск.
            actual = hasher.hexdigest()
            if digest is None:
                if self.require_digest:
                    raise UpdateIntegrityError(
                        f"no sha256 digest published for {target_asset['name']} — "
                        f"refusing to install unverified code "
                        f"(pass require_digest=False to override)"
                    )
                log.warning(f"Update installed WITHOUT integrity verification: "
                            f"no .sha256 sidecar for {target_asset['name']}")
            elif actual != digest:
                raise UpdateIntegrityError(
                    f"sha256 mismatch for {target_asset['name']}: "
                    f"expected {digest}, got {actual}"
                )
            else:
                log.info(f"sha256 verified: {actual[:16]}…")

            if total_size and downloaded != total_size:
                raise UpdateIntegrityError(
                    f"truncated download: got {downloaded} of {total_size} bytes"
                )

            # Применить
            if self._is_patch_zip(target_asset["name"]):
                return self._apply_patch(zip_path)
            else:
                return self._apply_full_update(zip_path, tmp_dir)

        except UpdateIntegrityError as e:
            log.error(f"Update rejected: {e}")
            return False
        except Exception as e:
            log.error(f"Failed to download/apply update: {e}")
            return False
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _fetch_digest_for(self, asset_name: str, browser_url: str) -> str | None:
        """Скачать sidecar `<asset>.sha256` с того же релиза, если он опубликован."""
        try:
            r = requests.get(f"{browser_url}.sha256", timeout=30,
                             headers={"Accept": "application/octet-stream"})
            if r.status_code == 200 and r.text.strip():
                return _parse_digest_text(r.text, asset_name)
        except Exception as e:
            log.debug(f"digest fetch failed for {asset_name}: {e}")
        return None

    def _is_patch_zip(self, name: str) -> bool:
        """Определить, является ли ZIP патчем (только .py файлы) или полным дистрибутивом."""
        return name.lower() in ("patch.zip", "update.zip")

    def _apply_patch(self, zip_path: Path, target_dir: Path | None = None) -> bool:
        """Применить патч — распаковать .py файлы в _internal/corpus_builder/.

        One-dir mode: Python-файлы находятся в _internal/corpus_builder/
        рядом с .exe. Можно заменить их без пересборки.

        Схема (C7): сначала ВЕРИФИКАЦИЯ всех путей архива и распаковка в
        staging-каталог, затем бэкап и перенос. Архив с обходом каталога
        отклоняется целиком, до первого записи на диск.

        `target_dir` позволяет вызвать метод не только из frozen-сборки
        (используется тестами); в обычном рантайме каталог ищется сам.
        """
        if target_dir is None:
            if not self._is_frozen():
                log.warning("Patch can only be applied in frozen (PyInstaller) mode")
                return False
            target_dir = self._get_internal_corpus_builder_dir()
        if not target_dir or not target_dir.exists():
            log.error(f"Cannot find target directory: {target_dir}")
            return False

        log.info(f"Applying patch to {target_dir}...")

        staging = Path(tempfile.mkdtemp(prefix="corpus_builder_patch_"))
        backup_dir = target_dir.parent / "corpus_builder_backup"
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = [n for n in zf.namelist() if not n.endswith("/")]
                # 1. Прокинуть пути ДО распаковки: ни одного побора за пределы
                for name in members:
                    rel = name[len("corpus_builder/"):] if name.startswith("corpus_builder/") else name
                    verify_member_path(staging, rel)
                if not members:
                    log.warning("Patch ZIP is empty, nothing applied")
                    return False
                # 2. Распаковать во staging
                for name in members:
                    rel = name[len("corpus_builder/"):] if name.startswith("corpus_builder/") else name
                    src = zf.open(name)
                    dest = staging / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with src, open(dest, "wb") as dst:
                        dst.write(src.read())
                extracted = len(members)

            # 3. Бэкап + перенос поверх текущей версии
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(target_dir, backup_dir)
            log.info(f"Backup created: {backup_dir}")

            applied = 0
            for name in members:
                rel = name[len("corpus_builder/"):] if name.startswith("corpus_builder/") else name
                src = staging / rel
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                applied += 1
                log.debug(f"Updated: {rel}")

            log.info(f"Patch applied: {applied} files updated (of {extracted} in archive)")
            log.info("Please restart CorpusBuilder to apply changes.")
            return True

        except (UnsafeArchiveEntry, UpdateIntegrityError) as e:
            # архив отвергнут целиком — бэкап не создавался, восстанавливать нечего
            log.error(f"Patch rejected, nothing was written: {e}")
            return False
        except Exception as e:
            log.error(f"Failed to apply patch: {e}")
            # Восстанавливаем backup
            try:
                if backup_dir.exists():
                    shutil.rmtree(target_dir)
                    shutil.copytree(backup_dir, target_dir)
                    log.info("Restored from backup")
            except Exception:
                pass
            return False
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _apply_full_update(self, zip_path: Path, tmp_dir: Path) -> bool:
        """Применить полное обновление — распаковать весь ZIP-дистрибутив.

        Заменяет всю папку dist/CorpusBuilder/ целиком.
        """
        if not self._is_frozen():
            log.warning("Full update can only be applied in frozen mode")
            return False

        # Папка, где находится текущий .exe
        exe_dir = Path(sys.executable).parent
        parent_dir = exe_dir.parent

        try:
            # Распаковываем во временную папку
            extract_dir = tmp_dir / "extracted"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            # Находим распакованную папку CorpusBuilder
            new_dir = None
            for d in extract_dir.iterdir():
                if d.is_dir() and "CorpusBuilder" in d.name:
                    new_dir = d
                    break

            if not new_dir:
                # Если внутри нет вложенной папки — используем сам extract_dir
                new_dir = extract_dir

            # Backup текущей папки
            backup_dir = parent_dir / f"{exe_dir.name}_backup"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(exe_dir, backup_dir)
            log.info(f"Backup created: {backup_dir}")

            # Копируем новые файлы поверх старых
            for item in new_dir.iterdir():
                target = exe_dir / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)

            log.info("Full update applied. Please restart CorpusBuilder.")
            return True

        except Exception as e:
            log.error(f"Failed to apply full update: {e}")
            return False

    def _is_frozen(self) -> bool:
        """Проверить, запущен ли код из PyInstaller-сборки."""
        return getattr(sys, "frozen", False)

    def _get_internal_corpus_builder_dir(self) -> Path | None:
        """Найти папку _internal/corpus_builder/ рядом с .exe.

        В one-dir режиме PyInstaller кладёт Python-модули в _internal/.
        """
        if not self._is_frozen():
            return None

        exe_dir = Path(sys.executable).parent
        # PyInstaller 6.x: _internal/corpus_builder/
        candidates = [
            exe_dir / "_internal" / "corpus_builder",
            exe_dir / "corpus_builder",  # fallback
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        return None

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """Сравнить версии в формате '0.2.0'.

        Возвращает: 1 если v1 > v2, -1 если v1 < v2, 0 если равны.
        """
        def parse(v: str) -> tuple:
            parts = v.split(".")
            return tuple(int(p) for p in parts[:3])

        p1 = parse(v1)
        p2 = parse(v2)

        if p1 > p2:
            return 1
        elif p1 < p2:
            return -1
        return 0

    @staticmethod
    def create_patch_zip(
        source_dir: str | Path,
        output_path: str | Path,
        files_to_include: list[str] | None = None,
    ) -> str:
        """Создать patch.zip с только изменившимися .py файлами.

        Используется разработчиком для создания патча для GitHub Releases.

        Параметры:
            source_dir: папка с исходным кодом (corpus_builder/)
            output_path: путь к выходному .zip файлу
            files_to_include: список конкретных файлов (если None — все .py)
        """
        source_dir = Path(source_dir)
        output_path = Path(output_path)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if files_to_include:
                for f in files_to_include:
                    file_path = source_dir / f
                    if file_path.exists():
                        zf.write(file_path, f"corpus_builder/{f}")
            else:
                for py_file in source_dir.rglob("*.py"):
                    rel_path = py_file.relative_to(source_dir)
                    zf.write(py_file, f"corpus_builder/{rel_path}")

        size = output_path.stat().st_size
        log.info(f"Patch ZIP created: {output_path} ({size} bytes)")
        return str(output_path)


# ============================================================
# Commit-based updates: подтягивание .py файлов с main ветки
# ============================================================

GITHUB_API_COMMITS = "https://api.github.com/repos/{repo}/commits?sha={branch}&per_page=1"
GITHUB_API_CONTENTS = "https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
GITHUB_API_TREE = "https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1"


def _looks_like_python_module(rel_path: str, content: bytes) -> bool:
    """Проверить, что downloaded payload действительно Python-модуль.

    GitHub Contents API отдаёт base64; при обрезке/ошибке вместо кода может
    прилететь HTML-страница или мусор, который после перезапуска приложения
    всплывёт SyntaxError'ом в random-ном месте. Проверяем дешёвым parse.
    """
    if not rel_path.endswith(".py"):
        return False
    if b"<!DOCTYPE" in content[:200] or b"<html" in content[:200].lower():
        return False
    import ast
    try:
        ast.parse(content.decode("utf-8"))
        return True
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return False


def _atomic_write(dest: Path, content: bytes) -> bool:
    """Записать файл атомарно (tmp + os.replace). True при успехе."""
    import os
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / (dest.name + ".new.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, dest)
        return True
    except OSError as e:
        log.warning(f"Cannot write {dest}: {e}")
        try:
            (dest.parent / (dest.name + ".new.tmp")).unlink(missing_ok=True)
        except OSError:
            pass
        return False


class CommitUpdater:
    """Подтягивание обновлений напрямую из коммитов GitHub (без релизов).

    Проверяет последний коммит в main ветке, сравнивает SHA с сохранённым,
    и если есть новый коммит — скачивает все .py файлы из corpus_builder/
    через GitHub Contents API и заменяет их в _internal/corpus_builder/.

    Преимущества:
      - Не нужно создавать GitHub Releases
      - Не нужно пересобирать .exe
      - Обновление через несколько секунд после push в main
      - Скачивает только .py файлы (~150 КБ), не всё

    Использование:
        updater = CommitUpdater("draco74-glitch/corpus_builder")
        if updater.check_for_commit_updates():
            updater.apply_commit_update()
    """

    def __init__(
        self,
        repo: str = "draco74-glitch/corpus_builder",
        branch: str = "main",
    ):
        self.repo = repo
        self.branch = branch
        self._latest_sha: str | None = None
        self._commit_info: dict | None = None

    def _get_headers(self) -> dict:
        """HTTP headers для GitHub API с токеном если есть."""
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_last_known_sha(self) -> str | None:
        """Получить SHA последнего применённого коммита.

        Хранится в файле .corpus_builder_last_commit.txt рядом с .exe
        (frozen mode) или в home (dev mode).
        """
        if getattr(sys, "frozen", False):
            sha_file = Path(sys.executable).parent / ".corpus_builder_last_commit.txt"
        else:
            sha_file = Path.home() / ".corpus_builder_last_commit.txt"

        try:
            if sha_file.exists():
                return sha_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return None

    def _save_last_known_sha(self, sha: str) -> None:
        """Сохранить SHA последнего применённого коммита."""
        if getattr(sys, "frozen", False):
            sha_file = Path(sys.executable).parent / ".corpus_builder_last_commit.txt"
        else:
            sha_file = Path.home() / ".corpus_builder_last_commit.txt"
        try:
            sha_file.write_text(sha, encoding="utf-8")
        except Exception as e:
            log.warning(f"Cannot save last commit SHA: {e}")

    def check_for_commit_updates(self) -> dict | None:
        """Проверить, есть ли новые коммиты в main ветке.

        Возвращает dict с информацией о коммите, если есть обновление,
        иначе None.
        """
        try:
            url = GITHUB_API_COMMITS.format(repo=self.repo, branch=self.branch)
            r = requests.get(url, headers=self._get_headers(), timeout=15)
            if r.status_code == 403:
                log.warning("GitHub API rate limit hit for /commits")
                return None
            r.raise_for_status()
            commits = r.json()
            if not commits:
                log.info("No commits found")
                return None

            latest = commits[0]
            latest_sha = latest.get("sha", "")
            if not latest_sha:
                log.warning("Cannot determine latest commit SHA")
                return None

            last_known = self._get_last_known_sha()
            if last_known == latest_sha:
                log.info(f"Already up to date (commit {latest_sha[:8]})")
                return None

            self._latest_sha = latest_sha
            self._commit_info = latest

            commit_msg = (latest.get("commit", {}).get("message", "") or "")[:200]
            author = (latest.get("commit", {}).get("author", {}).get("name", "")) or "unknown"
            date = (latest.get("commit", {}).get("author", {}).get("date", "")) or ""

            log.info(f"Update available: commit {latest_sha[:8]} by {author}")
            log.info(f"  Message: {commit_msg[:80]}")

            return {
                "sha": latest_sha,
                "short_sha": latest_sha[:8],
                "message": commit_msg,
                "author": author,
                "date": date,
                "url": latest.get("html_url", ""),
            }

        except Exception as e:
            log.warning(f"Failed to check for commit updates: {e}")
            return None

    def _get_py_files_in_repo(self, sha: str) -> list[str]:
        """Получить список всех .py файлов в corpus_builder/ через Git Tree API.

        Возвращает список относительных путей (например: "gui.py", "crawlers/html_crawler.py").
        """
        try:
            url = GITHUB_API_TREE.format(repo=self.repo, sha=sha)
            r = requests.get(url, headers=self._get_headers(), timeout=20)
            r.raise_for_status()
            tree = r.json()

            py_files: list[str] = []
            for item in tree.get("tree", []):
                path = item.get("path", "")
                # Только .py файлы в corpus_builder/
                if path.startswith("corpus_builder/") and path.endswith(".py"):
                    # Убираем префикс corpus_builder/
                    rel_path = path[len("corpus_builder/"):]
                    py_files.append(rel_path)

            log.info(f"Found {len(py_files)} .py files in repository")
            return py_files

        except Exception as e:
            log.warning(f"Failed to get file tree: {e}")
            return []

    def _download_file_from_github(
        self, rel_path: str, sha: str, on_progress=None, total_files: int = 0,
        downloaded_count: int = 0,
    ) -> bytes | None:
        """Скачать один .py файл из GitHub через Contents API.

        Возвращает содержимое файла в bytes (декодированное из base64).
        """
        try:
            # Contents API возвращает base64-кодированный контент
            github_path = f"corpus_builder/{rel_path}"
            url = GITHUB_API_CONTENTS.format(
                repo=self.repo, path=github_path, ref=sha
            )
            r = requests.get(url, headers=self._get_headers(), timeout=20)
            if r.status_code == 404:
                log.debug(f"File not found in repo: {rel_path}")
                return None
            r.raise_for_status()
            data = r.json()

            # Contents API возвращает content в base64
            import base64
            content_b64 = data.get("content", "")
            if not content_b64:
                return None

            # Декодируем base64 (GitHub отдаёт с переносами строк)
            content_b64 = content_b64.replace("\n", "")
            content = base64.b64decode(content_b64)

            if on_progress and total_files > 0:
                on_progress(downloaded_count + 1, total_files, f"Скачано: {rel_path}")

            return content

        except Exception as e:
            log.debug(f"Failed to download {rel_path}: {e}")
            return None

    def apply_commit_update(
        self,
        on_progress=None,
        verify_source: bool = True,
    ) -> dict:
        """Скачать и применить обновление из последнего коммита.

        Скачивает все .py файлы из corpus_builder/ и заменяет их
        в _internal/corpus_builder/ (one-dir mode) или в исходной папке (dev mode).

        Правила целостности (C7):
          * маркер «установленного» коммита двигается ТОЛЬКО когда обновление
            применилось целиком (`failed == 0`); иначе часть файлов осталась от
            старого коммита и программа смешивает версии навсегда;
          * частичный сбой откатывается на бэкап, чтобы не оставить mixed state;
          * каждый файл проверяется как валидный Python (`ast.parse`) и пишется
            атомарно (tmp + os.replace).

        Возвращает dict с результатом:
            {success, files_updated, files_failed, failed_files, sha, short_sha}
        """
        if not self._latest_sha:
            info = self.check_for_commit_updates()
            if not info:
                return {"success": False, "error": "No updates available"}
            self._latest_sha = info["sha"]

        sha = self._latest_sha

        # Получаем список .py файлов
        py_files = self._get_py_files_in_repo(sha)
        if not py_files:
            return {"success": False, "error": "No .py files found in repository"}

        # Определяем целевую папку
        target_dir = self._get_target_dir()
        if not target_dir:
            return {"success": False, "error": "Cannot determine target directory"}

        log.info(f"Updating {len(py_files)} files in {target_dir}...")

        # Backup
        backup_dir = target_dir.parent / "corpus_builder_backup"
        try:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(target_dir, backup_dir)
            log.info(f"Backup created: {backup_dir}")
        except Exception as e:
            log.warning(f"Backup failed: {e}")

        # Скачиваем и заменяем каждый файл
        updated = 0
        failed = 0
        failed_files: list[str] = []
        invalid_files: list[str] = []

        for i, rel_path in enumerate(py_files):
            content = self._download_file_from_github(
                rel_path, sha, on_progress, len(py_files), i
            )
            if content is None:
                failed += 1
                failed_files.append(rel_path)
                continue

            if verify_source and not _looks_like_python_module(rel_path, content):
                # страница ошибки/обрезанный base64: не должен попасть в ран
                log.warning(f"Skipped non-Python payload: {rel_path}")
                invalid_files.append(rel_path)
                failed += 1
                failed_files.append(rel_path)
                continue

            if not _atomic_write(target_dir / rel_path, content):
                failed += 1
                failed_files.append(rel_path)
                continue
            updated += 1

        if on_progress:
            on_progress(len(py_files), len(py_files),
                       f"Готово: обновлено {updated}, ошибок {failed}")

        complete = updated > 0 and failed == 0
        restored = False
        if complete:
            self._save_last_known_sha(sha)
            log.info(f"Update applied: {updated} files updated")
        else:
            # НЕ двигаем маркер: повторный запуск предложит то же обновление и
            # доведёт его до конца (раньше при updated>0 маркер уходил вперёд).
            log.warning(
                f"Update incomplete ({updated} updated, {failed} failed) — "
                f"commit marker kept at previous version"
            )
            if failed_files:
                restored = self.restore_backup()
            log.info("Rolled back to backup" if restored else
                     "Left files in place; re-run update to finish")

        result = {
            "success": complete,
            "files_updated": updated,
            "files_failed": failed,
            "failed_files": failed_files,
            "invalid_files": invalid_files,
            "rolled_back": restored,
            "sha": sha,
            "short_sha": sha[:8],
        }
        if not complete:
            result["error"] = f"partial update: {failed} of {len(py_files)} files failed"
        return result

    def _get_target_dir(self) -> Path | None:
        """Определить папку corpus_builder/ для обновления.

        - Frozen (one-dir): _internal/corpus_builder/ рядом с .exe
        - Dev: corpus_builder/ в текущей директории
        """
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            candidates = [
                exe_dir / "_internal" / "corpus_builder",
                exe_dir / "corpus_builder",
            ]
            for c in candidates:
                if c.exists() and c.is_dir():
                    return c
            # Если папки нет — создаём
            target = exe_dir / "_internal" / "corpus_builder"
            target.mkdir(parents=True, exist_ok=True)
            return target
        else:
            # Dev mode: обновляем ТОТ же путь, из которого импортирован пакет,
            # а не `cwd` — иначе запуск `python -m corpus_builder.gui` из
            # произвольной директории перезаписывал бы random-ный каталог
            # `corpus_builder/` рядом с cwd (или падал с "не определено").
            import corpus_builder as _pkg
            pkg_dir = Path(_pkg.__file__).resolve().parent
            return pkg_dir if pkg_dir.is_dir() else None

    def backup_dir(self) -> Path | None:
        """Каталог с бэкапом последней успешной версии (или None)."""
        target = self._get_target_dir()
        return (target.parent / "corpus_builder_backup") if target else None

    def restore_backup(self) -> bool:
        """Восстановить из backup если обновление сломало программу.

        Каталог ищем так же, как при создании бэкапа (через `_get_target_dir`),
        иначе frozen/dev-режимы смотрели в разные места и откат не работал.
        """
        backup_dir = self.backup_dir()
        if backup_dir is None or not backup_dir.exists():
            log.warning("No backup found")
            return False

        target_dir = self._get_target_dir()
        if not target_dir:
            return False

        try:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(backup_dir, target_dir)
            log.info("Restored from backup")
            return True
        except Exception as e:
            log.error(f"Restore failed: {e}")
            return False
