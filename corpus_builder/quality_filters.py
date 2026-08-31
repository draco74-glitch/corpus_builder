"""Расширенные фильтры качества для корпуса:
  1. Perplexity-фильтр через kenlm (нужна модель .arpa или .binary)
  2. Классификатор языка через fasttext-langdetect (точнее эвристики)
  3. Токсичность/спам-фильтр — детектор явных признаков рекламы для RU/EN
  4. Соотношение код/текст — выделение code blocks из HTML-текста
"""
from __future__ import annotations

import re
from pathlib import Path

# Ленивый импорт — модули тяжёлые, не нужны для всех тестов
_fasttext_detector = None
_kenlm_model = None

# Regex для типичных признаков спама/рекламы.
#
# ВАЖНО (C1): здесь НЕ должно быть правила «длинный текст без технических
# слов = спам». Такое правило совпадало с ЛЮБЫМ текстом длиннее 200 символов
# (`.` без DOTALL не видит `\n`, а `{200,}$` матчит любой достаточно длинный
# хвост), и фильтр качества с дефолтным `spam_check: true` молча удалял из
# корпуса весь нелектронный текст — включая страницы про I2C и резисторы,
# если в них не встречалось слово из списка «технических». Спам обязан
# определяться по ЯВНЫМ признакам рекламы, а не по их отсутствию.
_SPAM_PATTERNS: dict[str, str] = {
    "commercial": r"\b(куп[иу][а-я]{0,3}|заказыв?а[яе]?м?\w*|скидк[аи]|акци[яи]|"
                  r"распродаж[аи]|промокод|купон|дешевл[её]?\w*)\b",
    "giveaway": r"\b(выигрыш|выигра[йя]\w*|розыгрыш|"
                r"бесплатн\w+\s+(подарок|доставк|сертификат))\b",
    "financial_spam": r"\b(кредит[ыаое]?|займ[ауы]?|микрозайм\w*|взять\s+в\s+долг)\b",
    "gambling": r"\b(казино|беттинг|букмекер|ставк[аи]\s+на\s+спорт)\b",
    "link_farm": r"(https?://\S+\s+){5,}",  # 5+ URL подряд — явная мусорная страница
}
_SPAM_RE: dict[str, re.Pattern[str]] = {
    name: re.compile(p, re.IGNORECASE) for name, p in _SPAM_PATTERNS.items()
}

# Код-блоки в тексте (markdown + bbcode + простой <pre>)
_CODE_RE = re.compile(
    r"```[a-zA-Z0-9]*\n(.*?)```|"  # markdown ```
    r"\[code\](.*?)\[/code\]|"      # bbcode [code]
    r"<pre>(.*?)</pre>",            # html <pre>
    re.DOTALL | re.IGNORECASE,
)

# Минимальная длина блока кода — короче 50 символов это, скорее всего, не код
MIN_CODE_BLOCK_LEN = 50


# ============================================================
# Языковая детекция через fasttext-langdetect
# ============================================================

def detect_language_fasttext(text: str) -> str | None:
    """Определить язык через fasttext-langdetect (точнее эвристики).

    Возвращает ISO 639-1 код ('ru', 'en', 'de', 'fr', ...) или None.
    """
    global _fasttext_detector
    if not text or len(text.strip()) < 20:
        return None
    try:
        if _fasttext_detector is None:
            from ftlangdetect import detect as _detect
            _fasttext_detector = _detect
        result = _fasttext_detector(text.replace("\n", " "))
        if isinstance(result, list) and result:
            top = result[0]
            return top.get("lang")
        if isinstance(result, dict):
            return result.get("lang")
    except ImportError:
        return None
    except Exception:
        return None
    return None


# ============================================================
# Perplexity-фильтр через kenlm
# ============================================================

def load_kenlm_model(model_path: str | Path) -> bool:
    """Загрузить kenlm-модель (.binary или .arpa).

    Модель нужно скачать отдельно — например, с:
      - https://fasttext.cc/docs/en/language-identification.html (язык)
      - https://github.com/kpu/kenlm (самодельная на вашем корпусе)

    Без модели perplexity-фильтр работать не будет, остальное работает.
    """
    global _kenlm_model
    try:
        import kenlm
        _kenlm_model = kenlm.Model(str(model_path))
        return True
    except ImportError:
        return False
    except Exception:
        return False


