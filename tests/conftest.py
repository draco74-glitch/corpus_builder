"""Общие фикстуры тестов.

Две вещи, без которых набор тестов непереносим:

1. `REPO_ROOT`/`SRC_ROOT` — пути к репозиторию. Тесты, читающие исходники
   (`ast.parse(open('corpus_builder/finetune_window.py'))`), падали при запуске
   не из корня репозитория.
2. Маркер `network` + опция `--run-network`. Тесты, которым нужен живой
   интернет, помечаются явно и по умолчанию ПРОПУСКАЮТСЯ: иначе CI офлайн или
   за фаерволом показывает красные результаты, а «зелёный CI» зависит от того,
   что вернул внешний API сегодня.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "corpus_builder"
TESTS_ROOT = Path(__file__).resolve().parent
CASSETTES_DIR = TESTS_ROOT / "cassettes"

# чтобы `python -m pytest` работал из любой директории
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network", action="store_true", default=False,
        help="разрешить тесты, которым нужен живой интернет (по умолчанию пропускаются)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers",
                            "network: требует доступа в интернет (см. --run-network)")


def pytest_collection_modifyitems(config: pytest.Config,
                                  items: list[pytest.Item]) -> None:
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="нужен интернет; запуск с --run-network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
