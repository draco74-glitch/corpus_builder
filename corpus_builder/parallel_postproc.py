"""Параллельная пост-обработка корпуса через multiprocessing."""
from __future__ import annotations

import json
from multiprocessing import Pool, cpu_count
from pathlib import Path

from .logging_setup import get_logger
from .models import QualityConfig
from .text_utils import normalize_text

log = get_logger(__name__)


def _process_chunk_worker(args):
    lines, config_dict, mode = args
    results = []
    if mode == "quality":
        cfg = QualityConfig(**config_dict)
        from .postproc.quality import passes_quality
    elif mode == "normalize":
        cfg = None
    else:
        return [(i, None) for i in range(len(lines))]

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            results.append(None)
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            results.append(None)
            continue

        if mode == "quality":
            if r.get("is_duplicate") or r.get("status") != "ok":
                results.append(None)
                continue
            text = normalize_text(r.get("content") or "")
            passed, metrics = passes_quality(text, cfg)
            if not passed:
                results.append(None)
                continue
            r["content"] = text
            r["quality_score"] = metrics.get("quality_score")
            r["language"] = metrics.get("language")
            results.append(json.dumps(r, ensure_ascii=False))
        elif mode == "normalize":
            text = normalize_text(r.get("content") or "")
            r["content"] = text
            results.append(json.dumps(r, ensure_ascii=False))

    return [(i, r) for i, r in enumerate(results) if r is not None]


def _chunk_lines(lines, chunk_size=1000):
    for i in range(0, len(lines), chunk_size):
        yield lines[i:i + chunk_size]


def run_quality_filter_parallel(corpus_file, output_file, config, workers=None, chunk_size=500):
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    workers = workers or max(1, cpu_count() - 1)
    with open(corpus_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    total = len(all_lines)
    config_dict = config.model_dump()
    chunks = list(_chunk_lines(all_lines, chunk_size))
    args_list = [(chunk, config_dict, "quality") for chunk in chunks]
    kept = 0
    with open(output_file, "w", encoding="utf-8") as fout:
        with Pool(workers) as pool:
            for chunk_result in pool.imap(_process_chunk_worker, args_list):
                for _, line_json in chunk_result:
                    if line_json:
                        fout.write(line_json + "\n")
                        kept += 1
    return {"total": total, "kept": kept, "rejected_total": total - kept, "workers": workers}


def run_normalize_parallel(corpus_file, output_file, workers=None, chunk_size=1000):
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    workers = workers or max(1, cpu_count() - 1)
    with open(corpus_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
    total = len(all_lines)
    chunks = list(_chunk_lines(all_lines, chunk_size))
    args_list = [(chunk, {}, "normalize") for chunk in chunks]
    with open(output_file, "w", encoding="utf-8") as fout:
        with Pool(workers) as pool:
            for chunk_result in pool.imap(_process_chunk_worker, args_list):
                for _, line_json in chunk_result:
                    if line_json:
                        fout.write(line_json + "\n")
    return {"total": total, "workers": workers}
