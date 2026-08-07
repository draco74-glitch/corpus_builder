"""Извлечение пар для instruction-tuning.

Делаем два набора пар:
  1. README ↔ KiCad: схема из GitHub-репозитория (если в записи есть README.md и .kicad_sch)
  2. Вопрос ↔ Принятый ответ: из StackExchange-записей

Выходной формат — JSONL с полями {prompt, completion, source, task_type}.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger(__name__)


def _read_downloaded_text(record: dict, predicate) -> str | None:
    """Вернуть текст первого downloaded_files-файла, подходящего под predicate(file)."""
    for f in record.get("downloaded_files", []):
        if predicate(f):
            # Пытаемся прочитать как UTF-8
            path = f.get("local_path")
            if not path or not Path(path).exists():
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            except Exception:
                continue
    return None


def extract_kicad_pairs(record: dict) -> list[dict]:
    """Пара (prompt=описание README, completion=KiCad-файл)."""
    pairs = []
    if record.get("source_type") != "github_repo":
        return pairs

    # README-контент уже в record.content, но мы пытаемся вытащить отдельно README из downloaded_files
    readme_text = record.get("content") or ""

    # Если README не в content (например, репозиторий без md-файла) — пропускаем
    if not readme_text.strip():
        return pairs

    # Ищем KiCad-файлы
    for f in record.get("downloaded_files", []):
        if f.get("type") != "kicad":
            continue
        path = f.get("local_path", "")
        if not path or not Path(path).exists():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                kicad_text = fh.read()
        except Exception:
            continue
        if len(kicad_text) < 50:
            continue
        pairs.append({
            "prompt": (
                "На основе следующего KiCad-описания проекта сгенерируй текстовое "
                "описание схемы, ключевые компоненты и назначение устройства.\n\n"
                f"KiCad-файл ({f.get('original_file', '')}):\n{kicad_text[:8000]}"
            ),
            "completion": readme_text[:8000],
            "source": record.get("source_url"),
            "task_type": "kicad_to_description",
        })
        # Обратное направление: описание → KiCad
        pairs.append({
            "prompt": (
                "Сгенерируй KiCad-описание схемы (.kicad_sch) по следующему текстовому "
                "описанию проекта:\n\n{desc}"
            ).format(desc=readme_text[:4000]),
            "completion": kicad_text[:8000],
            "source": record.get("source_url"),
            "task_type": "description_to_kicad",
        })
    return pairs


def extract_qa_pairs(record: dict) -> list[dict]:
    """Пара (prompt=вопрос, completion=принятый ответ) из StackExchange."""
    if record.get("source_type") != "stackexchange":
        return []
    meta = record.get("metadata") or {}
    answers = meta.get("answers") or []
    accepted = [a for a in answers if a.get("is_accepted")]
    if not accepted:
        # Берём ответ с наибольшим score
        accepted = sorted(answers, key=lambda a: a.get("score", 0), reverse=True)[:1]
    if not accepted:
        return []

    # content уже содержит структуру "# Title\n## Вопрос\n...\n## Ответ..."
    content = record.get("content") or ""
    # Простое разбиение
    parts = content.split("## Вопрос")
    if len(parts) < 2:
        return []
    question_body = parts[1].split("## Ответ")[0].strip()
    answer_section = "## Ответ".join(content.split("## Ответ")[1:])
    # Найти тело принятого ответа — упрощённо: первое тело после [ПРИНЯТ]
    accepted_blocks = answer_section.split("[ПРИНЯТ]")
    if len(accepted_blocks) < 2:
        return []
    answer_body = accepted_blocks[1].split("## Ответ")[0].strip()
    # Убираем маркер
    answer_body = answer_body.lstrip(" ").rstrip()
    if not answer_body:
        return []

    title = meta.get("title") or ""

    return [{
        "prompt": f"Вопрос: {title}\n\n{question_body}",
        "completion": answer_body,
        "source": record.get("source_url"),
        "task_type": "qa_stackexchange",
        "tags": meta.get("tags") or [],
    }]


# ============================================================
# Новые типы пар (Этап 9)
# ============================================================

def extract_datasheet_pairs(record: dict) -> list[dict]:
    """Пара (описание компонента → параметры) из PDF datasheet.

    Извлекает структурированные данные из content:
      - Если есть раздел 'EXTRACTED TABLES', формирует пары "что за компонент → таблицы"
      - Из TOC пытаемся определить тип компонента (Op Amp, MCU, ADC и т.д.)
    """
    if record.get("source_type") != "pdf":
        return []

    pairs: list[dict] = []
    content = record.get("content") or ""
    metadata = record.get("metadata") or {}
    toc = metadata.get("toc") or []

    # Пытаемся определить тип компонента из TOC
    component_type = None
    for entry in toc[:5]:
        title = entry[1] if len(entry) > 1 else ""
        title_lower = title.lower()
        if any(kw in title_lower for kw in ["operational amplifier", "op amp", "op-amp"]):
            component_type = "Operational Amplifier"
            break
        elif "microcontroller" in title_lower or "mcu" in title_lower:
            component_type = "Microcontroller"
            break
        elif "adc" in title_lower or "analog-to-digital" in title_lower:
            component_type = "ADC"
            break
        elif "dac" in title_lower or "digital-to-analog" in title_lower:
            component_type = "DAC"
            break
        elif "regulator" in title_lower or "ldo" in title_lower:
            component_type = "Voltage Regulator"
            break

    # Если есть таблицы — формируем пары
    if "EXTRACTED TABLES" in content:
        tables_section = content.split("EXTRACTED TABLES")[-1]
        if tables_section:
            pairs.append({
                "prompt": (
                    "Опиши ключевые электрические характеристики компонента из datasheet. "
                    "Сформируй структурированный ответ с указанием pinout, диапазонов напряжений "
                    "и особенностей."
                    + (f"\n\nТип компонента: {component_type}" if component_type else "")
                ),
                "completion": tables_section[:8000],
                "source": record.get("source_url"),
                "task_type": "datasheet_to_specs",
                "component_type": component_type,
            })

    # Если есть TOC — формируем пары "структура документации"
    if toc:
        toc_text = "\n".join(
            f"{'  ' * (e[0] - 1) if len(e) > 0 else ''}{e[1]}"
            for e in toc[:50]
        )
        pairs.append({
            "prompt": (
                "Сформируй структуру документации для электронного компонента. "
                "Перечисли основные разделы datasheet'а."
                + (f"\n\nТип компонента: {component_type}" if component_type else "")
            ),
            "completion": toc_text,
            "source": record.get("source_url"),
            "task_type": "datasheet_structure",
            "component_type": component_type,
        })

    return pairs


def extract_summary_pairs(record: dict) -> list[dict]:
    """Пара (текст → TL;DR) для длинных статей.

    Использует простую эвристику: первый абзац — TL;DR, остальное — полное описание.
    """
    content = record.get("content") or ""
    if len(content) < 2000:  # короткие статьи не имеют смысла суммаризировать
        return []

    # Берём первый абзац как "summary"
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paragraphs) < 3:
        return []

    summary = paragraphs[0]
    # Если первый абзац слишком короткий — берём первые 2-3
    if len(summary) < 200:
        summary = "\n\n".join(paragraphs[:3])

    # Обрезаем summary, чтобы был реалистичным TL;DR
    if len(summary) > 1000:
        summary = summary[:1000] + "..."

    pairs = [{
        "prompt": "Сформируй краткое резюме статьи для технического блога.",
        "completion": summary,
        "source": record.get("source_url"),
        "task_type": "article_to_summary",
    }]

    # Обратная пара: summary → полный текст (для генерации)
    if len(content) > 5000:
        pairs.append({
            "prompt": f"На основе этого краткого описания, разверни полный технический текст:\n\n{summary}",
            "completion": content[:8000],
            "source": record.get("source_url"),
            "task_type": "summary_to_article",
        })

    return pairs


def extract_code_explanation_pairs(record: dict) -> list[dict]:
    """Пара (код → описание из окружающего текста) для HTML-статей.

    Ищет в content markdown-блоки ```code```, берёт описание из 2-3 строк до/после.
    """
    import re

    if record.get("source_type") not in ("html", "github_repo"):
        return []

    content = record.get("content") or ""
    pairs: list[dict] = []

    # Находим все блоки кода
    code_block_re = re.compile(r"```([a-zA-Z0-9]*)\n(.*?)```", re.DOTALL)
    matches = list(code_block_re.finditer(content))

    for m in matches:
        language = m.group(1) or "unknown"
        code = m.group(2).strip()
        if len(code) < 50:
            continue

        # Берём контекст: 200 символов до и после блока
        start = max(0, m.start() - 300)
        end = min(len(content), m.end() + 200)
        before = content[start:m.start()].strip()
        after = content[m.end():end].strip()
        context = (before + "\n" + after).strip()

        if len(context) < 100:
            continue

        pairs.append({
            "prompt": (
                f"Опиши, что делает этот код, и объясни его назначение.\n\n"
                f"Контекст из статьи:\n{context[:1500]}\n\n"
                f"Код ({language}):\n```\n{code[:3000]}\n```"
            ),
            "completion": f"Этот код написан на {language}. Контекст: {context[:500]}",
            "source": record.get("source_url"),
            "task_type": "code_to_explanation",
            "language": language,
        })

    # Ограничиваем число пар с одной записи (иначе 100+ пар с одного GitHub-репо)
    return pairs[:5]


def extract_faq_pairs(record: dict) -> list[dict]:
    """Пара (вопрос → ответ) из FAQ-секций статей и учебников.

    Ищет в content секции с заголовками FAQ, Questions, Часто задаваемые вопросы
    и парсит пары Q: ... A: ...
    """
    import re

    content = record.get("content") or ""
    if not content:
        return []

    # Поиск заголовков FAQ
    faq_patterns = [
        r"(?:^|\n)#+\s*(?:FAQ|Frequently Asked Questions|Часто задаваемые вопросы|Вопросы и ответы)\s*\n",
        r"(?:^|\n)(?:## |### |==== )\s*(?:FAQ|Questions)\s*\n",
    ]

    pairs: list[dict] = []
    for pattern in faq_patterns:
        for m in re.finditer(pattern, content, re.IGNORECASE):
            # Берём 5000 символов после заголовка FAQ
            faq_section = content[m.end():m.end() + 5000]
            # Ищем пары Q: ... A: ... или **Q:** ... **A:** ...
            q_a_re = re.compile(
                r"(?:\*\*)?Q(?:uestion)?:?\s*\*{0,2}(.*?)(?:\n\s*\n)"
                r"(?:\*\*)?A(?:nswer)?:?\s*\*{0,2}(.*?)(?=\n\s*\n|\nQ|\Z)",
                re.DOTALL | re.IGNORECASE,
            )
            for qm in q_a_re.finditer(faq_section):
                question = qm.group(1).strip().strip("*").strip()
                answer = qm.group(2).strip().strip("*").strip()
                if len(question) > 20 and len(answer) > 20:
                    pairs.append({
                        "prompt": question,
                        "completion": answer,
                        "source": record.get("source_url"),
                        "task_type": "faq_qa",
                    })

    return pairs[:20]  # ограничение на 20 пар с одной записи


def run_extract_pairs(corpus_file: str | Path, output_file: str | Path) -> dict:
    """Извлечь все пары для instruction-tuning."""
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total_pairs = 0
    by_type: dict[str, int] = {}

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
            if r.get("is_duplicate") or r.get("status") != "ok":
                continue

            pairs = []
            pairs.extend(extract_kicad_pairs(r))
            pairs.extend(extract_qa_pairs(r))
            # Новые типы пар (Этап 9)
            pairs.extend(extract_datasheet_pairs(r))
            pairs.extend(extract_summary_pairs(r))
            pairs.extend(extract_code_explanation_pairs(r))
            pairs.extend(extract_faq_pairs(r))

            for p in pairs:
                fout.write(json.dumps(p, ensure_ascii=False) + "\n")
                total_pairs += 1
                by_type[p["task_type"]] = by_type.get(p["task_type"], 0) + 1

    log.info(f"Extracted {total_pairs} pairs: {by_type}")
    return {"total_pairs": total_pairs, "by_type": by_type}
