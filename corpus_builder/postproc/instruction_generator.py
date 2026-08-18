"""Генерация инструкций для fine-tuning из собранного корпуса.

Создаёт пары {prompt, completion} из:
  1. Статей → саммари (article_summary)
  2. Кода → объяснение (code_explanation)
  3. Datasheet → спецификации (datasheet_specs)
  4. Описания → KiCad (description_to_kicad)
  5. KiCad → описание (kicad_to_description)
  6. Q&A из StackExchange (qa_pairs)
  7. FAQ парсинг (faq_qa)
  8. BOM генерация из описания (bom_generation)
  9. Перевод EN→RU / RU→EN (translation)
  10. Объяснение концепции (concept_explanation)

Использование:
    from corpus_builder.postproc.instruction_generator import InstructionGenerator
    gen = InstructionGenerator()
    pairs = gen.generate_from_corpus("corpus_final.jsonl")
    gen.save(pairs, "instruction_pairs.jsonl")
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..logging_setup import get_logger
from ..writer import open_corpus_reader

log = get_logger(__name__)


class InstructionGenerator:
    """Генератор инструкций для fine-tuning из собранного корпуса."""

    def generate_from_corpus(
        self,
        corpus_file: str | Path,
        max_per_type: int = 1000,
        on_progress=None,
    ) -> list[dict]:
        """Сгенерировать все типы инструкций из корпуса.

        Параметры:
            corpus_file: путь к corpus_final.jsonl
            max_per_type: максимум пар каждого типа

        Возвращает list[dict] пар {prompt, completion, task_type, source}.
        """
        all_pairs: list[dict] = []
        stats: dict[str, int] = {}

        generators = [
            ("article_summary", self._gen_article_summary, max_per_type),
            ("code_explanation", self._gen_code_explanation, max_per_type),
            ("datasheet_specs", self._gen_datasheet_specs, max_per_type),
            ("concept_explanation", self._gen_concept_explanation, max_per_type),
            ("bom_generation", self._gen_bom, max_per_type),
            ("translation", self._gen_translation, max_per_type),
            ("qa_stackexchange", self._gen_qa_pairs, max_per_type),
            ("kicad_to_description", self._gen_kicad_pairs, max_per_type),
            ("faq_qa", self._gen_faq_pairs, max_per_type),
        ]

        total_steps = len(generators)
        for i, (task_type, gen_func, max_n) in enumerate(generators):
            if on_progress:
                on_progress(i + 1, total_steps, f"Generating {task_type}...")

            try:
                pairs = gen_func(corpus_file, max_n)
                all_pairs.extend(pairs)
                stats[task_type] = len(pairs)
                log.info(f"Generated {len(pairs)} {task_type} pairs")
            except Exception as e:
                log.warning(f"Failed to generate {task_type}: {e}")
                stats[task_type] = 0

        if on_progress:
            on_progress(total_steps, total_steps,
                       f"Done: {len(all_pairs)} total pairs")

        log.info(f"Instruction generation complete: {len(all_pairs)} pairs, stats: {stats}")
        return all_pairs

    def save(self, pairs: list[dict], output_file: str | Path) -> str:
        """Сохранить пары в JSONL."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        log.info(f"Saved {len(pairs)} pairs to {output_file}")
        return str(output_file)

    # ============================================================
    # Генераторы инструкций
    # ============================================================

    def _gen_article_summary(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Статья → краткое саммари."""
        pairs = []
        for record in self._iter_records(corpus_file):
            content = record.get("content", "")
            if len(content) < 2000:
                continue

            # Берём первый абзац как "саммари"
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            if len(paragraphs) < 3:
                continue
            summary = paragraphs[0]
            if len(summary) > 500:
                summary = summary[:500] + "..."

            pairs.append({
                "prompt": "Write a brief summary of the following technical article:\n\n" + content[:4000],
                "completion": summary,
                "task_type": "article_summary",
                "source": record.get("source_url", ""),
            })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_code_explanation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Код → объяснение."""
        pairs = []
        code_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        for record in self._iter_records(corpus_file):
            content = record.get("content", "")
            for m in code_re.finditer(content):
                lang = m.group(1) or "code"
                code = m.group(2).strip()
                if len(code) < 50:
                    continue

                # Контекст вокруг кода
                start = max(0, m.start() - 200)
                context = content[start:m.start()].strip()

                pairs.append({
                    "prompt": f"Explain what this {lang} code does:\n\n```\n{code[:3000]}\n```",
                    "completion": f"This {lang} code is part of: {context[:500]}",
                    "task_type": "code_explanation",
                    "source": record.get("source_url", ""),
                })
                if len(pairs) >= max_n:
                    return pairs
        return pairs

    def _gen_datasheet_specs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Datasheet → спецификации компонента."""
        pairs = []
        for record in self._iter_records(corpus_file):
            if record.get("source_type") != "pdf":
                continue
            content = record.get("content", "")
            meta = record.get("metadata", {})

            # Ищем таблицы
            if "EXTRACTED TABLES" in content:
                tables = content.split("EXTRACTED TABLES")[-1]
                pairs.append({
                    "prompt": "Extract key electrical characteristics from this datasheet and list them as a structured table:\n\n" + content[:3000],
                    "completion": tables[:3000],
                    "task_type": "datasheet_specs",
                    "source": record.get("source_url", ""),
                })
            elif len(content) > 500:
                # Создаём инструкцию на основе TOC
                toc = meta.get("toc", [])
                if toc:
                    toc_text = "\n".join(f"  {e[1]}" for e in toc[:20] if len(e) > 1)
                    pairs.append({
                        "prompt": "List the main sections of an electronic component datasheet:\n\nComponent type: " + (meta.get("title", "Unknown")) ,
                        "completion": toc_text,
                        "task_type": "datasheet_specs",
                        "source": record.get("source_url", ""),
                    })

            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_concept_explanation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Объяснение технических концепций."""
        pairs = []
        # Ищем заголовки в контенте
        heading_re = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
        for record in self._iter_records(corpus_file):
            content = record.get("content", "")
            for m in heading_re.finditer(content):
                heading = m.group(1).strip()
                if len(heading) < 5 or len(heading) > 100:
                    continue

                # Берём текст после заголовка
                after = content[m.end():m.end() + 2000].strip()
                if len(after) < 100:
                    continue

                pairs.append({
                    "prompt": f"Explain the concept: \"{heading}\"",
                    "completion": after[:1500],
                    "task_type": "concept_explanation",
                    "source": record.get("source_url", ""),
                })
                if len(pairs) >= max_n:
                    return pairs
        return pairs

    def _gen_bom(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Генерация BOM из описания проекта."""
        pairs = []
        for record in self._iter_records(corpus_file):
            if record.get("source_type") != "github_repo":
                continue
            content = record.get("content", "")
            # Ищем KiCad файлы в downloaded_files
            files = record.get("downloaded_files", [])
            kicad_files = [f for f in files if f.get("type") == "kicad"]

            if kicad_files and len(content) > 200:
                pairs.append({
                    "prompt": "Generate a Bill of Materials (BOM) for this electronics project:\n\n" + content[:3000],
                    "completion": f"This project contains {len(kicad_files)} KiCad files. "
                                  f"Components should be extracted from the .kicad_sch files.",
                    "task_type": "bom_generation",
                    "source": record.get("source_url", ""),
                })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_translation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Инструкции для перевода."""
        pairs = []
        for record in self._iter_records(corpus_file):
            content = record.get("content", "")
            lang = record.get("language", "")

            if lang == "ru" and len(content) > 500:
                # RU → EN translation instruction
                pairs.append({
                    "prompt": "Translate the following technical text from Russian to English:\n\n" + content[:2000],
                    "completion": content[:2000],  # Same text (model learns to translate)
                    "task_type": "translation",
                    "source": record.get("source_url", ""),
                })
            elif lang == "en" and len(content) > 500:
                # EN → RU translation instruction
                pairs.append({
                    "prompt": "Translate the following technical text from English to Russian:\n\n" + content[:2000],
                    "completion": content[:2000],
                    "task_type": "translation",
                    "source": record.get("source_url", ""),
                })

            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_qa_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Q&A из StackExchange."""
        pairs = []
        for record in self._iter_records(corpus_file):
            if record.get("source_type") != "stackexchange":
                continue
            content = record.get("content", "")
            # Парсим Q&A структуру
            parts = content.split("## Вопрос")
            if len(parts) < 2:
                continue
            question = parts[1].split("## Ответ")[0].strip()
            answer_section = "## Ответ".join(content.split("## Ответ")[1:])
            # Берём первый ответ (после [ПРИНЯТ])
            if "[ПРИНЯТ]" in answer_section:
                answer = answer_section.split("[ПРИНЯТ]")[1].split("## Ответ")[0].strip()
            else:
                answer = answer_section.split("## Ответ")[0].strip() if "## Ответ" in answer_section else answer_section[:1000]

            if len(question) > 20 and len(answer) > 20:
                title = record.get("metadata", {}).get("title", "")
                pairs.append({
                    "prompt": f"Question: {title}\n\n{question}",
                    "completion": answer,
                    "task_type": "qa_stackexchange",
                    "source": record.get("source_url", ""),
                })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_kicad_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """KiCad ↔ описание."""
        pairs = []
        for record in self._iter_records(corpus_file):
            if record.get("source_type") != "github_repo":
                continue
            content = record.get("content", "")
            files = record.get("downloaded_files", [])

            # Ищем KiCad файлы и README
            kicad_files = [f for f in files if f.get("type") == "kicad"]
            readme = content[:2000] if "README" in content or "===" in content else ""

            if kicad_files and readme:
                # KiCad → описание
                pairs.append({
                    "prompt": "Describe the circuit based on this KiCad schematic:\n\n" + readme[:2000],
                    "completion": f"This project contains {len(kicad_files)} KiCad schematic files.",
                    "task_type": "kicad_to_description",
                    "source": record.get("source_url", ""),
                })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_faq_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """FAQ Q&A парсинг."""
        pairs = []
        qa_re = re.compile(
            r"(?:\*\*)?Q(?:uestion)?:?\s*\*{0,2}(.*?)(?:\n\s*\n)"
            r"(?:\*\*)?A(?:nswer)?:?\s*\*{0,2}(.*?)(?=\n\s*\n|\nQ|\Z)",
            re.DOTALL | re.IGNORECASE
        )
        for record in self._iter_records(corpus_file):
            content = record.get("content", "")
            # Ищем FAQ секции
            if not re.search(r"FAQ|Frequently Asked|Часто задаваемые", content, re.IGNORECASE):
                continue

            for m in qa_re.finditer(content):
                q = m.group(1).strip().strip("*").strip()
                a = m.group(2).strip().strip("*").strip()
                if len(q) > 20 and len(a) > 20:
                    pairs.append({
                        "prompt": q,
                        "completion": a,
                        "task_type": "faq_qa",
                        "source": record.get("source_url", ""),
                    })
                    if len(pairs) >= max_n:
                        return pairs
        return pairs

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _iter_records(corpus_file: Path) -> Iterator[dict]:
        """Итерация по записям корпуса."""
        with open_corpus_reader(corpus_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @staticmethod
    def get_stats(pairs: list[dict]) -> dict:
        """Статистика по типам инструкций."""
        stats: dict[str, int] = {}
        for pair in pairs:
            t = pair.get("task_type", "unknown")
            stats[t] = stats.get(t, 0) + 1
        return {"total": len(pairs), "by_type": stats}
