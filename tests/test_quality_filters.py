"""Тесты на расширенные фильтры качества (Этап 1)."""

from corpus_builder.quality_filters import (
    compute_code_text_ratio,
    compute_perplexity,
    detect_language_fasttext,
    evaluate_quality,
    extract_code_blocks,
    is_spam_or_low_quality,
    load_kenlm_model,
)

# ============================================================
# Тесты на языковую детекцию через fasttext-langdetect
# ============================================================

def test_detect_language_english():
    text = "This is a normal English text about electronics and circuits. " * 5
    lang = detect_language_fasttext(text)
    assert lang == "en"


def test_detect_language_russian():
    text = "Привет мир это тестовый текст на русском языке про электронику и схемы. " * 5
    lang = detect_language_fasttext(text)
    assert lang == "ru"


def test_detect_language_short_text_returns_none():
    """Слишком короткий текст — fasttext не определит."""
    lang = detect_language_fasttext("hi")
    assert lang is None


# ============================================================
# Тесты на спам-фильтр
# ============================================================

def test_spam_filter_clean_technical_text():
    text = "This article describes the operational amplifier circuit design. " * 5
    assert not is_spam_or_low_quality(text)


def test_spam_filter_buy_now():
    text = "Купите сейчас! Скидка 50% только сегодня. Закажите доставку."
    assert is_spam_or_low_quality(text)


def test_spam_filter_keywords_override():
    """Текст со словом 'купить' должен пройти, если есть техническое слово."""
    text = "Купите операционный усилитель для вашей схемы. Это качественный компонент."
    # Хотя есть 'купите', есть и 'усилитель'/'схемы'/'компонент' — технический текст
    assert not is_spam_or_low_quality(text, technical_keywords=["усилитель", "схема"])


def test_spam_filter_short_text_skipped():
    """Короткие тексты не проверяются на спам."""
    assert not is_spam_or_low_quality("Привет")


def test_spam_filter_too_many_urls():
    text = (
        "Some text http://example1.com http://example2.com http://example3.com "
        "http://example4.com http://example5.com http://example6.com and more text"
    )
    assert is_spam_or_low_quality(text)


# ============================================================
# Тесты на извлечение блоков кода
# ============================================================

def test_extract_markdown_code_block():
    # Код длиннее 50 символов, иначе не извлекается
    text = (
        "Some prose\n"
        "```python\n"
        "def calculate_voltage(current, resistance):\n"
        "    return current * resistance\n"
        "```\n"
        "More prose"
    )
    prose, blocks = extract_code_blocks(text)
    assert "calculate_voltage" not in prose
    assert len(blocks) == 1
    assert blocks[0]["language"] == "python"
    assert "calculate_voltage" in blocks[0]["code"]


def test_extract_html_pre_block():
    text = (
        "Some prose\n<pre>\n"
        "def hello_world():\n"
        "    print('Hello, World!')\n"
        "    return True\n"
        "</pre>\nMore"
    )
    prose, blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert "def hello_world" in blocks[0]["code"]


def test_extract_bbcode_block():
    text = (
        "Some prose\n[code]\n"
        "for i in range(10):\n"
        "    print(f'Iteration {i}')\n"
        "    result = i * 2\n"
        "[/code]\nMore"
    )
    prose, blocks = extract_code_blocks(text)
    assert len(blocks) == 1
    assert "for i in range" in blocks[0]["code"]


def test_short_code_block_ignored():
    """Код короче 50 символов не извлекается."""
    text = "Some prose\n```python\nx=1\n```\nMore"
    _, blocks = extract_code_blocks(text)
    assert len(blocks) == 0


def test_compute_code_ratio_no_code():
    text = "This is just prose without any code blocks at all."
    assert compute_code_text_ratio(text) == 0.0


def test_compute_code_ratio_all_code():
    text = "```python\n" + "x = 1\n" * 30 + "```"
    ratio = compute_code_text_ratio(text)
    assert ratio > 0.8


# ============================================================
# Тесты на evaluate_quality (сводная функция)
# ============================================================

def test_evaluate_quality_passes_technical_text():
    text = (
        "This is a technical article about electronics and circuits. "
        "The operational amplifier is a key component. " * 20
    )
    result = evaluate_quality(text, language_check=True, languages_allowed=["en", "ru"])
    assert result["passed"]
    assert result["rejection_reason"] is None
    assert result["metrics"]["language"] == "en"


def test_evaluate_quality_rejects_too_short():
    result = evaluate_quality("short", min_chars=200)
    assert not result["passed"]
    assert result["rejection_reason"] == "too_short"


def test_evaluate_quality_rejects_too_much_code():
    # Длинный текст с большим количеством разного кода (не повторяющегося)
    code_block = (
        "```python\n"
        "def func_{i}(x, y):\n"
        "    result = x * y + {i}\n"
        "    return result * 2\n"
        "```\n"
    )
    text = "".join(code_block.format(i=i) for i in range(20))
    text += "Some technical prose about electronics and circuits. " * 10
    result = evaluate_quality(text, max_code_ratio=0.5, min_chars=10,
                              max_non_alpha_ratio=0.8,
                              max_dup_line_ratio=0.9)  # ослабляем dup-фильтр для теста
    assert not result["passed"]
    assert result["rejection_reason"] == "too_much_code"


def test_evaluate_quality_rejects_wrong_language():
    text = "Это текст на русском языке про электронику и схемы. " * 20
    result = evaluate_quality(text, language_check=True, languages_allowed=["en"])
    assert not result["passed"]
    assert "wrong_language" in (result["rejection_reason"] or "")


def test_evaluate_quality_with_code_blocks_extraction():
    """Блоки кода извлекаются и сохраняются в результате."""
    text = (
        "This is an article about electronics. " * 5 +
        "```python\nimport numpy as np\nx = np.array([1, 2, 3])\nprint(x)\n```\n" +
        "More prose about circuits. " * 5
    )
    result = evaluate_quality(text)
    # Должен пройти (текст технический, длина достаточная)
    assert result["passed"]
    assert len(result["code_blocks"]) >= 1


# ============================================================
# Тесты на perplexity (если kenlm не установлен — пропускаем)
# ============================================================

def test_perplexity_without_model_returns_none():
    """Если kenlm модель не загружена — perplexity возвращает None."""
    # Перед тестом убедимся, что модель не загружена
    import corpus_builder.quality_filters as qf
    qf._kenlm_model = None
    assert compute_perplexity("some text") is None


def test_load_kenlm_model_nonexistent_file():
    """Загрузка несуществующей модели возвращает False."""
    result = load_kenlm_model("/nonexistent/model.binary")
    assert result is False
