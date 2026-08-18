"""Разделение длинных текстов на чанки для fine-tuning."""
from __future__ import annotations
import re
from typing import Iterator


def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    """Разделить текст на чанки, сохраняя границы абзацев где возможно.

    Algorithm:
      1. Split by paragraphs (\\n\\n) first.
      2. Greedily pack paragraphs into a chunk until adding the next would
         exceed max_chars.
      3. If a single paragraph is longer than max_chars, split it further by
         sentences (preserving sentence boundaries).
      4. Overlap: when starting a new chunk, optionally carry the last
         `overlap` chars of the previous chunk as context.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    # Split on double-newline paragraph boundaries, keep separator-aware
    paragraphs = re.split(r'(\n\n+)', text)
    # Re-join paragraph + its separator so we can pack correctly
    # paragraphs[0] is text, [1] is "\n\n", [2] is text, [3] is "\n\n", etc.
    units: list[str] = []
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        sep = paragraphs[i + 1] if i + 1 < len(paragraphs) else ""
        if para or sep:
            units.append(para + sep)
        i += 2

    current = ""
    for unit in units:
        # If the unit alone exceeds max_chars, split it by sentences
        if len(unit) > max_chars:
            # Flush current chunk first
            if current.strip():
                chunks.append(current.strip())
                # Overlap: carry last `overlap` chars
                if overlap > 0 and len(current) > overlap:
                    current = current[-overlap:] + " "
                else:
                    current = ""
            # Split this oversized unit by sentences
            sub_chunks = _split_by_sentences(unit, max_chars, overlap)
            for sc in sub_chunks:
                if len(current) + len(sc) > max_chars and current:
                    chunks.append(current.strip())
                    current = sc if not overlap else (current[-overlap:] + " " + sc)
                else:
                    current = (current + " " + sc).strip() if current else sc
            continue

        # Normal case: pack unit into current chunk
        if len(current) + len(unit) > max_chars and current:
            chunks.append(current.strip())
            # Overlap: carry last `overlap` chars of the previous chunk
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:] + unit
            else:
                current = unit
        else:
            current = (current + unit).strip() if not current.endswith("\n") else (current + unit)

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _split_by_sentences(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split a single long paragraph by sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) > max_chars and current:
            chunks.append(current.strip())
            if overlap > 0 and len(current) > overlap:
                # Take last ~overlap chars worth of words
                words = current.split()
                tail = " ".join(words[-max(1, overlap // 10):])
                current = tail + " " + sent
            else:
                current = sent
        else:
            current = (current + " " + sent).strip() if current else sent
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_record(record: dict, max_chars: int = 4000) -> list[dict]:
    """Разделить запись корпуса на чанки, сохраняя метаданные.

    Each chunk is a copy of the original record with `content` replaced
    by the chunk text, and `chunk_index` / `total_chunks` added.
    """
    content = record.get("content", "")
    if len(content) <= max_chars:
        return [record]

    chunks = chunk_text(content, max_chars)
    result = []
    for i, chunk in enumerate(chunks):
        r = dict(record)
        r["content"] = chunk
        r["chunk_index"] = i
        r["total_chunks"] = len(chunks)
        result.append(r)
    return result
