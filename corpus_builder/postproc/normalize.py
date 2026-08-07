"""Нормализация корпуса: применяем normalize_yo + финальная чистка."""
from __future__ import annotations

import json
from pathlib import Path

from ..logging_setup import get_logger
from ..text_utils import normalize_text, normalize_yo

log = get_logger(__name__)


def run_normalize(
    corpus_file: str | Path,
    output_file: str | Path,
    normalize_yo_enabled: bool = True,
) -> dict:
    """Финальная нормализация: NFKC + ftfy + (опц.) ё→е."""
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total = 0
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

            text = r.get("content") or ""
            text = normalize_text(text)
            if normalize_yo_enabled and r.get("language") in ("ru", "mixed", None):
                text = normalize_yo(text, lang="ru")
            r["content"] = text
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info(f"Normalized {total} records → {output_file}")
    return {"total": total}
