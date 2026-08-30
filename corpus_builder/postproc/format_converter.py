"""Конвертация instruction pairs в форматы для fine-tuning.

Поддерживаемые форматы:
  - JSONL    : {"prompt": "...", "completion": "..."}          (стандартный)
  - ChatML   : <|im_start|>user\n...<|im_end|>\n<|im_start|>assistant\n...<|im_end|>
  - Alpaca   : {"instruction": "...", "input": "", "output": "..."}
  - ShareGPT : {"conversations": [{"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]}

Использование:
    from corpus_builder.postproc.format_converter import FormatConverter
    converter = FormatConverter()
    converter.convert("instruction_pairs.jsonl", "finetune_chatml.jsonl", "chatml")
"""
from __future__ import annotations

import json
from pathlib import Path

from ..logging_setup import get_logger
from ..writer import open_corpus_reader

log = get_logger(__name__)


class FormatConverter:
    """Конвертер instruction pairs в форматы для fine-tuning."""

    @staticmethod
    def convert(
        input_file: str | Path,
        output_file: str | Path,
        format: str = "jsonl",
        system_prompt: str = "",
    ) -> dict:
        """Конвертировать instruction_pairs.jsonl в указанный формат.

        Параметры:
            input_file: путь к instruction_pairs.jsonl
            output_file: путь к выходному файлу
            format: "jsonl" | "chatml" | "alpaca" | "sharegpt" |
                    "axolotl" | "llama_factory"
            system_prompt: optional system message (only used by chatml,
                axolotl, llama_factory formats)

        Возвращает dict со статистикой.
        """
        input_file = Path(input_file)
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Map format names to converter functions.
        # Note: chatml, axolotl, llama_factory accept system_prompt.
        converter_map = {
            "jsonl": FormatConverter._to_jsonl,
            "alpaca": FormatConverter._to_alpaca,
            "sharegpt": FormatConverter._to_sharegpt,
            "chatml": FormatConverter._to_chatml,
            "axolotl": FormatConverter._to_axolotl,
            "llama_factory": FormatConverter._to_llama_factory,
        }

        converter = converter_map.get(format)
        if converter is None:
            raise ValueError(f"Unknown format: {format}. Supported: {list(converter_map)}")

        count = 0
        with open_corpus_reader(input_file) as fin, \
             open(output_file, "w", encoding="utf-8") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    pair = json.loads(line)
                except json.JSONDecodeError:
                    continue

                prompt = pair.get("prompt", "")
                completion = pair.get("completion", "")
                if not prompt or not completion:
                    continue

                # Pass system_prompt only to formats that support it
                if format in ("chatml", "axolotl", "llama_factory"):
                    output_line = converter(prompt, completion, pair, system_prompt=system_prompt)
                else:
                    output_line = converter(prompt, completion, pair)
                fout.write(output_line + "\n")
                count += 1

        stats = {"format": format, "count": count, "output": str(output_file)}
        log.info(f"Converted {count} pairs to {format} → {output_file}")
        return stats

    @staticmethod
    def _to_jsonl(prompt: str, completion: str, pair: dict) -> str:
        """Standard JSONL format: {"prompt": "...", "completion": "..."}"""
        record = {"prompt": prompt, "completion": completion}
        if "task_type" in pair:
            record["task_type"] = pair["task_type"]
        if "source" in pair:
            record["source"] = pair["source"]
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def _to_chatml(prompt: str, completion: str, pair: dict,
                   system_prompt: str = "") -> str:
        """ChatML format: text with special tokens.

        If pair has a 'conversation' field (multi-turn), build a proper
        multi-turn ChatML text with alternating user/assistant turns.
        Otherwise fall back to single-turn prompt/completion.

        Args:
            system_prompt: optional system message prepended to the text
                (e.g. "You are a helpful assistant that explains electronics.")
        """
        parts = []
        if system_prompt:
            parts.append(f"<|im_start|>system\n{system_prompt}<|im_end|>")

        conv = pair.get("conversation")
        if conv and isinstance(conv, list) and len(conv) >= 2:
            # Build multi-turn ChatML from the conversation field.
            for turn in conv:
                role = turn.get("role", "user")
                value = turn.get("content", "")
                if value:
                    parts.append(f"<|im_start|>{role}\n{value}<|im_end|>")
        else:
            # Single-turn fallback
            parts.append(f"<|im_start|>user\n{prompt}<|im_end|>")
            parts.append(f"<|im_start|>assistant\n{completion}<|im_end|>")
        text = "\n".join(parts)
        return json.dumps({"text": text, "task_type": pair.get("task_type", "")},
                         ensure_ascii=False)

    @staticmethod
    def _to_alpaca(prompt: str, completion: str, pair: dict) -> str:
        """Alpaca format: {"instruction": "...", "input": "", "output": "..."}"""
        # Try to split prompt into instruction + input
        # Heuristic: if prompt has a newline, first line = instruction, rest = input
        if "\n" in prompt:
            parts = prompt.split("\n", 1)
            instruction = parts[0].strip()
            input_text = parts[1].strip()
        else:
            instruction = prompt.strip()
            input_text = ""

        record = {
            "instruction": instruction,
            "input": input_text,
            "output": completion,
        }
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def _to_sharegpt(prompt: str, completion: str, pair: dict) -> str:
        """ShareGPT format: {"conversations": [{"from": "human", "value": "..."}, ...]}

        If pair has a 'conversation' field (multi-turn), use it to build
        a proper multi-turn ShareGPT record. Otherwise fall back to
        single-turn prompt/completion.
        """
        conv = pair.get("conversation")
        if conv and isinstance(conv, list) and len(conv) >= 2:
            # Build multi-turn conversations from the conversation field.
            # Map roles: user -> human, assistant -> gpt.
            role_map = {"user": "human", "assistant": "gpt", "system": "system"}
            conversations = []
            for turn in conv:
                role = turn.get("role", "user")
                from_role = role_map.get(role, "human")
                value = turn.get("content", "")
                if value:  # skip empty turns
                    conversations.append({"from": from_role, "value": value})
            record = {"conversations": conversations}
        else:
            # Single-turn fallback
            record = {
                "conversations": [
                    {"from": "human", "value": prompt},
                    {"from": "gpt", "value": completion},
                ]
            }
        if "source" in pair:
            record["source"] = pair["source"]
        if "task_type" in pair:
            record["task_type"] = pair["task_type"]
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def _to_axolotl(prompt: str, completion: str, pair: dict,
                    system_prompt: str = "") -> str:
        """Axolotl format: {"conversation": [{"from": "human"/"gpt"/"system", "value": "..."}]}

        Axolotl uses the same ShareGPT-like structure but the field is named
        'conversation' instead of 'conversations'. Supports multi-turn via
        the 'conversation' field and optional system prompt.
        """
        conv = pair.get("conversation")
        role_map = {"user": "human", "assistant": "gpt", "system": "system"}
        messages = []
        if system_prompt:
            messages.append({"from": "system", "value": system_prompt})
        if conv and isinstance(conv, list) and len(conv) >= 2:
            for turn in conv:
                role = turn.get("role", "user")
                from_role = role_map.get(role, "human")
                value = turn.get("content", "")
                if value:
                    messages.append({"from": from_role, "value": value})
        else:
            messages.append({"from": "human", "value": prompt})
            messages.append({"from": "gpt", "value": completion})
        record = {"conversation": messages}
        if "source" in pair:
            record["source"] = pair["source"]
        if "task_type" in pair:
            record["task_type"] = pair["task_type"]
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def _to_llama_factory(prompt: str, completion: str, pair: dict,
                          system_prompt: str = "") -> str:
        """LLaMA-Factory format: {"messages": [{"role": "user"/"assistant"/"system", "content": "..."}]}

        LLaMA-Factory uses OpenAI-style message format with a 'messages' list.
        Supports multi-turn via the 'conversation' field and optional system prompt.
        """
        conv = pair.get("conversation")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if conv and isinstance(conv, list) and len(conv) >= 2:
            for turn in conv:
                role = turn.get("role", "user")
                value = turn.get("content", "")
                if value:
                    messages.append({"role": role, "content": value})
        else:
            messages.append({"role": "user", "content": prompt})
            messages.append({"role": "assistant", "content": completion})
        record = {"messages": messages}
        if "source" in pair:
            record["source"] = pair["source"]
        if "task_type" in pair:
            record["task_type"] = pair["task_type"]
        return json.dumps(record, ensure_ascii=False)

    @staticmethod
    def convert_all(
        input_file: str | Path,
        output_dir: str | Path,
        formats: list[str] | None = None,
    ) -> dict:
        """Конвертировать во все поддерживаемые форматы сразу.

        Параметры:
            input_file: instruction_pairs.jsonl
            output_dir: папка для выходных файлов
            formats: список форматов (по умолчанию — все)

        Возвращает dict со статистикой по каждому формату.
        """
        if formats is None:
            formats = ["jsonl", "chatml", "alpaca", "sharegpt", "axolotl", "llama_factory"]

        input_file = Path(input_file)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = {}
        for fmt in formats:
            ext = "jsonl"
            output_file = output_dir / f"finetune_{fmt}.{ext}"
            try:
                stats = FormatConverter.convert(input_file, output_file, fmt)
                results[fmt] = stats
            except Exception as e:
                log.error(f"Failed to convert to {fmt}: {e}")
                results[fmt] = {"error": str(e)}

        return results

    # ============================================================
    # Train / Validation split
    # ============================================================

    @staticmethod
    def split_dataset(
        input_file: str | Path,
        output_dir: str | Path,
        val_ratio: float = 0.1,
        seed: int = 42,
        formats: list[str] | None = None,
        stratify_by_type: bool = True,
    ) -> dict:
        """Split instruction_pairs.jsonl into train/val sets.

        For each format, produces:
            - train.{format}.jsonl  (1 - val_ratio of pairs)
            - val.{format}.jsonl    (val_ratio of pairs)

        Args:
            input_file: path to instruction_pairs.jsonl
            output_dir: directory to write train/val files
            val_ratio: fraction of pairs for validation (0.0-1.0)
            seed: random seed for reproducibility
            formats: list of formats to convert (default: all 4)
            stratify_by_type: if True, split is stratified by task_type
                so each type appears in both train and val proportionally.

        Returns:
            stats dict with per-format train/val counts.
        """
        if not (0.0 < val_ratio < 1.0):
            raise ValueError(f"val_ratio must be in (0, 1), got {val_ratio}")
        if formats is None:
            formats = ["jsonl", "chatml", "alpaca", "sharegpt", "axolotl", "llama_factory"]

        input_file = Path(input_file)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load all pairs
        import random as _random
        all_pairs: list[dict] = []
        with open_corpus_reader(input_file) as fin:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    all_pairs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if not all_pairs:
            raise ValueError(f"No valid pairs found in {input_file}")

        rng = _random.Random(seed)

        if stratify_by_type:
            # Group by task_type, split each group independently
            by_type: dict[str, list[dict]] = {}
            for p in all_pairs:
                t = p.get("task_type", "unknown")
                by_type.setdefault(t, []).append(p)

            train_pairs: list[dict] = []
            val_pairs: list[dict] = []
            for t, type_pairs in by_type.items():
                shuffled = list(type_pairs)
                rng.shuffle(shuffled)
                n_val = max(1, int(len(shuffled) * val_ratio))
                # Ensure train has at least 1 if group has >= 2
                if len(shuffled) >= 2 and n_val >= len(shuffled):
                    n_val = len(shuffled) - 1
                val_pairs.extend(shuffled[:n_val])
                train_pairs.extend(shuffled[n_val:])
        else:
            shuffled = list(all_pairs)
            rng.shuffle(shuffled)
            n_val = max(1, int(len(shuffled) * val_ratio))
            val_pairs = shuffled[:n_val]
            train_pairs = shuffled[n_val:]

        # Write splits for each format
        results = {}
        for fmt in formats:
            train_file = output_dir / f"train.{fmt}.jsonl"
            val_file = output_dir / f"val.{fmt}.jsonl"
            try:
                train_stats = FormatConverter._write_pairs(train_pairs, train_file, fmt)
                val_stats = FormatConverter._write_pairs(val_pairs, val_file, fmt)
                results[fmt] = {
                    "train": {"count": train_stats, "file": str(train_file)},
                    "val": {"count": val_stats, "file": str(val_file)},
                }
                log.info(f"[split/{fmt}] train={train_stats} val={val_stats}")
            except Exception as e:
                log.error(f"Failed to split {fmt}: {e}")
                results[fmt] = {"error": str(e)}

        # Summary
        results["_summary"] = {
            "total_input": len(all_pairs),
            "total_train": len(train_pairs),
            "total_val": len(val_pairs),
            "val_ratio": val_ratio,
            "seed": seed,
            "stratified": stratify_by_type,
            "by_type": {
                t: {"train": sum(1 for p in train_pairs if p.get("task_type", "unknown") == t),
                    "val": sum(1 for p in val_pairs if p.get("task_type", "unknown") == t)}
                for t in by_type
            } if stratify_by_type else None,
        }
        return results

    @staticmethod
    def _write_pairs(pairs: list[dict], output_file: Path, fmt: str) -> int:
        """Write a list of pairs to output_file in the given format.

        Returns the number of pairs written.
        """
        converter_map = {
            "jsonl": FormatConverter._to_jsonl,
            "alpaca": FormatConverter._to_alpaca,
            "sharegpt": FormatConverter._to_sharegpt,
            "chatml": FormatConverter._to_chatml,
            "axolotl": FormatConverter._to_axolotl,
            "llama_factory": FormatConverter._to_llama_factory,
        }
        converter = converter_map.get(fmt)
        if converter is None:
            raise ValueError(f"Unknown format: {fmt}. Supported: {list(converter_map)}")

        output_file.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(output_file, "w", encoding="utf-8") as fout:
            for pair in pairs:
                prompt = pair.get("prompt", "")
                completion = pair.get("completion", "")
                if not prompt or not completion:
                    continue
                if fmt in ("chatml", "axolotl", "llama_factory"):
                    output_line = converter(prompt, completion, pair, system_prompt="")
                else:
                    output_line = converter(prompt, completion, pair)
                fout.write(output_line + "\n")
                count += 1
        return count