def compute_perplexity(text: str) -> float | None:
    """Вычислить perplexity текста через kenlm.

    Низкое perplexity = естественный язык (хороший текст).
    Высокое = мусор/битый текст/таблицы/код.

    Возвращает None, если модель не загружена.
    """
    if _kenlm_model is None:
        return None
    if not text or len(text.strip()) < 50:
        return None
    try:
        # Нормализуем: убираем переносы строк, оставляем буквенно-цифровые токены
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return None
        normalized = " ".join(tokens)
        n_tokens = len(tokens)
        score = _kenlm_model.score(normalized)
        # Переводим log10 probability → perplexity: ppl = 10^(-score / n_tokens)
        ppl = 10 ** (-score / n_tokens)
        return float(ppl)
    except Exception:
        return None


def is_text_perplexity_ok(text: str, max_ppl: float = 1000.0) -> bool:
    """Возвращает True, если perplexity в норме (или если модель не загружена)."""
    ppl = compute_perplexity(text)
    if ppl is None:
        return True  # Модель не загружена — пропускаем проверку
    return ppl <= max_ppl


# ============================================================
# Токсичность/спам-фильтр
# ============================================================

# Слова, наличие которых делает текст «техническим». Спам-фильтр срабатывает
# только на тексты без единого такого слова (см. is_spam_or_low_quality).
DEFAULT_TECHNICAL_KEYWORDS = [
    "electronics", "circuit", "schematic", "datasheet", "microcontroller",
    "component", "resistor", "capacitor", "voltage", "current", "signal",
    "транзистор", "микросхема", "схема", "печатная плата", "питание",
    "компонент", "сопротивление", "ёмкость", "индуктивность", "напряжение",
    "сигнал", "усилитель", "датчик", "резистор", "конденсатор",
]


def spam_reason(text: str, technical_keywords: list[str] | None = None) -> str | None:
    """Вернуть имя сработавшего спам-паттерна или None.

    Нужно для честной статистики отказа: без причины отбраковки
    `rejected_by_reason` превращается в «unknown», и дефекты не видно (C1).
    """
    if not text or len(text.strip()) < 30:
        return None      # короткие тексты не рассматриваем

    keywords = technical_keywords or DEFAULT_TECHNICAL_KEYWORDS
    text_lower = text.lower()

    # Если текст содержит техническое слово — не считаем спамом
    if any(kw.lower() in text_lower for kw in keywords):
        return None

    for name, pattern in _SPAM_RE.items():
        if pattern.search(text):
            return name
    return None


def is_spam_or_low_quality(text: str, technical_keywords: list[str] | None = None) -> bool:
    """Простой эвристический детектор спама/рекламы.

    Возвращает True только при ЯВНОМ признаке рекламы: коммерческие клише,
    финансовые/игровые слова или «ссылочная ферма».

    Дополнительный список technical_keywords: если хоть одно встречается —
    текст считается техническим и не помечается как спам (в datasheet'ах
    бывает «buy»/«заказать» в разделе поставщиков).
    """
    return spam_reason(text, technical_keywords) is not None


# ============================================================
# Соотношение код/текст и извлечение блоков кода
# ============================================================

