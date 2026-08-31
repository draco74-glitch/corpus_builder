"""Утилиты нормализации и хэширования текста."""
from __future__ import annotations

import hashlib
import re
import unicodedata

import ftfy

# Zero-width и управляющие символы, которые нужно удалить
_ZWSP_RE = re.compile(r"[\u200B\u200C\u200D\uFEFF\u00A0]")
_MULTI_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+")
_UTM_RE = re.compile(r"utm_[a-z_]+=[^&]+")


def normalize_text(text: str, fix_encoding: bool = True, normalize_chars: bool = True) -> str:
    """Полная нормализация текста.

    1. Исправление «сломанных» кодировок (ftfy).
    2. NFKC: совместимые символы → канонические (полноширинные, лигатуры).
    3. Удаление zero-width и NBSP.
    4. Нормализация пробелов и переносов.
    5. Trim trailing whitespace в каждой строке.
    """
    if not text:
        return ""

    if fix_encoding:
        text = ftfy.fix_text(text)

    if normalize_chars:
        text = unicodedata.normalize("NFKC", text)

    # Удаляем zero-width и неразрывные пробелы
    text = _ZWSP_RE.sub(" ", text)

    # Нормализуем переносы (Windows → Unix)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Trim trailing whitespace в строках
    text = _TRAILING_WS_RE.sub("", text)

    # Сжимаем множественные пробелы
    text = _MULTI_WS_RE.sub(" ", text)

    # Сжимаем множественные пустые строки
    text = _MULTI_NL_RE.sub("\n\n", text)

    return text.strip()


def normalize_yo(text: str, lang: str = "ru") -> str:
    """Привести 'ё' к 'е' (для русскоязычного корпуса).

    В некоторых текстах 'ё' заменена на 'е', в других — нет.
    Без этого модель учит 'ёлка' и 'елка' как разные токены.
    """
    if lang != "ru":
        return text
    return text.replace("ё", "е").replace("Ё", "Е")


def text_sha1(text: str) -> str:
    """SHA1 от нормализованного текста (для точной дедупликации)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def canonical_url(url: str) -> str:
    """Канонизация URL: убрать utm_*, отсортировать query, убрать фрагмент."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    # Фильтруем utm_* параметры и сортируем query для стабильности
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    qs.sort()
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))


def extension_of(url_or_path: str) -> str:
    """Расширение файла из URL/пути, без query и fragment ('a.PDF?x#y' → 'pdf').

    Используется краулерами: старый инлайн-разбор `s.rsplit('.', 1)[-1].split('?')`
    не резал `#fragment`, из-за чего `report.pdf#page=2` не скачивался.
    """
    from urllib.parse import urlparse

    path = urlparse(url_or_path).path if "://" in url_or_path else url_or_path
    tail = path.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else ""


def matches_patterns(path: str, patterns: list[str] | None) -> bool:
    """Проверить путь/URL списком glob-паттернов (`*.md`, `docs/**`, `README*`).

    Паттерн без `*`/`?` считается подстрокой — так работают «расширения»
    (`".md"`), префиксы каталогов (`"docs/"`) и простые имена файлов, которые
    люди пишут в config.yaml.
    """
    import fnmatch

    if not patterns:
        return True
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    for p in patterns:
        pat = p.strip()
        if not pat:
            continue
        if pat in path:                       # подстрока: ".md", "docs/", "README"
            return True
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(path, pat):
            return True
        if fnmatch.fnmatch(name, f"*{pat}*") and ("*" in pat or "?" in pat):
            return True
    return False


def shingles_bytes(text: str, k: int = 5) -> list[bytes]:
    """k-словные шинглы сразу в bytes — для пакетной загрузки в MinHash.

    Отдельная функция потому, что `for s in shingles(): mh.update(s.encode())`
    был самым горячим циклом пост-обработки (~86% времени `run_dedup`), а
    datasketch умеет считать пакет за один вызов (`update_batch`).
    """
    words = re.findall(r"\w+", text.lower())
    if not words:
        return []
    if len(words) < k:
        return [" ".join(words).encode("utf-8")]
    return [" ".join(words[i:i + k]).encode("utf-8")
            for i in range(len(words) - k + 1)]


def shingles(text: str, k: int = 5) -> set[str]:
    """Набор k-словных шинглов для MinHash."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def estimate_quality(text: str) -> dict:
    """Простейшие метрики качества текста."""
    if not text:
        return {"chars": 0, "alpha_ratio": 0.0, "dup_line_ratio": 0.0, "language": None}

    chars = len(text)
    alpha = sum(1 for c in text if c.isalpha())
    alpha_ratio = alpha / chars if chars > 0 else 0.0

    lines = [l for l in text.split("\n") if l.strip()]
    if lines:
        seen: set[str] = set()
        dup = 0
        for l in lines:
            key = l.strip().lower()
            if key in seen:
                dup += 1
            seen.add(key)
        dup_line_ratio = dup / len(lines)
    else:
        dup_line_ratio = 0.0

    return {
        "chars": chars,
        "alpha_ratio": round(alpha_ratio, 3),
        "dup_line_ratio": round(dup_line_ratio, 3),
    }


def detect_language(text: str) -> str | None:
    """Грубое определение языка по символам.

    Возвращает 'ru', 'en' или None.
    Для надёжного определения нужен fasttext-langdetect,
    но это тяжёлая зависимость — поэтому простая эвристика.
    """
    if not text:
        return None
    sample = text[:5000]
    cyr = sum(1 for c in sample if "\u0400" <= c <= "\u04FF")
    lat = sum(1 for c in sample if "a" <= c.lower() <= "z")
    if cyr > lat * 1.5:
        return "ru"
    if lat > cyr * 1.5:
        return "en"
    if cyr + lat == 0:
        return None
    return "mixed" if cyr > 0 and lat > 0 else (None if cyr == lat == 0 else ("ru" if cyr > lat else "en"))
