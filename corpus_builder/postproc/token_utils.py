"""Token-based length estimation for fine-tuning pairs.

tiktoken is the same BPE tokenizer used by OpenAI for GPT-4 / GPT-3.5
(cl100k_base encoding). It gives a good approximation for most modern LLMs.

If tiktoken is not installed (e.g. on Python 3.13+ where wheels may be
missing), we fall back to a character-based heuristic:
    English:   ~4 chars per token
    Russian:   ~2.7 chars per token (Cyrillic chars take more BPE tokens)
    Mixed/other: ~3.5 chars per token

Usage:
    from corpus_builder.postproc.token_utils import count_tokens, estimate_tokens
    n = count_tokens("Hello world")              # tiktoken if available
    n = estimate_tokens("Hello world", lang="en")  # heuristic always
    n = count_tokens_batch(["text1", "text2"])   # batch mode (faster)
"""
from __future__ import annotations

import re
from typing import Literal

from ..logging_setup import get_logger

log = get_logger(__name__)

# Language-specific char-per-token ratios (heuristic fallback).
# Based on empirical measurement of cl100k_base BPE on technical text:
#   - English: ~4.0 chars/token (matches OpenAI's documented average)
#   - Russian: Cyrillic is less efficient in BPE → ~2.7 chars/token
#   - Chinese/Japanese/Korean: ~1.5 chars/token (each char is often 1+ tokens)
#   - Mixed/unknown: conservative ~3.5 chars/token
_CHARS_PER_TOKEN = {
    "en": 4.0,
    "ru": 2.7,
    "zh": 1.5,
    "ja": 1.5,
    "ko": 1.5,
    "mixed": 3.5,
}

# Russian Cyrillic range
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
# CJK Unified Ideographs + Hiragana + Katakana + Hangul
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _detect_lang_simple(text: str) -> Literal["en", "ru", "zh", "ja", "ko", "mixed"]:
    """Cheap language detector: count Cyrillic vs CJK vs Latin chars."""
    if not text:
        return "en"
    cyr = len(_CYRILLIC_RE.findall(text[:2000]))
    cjk = len(_CJK_RE.findall(text[:2000]))
    if cjk > 20:
        return "zh"  # approximation; could be ja/ko but heuristic ratio is same
    if cyr > 20:
        return "ru"
    return "en"


# ============================================================
# Eager encoder loading at module level (Improvement 15)
# ============================================================
# Previously _get_encoder() was called on every count_tokens() invocation,
# with @lru_cache(maxsize=1) to avoid re-loading. But lru_cache still has
# overhead per call. For 100K+ pairs, this adds up. Now we load the encoder
# ONCE at module import time (if tiktoken is available) and store it in a
# module-level variable. count_tokens() does a simple None check.
_ENCODER = None
_TIKTOKEN_AVAILABLE = False

try:
    import tiktoken as _tiktoken
    _ENCODER = _tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
    log.debug("tiktoken loaded at module import: cl100k_base")
except Exception as _e:
    _TIKTOKEN_AVAILABLE = False
    log.info(f"tiktoken unavailable ({_e}); falling back to char-based estimate")


def _get_encoder():
    """Return the module-level encoder, or None if tiktoken unavailable.

    Kept for backward compatibility with code that calls _get_encoder().
    """
    return _ENCODER


def count_tokens(text: str) -> int:
    """Return exact token count via tiktoken, or heuristic if tiktoken missing.

    This is the recommended function — it always returns a number.
    """
    if not text:
        return 0
    if _ENCODER is not None:
        return len(_ENCODER.encode(text))
    return estimate_tokens(text)


def count_tokens_batch(texts: list[str]) -> list[int]:
    """Return token counts for a list of texts.

    Uses tiktoken.encode_batch() if available (faster than calling
    encode() one-by-one for large lists), otherwise falls back to
    per-text count_tokens().
    """
    if not texts:
        return []
    if _ENCODER is not None:
        # tiktoken supports encode_batch for bulk encoding
        if hasattr(_ENCODER, "encode_batch"):
            try:
                encoded = _ENCODER.encode_batch(texts)
                return [len(e) for e in encoded]
            except Exception as e:
                log.debug(f"encode_batch failed ({e}), falling back to per-text")
        # Fallback: encode one by one (still uses cached encoder)
        return [len(_ENCODER.encode(t)) for t in texts]
    # No tiktoken — use heuristic
    return [estimate_tokens(t) for t in texts]


def estimate_tokens(text: str, lang: str | None = None) -> int:
    """Heuristic token estimate from character count.

    Use this when you explicitly want the heuristic (e.g. for fast filtering
    on huge corpora where tiktoken overhead matters).
    """
    if not text:
        return 0
    if lang is None:
        lang = _detect_lang_simple(text)
    ratio = _CHARS_PER_TOKEN.get(lang, 3.5)
    return max(1, int(len(text) / ratio))


def count_pair_tokens(pair: dict) -> dict:
    """Return {prompt_tokens, completion_tokens, total_tokens} for a pair."""
    p = count_tokens(pair.get("prompt", ""))
    c = count_tokens(pair.get("completion", ""))
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def passes_token_limits(
    pair: dict,
    min_prompt_tokens: int = 5,
    max_prompt_tokens: int = 2000,
    min_completion_tokens: int = 5,
    max_completion_tokens: int = 4000,
) -> tuple[bool, str]:
    """Check pair against token-based limits. Mirrors passes_finetune_quality API."""
    p = count_tokens(pair.get("prompt", ""))
    c = count_tokens(pair.get("completion", ""))
    if p < min_prompt_tokens:
        return False, "prompt_too_few_tokens"
    if p > max_prompt_tokens:
        return False, "prompt_too_many_tokens"
    if c < min_completion_tokens:
        return False, "completion_too_few_tokens"
    if c > max_completion_tokens:
        return False, "completion_too_many_tokens"
    return True, "ok"


def is_tiktoken_available() -> bool:
    """Public probe for tests / UI display."""
    return _TIKTOKEN_AVAILABLE