def extract_code_blocks(text: str) -> tuple[str, list[dict]]:
    """Извлечь блоки кода из текста (markdown/bbcode/html).

    Возвращает (prose_text, code_blocks):
      - prose_text: текст без блоков кода
      - code_blocks: список {language, code, char_count}
    """
    code_blocks: list[dict] = []
    # Фикс: ищем markdown с языком и без отдельно, чтобы корректно извлечь language
    md_pattern = re.compile(r"```([a-zA-Z0-9]*)\n(.*?)```", re.DOTALL)
    html_pattern = re.compile(r"<pre>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
    bbcode_pattern = re.compile(r"\[code\](.*?)\[/code\]", re.DOTALL | re.IGNORECASE)

    def replace_md(m: re.Match) -> str:
        language = (m.group(1) or "unknown").lower()
        code = m.group(2).strip()
        if len(code) >= MIN_CODE_BLOCK_LEN:
            code_blocks.append({
                "language": language,
                "code": code,
                "char_count": len(code),
            })
        return ""

    def replace_simple(m: re.Match, language: str = "unknown") -> str:
        code = m.group(1).strip()
        if len(code) >= MIN_CODE_BLOCK_LEN:
            code_blocks.append({
                "language": language,
                "code": code,
                "char_count": len(code),
            })
        return ""

    prose = md_pattern.sub(replace_md, text)
    prose = html_pattern.sub(replace_simple, prose)
    prose = bbcode_pattern.sub(replace_simple, prose)

    # Сжать множественные пустые строки, оставшиеся после вырезания
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()
    return prose, code_blocks


def compute_code_text_ratio(text: str,
                            blocks: list[dict] | None = None) -> float:
    """Доля кода в тексте (0.0 — нет кода, 1.0 — только код).

    `blocks` — уже извлечённые блоки: без них текст сканируется регулярками
    ВТОРОЙ раз подряд (A3: двойной проход на каждую запись корпуса).
    """
    if not text:
        return 0.0
    if blocks is None:
        _, blocks = extract_code_blocks(text)
    code_chars = sum(b["char_count"] for b in blocks)
    total_chars = len(text)
    if total_chars == 0:
        return 0.0
    return code_chars / total_chars


# ============================================================
# Сводная функция оценки качества
# ============================================================

def evaluate_quality(
    text: str,
    min_chars: int = 200,
    max_chars: int = 200_000,
    max_non_alpha_ratio: float = 0.30,
    max_dup_line_ratio: float = 0.50,
    max_code_ratio: float = 0.50,
    max_perplexity: float = 1000.0,
    spam_check: bool = True,
    language_check: bool = True,
    languages_allowed: list[str] | None = None,
    perplexity_check: bool = False,  # opt-in — требует kenlm модель
) -> dict:
    """Полная оценка качества текста.

    Возвращает словарь:
      {
        passed: bool,
        metrics: {chars, alpha_ratio, dup_line_ratio, code_ratio, language, perplexity},
        rejection_reason: str | None,   # первая сработавшая проверка
        rejection_reasons: list[str],   # все сработавшие проверки
        code_blocks: list[dict],        # извлечённые блоки кода
      }
    """
    from .text_utils import estimate_quality  # ленивый импорт

    if languages_allowed is None:
        languages_allowed = ["ru", "en"]

    # Базовые метрики из text_utils
    base = estimate_quality(text)
    prose_text, code_blocks = extract_code_blocks(text)
    code_ratio = compute_code_text_ratio(text, code_blocks)   # A3: без второго прохода
    language = None
    if language_check:
        language = detect_language_fasttext(text)
        if language is None:
            # Fallback на эвристику
            from .text_utils import detect_language
            language = detect_language(text)

    perplexity = None
    if perplexity_check:
        perplexity = compute_perplexity(text)

    metrics = {
        **base,
        "code_ratio": round(code_ratio, 3),
        "language": language,
        "perplexity": round(perplexity, 2) if perplexity is not None else None,
    }

    # Проверки: собираем ВСЕ причины (см. C1 — «unknown» в отчёте скрывал,
    # что корпус отсеяли не длиной и не языком).
    rejection_reasons: list[str] = []

    if metrics["chars"] < min_chars:
        rejection_reasons.append("too_short")
    if metrics["chars"] > max_chars:
        rejection_reasons.append("too_long")
    if metrics["alpha_ratio"] < (1 - max_non_alpha_ratio):
        rejection_reasons.append("low_alpha")
    if metrics["dup_line_ratio"] > max_dup_line_ratio:
        rejection_reasons.append("too_many_dup_lines")
    if code_ratio > max_code_ratio:
        rejection_reasons.append("too_much_code")
    if spam_check:
        spam = spam_reason(text)
        if spam:
            rejection_reasons.append(f"spam:{spam}")
    if language_check and language and language not in languages_allowed:
        rejection_reasons.append(f"wrong_language:{language}")
    if perplexity_check and perplexity is not None and perplexity > max_perplexity:
        rejection_reasons.append("high_perplexity")

    rejection_reason = rejection_reasons[0] if rejection_reasons else None

    return {
        "passed": rejection_reason is None,
        "metrics": metrics,
        "rejection_reason": rejection_reason,
        "rejection_reasons": rejection_reasons,
        "code_blocks": code_blocks,
    }
