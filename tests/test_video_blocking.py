"""Тесты на защиту от видеопотоков (зависание краулинга)."""
import pytest


def test_blocked_video_extensions():
    """Видео-расширения блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://example.com/video.mp4") is True
    assert is_blocked_url("https://example.com/video.webm") is True
    assert is_blocked_url("https://example.com/video.avi") is True
    assert is_blocked_url("https://example.com/audio.mp3") is True
    assert is_blocked_url("https://example.com/audio.wav") is True
    assert is_blocked_url("https://example.com/playlist.m3u8") is True


def test_allowed_extensions():
    """Нормальные расширения НЕ блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://example.com/image.png") is False
    assert is_blocked_url("https://example.com/image.svg") is False
    assert is_blocked_url("https://example.com/doc.pdf") is False
    assert is_blocked_url("https://example.com/schematic.kicad_sch") is False
    assert is_blocked_url("https://example.com/page.html") is False
    assert is_blocked_url("https://example.com/data.csv") is False


def test_blocked_domains():
    """Видеостриминг-домены блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://youtube.com/watch?v=123") is True
    assert is_blocked_url("https://www.youtube.com/watch?v=123") is True
    assert is_blocked_url("https://youtu.be/123") is True
    assert is_blocked_url("https://vimeo.com/123") is True
    assert is_blocked_url("https://rutube.ru/video/123") is True
    assert is_blocked_url("https://player.vimeo.com/video/123") is True
    assert is_blocked_url("https://twitch.tv/video/123") is True


def test_allowed_domains():
    """Нормальные домены НЕ блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://habr.com/ru/articles/123") is False
    assert is_blocked_url("https://github.com/user/repo") is False
    assert is_blocked_url("https://example.com/page") is False
    assert is_blocked_url("https://www.allaboutcircuits.com/textbook/") is False


def test_blocked_subdomain():
    """Поддомены заблокированных доменов тоже блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://api.youtube.com/embed/123") is True
    assert is_blocked_url("https://cdn.vimeo.com/video/123") is True


def test_blocked_query_extension():
    """Видео-расширения в query-string блокируются."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("https://example.com/download?file=video.mp4") is True
    assert is_blocked_url("https://cdn.example.com/stream?file=video.webm") is True


def test_blocked_empty_url():
    """Пустой URL блокируется."""
    from corpus_builder.http import is_blocked_url

    assert is_blocked_url("") is True
    assert is_blocked_url(None) is True


def test_blocked_extensions_set():
    """Все расширения в BLOCKED_EXTENSIONS — видео/аудио."""
    from corpus_builder.http import BLOCKED_EXTENSIONS

    assert ".mp4" in BLOCKED_EXTENSIONS
    assert ".webm" in BLOCKED_EXTENSIONS
    assert ".mp3" in BLOCKED_EXTENSIONS
    assert ".m3u8" in BLOCKED_EXTENSIONS
    assert ".torrent" in BLOCKED_EXTENSIONS
    # Нормальные расширения НЕ в блоклисте
    assert ".pdf" not in BLOCKED_EXTENSIONS
    assert ".png" not in BLOCKED_EXTENSIONS
    assert ".kicad_sch" not in BLOCKED_EXTENSIONS


def test_blocked_domains_set():
    """Все домены в BLOCKED_DOMAINS — видеостриминг."""
    from corpus_builder.http import BLOCKED_DOMAINS

    assert "youtube.com" in BLOCKED_DOMAINS
    assert "vimeo.com" in BLOCKED_DOMAINS
    assert "twitch.tv" in BLOCKED_DOMAINS
    # Нормальные домены НЕ в блоклисте
    assert "github.com" not in BLOCKED_DOMAINS
    assert "habr.com" not in BLOCKED_DOMAINS
