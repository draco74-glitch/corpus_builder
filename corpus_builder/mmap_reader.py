"""Memory-mapped чтение для пост-обработки больших корпусов."""
from __future__ import annotations

import json
import mmap
from collections.abc import Iterator
from pathlib import Path

from .logging_setup import get_logger
from .writer import is_gzip_file, open_corpus_reader

log = get_logger(__name__)


class MmapJsonlReader:
    def __init__(self, path: str | Path, encoding: str = "utf-8",
                 min_size_for_mmap: int = 100 * 1024 * 1024):
        self.path = Path(path)
        self.encoding = encoding
        self.min_size_for_mmap = min_size_for_mmap
        self._file_size = self.path.stat().st_size if self.path.exists() else 0
        self._use_mmap = (
            self._file_size >= min_size_for_mmap
            and not is_gzip_file(self.path)
            and self.path.suffix != ".gz"
        )
        self._fh = None
        self._mmap = None

    def __enter__(self):
        if self._use_mmap:
            self._fh = open(self.path, "rb")
            try:
                self._mmap = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):
                self._fh.close()
                self._use_mmap = False
                self._fh = None
        if not self._use_mmap:
            self._fh = open_corpus_reader(self.path, self.encoding)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._mmap:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        return False

    def __iter__(self) -> Iterator[str]:
        if self._use_mmap and self._mmap:
            for line in iter(self._mmap.readline, b""):
                try:
                    yield line.decode(self.encoding, errors="replace").rstrip("\n")
                except Exception:
                    continue
        elif self._fh:
            for line in self._fh:
                yield line.rstrip("\n")

    def iter_records(self) -> Iterator[dict]:
        for line in self:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def count_lines(self) -> int:
        if self._use_mmap and self._mmap:
            return self._mmap.read().count(b"\n")
        count = 0
        with open_corpus_reader(self.path, self.encoding) as f:
            for _ in f:
                count += 1
        return count
