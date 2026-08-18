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
from .chunker import chunk_record
from .prompt_variations import get_prompt

log = get_logger(__name__)

# Default chunk size for content that gets truncated to fit in a prompt.
# 4000 chars ≈ 1000 tokens (English) / 1500 tokens (Russian), which leaves
# headroom for the prompt wrapper + completion under a 2048-token context.
DEFAULT_CHUNK_CHARS = 4000


class InstructionGenerator:
    """Генератор инструкций для fine-tuning из собранного корпуса.

    Uses an internal corpus cache to avoid reading the file 9+ times
    (once per generator). The cache is populated on the first call to
    _get_records_for_type() and stores records grouped by source_type.
    """

    def __init__(self):
        # Cache: {corpus_file_path: {source_type: [records]}}
        # Records are chunked if needed. Populated lazily on first access.
        self._corpus_cache: dict[str, dict[str, list[dict]]] = {}
        self._corpus_cache_chunked: dict[str, dict[str, list[dict]]] = {}

    def _clear_cache(self):
        """Clear the corpus cache. Call this if the corpus file changes."""
        self._corpus_cache.clear()
        self._corpus_cache_chunked.clear()

    def _get_records_for_type(
        self, corpus_file: Path, source_type: str, chunked: bool = False
    ) -> list[dict]:
        """Return records matching source_type, using a cache.

        On first call for a given corpus_file, reads the ENTIRE file once
        and distributes records by source_type. Subsequent calls for any
        source_type return from cache — no re-reading.

        Args:
            corpus_file: path to corpus JSONL
            source_type: filter records by this source_type
            chunked: if True, return chunked records (long records split
                into DEFAULT_CHUNK_CHARS pieces); if False, return raw

        Returns:
            list of records (possibly chunked) matching source_type.
            Returns ALL records if source_type is None or "".
        """
        cache_key = str(corpus_file)
        if chunked:
            cache = self._corpus_cache_chunked
        else:
            cache = self._corpus_cache

        if cache_key not in cache:
            # Read the entire corpus ONCE, distribute by source_type
            by_type: dict[str, list[dict]] = {}
            if chunked:
                # Use the chunked iterator
                for record in self._iter_records_chunked(corpus_file, DEFAULT_CHUNK_CHARS):
                    st = record.get("source_type", "")
                    by_type.setdefault(st, []).append(record)
            else:
                for record in self._iter_records(corpus_file):
                    st = record.get("source_type", "")
                    by_type.setdefault(st, []).append(record)
            cache[cache_key] = by_type
            log.debug(f"Corpus cache {'(chunked)' if chunked else ''} populated: "
                      f"{cache_key} → {sum(len(v) for v in by_type.values())} records, "
                      f"{len(by_type)} source_types")

        by_type = cache[cache_key]
        if source_type is None or source_type == "":
            # Return all records (concatenated)
            all_recs = []
            for recs in by_type.values():
                all_recs.extend(recs)
            return all_recs
        return by_type.get(source_type, [])

    def generate_from_corpus(
        self,
        corpus_file: str | Path,
        max_per_type: int = 1000,
        on_progress=None,
        task_types: list[str] | None = None,
    ) -> list[dict]:
        """Сгенерировать инструкции из корпуса.

        Параметры:
            corpus_file: путь к corpus_final.jsonl
            max_per_type: максимум пар каждого типа
            on_progress: callback(current, total, message)
            task_types: список типов для генерации (None = все).
                Поддерживаемые: article_summary, code_explanation,
                datasheet_specs, concept_explanation, bom_generation,
                translation, qa_stackexchange, multi_turn_dialogue,
                kicad_to_description, faq_qa.

        Возвращает list[dict] пар {prompt, completion, task_type, source}.
        """
        all_pairs: list[dict] = []
        stats: dict[str, int] = {}

        all_generators = [
            ("article_summary", self._gen_article_summary, max_per_type),
            ("code_explanation", self._gen_code_explanation, max_per_type),
            ("datasheet_specs", self._gen_datasheet_specs, max_per_type),
            ("concept_explanation", self._gen_concept_explanation, max_per_type),
            ("bom_generation", self._gen_bom, max_per_type),
            ("translation", self._gen_translation, max_per_type),
            ("qa_stackexchange", self._gen_qa_pairs, max_per_type),
            ("multi_turn_dialogue", self._gen_multi_turn_dialogue, max_per_type),
            ("kicad_to_description", self._gen_kicad_pairs, max_per_type),
            ("faq_qa", self._gen_faq_pairs, max_per_type),
        ]
        # Filter by task_types if specified
        if task_types is not None:
            task_set = set(task_types)
            generators = [g for g in all_generators if g[0] in task_set]
            skipped = len(all_generators) - len(generators)
            if skipped:
                log.info(f"Filtered generators: {len(generators)}/{len(all_generators)} "
                         f"(skipped {skipped} types not in {sorted(task_set)})")
        else:
            generators = all_generators

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
        """Article → extractive summary (first sentence of each paragraph).

        Long articles are split into chunks via _iter_records_chunked so the
        summary covers the WHOLE article (one summary per chunk) rather than
        only the first 4000 chars. Uses the corpus cache to avoid re-reading.
        """
        pairs = []
        # article_summary doesn't filter by source_type — process all records
        records = self._get_records_for_type(corpus_file, None, chunked=True)
        for record in records:
            content = record.get("content", "")
            if len(content) < 500:
                # Skip very short chunks (likely tail of a split article)
                continue

            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            if len(paragraphs) < 2:
                continue

            # Extractive summarization: take first sentence from each paragraph
            summary_sentences = []
            for para in paragraphs[:10]:  # max 10 paragraphs
                # Split by sentence endings
                sentences = re.split(r'(?<=[.!?])\s+', para)
                if sentences:
                    first_sent = sentences[0].strip()
                    if len(first_sent) > 20:
                        summary_sentences.append(first_sent)

            if len(summary_sentences) < 2:
                continue

            summary = " ".join(summary_sentences)
            if len(summary) > 500:
                summary = summary[:500] + "..."

            # Use full chunk content (already <= DEFAULT_CHUNK_CHARS)
            chunk_tag = ""
            total = record.get("total_chunks", 1)
            if total > 1:
                chunk_tag = f" (part {record.get('chunk_index', 0) + 1}/{total})"

            base_prompt = get_prompt("article_summary", content=content)
            if chunk_tag:
                base_prompt = base_prompt.replace("article:\n\n", f"article{chunk_tag}:\n\n")
            pairs.append({
                "prompt": base_prompt,
                "completion": summary,
                "task_type": "article_summary",
                "source": record.get("source_url", ""),
            })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_code_explanation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Code → explanation (extract sentences describing the code).

        Uses _iter_records_chunked so code blocks beyond the first 4000 chars
        of an article are also found and explained.
        """
        pairs = []
        code_re = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        # Keywords that indicate an explanation sentence
        explain_keywords = [
            "this code", "this function", "this class", "this script",
            "this snippet", "the following", "below code", "above code",
            "this module", "this program", "this example", "this method",
            "этом коде", "эта функция", "этот класс", "этот скрипт",
            "этот код", "следующий код", "нижеприведенный",
        ]
        # code_explanation doesn't filter by source_type — process all records
        records = self._get_records_for_type(corpus_file, None, chunked=True)
        for record in records:
            content = record.get("content", "")
            for m in code_re.finditer(content):
                lang = m.group(1) or "code"
                code = m.group(2).strip()
                if len(code) < 50:
                    continue

                # Look for explanatory sentences BEFORE and AFTER the code block
                # Search in a window of 500 chars before and after
                before_start = max(0, m.start() - 500)
                before_text = content[before_start:m.start()].strip()
                after_text = content[m.end():m.end() + 500].strip()

                # Find sentences containing explanation keywords
                explanation_parts = []
                for text in [before_text, after_text]:
                    sentences = re.split(r'(?<=[.!?])\s+', text)
                    for sent in sentences:
                        sent_lower = sent.lower()
                        if any(kw in sent_lower for kw in explain_keywords):
                            # Clean up the sentence
                            clean = sent.strip().strip("*").strip()
                            if len(clean) > 20:
                                explanation_parts.append(clean)

                if not explanation_parts:
                    # Fallback: take the sentence right before the code
                    if before_text:
                        sentences = re.split(r'(?<=[.!?])\s+', before_text)
                        if sentences and len(sentences[-1]) > 20:
                            explanation_parts.append(sentences[-1].strip())

                if not explanation_parts:
                    continue  # Skip if no explanation found

                explanation = " ".join(explanation_parts[:3])  # max 3 sentences
                if len(explanation) > 1000:
                    explanation = explanation[:1000] + "..."

                pairs.append({
                    "prompt": get_prompt("code_explanation", code=code[:3000], lang=lang),
                    "completion": explanation,
                    "task_type": "code_explanation",
                    "source": record.get("source_url", ""),
                })
                if len(pairs) >= max_n:
                    return pairs
        return pairs

    def _gen_datasheet_specs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Datasheet → спецификации компонента.

        Uses _iter_records_chunked so long PDFs are not truncated.
        The TOC fallback uses get_prompt() for diverse phrasing, and
        the component title is escaped to prevent prompt injection.
        Uses the corpus cache (filtered to source_type=pdf).
        """
        pairs = []
        records = self._get_records_for_type(corpus_file, "pdf", chunked=True)
        for record in records:
            content = record.get("content", "")
            meta = record.get("metadata", {})

            # Ищем таблицы
            if "EXTRACTED TABLES" in content:
                tables = content.split("EXTRACTED TABLES")[-1]
                pairs.append({
                    "prompt": get_prompt("datasheet_specs", content=content[:3000]),
                    "completion": tables[:3000],
                    "task_type": "datasheet_specs",
                    "source": record.get("source_url", ""),
                })
            elif len(content) > 500:
                # Создаём инструкцию на основе TOC
                toc = meta.get("toc", [])
                if toc:
                    toc_text = "\n".join(f"  {e[1]}" for e in toc[:20] if len(e) > 1)
                    # Escape the title to prevent prompt injection / broken
                    # formatting when title contains special chars.
                    title = meta.get("title", "Unknown")
                    # Build a prompt that includes the component type.
                    # Use get_prompt for diverse phrasing, then append the
                    # component type as context.
                    base_prompt = get_prompt("datasheet_specs", content=title)
                    pairs.append({
                        "prompt": base_prompt,
                        "completion": toc_text,
                        "task_type": "datasheet_specs",
                        "source": record.get("source_url", ""),
                    })

            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_concept_explanation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Объяснение технических концепций.

        Uses chunked iteration so headings in the latter half of long articles
        are also extracted. The completion is truncated at the NEXT heading
        to avoid mixing content from different sections.
        """
        pairs = []
        # Ищем заголовки в контенте
        heading_re = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
        # Regex to find the next heading after the current one
        next_heading_re = re.compile(r"\n#{1,3}\s+", re.MULTILINE)
        # concept_explanation doesn't filter by source_type — process all
        records = self._get_records_for_type(corpus_file, None, chunked=True)
        for record in records:
            content = record.get("content", "")
            for m in heading_re.finditer(content):
                heading = m.group(1).strip()
                if len(heading) < 5 or len(heading) > 100:
                    continue

                # Берём текст после заголовка (up to 2000 chars)
                after = content[m.end():m.end() + 2000]
                # Truncate at the next heading to avoid mixing sections
                next_match = next_heading_re.search(after)
                if next_match:
                    after = after[:next_match.start()]
                after = after.strip()
                if len(after) < 100:
                    continue

                pairs.append({
                    "prompt": get_prompt("concept_explanation", heading=heading),
                    "completion": after[:1500],
                    "task_type": "concept_explanation",
                    "source": record.get("source_url", ""),
                })
                if len(pairs) >= max_n:
                    return pairs
        return pairs

    def _gen_bom(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Generate BOM by parsing KiCad .kicad_sch files for real components.

        Uses the corpus cache (filtered to source_type=github_repo).
        """
        pairs = []
        # Regex for KiCad v6 symbol blocks
        symbol_re = re.compile(
            r'\(lib_id\s*"([^"]*)"\)'
            r'.*?\(property\s*"Reference"\s*"([^"]*)"\)'
            r'.*?\(property\s*"Value"\s*"([^"]*)"\)',
            re.DOTALL
        )
        records = self._get_records_for_type(corpus_file, "github_repo", chunked=False)
        for record in records:
            content = record.get("content", "")
            files = record.get("downloaded_files", [])
            kicad_files = [f for f in files if f.get("type") == "kicad"]

            if not kicad_files or len(content) < 200:
                continue

            # Parse KiCad files to extract components
            components = []
            for kf in kicad_files:
                kicad_path = kf.get("local_path", "")
                if kicad_path and Path(kicad_path).exists():
                    try:
                        kicad_content = Path(kicad_path).read_text(encoding="utf-8", errors="replace")
                        for m in symbol_re.finditer(kicad_content):
                            lib_id, ref, value = m.groups()
                            if ref and value:
                                components.append({
                                    "reference": ref,
                                    "value": value,
                                    "lib_id": lib_id,
                                })
                    except Exception:
                        continue

            if components:
                # Build a real BOM table
                bom_lines = ["Reference | Value | Library"]
                bom_lines.append("-" * 40)
                for comp in components[:50]:  # max 50 components
                    bom_lines.append(f"{comp['reference']} | {comp['value']} | {comp['lib_id']}")

                bom_text = "\n".join(bom_lines)
                pairs.append({
                    "prompt": get_prompt("bom_generation", content=content[:3000]),
                    "completion": f"BOM ({len(components)} components):\n\n{bom_text}",
                    "task_type": "bom_generation",
                    "source": record.get("source_url", ""),
                })
            # NOTE: Previously there was a fallback here that created a pair
            # with completion=content[:1000] when no KiCad components were
            # found. This taught the model "BOM = first 1000 chars of README"
            # which is useless. The fallback has been removed — only real
            # BOMs (parsed from .kicad_sch files) are generated now.

            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_translation(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Translation instructions — requires parallel corpora.

        Disabled: without parallel text pairs (RU↔EN on same topic),
        translation pairs would have prompt==completion, teaching the model
        identity instead of translation.

        To enable: collect Wikipedia articles via interlanguage links,
        then match RU article with its EN counterpart.
        """
        # TODO: Implement using Wikipedia interlanguage links
        # For now, return empty list
        log.info("Translation generator disabled (requires parallel corpora)")
        return []

    def _gen_qa_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Q&A from StackExchange — supports both RU and EN markers.

        The SE crawler produces content with headers like:
            ## Ответ (score=N) [ПРИНЯТ]\n\n<body>
            ## Answer (score=N) [ACCEPTED]\\n\\n<body>

        This parser uses a regex to extract the answer BODY (without the
        score/accepted prefix) and picks the accepted answer if present,
        otherwise the highest-scored one.
        """
        pairs = []
        # Regex to extract answer bodies with their metadata.
        # Matches: ## Ответ (score=N) [ПРИНЯТ]\n\n<body>
        #      or: ## Ответ (score=N)\n\n<body>  (no accepted marker)
        answer_re = re.compile(
            r'## (?:Ответ|Answer)\s*'
            r'(?:\(score=([\-\d]+)\)\s*)?'  # (score=N) — optional
            r'(\[ПРИНЯТ\]|\[ACCEPTED\])?\s*'
            r'\n\n'
            r'(.*?)(?=\n## (?:Ответ|Answer)|\Z)',
            re.DOTALL,
        )
        # Regex to extract the question body
        question_re = re.compile(
            r'## (?:Вопрос|Question)\n\n(.*?)(?=\n## (?:Ответ|Answer)|\Z)',
            re.DOTALL,
        )

        records = self._get_records_for_type(corpus_file, "stackexchange", chunked=False)
        for record in records:
            content = record.get("content", "")

            # Extract question body
            q_match = question_re.search(content)
            if not q_match:
                continue
            question = q_match.group(1).strip()
            if len(question) < 20:
                continue

            # Extract all answers with their score + accepted status + body
            answers = []
            for m in answer_re.finditer(content):
                score_str = m.group(1)
                score = int(score_str) if score_str else 0
                is_accepted = bool(m.group(2))
                body = m.group(3).strip()
                if len(body) < 20:
                    continue
                answers.append({
                    "score": score,
                    "is_accepted": is_accepted,
                    "body": body,
                })

            if not answers:
                # Fallback: try to parse from metadata (SE API structure)
                meta = record.get("metadata", {})
                meta_answers = meta.get("answers") or []
                accepted_meta = [a for a in meta_answers if a.get("is_accepted")]
                if not accepted_meta:
                    accepted_meta = sorted(meta_answers, key=lambda a: a.get("score", 0), reverse=True)[:1]
                if accepted_meta:
                    # body_chars is just a count, not the text; we can't
                    # reconstruct the answer from metadata alone. Skip.
                    continue
                else:
                    continue

            # Pick the best answer: accepted first, else highest score
            answers.sort(key=lambda a: (not a["is_accepted"], -a["score"]))
            answer = answers[0]["body"]

            if len(question) > 20 and len(answer) > 20:
                title = record.get("metadata", {}).get("title", "")
                pairs.append({
                    "prompt": get_prompt("qa_stackexchange", question=f"{title}\n\n{question}" if title else question),
                    "completion": answer,
                    "task_type": "qa_stackexchange",
                    "source": record.get("source_url", ""),
                })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_kicad_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """KiCad ↔ описание. Uses the corpus cache (filtered to github_repo)."""
        pairs = []
        records = self._get_records_for_type(corpus_file, "github_repo", chunked=False)
        for record in records:
            content = record.get("content", "")
            files = record.get("downloaded_files", [])

            # Ищем KiCad файлы и README
            kicad_files = [f for f in files if f.get("type") == "kicad"]
            readme = content[:2000] if "README" in content or "===" in content else ""

            if kicad_files and readme:
                # KiCad → описание
                pairs.append({
                    "prompt": get_prompt("kicad_to_description", content=readme[:2000]),
                    "completion": f"This project contains {len(kicad_files)} KiCad schematic files.",
                    "task_type": "kicad_to_description",
                    "source": record.get("source_url", ""),
                })
            if len(pairs) >= max_n:
                break
        return pairs

    def _gen_faq_pairs(self, corpus_file: Path, max_n: int) -> list[dict]:
        """FAQ Q&A парсинг. Uses the corpus cache (all source_types)."""
        pairs = []
        qa_re = re.compile(
            r"(?:\*\*)?Q(?:uestion)?:?\s*\*{0,2}(.*?)(?:\n\s*\n)"
            r"(?:\*\*)?A(?:nswer)?:?\s*\*{0,2}(.*?)(?=\n\s*\n|\nQ|\Z)",
            re.DOTALL | re.IGNORECASE
        )
        records = self._get_records_for_type(corpus_file, None, chunked=False)
        for record in records:
            content = record.get("content", "")
            # Ищем FAQ секции
            if not re.search(r"FAQ|Frequently Asked|Часто задаваемые", content, re.IGNORECASE):
                continue

            for m in qa_re.finditer(content):
                q = m.group(1).strip().strip("*").strip()
                a = m.group(2).strip().strip("*").strip()
                if len(q) > 20 and len(a) > 20:
                    pairs.append({
                        "prompt": get_prompt("faq_qa", question=q),
                        "completion": a,
                        "task_type": "faq_qa",
                        "source": record.get("source_url", ""),
                    })
                    if len(pairs) >= max_n:
                        return pairs
        return pairs

    def _gen_multi_turn_dialogue(self, corpus_file: Path, max_n: int) -> list[dict]:
        """Multi-turn dialogue from StackExchange Q + multiple answers.

        Builds a conversation:
            User: <question>
            Assistant: <accepted or top answer>
            User: <follow-up derived from next answer's framing>
            Assistant: <next answer>
            ...

        Each pair's `prompt` is the FULL conversation up to the last user
        turn, and `completion` is the assistant's next response. This is the
        standard "supervised fine-tuning on conversations" format used by
        OpenAI / Anthropic / Mistral.

        We also store a `conversation` list in the pair metadata so the
        FormatConverter can emit proper ShareGPT/ChatML multi-turn format.
        """
        pairs = []
        # Pattern matches both RU and EN answer headers:
        #   ## Ответ (score=N) [ПРИНЯТ]
        #   ## Answer (score=N) [ACCEPTED]
        answer_re = re.compile(
            r'## (?:Ответ|Answer)\s*\(score=([\-\d]+)\)\s*(\[ПРИНЯТ\]|\[ACCEPTED\])?\s*\n\n(.*?)(?=\n## (?:Ответ|Answer)|\Z)',
            re.DOTALL,
        )

        records = self._get_records_for_type(corpus_file, "stackexchange", chunked=False)
        for record in records:
            content_text = record.get("content", "")
            meta = record.get("metadata", {})
            title = meta.get("title", "")

            # Extract question body
            q_match = re.search(
                r'## (?:Вопрос|Question)\n\n(.*?)(?=\n## (?:Ответ|Answer)|\Z)',
                content_text, re.DOTALL,
            )
            if not q_match:
                continue
            question_body = q_match.group(1).strip()
            if len(question_body) < 30:
                continue

            # Extract all answers, sorted by (accepted first, then score desc)
            answers = []
            for m in answer_re.finditer(content_text):
                score = int(m.group(1))
                is_accepted = bool(m.group(2))
                body = m.group(3).strip()
                if len(body) < 30:
                    continue
                answers.append({
                    "score": score,
                    "is_accepted": is_accepted,
                    "body": body,
                })
            if len(answers) < 2:
                continue  # Need at least 2 answers for multi-turn

            # Sort: accepted first, then by score desc
            answers.sort(key=lambda a: (not a["is_accepted"], -a["score"]))

            # Build conversation turns
            # Turn 1: user asks the question
            conversation = [
                {"role": "user", "content": f"{title}\n\n{question_body}" if title else question_body},
            ]
            # Turn 2: assistant gives first (best) answer
            conversation.append({"role": "assistant", "content": answers[0]["body"]})

            # For each subsequent answer, generate a context-aware follow-up
            # question that bridges from the previous answer to the next.
            # Instead of hardcoded templates, we extract key terms from the
            # previous answer and build a question around them. This produces
            # more natural and diverse follow-ups.
            import random as _random
            _rng = _random.Random(hash(record.get("source_url", "")) & 0xFFFFFFFF)

            # Take up to 3 more answers (so max 4 turns total: 2 user + 2 assistant)
            for ans in answers[1:4]:
                follow_up = self._generate_follow_up(
                    conversation[-1]["content"],  # previous assistant answer
                    ans["body"],                   # next answer (for context)
                    _rng,
                )
                conversation.append({"role": "user", "content": follow_up})
                conversation.append({"role": "assistant", "content": ans["body"]})

            # Build the multi-turn pair.
            # `prompt` = full conversation up to (but not including) the
            #   last assistant turn, formatted as "User: ...\n\nAssistant: ...".
            #   This is the text the model sees as context.
            # `completion` = the last assistant message (what the model
            #   should produce).
            # `conversation` = the full structured conversation list, used
            #   by FormatConverter to emit proper multi-turn ShareGPT/ChatML.
            prompt_parts = []
            for turn in conversation[:-1]:  # all but last assistant
                role = "User" if turn["role"] == "user" else "Assistant"
                prompt_parts.append(f"{role}: {turn['content']}")
            full_prompt = "\n\n".join(prompt_parts)
            completion = conversation[-1]["content"]

            pairs.append({
                "prompt": full_prompt,
                "completion": completion,
                "task_type": "multi_turn_dialogue",
                "source": record.get("source_url", ""),
                "conversation": conversation,  # full conversation for converters
            })
            if len(pairs) >= max_n:
                break
        return pairs

    @staticmethod
    def _generate_follow_up(prev_answer: str, next_answer: str,
                            rng: _random.Random) -> str:
        """Generate a context-aware follow-up question.

        Instead of hardcoded templates, extracts key terms from the previous
        answer and builds a question that naturally leads to the next answer.
        Falls back to generic questions if extraction fails.

        Args:
            prev_answer: the assistant's previous answer
            next_answer: the next answer (used to pick relevant terms)
            rng: random number generator for reproducibility

        Returns:
            A follow-up question string.
        """
        import re

        # Extract candidate terms: words 4+ chars, not stopwords, that appear
        # in BOTH answers (these are likely the "bridge" concepts).
        stop_words = {
            "the", "this", "that", "with", "from", "have", "will", "your",
            "what", "when", "which", "their", "they", "them", "then", "than",
            "been", "were", "would", "could", "should", "about", "there",
            "where", "into", "over", "after", "also", "more", "such", "only",
            "some", "very", "just", "much", "many", "most", "other", "into",
            "through", "during", "before", "above", "below", "between",
            "этом", "эта", "этот", "что", "как", "для", "при", "или", "также",
        }
        prev_words = set(re.findall(r'[A-Za-zА-Яа-я]{4,}', prev_answer.lower()))
        next_words = set(re.findall(r'[A-Za-zА-Яа-я]{4,}', next_answer.lower()))
        # Terms in both answers, excluding stopwords
        bridge_terms = (prev_words & next_words) - stop_words

        # Question templates that incorporate a bridge term
        if bridge_terms:
            term = rng.choice(sorted(bridge_terms))
            templates = [
                f"Can you tell me more about {term}?",
                f"What did you mean by {term}?",
                f"How does {term} relate to the rest?",
                f"Could you expand on {term}?",
                f"I'm not sure I understand {term} — can you clarify?",
                f"What are the implications of {term}?",
            ]
            return rng.choice(templates)

        # Fallback: generic but varied follow-ups (no hardcoded single phrase)
        generic = [
            "Can you elaborate on that?",
            "Could you explain that in more detail?",
            "What about the second part of my question?",
            "Are there any caveats or edge cases I should know about?",
            "How does that work in practice? Can you give an example?",
            "What if I need to handle a different scenario?",
            "Is there a simpler way to think about this?",
            "Could you expand on the trade-offs here?",
        ]
        return rng.choice(generic)

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
    def _iter_records_chunked(corpus_file: Path, max_chars: int = DEFAULT_CHUNK_CHARS) -> Iterator[dict]:
        """Iterate corpus records, splitting long ones into chunks.

        Each yielded record has the same metadata as the original, but
        `content` is replaced with a chunk of at most `max_chars` chars.
        Long articles become multiple chunks, allowing the instruction
        generators to see the FULL article (in pieces) instead of only
        the first 4000 chars.

        Chunk metadata is added to each record:
            chunk_index: 0-based index of this chunk
            total_chunks: total number of chunks for the original record
        """
        with open_corpus_reader(corpus_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for chunk in chunk_record(record, max_chars=max_chars):
                    yield chunk

    @staticmethod
    def get_stats(pairs: list[dict]) -> dict:
        """Статистика по типам инструкций."""
        stats: dict[str, int] = {}
        for pair in pairs:
            t = pair.get("task_type", "unknown")
            stats[t] = stats.get(t, 0) + 1
        return {"total": len(pairs), "by_type": stats}
