"""Экспорт финального корпуса в разные форматы:
  - HuggingFace dataset (load_dataset-совместимый JSONL + loading script)
  - Parquet (сжатый, колонный формат)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger(__name__)


def _iter_jsonl(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def export_huggingface(corpus_file: str | Path, output_dir: str | Path) -> dict:
    """Экспортировать корпус как HuggingFace dataset.

    Структура:
      output_dir/
      ├── data.jsonl             # сама выборка
      ├── dataset_infos.json     # метаданные
      ├── README.md              # карточка датасета
      └── loading_script.py      # загрузчик (опц., для datasets.load_dataset)
    """
    corpus_file = Path(corpus_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Копируем корпус под именем data.jsonl
    target_jsonl = output_dir / "data.jsonl"
    shutil.copy(corpus_file, target_jsonl)

    # Метаданные
    sample = next(_iter_jsonl(corpus_file), {}) or {}
    features = {
        "source_url": {"_type": "Value", "dtype": "string"},
        "source_type": {"_type": "Value", "dtype": "string"},
        "content": {"_type": "Value", "dtype": "string"},
        "content_sha1": {"_type": "Value", "dtype": "string"},
        "language": {"_type": "Value", "dtype": "string"},
        "license": {"_type": "Value", "dtype": "string"},
        "quality_score": {"_type": "Value", "dtype": "float64"},
        "categories": {"_type": "Sequence", "feature": {"_type": "Value", "dtype": "string"}},
        "date_accessed": {"_type": "Value", "dtype": "string"},
        "is_duplicate": {"_type": "Value", "dtype": "bool"},
    }
    infos = {
        "corpus_builder": {
            "description": "Corpus built by corpus-builder for LLM pretraining",
            "citation": "",
            "homepage": "https://github.com/draco74-glitch/corpus_builder",
            "license": "mixed (see per-record 'license' field)",
            "features": features,
            "builder_name": "corpus_builder",
            "config_name": "default",
            "version": {"version_str": "0.2.0", "major": 0, "minor": 2, "patch": 0},
        }
    }
    (output_dir / "dataset_infos.json").write_text(
        json.dumps(infos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Карточка датасета (README.md в формате HF)
    readme = """---
language:
  - ru
  - en
license: other
task_categories:
  - text-generation
size_categories:
  - 1K<n<10K
---

# Corpus Builder dataset

This dataset was produced by [corpus-builder](https://github.com/draco74-glitch/corpus_builder).

## Loading

```python
from datasets import load_dataset

dataset = load_dataset("json", data_files="data.jsonl")
print(dataset)
```

## Fields

- `source_url` — original URL
- `source_type` — html / pdf / github_repo / stackexchange
- `content` — extracted normalized text
- `content_sha1` — sha1 hash for dedup tracking
- `language` — ru / en / mixed
- `license` — per-record license (CC BY-SA 4.0 for SE, repo license for GitHub)
- `quality_score` — 0..1 metric
- `categories` — user-defined tags
- `is_duplicate` — whether this was flagged as duplicate (kept for reference)
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    n = sum(1 for _ in _iter_jsonl(target_jsonl))
    log.info(f"Exported HuggingFace dataset: {n} records → {output_dir}")
    return {"records": n, "path": str(output_dir)}


def export_parquet(corpus_file: str | Path, output_file: str | Path) -> dict:
    """Экспортировать корпус в Parquet (сжатый, колонный формат)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    records = list(_iter_jsonl(corpus_file))

    # Приводим к однородной схеме
    cols = {
        "source_url": [r.get("source_url", "") for r in records],
        "source_type": [r.get("source_type", "") for r in records],
        "content": [r.get("content", "") for r in records],
        "content_sha1": [r.get("content_sha1", "") for r in records],
        "language": [r.get("language") or "" for r in records],
        "license": [r.get("license") or "" for r in records],
        "quality_score": [r.get("quality_score") or 0.0 for r in records],
        "is_duplicate": [bool(r.get("is_duplicate")) for r in records],
        "categories": [r.get("categories") or [] for r in records],
        "date_accessed": [r.get("date_accessed", "") for r in records],
    }

    table = pa.table(cols)
    pq.write_table(table, output_file, compression="zstd")
    size = output_file.stat().st_size
    log.info(f"Exported Parquet: {len(records)} records, {size} bytes → {output_file}")
    return {"records": len(records), "size_bytes": size, "path": str(output_file)}


def compute_statistics(corpus_file: str | Path) -> dict:
    """Собрать расширенную статистику по корпусу для графиков и сводок."""
    corpus_file = Path(corpus_file)
    if not corpus_file.exists():
        return {"total": 0}

    by_type: dict[str, int] = {}
    by_language: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_license: dict[str, int] = {}
    by_date: dict[str, int] = {}
    quality_scores: list[float] = []
    content_lengths: list[int] = []
    duplicates = 0
    total = 0
    total_chars = 0

    for r in _iter_jsonl(corpus_file):
        total += 1
        st = r.get("source_type", "unknown")
        by_type[st] = by_type.get(st, 0) + 1

        lang = r.get("language") or "unknown"
        by_language[lang] = by_language.get(lang, 0) + 1

        lic = r.get("license") or "unknown"
        by_license[lic] = by_license.get(lic, 0) + 1

        for cat in r.get("categories") or []:
            by_category[cat] = by_category.get(cat, 0) + 1

        date = (r.get("date_accessed") or "")[:10]
        if date:
            by_date[date] = by_date.get(date, 0) + 1

        if r.get("is_duplicate"):
            duplicates += 1

        qs = r.get("quality_score")
        if qs is not None:
            quality_scores.append(float(qs))

        clen = len(r.get("content") or "")
        content_lengths.append(clen)
        total_chars += clen

    return {
        "total": total,
        "duplicates": duplicates,
        "total_chars": total_chars,
        "avg_chars": (total_chars // total) if total else 0,
        "by_type": by_type,
        "by_language": by_language,
        "by_category": by_category,
        "by_license": by_license,
        "by_date": dict(sorted(by_date.items())),
        "quality_scores": quality_scores,
        "content_lengths": content_lengths,
    }
