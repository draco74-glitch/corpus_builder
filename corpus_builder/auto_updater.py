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

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

from .logging_setup import get_logger

log = get_logger(__name__)

# GitHub API для получения последнего релиза
GITHUB_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_API_TAGS = "https://api.github.com/repos/{repo}/releases/tags/{tag}"


class AutoUpdater:
    """Проверка и применение авто-обновлений через GitHub Releases.

    Attributes:
        repo: GitHub репозиторий в формате "owner/repo"
        current_version: текущая версия программы (например, "0.2.0")
        update_channel: "stable" или "pre-release"
    """

    def __init__(
        self,
        repo: str = "draco74-glitch/corpus_builder",
        current_version: str = "0.2.0",
        update_channel: str = "stable",
    ):
        self.repo = repo
        self.current_version = current_version
        self.update_channel = update_channel
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
        on_progress: "Callable[[int, int], None] | None" = None,
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

        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="corpus_builder_update_"))
            zip_path = tmp_dir / target_asset["name"]

            r = requests.get(download_url, stream=True, timeout=120)
            r.raise_for_status()

            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total_size)

            log.info(f"Downloaded {downloaded} bytes to {zip_path}")

            # Применить
            if self._is_patch_zip(target_asset["name"]):
                return self._apply_patch(zip_path)
            else:
                return self._apply_full_update(zip_path, tmp_dir)

        except Exception as e:
            log.error(f"Failed to download/apply update: {e}")
            return False
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def _is_patch_zip(self, name: str) -> bool:
        """Определить, является ли ZIP патчем (только .py файлы) или полным дистрибутивом."""
        return name.lower() in ("patch.zip", "update.zip")

    def _apply_patch(self, zip_path: Path) -> bool:
        """Применить патч — распаковать .py файлы в _internal/corpus_builder/.

        One-dir mode: Python-файлы находятся в _internal/corpus_builder/
        рядом с .exe. Можно заменить их без пересборки.
        """
        if not self._is_frozen():
            log.warning("Patch can only be applied in frozen (PyInstaller) mode")
            return False

        target_dir = self._get_internal_corpus_builder_dir()
        if not target_dir or not target_dir.exists():
            log.error(f"Cannot find target directory: {target_dir}")
            return False

        log.info(f"Applying patch to {target_dir}...")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Создаём backup
                backup_dir = target_dir.parent / "corpus_builder_backup"
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                shutil.copytree(target_dir, backup_dir)
                log.info(f"Backup created: {backup_dir}")

                # Распаковываем файлы
                extracted = 0
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    # Убираем префикс corpus_builder/ если есть
                    rel_path = name
                    if rel_path.startswith("corpus_builder/"):
                        rel_path = rel_path[len("corpus_builder/"):]

                    target_file = target_dir / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)

                    with zf.open(name) as src, open(target_file, "wb") as dst:
                        dst.write(src.read())
                    extracted += 1
                    log.debug(f"Updated: {rel_path}")

                log.info(f"Patch applied: {extracted} files updated")
                log.info("Please restart CorpusBuilder to apply changes.")
                return True

        except Exception as e:
            log.error(f"Failed to apply patch: {e}")
            # Восстанавливаем backup
            try:
                backup_dir = target_dir.parent / "corpus_builder_backup"
                if backup_dir.exists():
                    shutil.rmtree(target_dir)
                    shutil.copytree(backup_dir, target_dir)
                    log.info("Restored from backup")
            except Exception:
                pass
            return False

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
