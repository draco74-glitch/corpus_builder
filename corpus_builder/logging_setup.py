"""Настройка логирования на базе loguru.

Безопасно работает в трёх окружениях:
  1. CLI/обычный Python: логи идут в stderr + файл
  2. Windowed GUI (.exe без консоли): sys.stderr равен None,
     логи идут только в файл (если указан) или пропускаются
  3. Fallback: если ни stderr, ни файл недоступны, логи буферизируются
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger as loguru_logger

_configured = False
_pending_log_file: str | Path | None = None


def setup_logging(log_file: str | Path | None = None, verbose: bool = False) -> None:
    """Настроить логи: stderr (если доступен) + вращаемый файл.

    В windowed-режиме PyInstaller (console=False) sys.stderr равен None,
    поэтому нельзя вызывать loguru_logger.add(sys.stderr, ...) — упадёт с
    TypeError: Cannot log to objects of type 'NoneType'.
    """
    global _configured, _pending_log_file
    if _configured:
        if log_file is not None:
            _add_file_handler(log_file, verbose)
        return

    loguru_logger.remove()
    level = "DEBUG" if verbose else "INFO"

    # stderr — только если он существует и writable
    stderr = sys.stderr
    if stderr is not None and hasattr(stderr, "write"):
        try:
            loguru_logger.add(
                stderr,
                level=level,
                format="<green>{time:HH:mm:ss}</green> <level>[{level:<7}]</level> <cyan>{name}</cyan>: {message}",
                colorize=True,
            )
        except (TypeError, ValueError, OSError):
            pass

    if log_file is not None:
        _add_file_handler(log_file, verbose)
    else:
        _pending_log_file = log_file

    _configured = True


def _add_file_handler(log_file: str | Path, verbose: bool = False) -> None:
    global _pending_log_file
    try:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        loguru_logger.add(
            str(log_file),
            level="DEBUG" if verbose else "INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} [{level:<7}] {name}:{line} {message}",
            rotation="10 MB",
            retention="5 days",
            encoding="utf-8",
        )
        _pending_log_file = None
    except (OSError, PermissionError) as e:
        try:
            loguru_logger.warning(f"Cannot create log file {log_file}: {e}")
        except Exception:
            pass


def is_frozen() -> bool:
    """Возвращает True, если код запущен из PyInstaller-сборки."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_log_dir() -> Path:
    """Папка для логов в зависимости от окружения."""
    if is_frozen():
        base = Path(sys.executable).parent
    else:
        base = Path.cwd()
    log_dir = base / "corpus_output"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_logger(name: str | None = None) -> Any:
    """Вернуть логгер с именем модуля."""
    global _configured, _pending_log_file
    if not _configured:
        default_log_file = get_log_dir() / "crawl.log"
        setup_logging(default_log_file)
    elif _pending_log_file is None and not _has_file_handler():
        default_log_file = get_log_dir() / "crawl.log"
        _add_file_handler(default_log_file)
    return loguru_logger.bind(name=name or "root")


def _has_file_handler() -> bool:
    """Проверить, есть ли в loguru хотя бы один файловый handler."""
    try:
        handlers = loguru_logger._core.handlers
        if not handlers:
            return False
        for handler in handlers.values():
            try:
                writer = handler._writer
                if hasattr(writer, "_stream") and hasattr(writer._stream, "name"):
                    return True
            except (AttributeError, RuntimeError):
                continue
        return False
    except Exception:
        return False
