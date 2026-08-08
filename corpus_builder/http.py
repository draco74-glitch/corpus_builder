"""HTTP-сессия и общие функции загрузки.

С защитой от зависания на видеопотоках и больших файлах:
  - Read-timeout при стриминге (socket timeout)
  - Ограничение на общее время скачивания одного файла
  - Блоклист видео/аудио расширений
  - Блоклист streaming-доменов (YouTube, Vimeo, etc.)
"""
from __future__ import annotations

import hashlib
import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from slugify import slugify

from .logging_setup import get_logger
from .models import AppConfig

log = get_logger(__name__)

# Расширения, которые НИКОГДА не скачиваем — это видео/аудио потоки
# Они вызывают зависание краулера при попытке стриминга
BLOCKED_EXTENSIONS = {
    # Видео
    ".mp4", ".webm", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".m4v",
    ".mpg", ".mpeg", ".m2ts", ".ts", ".3gp", ".3g2",
    # Аудио
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus",
    # Плейлисты/стриминг
    ".m3u8", ".m3u", ".pls", ".asx",
    # Торренты
    ".torrent",
}

# Домены, которые НИКОГДА не качаем — это видеостриминг/CDN
BLOCKED_DOMAINS = {
    "youtube.com", "www.youtube.com", "youtu.be",
    "vimeo.com", "player.vimeo.com",
    "rutube.ru", "dailymotion.com",
    "twitch.tv", "player.twitch.tv",
    "vk.com", "vk.ru",
    "tiktok.com", "www.tiktok.com",
    "facebook.com", "fb.watch",
    "instagram.com",
    "streamable.com",
    "wistia.com",
    "brightcove.com",
    "jwplatform.com",
    "cloudflare.com",
    "cloudflarestream.com",
    "cdn.jsdelivr.net",
}


def is_blocked_url(url: str) -> bool:
    """Проверить, нужно ли блокировать URL (видео/аудио/streaming).

    Возвращает True, если URL ведёт на видеопоток или заблокированный домен.
    """
    if not url:
        return True

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Проверяем домен
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    # Проверяем расширение из пути
    path = parsed.path.lower()
    for ext in BLOCKED_EXTENSIONS:
        if path.endswith(ext):
            return True

    # Проверяем расширение из query-string (некоторые CDN отдают ?file=video.mp4)
    query = parsed.query.lower()
    for ext in BLOCKED_EXTENSIONS:
        if ext in query:
            return True

    return False


def make_session(config: AppConfig) -> requests.Session:
    """Создать requests.Session с connection pooling."""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers.update({
        "User-Agent": config.output.user_agent,
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.8",
    })

    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=50,
        max_retries=retry_strategy,
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
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
    max_download_time: int = 120,
) -> tuple[str, str, int] | None:
    """Скачать файл с защитой от зависания.

    Защита от видеопотоков:
      1. Блоклист видео/аудио расширений (is_blocked_url)
      2. Read-timeout: socket timeout на каждом chunk (не ждём бесконечно)
      3. max_download_time: общее ограничение времени на один файл
      4. Проверка Content-Type: если video/audio — пропускаем

    Параметры:
        max_download_time: максимум секунд на скачивание одного файла.
                           Если сервер шлёт данные медленно (видеострим),
                           прерываем через это время.

    Возвращает (local_path, sha1, size_bytes) или None.
    """
    # Проверка блоклиста (видео/аудио/streaming)
    if is_blocked_url(url):
        log.debug(f"Blocked URL (video/audio/streaming): {url[:80]}")
        return None

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
            timeout=(timeout, timeout),  # (connect_timeout, read_timeout)
            headers=headers or {"User-Agent": "CorpusBuilder/0.2"},
            allow_redirects=True,
        ) as r:
            r.raise_for_status()

            # Проверка Content-Type: блокируем видео/аудио
            content_type = (r.headers.get("Content-Type") or "").lower()
            if content_type.startswith(("video/", "audio/", "application/vnd.apple.mpegurl",
                                         "application/x-mpegurl")):
                log.debug(f"Blocked by Content-Type ({content_type}): {url[:80]}")
                return None

            declared = int(r.headers.get("Content-Length", 0) or 0)
            if declared and declared > max_size_mb * 1024 * 1024:
                log.warning(
                    f"File too large: {url} ({declared / 1024 / 1024:.1f} MB), skipping"
                )
                return None

            tmp_path = local_path.with_suffix(local_path.suffix + ".tmp")
            written = 0
            start_time = time.monotonic()

            try:
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if not chunk:
                            continue

                        # Проверка ограничения времени (защита от видеопотоков)
                        elapsed = time.monotonic() - start_time
                        if elapsed > max_download_time:
                            log.warning(
                                f"Download timed out after {max_download_time}s: {url[:80]}"
                            )
                            raise IOError("download timeout")

                        written += len(chunk)
                        if written > max_size_mb * 1024 * 1024:
                            log.warning(
                                f"Stream exceeded limit while reading {url}, aborting"
                            )
                            raise IOError("file too large")
                        f.write(chunk)
            except (IOError, OSError, socket.timeout) as e:
                if tmp_path.exists():
                    tmp_path.unlink()
                if "timeout" in str(e).lower():
                    log.debug(f"Download timeout for {url[:80]}: {e}")
                else:
                    log.debug(f"Download aborted for {url[:80]}: {e}")
                return None
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
            os.replace(tmp_path, local_path)

        sha = file_hash(local_path)
        size = local_path.stat().st_size
        return str(local_path), sha, size

    except requests.exceptions.Timeout:
        log.debug(f"Request timeout for {url[:80]}")
        return None
    except requests.exceptions.ConnectionError as e:
        log.debug(f"Connection error for {url[:80]}: {e}")
        return None
    except Exception as e:
        log.debug(f"Download failed for {url[:80]}: {e}")
        return None
