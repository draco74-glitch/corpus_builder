"""HTTP-сессия и общие функции загрузки."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from slugify import slugify

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)


def make_session(config: AppConfig) -> requests.Session:
    """Создать requests.Session с правильными заголовками."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })
    return s


def file_hash(path: str | Path) -> str:
    """sha1 содержимого файла."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def url_hash(url: str) -> str:
    """Короткий хэш URL для уникальных имён файлов."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def safe_filename(url: str, max_len: int = 80) -> str:
    """Безопасное имя файла из URL: hash + расширение."""
    parsed = urlparse(url)
    orig_name = Path(parsed.path).name or "index"
    ext = Path(orig_name).suffix or ".bin"
    slug = slugify(Path(orig_name).stem)[: max_len - 16] or "file"
    return f"{slug}_{url_hash(url)}{ext.lower()}"


def download_file(
    url: str,
    dest_dir: str | Path,
    max_size_mb: int = 50,
    timeout: int = 30,
    headers: dict | None = None,
    session: requests.Session | None = None,
) -> tuple[str, str, int] | None:
    """Скачать файл.

    Возвращает (local_path, sha1, size_bytes) или None.
    Использует уникальные имена на основе хэша URL,
    так что одинаковые имена на разных сайтах не конфликтуют.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = safe_filename(url)
    local_path = dest_dir / fname

    # Кэш: если файл уже есть — возвращаем без перезакачки
    if local_path.exists() and local_path.stat().st_size > 0:
        sha = file_hash(local_path)
        return str(local_path), sha, local_path.stat().st_size

    sess = session or requests
    try:
        with sess.get(
            url,
            stream=True,
            timeout=timeout,
            headers=headers or {"User-Agent": "CorpusBuilder/0.2"},
            allow_redirects=True,
        ) as r:
            r.raise_for_status()
            declared = int(r.headers.get("Content-Length", 0) or 0)
            if declared and declared > max_size_mb * 1024 * 1024:
                log.warning(
                    f"File too large: {url} ({declared / 1024 / 1024:.1f} MB), skipping"
                )
                return None

            tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
            written = 0
            try:
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_size_mb * 1024 * 1024:
                            log.warning(
                                f"Stream exceeded limit while reading {url}, aborting"
                            )
                            raise IOError("file too large")
                        f.write(chunk)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
            os.replace(tmp_path, local_path)

        sha = file_hash(local_path)
        size = local_path.stat().st_size
        return str(local_path), sha, size

    except Exception as e:
        log.debug(f"Download failed for {url}: {e}")
        return None
