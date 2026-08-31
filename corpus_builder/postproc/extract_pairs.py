"""Извлечение пар для instruction-tuning.

Делаем два набора пар:
  1. README ↔ KiCad: схема из GitHub-репозитория (если в записи есть README.md и .kicad_sch)
  2. Вопрос ↔ Принятый ответ: из StackExchange-записей

Выходной формат — JSONL с полями {prompt, completion, source, task_type}.

Единая лексика task_type (I14): имена типов совпадают с теми, что выдаёт
`postproc/instruction_generator.py` (`article_summary`, `code_explanation`,
`datasheet_specs`, `kicad_to_description`, `qa_stackexchange`, `faq_qa`, …), и
тексты промптов берутся из `prompt_variations` — один и тот же датасет,
собранный двумя путями, не различается разметкой, а шаблоны настраиваются
через prompts.yaml вместо хардкода на одном языке.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from ..logging_setup import get_logger
from .prompt_variations import get_prompt

log = get_logger(__name__)


# ============================================================
# Языковые маркеры (I14)
# ============================================================
# Краулер Forum/StackExchange пишет заголовки треда на русском ИЛИ английском
# в зависимости от языка площадки. Раньше разбор жёстко зависел от русских
# строк («## Вопрос», «[ПРИНЯТ]»), и англоязычный тред давал 0 пар — молча,
# без единого предупреждения. Теперь поддерживаем оба набора маркеров.
Q_MARKERS = ("## Вопрос", "## Вопрос:", "## Question", "## Question:")
A_MARKERS = ("## Ответ", "## Ответ:", "## Answer", "## Answer:")
ACCEPTED_MARKERS = ("[ПРИНЯТ]", "[ACCEPTED]", "(принят)", "(accepted)")


def _find_marker(content: str, markers: tuple[str, ...]) -> str:
    """Первый маркер, реально присутствующий в контенте."""
    for m in markers:
        if m in content:
            return m
    return ""


def _is_russian(text: str) -> bool:
    """Доля кириллицы в тексте — по ней выбираем язык промпта-каркаса."""
    if not text:
        return False
    sample = text[:2000]
    cyr = sum(1 for c in sample if "\u0410" <= c.upper() <= "\u042F")
    lat = sum(1 for c in sample if c.isascii() and c.isalpha())
    return cyr > lat


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
            "prompt": get_prompt(
                "kicad_to_description",
                kicad=f"KiCad file ({f.get('original_file', '')}):\n{kicad_text[:8000]}"),
            "completion": readme_text[:8000],
            "source": record.get("source_url"),
            "task_type": "kicad_to_description",
        })
        # Обратное направление: описание → KiCad
        pairs.append({
            "prompt": get_prompt("description_to_kicad", desc=readme_text[:4000]),
            "completion": kicad_text[:8000],
            "source": record.get("source_url"),
            "task_type": "description_to_kicad",
        })
    return pairs


def extract_qa_pairs(record: dict) -> list[dict]:
    """Пара (вопрос → лучший ответ) из StackExchange-записи.

    Маркеры заголовков поддерживаются и русские, и английские (I14): раньше
    разбор зависел от строк «## Вопрос»/«[ПРИНЯТ]», и англоязычная ветка
    давала 0 пар — молча.
    """
    if record.get("source_type") != "stackexchange":
        return []
    meta = record.get("metadata") or {}
    answers = meta.get("answers") or []
    accepted = [a for a in answers if a.get("is_accepted")]
    if not accepted:
        accepted = sorted(answers, key=lambda a: a.get("score", 0), reverse=True)[:1]
    if not accepted:
        return []

    content = record.get("content") or ""
    q_marker = _find_marker(content, Q_MARKERS)
    a_marker = _find_marker(content, A_MARKERS)
    if not q_marker or not a_marker:
        return []

    after_q = content.split(q_marker, 1)[1]
    question_body = re.split(r"\n" + re.escape(a_marker), after_q)[0].strip()
    answer_section = after_q.split(a_marker, 1)[1] if a_marker in after_q else ""
    blocks = [b for b in answer_section.split(a_marker) if b.strip()]
    if not question_body or not blocks:
        return []

    # тело принятого ответа: блок с маркером «принят», иначе первый (краулер
    # сортирует ответы: принятый → по score)
    body = ""
    for b in blocks:
        head = b[:120]
        if any(mk in head for mk in ACCEPTED_MARKERS) or \
           (accepted[0].get("answer_id") is not None
            and str(accepted[0]["answer_id"]) in head):
            body = b
            break
    body = _strip_answer_header(body or blocks[0])
    if not body:
        return []

    title = meta.get("title") or ""
    prefix = "Вопрос" if _is_russian(question_body) else "Question"
    prompt = f"{prefix}: {title}\n\n{question_body}" if title else f"{prefix}: {question_body}"

    return [{
        "prompt": prompt,
        "completion": body,
        "source": record.get("source_url") or "",
        "task_type": "qa_stackexchange",
        "tags": meta.get("tags") or [],
    }]


def _accepted_from_metadata(answers: list[dict]) -> str:
    """Тело принятого ответа из metadata.answers (если краулер его сохранил)."""
    best = None
    for a in answers:
        if isinstance(a, dict) and a.get("is_accepted") and a.get("body"):
            return a["body"].strip()
    # принятого нет — берём самый рейтинговый с телом
    with_body = [a for a in answers if isinstance(a, dict) and a.get("body")]
    if with_body:
        best = max(with_body, key=lambda a: a.get("score", 0) or 0)
        return best["body"].strip()
    return ""


def _pick_answer_block(blocks: list[str], accepted: dict | None) -> str:
    """Выбрать блок ответа по маркеру «принят» или по id (fallback-разбор)."""
    for b in blocks:
        head = b[:160]
        if any(mk in head for mk in ACCEPTED_MARKERS):
            return _strip_answer_header(b)
    if accepted and accepted.get("answer_id") is not None:
        aid = str(accepted["answer_id"])
        for b in blocks:
            if aid in b.splitlines()[0]:
                return _strip_answer_header(b)
    return _strip_answer_header(blocks[0]) if blocks else ""


def _strip_answer_header(block: str) -> str:
    """Из блока «(score=12) [ПРИНЯТ]\n\n<тело>» оставить <тело>."""
    lines = block.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and ("score=" in lines[0].lower()
                  or any(mk in lines[0] for mk in ACCEPTED_MARKERS)):
        lines.pop(0)
    return "\n".join(lines).strip()


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
                "prompt": get_prompt("datasheet_specs",
                                     component=component_type or "unknown"),
                "completion": tables_section[:8000],
                "source": record.get("source_url"),
                "task_type": "datasheet_specs",
                "component_type": component_type,
            })

    # Если есть TOC — формируем пары "структура документации"
    if toc:
        toc_text = "\n".join(
            f"{'  ' * (e[0] - 1) if len(e) > 0 else ''}{e[1]}"
            for e in toc[:50]
        )
        pairs.append({
            "prompt": get_prompt("datasheet_structure",
                                 component=component_type or "unknown"),
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
        "prompt": get_prompt("article_summary", content=content[:8000]),
        "completion": summary,
        "source": record.get("source_url"),
        "task_type": "article_summary",
    }]

    # Обратная пара: summary → полный текст (для генерации)
    if len(content) > 5000:
        pairs.append({
            "prompt": get_prompt("article_expansion", summary=summary),
            "completion": content[:8000],
            "source": record.get("source_url"),
            "task_type": "article_expansion",
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

        # Объяснение = текст до блока (он обычно и описывает код); после блока
        # чаще всего идёт следующий пример, поэтому «before» приоритетнее.
        before = content[max(0, m.start() - 1200):m.start()].strip()
        after = content[m.end():m.end() + 600].strip()
        explanation = before if len(before) >= 100 else after
        # заголовок секции, если он рядом — полезный контекст
        if len(explanation) < 100:
            continue
        explanation = re.split(r"```", explanation)[0].strip()
        if len(explanation) < 100:
            continue

        prompt = get_prompt("code_explanation", code=code[:3000], lang=language)
        if not prompt:
            continue
        pairs.append({
            "prompt": prompt,
            "completion": explanation[:4000],
            "source": record.get("source_url"),
            "task_type": "code_explanation",
            "language": language,
        })

    # Ограничиваем число пар с одной записи (иначе 100+ пар с одного GitHub-репо)
    return pairs[:5]


def extract_faq_pairs(record: dict) -> list[dict]:
    """Пара (вопрос → ответ) из FAQ-секций статей и учебников.

    Ищет в content секции с заголовками FAQ/Questions/«Часто задаваемые вопросы»
    и парсит пары Q:/A:, Question:/Answer:, В:/О:, Вопрос:/Ответ: (I14).
    """
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
            # Пары вида «Q: … / A: …», «**Question:** …», «Вопрос: … / Ответ: …»
            q_a_re = re.compile(
                r"(?:\*\*)?(?:Q(?:uestion)?|В(?:опрос)?)[.:)]*\s*\*{0,2}(.*?)\s*(?:\n\s*\n)"
                r"(?:\*\*)?(?:A(?:nswer)?|О(?:твет)?)[.:)]*\s*\*{0,2}(.*?)"
                r"(?=\n\s*\n|\n\s*(?:\*\*)?(?:Q|В)(?:uestion|опрос)?[.:)]|\Z)",
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


def run_extract_pairs(corpus_file: str | Path, output_file: str | Path,
                      on_progress: "Callable[[int, int], None] | None" = None) -> dict:
    """Извлечь все пары для instruction-tuning (A4: с прогрессом стадии)."""
    corpus_file = Path(corpus_file)
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total_pairs = 0
    by_type: dict[str, int] = {}
    with open(corpus_file, "r", encoding="utf-8") as fcount:
        n_records = sum(1 for line in fcount if line.strip())
    seen = 0

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
            seen += 1
            if on_progress and seen % 500 == 0:
                on_progress(seen, n_records)

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
