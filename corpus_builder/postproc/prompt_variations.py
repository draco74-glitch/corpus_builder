"""Prompt variations for instruction types.

Each task_type has 5-10 alternative phrasings. Generators pick one at random
to add diversity to the dataset — this prevents the model from memorizing a
single template and improves generalization.

Custom prompts can be loaded from a YAML file (prompts.yaml) or JSON file.
If the file exists, its variations are MERGED with the built-in defaults
(custom variations are appended, so they add to the diversity pool).

Example prompts.yaml:
    article_summary:
      - "Summarize this article for a beginner audience:\\n\\n{content}"
      - "What are the key takeaways from this text?\\n\\n{content}"
    code_explanation:
      - "Review this {lang} code and explain its design:\\n\\n```\n{code}\n```"

Usage:
    from .prompt_variations import get_prompt
    prompt = get_prompt("code_explanation", code=code, lang="python")
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger(__name__)

# Built-in variations per task_type. Each variation is a format string that
# will be filled with kwargs from the generator (e.g. {content}, {code}, {lang}).
# All variations for a given task_type MUST accept the same kwargs.
_DEFAULT_PROMPT_VARIATIONS: dict[str, list[str]] = {
    "article_summary": [
        "Write a brief summary of the following technical article:\n\n{content}",
        "Summarize the key points of this article in 2-3 sentences:\n\n{content}",
        "What is this article about? Provide a concise summary:\n\n{content}",
        "Extract the main ideas from the following text:\n\n{content}",
        "Give me a short overview of this technical article:\n\n{content}",
        "TL;DR this article:\n\n{content}",
        "Briefly describe what the following article covers:\n\n{content}",
    ],
    "code_explanation": [
        "Explain what this {lang} code does:\n\n```\n{code}\n```",
        "What does this {lang} function do? Walk through it:\n\n```\n{code}\n```",
        "Describe the purpose of this {lang} code snippet:\n\n```\n{code}\n```",
        "Break down this {lang} code step by step:\n\n```\n{code}\n```",
        "I'm trying to understand this {lang} code. Can you explain it?\n\n```\n{code}\n```",
        "Analyze the following {lang} code and explain its behavior:\n\n```\n{code}\n```",
        "What is the logic behind this {lang} code?\n\n```\n{code}\n```",
        "Could you walk me through what this {lang} code is doing?\n\n```\n{code}\n```",
    ],
    "concept_explanation": [
        'Explain the concept: "{heading}"',
        'What is "{heading}"? Please explain in detail.',
        'Define and explain: {heading}',
        'I keep hearing about "{heading}". What does it mean?',
        'Could you explain "{heading}" to me?',
        'Describe "{heading}" and why it matters.',
        'Give a thorough explanation of: {heading}',
    ],
    "datasheet_specs": [
        "Extract key electrical characteristics from this datasheet and list them as a structured table:\n\n{content}",
        "What are the main specifications of the component described in this datasheet?\n\n{content}",
        "List the electrical parameters from the following datasheet:\n\n{content}",
        "Summarize the key specs from this component datasheet:\n\n{content}",
        "Identify the operating voltage, current, and other key parameters from this datasheet:\n\n{content}",
    ],
    "bom_generation": [
        "Generate a Bill of Materials (BOM) for this electronics project:\n\n{content}",
        "List all the components needed for this project:\n\n{content}",
        "What parts do I need to build this? Create a BOM:\n\n{content}",
        "Extract the bill of materials from the following project description:\n\n{content}",
        "I'm building this electronics project. What components should I buy?\n\n{content}",
    ],
    "kicad_to_description": [
        "Describe the circuit based on this KiCad schematic:\n\n{content}",
        "What does this KiCad project do? Explain the circuit.\n\n{content}",
        "Summarize the function of this KiCad schematic:\n\n{content}",
        "Explain the circuit design from this KiCad project:\n\n{content}",
    ],
    "qa_stackexchange": [
        "Question: {question}\n\nPlease answer this electronics question.",
        "Q: {question}",
        "{question}",
        "Can you answer this question?\n\n{question}",
        "I have a question: {question}",
    ],
    "article_expansion": [
        "Expand the following short description into a full technical article:\n\n{summary}",
        "Write a detailed section based on this summary:\n\n{summary}",
        "Turn this abstract into a complete explanation for an engineering audience:\n\n{summary}",
    ],
    "description_to_kicad": [
        "Generate a KiCad schematic description (.kicad_sch) for the following project "
        "description:\n\n{desc}",
        "Write the .kicad_sch netlist-style description matching this circuit brief:"
        "\n\n{desc}",
    ],
    "datasheet_structure": [
        "Propose the documentation structure (section list) for this component:\n\n{component}",
        "Which datasheet sections should a document about {component} contain?",
    ],
    "faq_qa": [
        "{question}",
        "Question: {question}",
        "Q: {question}",
    ],
    # Multi-turn: variations are full conversation starts (the rest of the
    # turns come from the SE comments chain). We still provide 3 variations.
    "multi_turn_dialogue": [
        "Let's have a conversation about electronics.\n\nUser: {first_question}",
        "I'd like to ask you something.\n\nUser: {first_question}",
        "{first_question}",
    ],
}

# The active variations (defaults + any loaded from custom file).
# Use copy.deepcopy so modifications to PROMPT_VARIATIONS don't leak into
# _DEFAULT_PROMPT_VARIATIONS (which should remain pristine for reset).
import copy as _copy

PROMPT_VARIATIONS: dict[str, list[str]] = _copy.deepcopy(_DEFAULT_PROMPT_VARIATIONS)

# Seed for reproducibility — set via set_seed() if needed.
_rng = random.Random(42)


def set_seed(seed: int) -> None:
    """Set the RNG seed for deterministic prompt selection."""
    global _rng
    _rng = random.Random(seed)


def merge_custom_prompts(file_path: str | Path) -> bool:
    """Загрузить кастомные промпты; True, если что-то добавилось.

    Встроенные варианты СОХРАНЯЮТСЯ (append): опечатка в prompts.yaml не должна
    молча лишать датасет разнообразия шаблонов.
    """
    return bool(load_custom_prompts(file_path))


def load_custom_prompts(file_path: str | Path) -> dict[str, list[str]]:
    """Load custom prompt variations from a YAML or JSON file.

    The file should map task_type → list of prompt format strings.
    Loaded variations are MERGED with the built-in defaults: custom
    variations are appended to each task_type's list.

    Args:
        file_path: path to .yaml or .json file

    Returns:
        dict of newly added variations (for reporting)
    """
    file_path = Path(file_path)
    if not file_path.exists():
        log.debug(f"Custom prompts file not found: {file_path}")
        return {}

    try:
        if file_path.suffix.lower() in ('.yaml', '.yml'):
            try:
                import yaml
            except ImportError:
                log.warning("PyYAML not installed — cannot load .yaml prompts file")
                return {}
            with open(file_path, 'r', encoding='utf-8') as f:
                custom = yaml.safe_load(f) or {}
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                custom = json.load(f)
    except Exception as e:
        log.warning(f"Failed to load custom prompts from {file_path}: {e}")
        return {}

    if not isinstance(custom, dict):
        log.warning(f"Custom prompts file must be a dict, got {type(custom)}")
        return {}

    added: dict[str, list[str]] = {}
    for task_type, variations in custom.items():
        if not isinstance(variations, list):
            log.warning(f"Custom prompts for '{task_type}' must be a list, got {type(variations)}")
            continue
        # Filter to valid string variations
        valid = [v for v in variations if isinstance(v, str)]
        if not valid:
            continue
        # Merge: append to existing or create new entry
        if task_type not in PROMPT_VARIATIONS:
            PROMPT_VARIATIONS[task_type] = []
        PROMPT_VARIATIONS[task_type].extend(valid)
        added[task_type] = valid
        log.info(f"Loaded {len(valid)} custom prompts for '{task_type}'")

    return added


def get_prompt(task_type: str, **kwargs: Any) -> str:
    """Pick a random prompt variation for task_type and fill in kwargs.

    Falls back to a simple join of kwargs['content'] (or first kwarg value)
    if task_type is unknown, so generators don't crash on missing variations.
    """
    variations = PROMPT_VARIATIONS.get(task_type)
    if not variations:
        # Fallback: use 'content' if available, else first string kwarg
        content = kwargs.get("content") or ""
        if not content:
            for v in kwargs.values():
                if isinstance(v, str) and v:
                    content = v
                    break
        return content
    template = _rng.choice(variations)

    # Подставляем через «безопасный» dict: неизвестный ключ не роняет генератор,
    # но и литерал вида "{content}" в датасет попасть не должен (I13).
    filled, missing = _try_format(template, kwargs)

    if missing:
        # выбранный вариант хочет ключи, которых у генератора нет —
        # пробуем другие варианты этого task_type
        for tmpl in variations:
            candidate, cand_missing = _try_format(tmpl, kwargs)
            if candidate is not None and not cand_missing:
                return candidate
        # ни один вариант не подходит: собираем текст из того, что дал
        # генератор, без фигурных скобок
        log.warning(f"None of {len(variations)} variations for '{task_type}' fit "
                    f"kwargs {sorted(kwargs)} (template wanted {sorted(set(missing))}); "
                    f"built a plain prompt instead")
        parts = [f"{k}: {v}" for k, v in kwargs.items() if isinstance(v, str) and v]
        return "\n\n".join(parts) or (filled or "")

    return filled or ""


def _try_format(template: str, kwargs: dict) -> tuple[str | None, list[str]]:
    """(результат, список отсутствующих ключей); результат None при ошибке."""
    probe = _SafeDict(kwargs)
    try:
        return template.format_map(probe), list(probe.missing)
    except (IndexError, KeyError, ValueError) as e:
        log.debug(f"prompt template {template[:30]!r} failed to format: {e}")
        return None, list(probe.missing)


class _SafeDict(dict):
    """dict для str.format_map: remembers missing keys instead of raising."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing: list[str] = []

    def __missing__(self, key):
        self.missing.append(key)
        return ""


def list_variations(task_type: str) -> list[str]:
    """Return all variations for a task_type (for inspection/testing)."""
    return list(PROMPT_VARIATIONS.get(task_type, []))


def get_variation_count(task_type: str) -> int:
    """Return number of variations for a task_type."""
    return len(PROMPT_VARIATIONS.get(task_type, []))


def reset_to_defaults() -> None:
    """Reset PROMPT_VARIATIONS to built-in defaults (discard custom)."""
    global PROMPT_VARIATIONS
    PROMPT_VARIATIONS = _copy.deepcopy(_DEFAULT_PROMPT_VARIATIONS)
