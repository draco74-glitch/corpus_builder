"""Состояние краулинга: какие URL уже обработаны, чтобы поддержать resume."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

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

    def reset(self) -> None:
        """Забыть всё состояние (запуск без resume)."""
        with self._lock:
            self._done.clear()
            self._errors.clear()

    def clear_errors(self) -> None:
        """Разрешить повторную обработку ранее упавших URL (retry-errors)."""
        with self._lock:
            self._errors.clear()

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

    def save(self, compact: bool = False) -> int:
        """Атомарная запись состояния (временный файл + os.replace).

        `compact=True` — для ПРОМЕЖУТОЧНЫХ чекпойнтов: без сортировки и без
        отступов. Прежний save() на каждый чекпойнт заново сортировал и
        форматировал всё множество URL, т.е. было O(n) на запись и O(n²) за
        ран (A5). Отсортированный человекочитаемый вид остаёт финальному
        сохранению. Возвращает число записанных URL.
        """
        with self._lock:
            done, errors = set(self._done), set(self._errors)
        if compact:
            data = {"done": list(done), "errors": list(errors), "sorted": False}
            dump_kwargs = {"ensure_ascii": False, "separators": (",", ":")}
        else:
            data = {"done": sorted(done), "errors": sorted(errors), "sorted": True}
            dump_kwargs = {"ensure_ascii": False, "indent": 2}
        tmp = str(self.state_file) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, **dump_kwargs)
            os.replace(tmp, self.state_file)
        except OSError as e:
            log.warning(f"Failed to save state: {e}")
            return 0
        return len(done) + len(errors)

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
