"""Фильтры качества для fine-tuning пар."""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter

from .token_utils import count_tokens


def passes_finetune_quality(pair: dict, min_prompt: int = 20, max_prompt: int = 8000,
                            min_completion: int = 20, max_completion: int = 16000) -> tuple[bool, str]:
    """Проверить пару {prompt, completion} на качество.

    Lengths are in CHARACTERS (legacy API). For token-based limits use
    passes_token_limits from token_utils.py.
    """
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


# ============================================================
# Deduplication
# ============================================================

def _pair_hash(pair: dict, mode: str = "prompt+completion") -> str:
    """Compute a content hash for a pair.

    Modes:
        "prompt+completion"  — full pair (default; strictest)
        "prompt"             — same prompt anywhere → duplicate
        "completion"         — same completion anywhere → duplicate
        "prompt_normalized"  — prompt lowercased + whitespace-collapsed

    For multi-turn pairs (those with a 'conversation' field), the
    conversation is included in the hash so that two pairs with the same
    first question + last answer but DIFFERENT middle turns are NOT
    considered duplicates.
    """
    # For multi-turn pairs with a conversation field, always include the
    # conversation in the hash (regardless of mode) so that structurally
    # different conversations are not collapsed.
    conv = pair.get("conversation")
    has_conversation = conv and isinstance(conv, list) and len(conv) >= 2

    if mode == "prompt+completion":
        text = pair.get("prompt", "") + "\n@@@\n" + pair.get("completion", "")
        if has_conversation:
            # Include the full conversation to distinguish multi-turn pairs
            # that share the same first/last turn but differ in the middle.
            text += "\n@@@\n" + json.dumps(conv, ensure_ascii=False, sort_keys=True)
    elif mode == "prompt":
        text = pair.get("prompt", "")
        if has_conversation:
            text += "\n@@@\n" + json.dumps(conv, ensure_ascii=False, sort_keys=True)
    elif mode == "completion":
        text = pair.get("completion", "")
        if has_conversation:
            text += "\n@@@\n" + json.dumps(conv, ensure_ascii=False, sort_keys=True)
    elif mode == "prompt_normalized":
        # Collapse whitespace, lowercase — catches near-duplicates that
        # differ only in capitalization or line endings.
        text = re.sub(r"\s+", " ", pair.get("prompt", "")).strip().lower()
        if has_conversation:
            text += "\n@@@\n" + json.dumps(conv, ensure_ascii=False, sort_keys=True)
    else:
        raise ValueError(f"Unknown dedup mode: {mode}")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def dedup_pairs(
    pairs: list[dict],
    mode: str = "prompt+completion",
    keep: str = "first",
) -> tuple[list[dict], dict]:
    """Remove duplicate pairs by content hash.

    Args:
        pairs: list of {prompt, completion, ...} dicts
        mode: hash mode — see _pair_hash docs
        keep: "first" (default) or "last" — which duplicate to keep

    Returns:
        (deduped_pairs, stats) where stats = {
            "input": N, "kept": M, "removed": N-M, "duplicates": K
        }
    """
    if not pairs:
        return [], {"input": 0, "kept": 0, "removed": 0, "duplicates": 0}

    seen: dict[str, int] = {}  # hash → first index where seen
    deduped: list[dict] = []

    if keep == "first":
        for pair in pairs:
            h = _pair_hash(pair, mode)
            if h in seen:
                # Duplicate — skip but count
                seen[h] += 1
                continue
            seen[h] = 1
            deduped.append(pair)
    elif keep == "last":
        # Walk in reverse, keep first occurrence (= last in original order)
        for pair in reversed(pairs):
            h = _pair_hash(pair, mode)
            if h in seen:
                seen[h] += 1
                continue
            seen[h] = 1
            deduped.append(pair)
        deduped.reverse()
    else:
        raise ValueError(f"keep must be 'first' or 'last', got {keep!r}")

    duplicates = sum(v - 1 for v in seen.values() if v > 1)
    stats = {
        "input": len(pairs),
        "kept": len(deduped),
        "removed": len(pairs) - len(deduped),
        "duplicates": duplicates,
        "mode": mode,
    }
    return deduped, stats


# ============================================================
# Combined: filter + dedup in one pass
# ============================================================

def filter_and_dedup_pairs(
    pairs: list[dict],
    dedup_mode: str = "prompt+completion",
    **filter_kwargs,
) -> tuple[list[dict], dict]:
    """Filter by quality, then dedup. Returns (kept, stats).

    Stats include both filter and dedup counts so the UI can show
    "filtered N, then removed M duplicates".
    """
    filtered, f_stats = filter_pairs(pairs, **filter_kwargs)
    deduped, d_stats = dedup_pairs(filtered, mode=dedup_mode)
    stats = {
        "filter": f_stats,
        "dedup": d_stats,
        "total_kept": len(deduped),
        "total_removed": len(pairs) - len(deduped),
    }
    return deduped, stats
