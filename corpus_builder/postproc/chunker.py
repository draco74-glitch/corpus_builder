"""Разделение длинных текстов на чанки для fine-tuning."""
from __future__ import annotations
import re
from typing import Iterator

def chunk_text(text: str, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    """Разделить текст на чанки по предложениям."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    # Split by sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current = ""
    for sent in sentences:
        if len(current) + len(sent) > max_chars and current:
            chunks.append(current.strip())
            # Overlap: take last few words
            if overlap > 0:
                words = current.split()
                current = " ".join(words[-overlap // 10:]) + " " + sent
            else:
                current = sent
        else:
            current = (current + " " + sent).strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks

def chunk_record(record: dict, max_chars: int = 4000) -> list[dict]:
    """Разделить запись корпуса на чанки, сохраняя метаданные."""
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
