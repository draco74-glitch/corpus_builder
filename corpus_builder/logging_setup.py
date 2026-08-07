"""Настройка логирования на базе loguru."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger as loguru_logger

_configured = False


def setup_logging(log_file: str | Path | None = None, verbose: bool = False) -> None:
    """Настроить логи: stderr + вращаемый файл."""
    global _configured
    if _configured:
        return

    loguru_logger.remove()
    level = "DEBUG" if verbose else "INFO"

    # stderr
    loguru_logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> <level>[{level:<7}]</level> <cyan>{name}</cyan>: {message}",
        colorize=True,
    )

    # Файл с ротацией
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        loguru_logger.add(
            str(log_file),
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} [{level:<7}] {name}:{line} {message}",
            rotation="10 MB",
            retention="5",
            encoding="utf-8",
        )

    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Вернуть логгер с именем модуля."""
    if not _configured:
        setup_logging()
    return loguru_logger.bind(name=name or "root")
