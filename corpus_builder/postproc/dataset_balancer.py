"""Балансировка классов в датасете для fine-tuning.

ВАЖНО о семантике `max_per_type` (I16): это НЕ «уравнивание» классов, а
ОБРЕЗКА больших классов случайной выборкой. Меньшинство не копируется и не
доучивается, большинство — ТЕРЯЕТСЯ. Сколько именно потеряно, возвращает
`get_balance_stats(..., original=...)`, чтобы это было видно в отчёте, а не
искалось глазами.
"""
from __future__ import annotations

import random
from collections import defaultdict


def balance_by_type(pairs: list[dict], max_per_type: int = 1000,
                    min_per_type: int = 0, seed: int = 42) -> list[dict]:
    """Ограничить каждый task_type сверху `max_per_type` (0 = без ограничения).

    Args:
        pairs: список пар
        max_per_type: максимум пар одного task_type; <= 0 — не ограничивать
        min_per_type: типы с меньшим числом пар удаляются целиком
        seed: RNG создаётся ОДИН на вызов (раньше `random.seed(42)` звался
            внутри цикла, и классы одинакового размера получали одинаковые
            индексы выборки).
    """
    by_type: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        t = pair.get("task_type", "unknown")
        by_type[t].append(pair)

    rng = random.Random(seed)
    cap = max_per_type if max_per_type and max_per_type > 0 else None

    result: list[dict] = []
    for _task_type, type_pairs in by_type.items():
        if cap is not None and len(type_pairs) > cap:
            selected = rng.sample(type_pairs, cap)
        else:
            selected = type_pairs

        if len(selected) < min_per_type:
            continue          # тип отсекается целиком — см. dropped в статистике

        result.extend(selected)

    return result


def get_balance_stats(pairs: list[dict], original: list[dict] | None = None) -> dict:
    """Статистика по (сбалансированному) списку пар.

    `original` — список ДО балансировки: тогда в результат попадает
    `dropped_by_type`/`dropped_total`, т.е. сколько пар съедал `max_per_type`.
    """
    by_type: dict[str, int] = defaultdict(int)
    for pair in pairs:
        t = pair.get("task_type", "unknown")
        by_type[t] += 1

    stats: dict = {
        "total": len(pairs),
        "by_type": dict(by_type),
        "num_types": len(by_type),
        "largest": max(by_type.values()) if by_type else 0,
        "smallest": min(by_type.values()) if by_type else 0,
    }

    if original is not None:
        before: dict[str, int] = defaultdict(int)
        for pair in original:
            before[pair.get("task_type", "unknown")] += 1
        dropped = {t: n - by_type.get(t, 0) for t, n in before.items()
                   if n - by_type.get(t, 0) > 0}
        if dropped:
            stats["dropped_by_type"] = dropped
            stats["dropped_total"] = sum(dropped.values())

    return stats
