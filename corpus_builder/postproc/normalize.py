"""Нормализация корпуса: применяем normalize_yo + финальная чистка."""
from __future__ import annotations

import json
from pathlib import Path

from typing import Callable

from ..logging_setup import get_logger
from ..text_utils import normalize_text, normalize_yo
from ..writer import CorpusWriter

log = get_logger(__name__)


def run_normalize(
    corpus_file: str | Path,
    output_file: str | Path,
    normalize_yo_enabled: bool = True,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> dict:
    """Финальная нормализация: NFKC + ftfy + (опц.) ё→е.

    Запись буферизованная (`CorpusWriter`): построчный `write()` без буфера —
    это syscall на каждую запись, что на корпусе в сотни тысяч записей
    заметно замедляло стадию.
    """
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with open(corpus_file, "r", encoding="utf-8") as fcount:
        n_records = sum(1 for line in fcount if line.strip())
    with CorpusWriter(output_file, buffer_size=512) as writer:
        with open(corpus_file, "r", encoding="utf-8") as fin:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1

                text = r.get("content") or ""
                if not r.get("content_normalized"):
                    text = normalize_text(text)
                if normalize_yo_enabled and r.get("language") in ("ru", "mixed", None):
                    text = normalize_yo(text, lang="ru")
                r["content"] = text
                r["content_normalized"] = True   # стадии ниже уже не нормализуют (A2)
                writer.write(r)
                if on_progress and total % 1000 == 0:
                    on_progress(total, n_records)

    log.info(f"Normalized {total} records → {output_file}")
    return {"total": total}
