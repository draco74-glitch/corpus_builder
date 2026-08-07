"""Состояние краулинга: какие URL уже обработаны, чтобы поддержать resume."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable

from .logging_setup import get_logger

log = get_logger(__name__)


class State:
    """Хранилище обработанных URL с атомарной записью."""

    def __init__(self, state_file: str | Path):
        self.state_file = Path(state_file)
        self._done: set[str] = set()
        self._errors: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self, silent: bool = False) -> None:
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._done = set(data.get("done", []))
            self._errors = set(data.get("errors", []))
            if not silent:
                log.info(f"State loaded: {len(self._done)} done, {len(self._errors)} errors")
        except Exception as e:
            if not silent:
                log.warning(f"Failed to load state, starting fresh: {e}")
            self._done = set()
            self._errors = set()

    def reload_silent(self) -> None:
        """Перечитать state без логирования — для периодических опросов в GUI."""
        self._load(silent=True)

    def is_done(self, url: str) -> bool:
        with self._lock:
            return url in self._done

    def is_error(self, url: str) -> bool:
        with self._lock:
            return url in self._errors

    def mark_done(self, url: str) -> None:
        with self._lock:
            self._done.add(url)

    def mark_error(self, url: str) -> None:
        with self._lock:
            self._errors.add(url)

    def save(self) -> None:
        """Атомарная запись состояния во временный файл с переименованием."""
        with self._lock:
            data = {
                "done": sorted(self._done),
                "errors": sorted(self._errors),
            }
        tmp = str(self.state_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_file)

    def __len__(self) -> int:
        with self._lock:
            return len(self._done)

    @property
    def done_count(self) -> int:
        return len(self)

    @property
    def error_count(self) -> int:
        with self._lock:
            return len(self._errors)
