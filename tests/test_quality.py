"""Тесты на фильтр качества."""
from corpus_builder.models import QualityConfig
from corpus_builder.postproc.quality import passes_quality


def test_short_text_rejected():
    cfg = QualityConfig(min_chars=200)
    text = "too short"
    ok, metrics = passes_quality(text, cfg)
    assert not ok
    assert metrics["chars"] < 200


def test_normal_text_passed():
    cfg = QualityConfig()
    # Используем технический текст (содержит ключевые слова, чтобы пройти spam-фильтр)
    text = (
        "This is a normal English text about electronics and circuits. "
        "The operational amplifier is a key component in analog circuit design. "
        "Transistors and microcontrollers are used in embedded systems. "
    ) * 20
    ok, metrics = passes_quality(text, cfg)
    assert ok, f"Expected to pass, got rejection: {metrics}"
    assert metrics["language"] == "en"
    assert metrics["quality_score"] > 0


def test_low_alpha_rejected():
    cfg = QualityConfig(max_non_alpha_ratio=0.3)
    text = "1234567890!@#$%^&*()" * 30
    ok, metrics = passes_quality(text, cfg)
    assert not ok


def test_dup_lines_rejected():
    cfg = QualityConfig(max_dup_line_ratio=0.5)
    text = "\n".join(["same line"] * 100)
    ok, metrics = passes_quality(text, cfg)
    assert not ok


def test_bilingual_allows_russian():
    cfg = QualityConfig(language="bilingual")
    # Технический текст на русском с ключевыми словами
    text = (
        "Привет мир, это тестовый текст на русском языке для проверки электроники. "
        "Операционный усилитель — ключевой компонент в аналоговой схемотехнике. "
        "Транзисторы и микроконтроллеры используются во встроенных системах. "
    ) * 20
    ok, metrics = passes_quality(text, cfg)
    assert ok, f"Expected to pass, got rejection: {metrics}"
    assert metrics["language"] == "ru"


def test_only_russian_language():
    cfg = QualityConfig(language="ru", languages_allowed=["ru"])
    # Технический текст на английском — должен быть отклонён при language=ru
    text = (
        "This is a normal English text about electronics and circuits. "
        "The operational amplifier is a key component in analog circuit design. "
    ) * 20
    ok, _ = passes_quality(text, cfg)
    assert not ok
