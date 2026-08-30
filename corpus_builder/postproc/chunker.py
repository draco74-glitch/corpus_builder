"""Разделение длинных текстов на чанки для fine-tuning."""
from __future__ import annotations

import re

# Regex to match fenced code blocks (```...```)
# Used to protect them from being split mid-block.
_CODE_BLOCK_RE = re.compile(r'```(\w*)\n.*?```', re.DOTALL)


def _extract_code_blocks(text: str) -> tuple[str, list[str]]:
    """Replace code blocks with placeholders, return (text_with_placeholders, blocks).

    This protects code blocks from being split by the chunker. Each code
    block is replaced with a unique placeholder like __CODE_BLOCK_0__ that
    won't be broken by sentence/paragraph splitting.
    """
    blocks: list[str] = []

    def _replace(m: re.Match) -> str:
        blocks.append(m.group(0))
        return f"__CODE_BLOCK_{len(blocks) - 1}__"

    text_with_placeholders = _CODE_BLOCK_RE.sub(_replace, text)
    return text_with_placeholders, blocks


def _restore_code_blocks(text: str, blocks: list[str]) -> str:
    """Restore code blocks from placeholders."""
    for i, block in enumerate(blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)
    return text


def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    """Разделить текст на чанки, сохраняя границы абзацев и code blocks.

    Algorithm:
      1. Extract fenced code blocks (```...```), replace with placeholders.
      2. Split by paragraphs (\\n\\n) first.
      3. Greedily pack paragraphs into a chunk until adding the next would
         exceed max_chars.
      4. If a single paragraph is longer than max_chars, split it further by
         sentences (preserving sentence boundaries).
      5. Overlap: when starting a new chunk, optionally carry the last
         `overlap` chars of the previous chunk as context.
      6. Restore code blocks from placeholders.
    """
    if len(text) <= max_chars:
        return [text]

    # Protect code blocks from being split
    text_with_placeholders, code_blocks = _extract_code_blocks(text)

    chunks_with_placeholders = _chunk_text_raw(text_with_placeholders, max_chars, overlap)

    # Restore code blocks in each chunk
    chunks = [_restore_code_blocks(c, code_blocks) for c in chunks_with_placeholders]
    return chunks


def _chunk_text_raw(text: str, max_chars: int, overlap: int) -> list[str]:
    """Internal chunker that operates on text with code blocks already extracted."""
    chunks: list[str] = []
    # Split on double-newline paragraph boundaries, keep separator-aware
    paragraphs = re.split(r'(\n\n+)', text)
    # Re-join paragraph + its separator so we can pack correctly
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
    Code blocks are protected from being split mid-block.
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
