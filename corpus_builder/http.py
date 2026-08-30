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
from urllib.parse import parse_qsl, urlparse

import requests
from slugify import slugify

from .logging_setup import get_logger

log = get_logger(__name__)

# Расширения, по которым ССЫЛКИ не скачиваем никогда (медиа/потоки/торренты).
# В путях проверяется все; в query — см. QUERY_CHECK_EXTENSIONS.
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

# `.ts` из проверки query-параметров исключён: в URL-параметре это чаще
# TypeScript/версия («?v=2024.03.ts», «?redirect=/app/index.ts»), и такие
# страницы молча выпадали из корпуса. Настоящие HLS-сегменты приходят через
# .m3u8-плейлист (заблокирован целиком), а по пути файла .ts режется и так.
QUERY_CHECK_EXTENSIONS = BLOCKED_EXTENSIONS - {".ts"}

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
    # "cloudflare.com" — не блокируем: док-сайты и блоги на Cloudflare (см. ниже)
    "cloudflarestream.com",
    # ВАЖНО: `cloudflare.com` и `cdn.jsdelivr.net` из списка убраны — это не
    # видеосервисы, а инфраструктура/CDN, на которых живут и нормальные доки
    # (например blog.cloudflare.com); их блокирование молча вырезало целые
    # разделы корпуса. Стриминг с CDN всё равно отсекается проверкой
    # Content-Type и max_download_time в download_file().
}


def is_blocked_url(url: str) -> bool:
    """Проверить, нужно ли блокировать URL (видео/аудио/streaming).

    Возвращает True, если URL ведёт на видеопоток или заблокированный домен.
    """
    if not url:
        return True

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if "@" in domain:                        # user@host — не наш формат, бережно
        return True

    # Проверяем домен
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True

    # Проверяем расширение из пути
    path = parsed.path.lower()
    for ext in BLOCKED_EXTENSIONS:
        if path.endswith(ext):
            return True

    # Проверяем query-string: некоторые CDN отдают ?file=video.mp4.
    #compare по ПОСЛЕДНЕМУ компоненту значения, а не подстрокой: прежний
    # `ext in query` молча выбрасывал обычные страницы, у которых в query
    # встретилось «.ts»/«mp3» (?v=2024.03.ts, ?redirect=/download.mp4) (I11).
    #
    # `.ts` из проверки исключён: в_query это чаще TypeScript/версия, а
    # MPEG-TC сегменты всё равно приходят через .m3u8-плейлист (заблокирован).
    for _key, value in parse_qsl(parsed.query):
        value = value.lower()
        value_path = urlparse(value).path if "://" in value else value
        tail = value_path.rsplit("/", 1)[-1]
        if any(tail.endswith(ext) for ext in QUERY_CHECK_EXTENSIONS):
            return True

    return False


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


def _is_cache_stale(stat, older_than_hours: float) -> bool:
    """Файл лежит в кэше дольше `older_than_hours` → стоит перепроверить."""
    import time
    return (time.time() - stat.st_mtime) > older_than_hours * 3600


def _probe_remote_size(url: str, session, headers: dict | None) -> int | None:
    """Размер удалённого файла через HEAD (None — если узнать не удалось).

    Нужен, чтобы отличать «файл уже скачан» от «файл на сервере изменился» (I5).
    HEAD дешёвый; если сервер его не поддерживает (405/501) или отдал без
    Content-Length — считаем, что проверять нечем, и оставляем кэш как есть.
    """
    try:
        r = session.head(url, headers=headers or {"User-Agent": "CorpusBuilder/0.2"},
                         timeout=10, allow_redirects=True)
        if r.status_code in (405, 501) or not r.headers.get("Content-Length"):
            # некоторые серверы не отдают Content-Length на HEAD — пробуем GET
            # с Range в 0 байт
            r = session.get(url, headers={"Range": "bytes=0-0",
                                          "User-Agent": "CorpusBuilder/0.2"},
                            timeout=10, stream=True)
        declared = r.headers.get("Content-Range") or r.headers.get("Content-Length") or ""
        if "/" in str(declared):                 # "bytes 0-0/12345"
            declared = str(declared).rsplit("/", 1)[-1]
        return int(declared) if str(declared).strip().isdigit() else None
    except Exception as e:                        # noqa: BLE001 — проверкой не рвём ран
        log.debug(f"size probe failed for {url[:80]}: {e}")
        return None


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
    revalidate: bool = True,
    revalidate_after_hours: float = 168.0,
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
        revalidate: сверять размер уже скачанного файла с сервером (I5).
        revalidate_after_hours: старше какого возраста файл worth перепроверки
            (HEAD-запрос только для «протухшего» кэша, чтобы не удваивать
            число запросов на корпус с тысячами картинок).

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
    sess = session or requests

    # Кэш: если файл уже есть — проверяем, что это всё ещё ОНСАМ (I5).
    # Прежняя логика «существует и непустой → используем» навсегда закрепляла
    # первую версию документа: перезагруженный даташит с тем же URL никогда
    # не попадал в корпус, а запись несла sha1 устаревшего файла.
    #
    # Чтобы не плодить HEAD-запросы на каждой картинке, проверяется только
    # «протухший» кэш: файл младше `cache_ttl_hours` используется как есть.
    if local_path.exists() and local_path.stat().st_size > 0:
        local_stat = local_path.stat()
        stale = revalidate and _is_cache_stale(local_stat, revalidate_after_hours)
        remote = _probe_remote_size(url, sess, headers) if stale else None
        local_size = local_stat.st_size
        if remote is None or remote == local_size:
            sha = file_hash(local_path)
            return str(local_path), sha, local_size
        log.info(f"Cached file is stale ({local_size} != {remote} bytes), "
                 f"re-downloading: {url[:80]}")
        try:
            local_path.unlink()
        except OSError:
            pass

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
                            raise OSError("download timeout")

                        written += len(chunk)
                        if written > max_size_mb * 1024 * 1024:
                            log.warning(
                                f"Stream exceeded limit while reading {url}, aborting"
                            )
                            raise OSError("file too large")
                        f.write(chunk)
            except (OSError, socket.timeout) as e:
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
