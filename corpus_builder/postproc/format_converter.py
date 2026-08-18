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
from typing import Iterator

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
    ) -> dict:
        """Конвертировать instruction_pairs.jsonl в указанный формат.

        Параметры:
            input_file: путь к instruction_pairs.jsonl
            output_file: путь к выходному файлу
            format: "jsonl" | "chatml" | "alpaca" | "sharegpt"

        Возвращает dict со статистикой.
        """
        input_file = Path(input_file)
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        converters = {
            "jsonl": FormatConverter._to_jsonl,
            "chatml": FormatConverter._to_chatml,
            "alpaca": FormatConverter._to_alpaca,
            "sharegpt": FormatConverter._to_sharegpt,
        }

        converter = converters.get(format)
        if converter is None:
            raise ValueError(f"Unknown format: {format}. Supported: {list(converters)}")

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
    def _to_chatml(prompt: str, completion: str, pair: dict) -> str:
        """ChatML format: text with special tokens."""
        text = (
            f"<|im_start|>user\n{prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n{completion}<|im_end|>"
        )
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
        """ShareGPT format: {"conversations": [{"from": "human", "value": "..."}, ...]}"""
        record = {
            "conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": completion},
            ]
        }
        if "source" in pair:
            record["source"] = pair["source"]
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
            formats = ["jsonl", "chatml", "alpaca", "sharegpt"]

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
