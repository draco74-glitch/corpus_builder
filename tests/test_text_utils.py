"""Тесты на text_utils."""
import pytest

from corpus_builder.text_utils import (
    canonical_url,
    detect_language,
    estimate_quality,
    normalize_text,
    normalize_yo,
    shingles,
    text_sha1,
)


def test_normalize_text_basic():
    assert normalize_text("  hello   world  ") == "hello world"


def test_normalize_text_zero_width():
    s = "hello\u200bworld"
    assert normalize_text(s) == "hello world"


def test_normalize_text_multiple_newlines():
    assert normalize_text("a\n\n\n\nb") == "a\n\nb"


def test_normalize_text_nfkc():
    # Полноширинные латинские буквы → обычные
    s = "Ｈｅｌｌｏ"
    assert normalize_text(s) == "Hello"


def test_normalize_text_trailing_ws():
    s = "line1   \nline2\t\n"
    out = normalize_text(s)
    assert "   " not in out
    assert "\t" not in out


def test_normalize_yo():
    assert normalize_yo("ёлка Ёлка") == "елка Елка"
    assert normalize_yo("hello", lang="en") == "hello"


def test_text_sha1_stable():
    a = text_sha1("hello")
    b = text_sha1("hello")
    assert a == b
    assert a != text_sha1("world")


def test_text_sha1_normalized():
    # SHA1 от нормализованного текста должен совпадать
    a = text_sha1(normalize_text("  hello  "))
    b = text_sha1(normalize_text("hello"))
    assert a == b


def test_canonical_url_removes_utm():
    url = "https://example.com/path?utm_source=email&id=42&utm_medium=newsletter"
    canon = canonical_url(url)
    assert "utm_" not in canon
    assert "id=42" in canon


def test_canonical_url_removes_fragment():
    assert canonical_url("https://x.com/page#section") == "https://x.com/page"


def test_shingles_basic():
    out = shingles("the quick brown fox", k=2)
    assert "the quick" in out
    assert "quick brown" in out
    assert "brown fox" in out


def test_shingles_short():
    out = shingles("hi", k=5)
    assert len(out) == 1
    assert "hi" in out


def test_estimate_quality_normal_text():
    text = "This is a normal English sentence with enough words. " * 20
    metrics = estimate_quality(text)
    assert metrics["chars"] > 100
    assert metrics["alpha_ratio"] > 0.7
    assert metrics["dup_line_ratio"] < 0.2


def test_estimate_quality_dup_lines():
    text = "\n".join(["same line"] * 10)
    metrics = estimate_quality(text)
    assert metrics["dup_line_ratio"] > 0.8


def test_estimate_quality_empty():
    metrics = estimate_quality("")
    assert metrics["chars"] == 0


def test_detect_language_russian():
    assert detect_language("Привет мир, это тестовый текст на русском языке") == "ru"


def test_detect_language_english():
    assert detect_language("Hello world, this is a test text in English") == "en"


def test_detect_language_mixed():
    lang = detect_language("Hello Привет world мир текст")
    assert lang in ("mixed", "ru", "en")


def test_detect_language_empty():
    assert detect_language("") is None
