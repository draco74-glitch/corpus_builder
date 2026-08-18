"""Балансировка классов в датасете для fine-tuning."""
from __future__ import annotations
from collections import defaultdict
import random

def balance_by_type(pairs: list[dict], max_per_type: int = 1000,
                    min_per_type: int = 0) -> list[dict]:
    """Сбалансировать пары по task_type."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        t = pair.get("task_type", "unknown")
        by_type[t].append(pair)

    result = []
    for task_type, type_pairs in by_type.items():
        if len(type_pairs) > max_per_type:
            # Случайная выборка
            random.seed(42)
            selected = random.sample(type_pairs, max_per_type)
        else:
            selected = type_pairs

        # Пропускаем типы с слишком малым числом пар
        if len(selected) < min_per_type:
            continue

        result.extend(selected)

    return result

def get_balance_stats(pairs: list[dict]) -> dict:
    """Статистика балансировки."""
    by_type: dict[str, int] = defaultdict(int)
    for pair in pairs:
        t = pair.get("task_type", "unknown")
        by_type[t] += 1
    return {
        "total": len(pairs),
        "by_type": dict(by_type),
        "num_types": len(by_type),
    }
