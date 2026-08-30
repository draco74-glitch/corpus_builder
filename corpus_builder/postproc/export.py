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
      └── dataset_infos.json     # метаданные
    Загрузчик: `load_dataset("json", data_files="data.jsonl")` — отдельный
    loading script не генерируется.
    """
    corpus_file = Path(corpus_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Копируем корпус под именем data.jsonl
    target_jsonl = output_dir / "data.jsonl"
    shutil.copy(corpus_file, target_jsonl)

    # Метаданные: описание колонок = фактическая схема JSONL (I16), см. ниже
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
        # поля, которые пишет краулер и которые нужны для пар instruction-данных
        "downloaded_files": {"_type": "Sequence", "feature": {"_type": "Value",
                                                              "dtype": "string"}},
        "metadata": {"_type": "Value", "dtype": "string"},   # JSON-строка
    }
    by_lang: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in _iter_jsonl(corpus_file):
        by_lang[r.get("language") or "unknown"] = by_lang.get(r.get("language") or "unknown", 0) + 1
        by_type[r.get("source_type") or "unknown"] = by_type.get(r.get("source_type") or "unknown", 0) + 1

    infos = {
        "corpus_builder": {
            "description": "Corpus built by corpus-builder for LLM pretraining",
            "citation": "",
            "homepage": "https://github.com/draco74-glitch/corpus_builder",
            "license": "mixed (see per-record 'license' field)",
            "features": features,
            "builder_name": "corpus_builder",
            # фактические языки/типы источников вместо hardcode ru,en (I16)
            "language": sorted(k for k in by_lang if k != "unknown"),
            "source_types": sorted(by_type),
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
{chr(10).join(f"  - {l}" for l in sorted(k for k in by_lang if k != "unknown")) or "  - unknown"}
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
- `is_duplicate` — whether this record was flagged as a duplicate

NOTE: records flagged as duplicates are NOT part of the final corpus: the
quality stage drops them. This card reflects `corpus_final.jsonl` as written
by `run_postprocess`.
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

    # Однородная схема, но БЕЗ превращения unknown в «пустое значение»:
    # "" и 0.0 читались бы ниже по потоку как настоящие данные (качество!) —
    # вместо этого nullable-колонки (I16).
    def col(key: str) -> list:
        return [r.get(key) for r in records]

    schema = pa.schema([
        ("source_url", pa.string()),
        ("source_type", pa.string()),
        ("content", pa.string()),
        ("content_sha1", pa.string()),
        ("language", pa.string()),
        ("license", pa.string()),
        ("quality_score", pa.float64()),
        ("is_duplicate", pa.bool_()),
        ("categories", pa.list_(pa.string())),
        ("date_accessed", pa.string()),
        # служебные поля, которые раньше терялись при экспорте, но нужны для
        # пар instruction-данных (KiCad-пути, таблицы, accepted_answer_id)
        ("downloaded_files", pa.string()),
        ("metadata", pa.string()),
    ])

    def json_col(key: str) -> list:
        return [json.dumps(r.get(key) or [], ensure_ascii=False) if key == "downloaded_files"
                else json.dumps(r.get(key) or {}, ensure_ascii=False) for r in records]

    cols = {
        "source_url": [r.get("source_url") for r in records],
        "source_type": [r.get("source_type") for r in records],
        "content": [r.get("content") for r in records],
        "content_sha1": [r.get("content_sha1") for r in records],
        "language": col("language"),
        "license": col("license"),
        "quality_score": col("quality_score"),
        "is_duplicate": [bool(r.get("is_duplicate")) for r in records],
        "categories": [r.get("categories") or [] for r in records],
        "date_accessed": [r.get("date_accessed") for r in records],
        "downloaded_files": json_col("downloaded_files"),
        "metadata": json_col("metadata"),
    }

    table = pa.table(cols, schema=schema)
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
