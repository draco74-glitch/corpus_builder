"""Буферизованная запись в JSONL."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, IO


class CorpusWriter:
    def __init__(self, path: str | Path, buffer_size: int = 100, encoding: str = "utf-8"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.buffer_size = buffer_size
        self.encoding = encoding
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self.fh: IO = open(self.path, "a", encoding=encoding)

    def write(self, record: dict | str) -> None:
        if isinstance(record, dict):
            line = json.dumps(record, ensure_ascii=False) + "\n"
        else:
            line = record if record.endswith("\n") else record + "\n"
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= self.buffer_size:
                self._flush_locked()

    def write_many(self, records: list[dict | str]) -> None:
        with self._lock:
            for r in records:
                line = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) + "\n"
                if not line.endswith("\n"):
                    line += "\n"
                self._buffer.append(line)
                if len(self._buffer) >= self.buffer_size:
                    self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        try:
            self.fh.writelines(self._buffer)
            self.fh.flush()
        except Exception:
            try:
                self.fh.close()
            except Exception:
                pass
            self.fh = open(self.path, "a", encoding=self.encoding)
            self.fh.writelines(self._buffer)
            self.fh.flush()
        self._buffer.clear()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        self.flush()
        try:
            self.fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class GzipCorpusWriter(CorpusWriter):
    def __init__(self, path: str | Path, buffer_size: int = 100,
                 compression_level: int = 6):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.buffer_size = buffer_size
        self.encoding = "utf-8"
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        import gzip
        self.fh = gzip.open(self.path, "at", encoding="utf-8",
                            compresslevel=compression_level)


def is_gzip_file(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            magic = f.read(2)
        return magic == b"\x1f\x8b"
    except Exception:
        return False


def open_corpus_reader(path: str | Path, encoding: str = "utf-8") -> Any:
    path = Path(path)
    if path.suffix == ".gz" or is_gzip_file(path):
        import gzip
        return gzip.open(path, "rt", encoding=encoding)
    return open(path, "r", encoding=encoding)
