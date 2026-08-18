"""Фильтры качества для fine-tuning пар."""
from __future__ import annotations
import re

def passes_finetune_quality(pair: dict, min_prompt: int = 20, max_prompt: int = 8000,
                            min_completion: int = 20, max_completion: int = 16000) -> tuple[bool, str]:
    """Проверить пару {prompt, completion} на качество."""
    prompt = pair.get("prompt", "")
    completion = pair.get("completion", "")

    if len(prompt) < min_prompt:
        return False, "prompt_too_short"
    if len(prompt) > max_prompt:
        return False, "prompt_too_long"
    if len(completion) < min_completion:
        return False, "completion_too_short"
    if len(completion) > max_completion:
        return False, "completion_too_long"

    # Проверка на дубликат prompt == completion
    if prompt.strip() == completion.strip():
        return False, "prompt_equals_completion"

    # Проверка на мусор (только спецсимволы)
    alpha_count = sum(1 for c in prompt if c.isalpha())
    if alpha_count < len(prompt) * 0.3:
        return False, "low_alpha_prompt"

    return True, "ok"

def filter_pairs(pairs: list[dict], **kwargs) -> tuple[list[dict], dict]:
    """Отфильтровать пары. Возвращает (kept, stats)."""
    kept = []
    rejected: dict[str, int] = {}
    for pair in pairs:
        ok, reason = passes_finetune_quality(pair, **kwargs)
        if ok:
            kept.append(pair)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    return kept, {"total": len(pairs), "kept": len(kept), "rejected": rejected}
