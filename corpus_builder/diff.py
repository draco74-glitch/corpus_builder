"""Сравнение двух корпусов (corpus.jsonl) — что нового появилось.

Использование:
    corpus-builder diff corpus_old.jsonl corpus_new.jsonl [--html report.html]

Выводит:
  - Добавленные записи (есть в new, нет в old) — по content_sha1
  - Удалённые записи (есть в old, нет в new)
  - Изменённые записи (тот же URL, но другой content_sha1)
  - Статистику: total_added, total_removed, total_changed
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

log = get_logger(__name__)


def _load_corpus(path: str | Path) -> dict[str, dict]:
    """Загрузить корпус в dict: content_sha1 → record.

    Если content_sha1 пустой — используем source_url как ключ.
    """
    records: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Ключ — content_sha1 (если есть), иначе source_url
            key = r.get("content_sha1") or r.get("source_url") or ""
            if key:
                records[key] = r
    return records


def diff_corpora(
    old_file: str | Path,
    new_file: str | Path,
    html_output: str | Path | None = None,
) -> dict:
    """Сравнить два корпуса и вернуть статистику.

    Параметры:
        old_file: путь к старому корпусу (JSONL)
        new_file: путь к новому корпусу (JSONL)
        html_output: если указан — сгенерировать HTML-отчёт

    Возвращает dict:
        {
            "added": [records],     # есть в new, нет в old
            "removed": [records],   # есть в old, нет в new
            "changed": [records],   # тот же URL, но другой content
            "stats": {
                "total_old": int,
                "total_new": int,
                "total_added": int,
                "total_removed": int,
                "total_changed": int,
            },
        }
    """
    old_file = Path(old_file)
    new_file = Path(new_file)

    if not old_file.exists():
        raise FileNotFoundError(f"Old corpus not found: {old_file}")
    if not new_file.exists():
        raise FileNotFoundError(f"New corpus not found: {new_file}")

    log.info(f"Loading old corpus: {old_file}")
    old_records = _load_corpus(old_file)
    log.info(f"Loading new corpus: {new_file}")
    new_records = _load_corpus(new_file)

    # Сравниваем
    old_keys = set(old_records.keys())
    new_keys = set(new_records.keys())

    added_keys = new_keys - old_keys
    removed_keys = old_keys - new_keys
    common_keys = old_keys & new_keys

    # Изменённые: общий URL, но разный content_sha1
    # (так как ключ — content_sha1, изменение контента = другой ключ)
    # Поэтому для changed нужно сравнивать по URL
    changed: list[dict] = []
    for key in common_keys:
        # Этот случай фактически означает "та же запись" (т.к. ключ = sha1)
        # Настоящие changed — это когда URL одинаковый, но sha1 разный
        pass

    # Сравнение по URL
    old_by_url: dict[str, dict] = {
        r.get("source_url", ""): r for r in old_records.values()
    }
    new_by_url: dict[str, dict] = {
        r.get("source_url", ""): r for r in new_records.values()
    }

    old_urls = set(old_by_url.keys())
    new_urls = set(new_by_url.keys())
    common_urls = old_urls & new_urls

    for url in common_urls:
        old_sha = old_by_url[url].get("content_sha1")
        new_sha = new_by_url[url].get("content_sha1")
        if old_sha != new_sha:
            changed.append({
                "url": url,
                "old_sha1": old_sha,
                "new_sha1": new_sha,
                "old_chars": len(old_by_url[url].get("content") or ""),
                "new_chars": len(new_by_url[url].get("content") or ""),
            })

    added = [new_records[k] for k in added_keys]
    removed = [old_records[k] for k in removed_keys]

    stats = {
        "total_old": len(old_records),
        "total_new": len(new_records),
        "total_added": len(added),
        "total_removed": len(removed),
        "total_changed": len(changed),
    }

    log.info(f"Diff done: {stats}")

    result = {
        "added": added,
        "removed": removed,
        "changed": changed,
        "stats": stats,
    }

    # Генерация HTML-отчёта
    if html_output:
        html_path = Path(html_output)
        html = _generate_html_report(result, old_file.name, new_file.name)
        html_path.write_text(html, encoding="utf-8")
        log.info(f"HTML report saved: {html_path}")

    return result


def _generate_html_report(result: dict, old_name: str, new_name: str) -> str:
    """Сгенерировать HTML-отчёт сравнения корпусов."""
    stats = result["stats"]

    # Топ-10 добавленных записей (для предпросмотра)
    added_top = result["added"][:10]
    added_rows = "\n".join([
        f"<tr><td>{r.get('source_url', '')[:80]}</td>"
        f"<td>{r.get('source_type', '')}</td>"
        f"<td>{len(r.get('content') or '')}</td>"
        f"<td>{r.get('language') or '?'}</td></tr>"
        for r in added_top
    ])

    # Топ-10 удалённых
    removed_top = result["removed"][:10]
    removed_rows = "\n".join([
        f"<tr><td>{r.get('source_url', '')[:80]}</td>"
        f"<td>{r.get('source_type', '')}</td></tr>"
        for r in removed_top
    ])

    # Изменённые
    changed_rows = "\n".join([
        f"<tr><td>{c['url'][:80]}</td>"
        f"<td>{c['old_chars']} → {c['new_chars']} chars</td>"
        f"<td>{c['old_sha1'][:8]}... → {c['new_sha1'][:8]}...</td></tr>"
        for c in result["changed"][:20]
    ])

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Corpus Diff Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        h1, h2 {{ color: #007acc; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat-card {{ background: white; padding: 15px 25px; border-radius: 8px;
                     box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-card h3 {{ margin: 0 0 5px 0; font-size: 14px; color: #555; }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; }}
        .added {{ color: #28a745; }}
        .removed {{ color: #dc3545; }}
        .changed {{ color: #ffc107; }}
        table {{ border-collapse: collapse; width: 100%; background: white;
                border-radius: 8px; overflow: hidden; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee;
                font-size: 13px; }}
        th {{ background: #007acc; color: white; }}
        tr:hover {{ background: #f8f9fa; }}
        .small {{ font-size: 11px; color: #888; }}
    </style>
</head>
<body>
    <h1>Отчёт сравнения корпусов</h1>
    <p class="small">Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p>Старый корпус: <code>{old_name}</code> ({stats['total_old']} записей)<br>
       Новый корпус: <code>{new_name}</code> ({stats['total_new']} записей)</p>

    <div class="stats">
        <div class="stat-card added">
            <h3>Добавлено</h3>
            <div class="value">+{stats['total_added']}</div>
        </div>
        <div class="stat-card removed">
            <h3>Удалено</h3>
            <div class="value">-{stats['total_removed']}</div>
        </div>
        <div class="stat-card changed">
            <h3>Изменено</h3>
            <div class="value">~{stats['total_changed']}</div>
        </div>
    </div>

    <h2>Добавленные записи (топ-10)</h2>
    <table>
        <thead>
            <tr><th>URL</th><th>Тип</th><th>Длина</th><th>Язык</th></tr>
        </thead>
        <tbody>
            {added_rows or '<tr><td colspan="4">Нет данных</td></tr>'}
        </tbody>
    </table>

    <h2>Удалённые записи (топ-10)</h2>
    <table>
        <thead>
            <tr><th>URL</th><th>Тип</th></tr>
        </thead>
        <tbody>
            {removed_rows or '<tr><td colspan="2">Нет данных</td></tr>'}
        </tbody>
    </table>

    <h2>Изменённые записи (топ-20)</h2>
    <table>
        <thead>
            <tr><th>URL</th><th>Размер</th><th>SHA1</th></tr>
        </thead>
        <tbody>
            {changed_rows or '<tr><td colspan="3">Нет данных</td></tr>'}
        </tbody>
    </table>
</body>
</html>"""


# Ленивый импорт datetime для HTML-отчёта
from datetime import datetime
