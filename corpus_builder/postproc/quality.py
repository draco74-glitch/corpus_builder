"""Фильтрация качества: длина, соотношение букв, повторяющиеся строки, язык,
perplexity (опц.), спам/токсичность (опц.), code/text ratio."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..logging_setup import get_logger
from ..models import QualityConfig
from ..text_utils import detect_language, estimate_quality, normalize_text
from ..quality_filters import evaluate_quality, load_kenlm_model, detect_language_fasttext

log = get_logger(__name__)


def passes_quality(text: str, cfg: QualityConfig) -> tuple[bool, dict]:
    """Проверить, проходит ли текст фильтр качества.

    Использует расширенные фильтры из quality_filters:
      - fasttext-langdetect для языка (точнее эвристики)
      - kenlm perplexity, если загружена модель
      - спам-фильтр
      - code/text ratio

    Возвращает (passed, metrics).
    """
    # Используем расширенный оценщик
    languages_allowed = list(cfg.languages_allowed)
    if cfg.language == "bilingual":
        languages_allowed.append("mixed")

    result = evaluate_quality(
        text,
        min_chars=cfg.min_chars,
        max_chars=cfg.max_chars,
        max_non_alpha_ratio=cfg.max_non_alpha_ratio,
        max_dup_line_ratio=cfg.max_dup_line_ratio,
        max_code_ratio=getattr(cfg, "max_code_ratio", 0.50),
        max_perplexity=getattr(cfg, "max_perplexity", 1000.0),
        spam_check=getattr(cfg, "spam_check", True),
        language_check=cfg.language != "multi",
        languages_allowed=languages_allowed,
        perplexity_check=getattr(cfg, "perplexity_check", False),
    )

    metrics = result["metrics"]

    # Если fasttext не смог определить язык — fallback на эвристику
    if metrics.get("language") is None and cfg.language != "multi":
        metrics["language"] = detect_language(text)

    # Quality score: простая эвристика
    score = (
        0.4 * min(metrics["chars"] / 1000, 1.0)
        + 0.25 * metrics["alpha_ratio"]
        + 0.15 * (1 - metrics["dup_line_ratio"])
        + 0.10 * (1 - metrics.get("code_ratio", 0.0))
        + 0.10 * (1.0 if metrics.get("language") in (cfg.languages_allowed + ["mixed"]) else 0.0)
    )
    metrics["quality_score"] = round(score, 3)
    return result["passed"], metrics


def run_quality_filter(
    corpus_file: str | Path,
    output_file: str | Path,
    config: QualityConfig,
) -> dict:
    """Прогнать записи через фильтр качества. Дубликаты исключаются автоматически."""
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Опционально загрузить kenlm модель для perplexity-фильтра
    perplexity_model = getattr(config, "perplexity_model_path", None)
    if perplexity_model:
        if load_kenlm_model(perplexity_model):
            log.info(f"Loaded kenlm perplexity model: {perplexity_model}")
        else:
            log.warning(f"Failed to load kenlm model: {perplexity_model}")

    total = 0
    kept = 0
    rejected: dict[str, int] = {}

    with open(corpus_file, "r", encoding="utf-8") as fin, \
         open(output_file, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1

            if r.get("is_duplicate"):
                continue
            if r.get("status") != "ok":
                rejected["status_error"] = rejected.get("status_error", 0) + 1
                continue

            text = normalize_text(r.get("content") or "")
            passed, metrics = passes_quality(text, config)
            if not passed:
                # Определяем причину отклонения
                reason = "unknown"
                if metrics["chars"] < config.min_chars:
                    reason = "too_short"
                elif metrics["chars"] > config.max_chars:
                    reason = "too_long"
                elif metrics["alpha_ratio"] < (1 - config.max_non_alpha_ratio):
                    reason = "low_alpha"
                elif metrics["dup_line_ratio"] > config.max_dup_line_ratio:
                    reason = "too_many_dup_lines"
                elif metrics.get("code_ratio", 0) > getattr(config, "max_code_ratio", 0.50):
                    reason = "too_much_code"
                elif config.language != "multi" and metrics.get("language") not in (
                    config.languages_allowed + ["mixed"] if config.language == "bilingual" else []
                ):
                    reason = f"wrong_language:{metrics.get('language')}"
                elif metrics.get("perplexity") and metrics.get("perplexity", 0) > getattr(config, "max_perplexity", 1000):
                    reason = "high_perplexity"
                rejected[reason] = rejected.get(reason, 0) + 1
                continue

            r["content"] = text
            r["quality_score"] = metrics.get("quality_score")
            r["language"] = metrics.get("language")
            # Сохраняем расширенные метрики в metadata
            if "quality_metrics" not in r:
                r["quality_metrics"] = {}
            r["quality_metrics"].update({
                "code_ratio": metrics.get("code_ratio"),
                "perplexity": metrics.get("perplexity"),
            })
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1

    stats = {
        "total": total,
        "kept": kept,
        "rejected_total": total - kept,
        "rejected_by_reason": rejected,
    }
    log.info(f"Quality filter done: {stats}")
    return stats
