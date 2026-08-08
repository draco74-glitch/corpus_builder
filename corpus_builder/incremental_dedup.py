"""Incremental dedup: LSH-индекс сохраняется между запусками."""
from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Callable

from datasketch import MinHash, MinHashLSH

from .logging_setup import get_logger
from .mmap_reader import MmapJsonlReader
from .text_utils import normalize_text, shingles

log = get_logger(__name__)


class IncrementalDedup:
    def __init__(self, index_path: str | Path, threshold: float = 0.85, num_perm: int = 128):
        self.index_path = Path(index_path)
        self.threshold = threshold
        self.num_perm = num_perm
        self.lsh: MinHashLSH | None = None
        self.minhashes: dict[str, MinHash] = {}
        self.processed_urls: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
            return
        try:
            with open(self.index_path, "rb") as f:
                data = pickle.load(f)
            self.lsh = data.get("lsh")
            self.minhashes = data.get("minhashes", {})
            self.processed_urls = set(data.get("processed_urls", []))
            if self.lsh is None:
                self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        except Exception:
            self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
            self.minhashes = {}
            self.processed_urls = set()

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp_path = str(self.index_path) + ".tmp"
            with open(tmp_path, "wb") as f:
                pickle.dump({
                    "lsh": self.lsh, "minhashes": self.minhashes,
                    "processed_urls": list(self.processed_urls),
                    "threshold": self.threshold, "num_perm": self.num_perm,
                }, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, self.index_path)
        except Exception as e:
            log.warning(f"Failed to save LSH index: {e}")

    def compute_minhash(self, text: str) -> MinHash:
        mh = MinHash(num_perm=self.num_perm)
        for s in shingles(text, k=5):
            mh.update(s.encode("utf-8"))
        return mh

    def add(self, url: str, text: str) -> str | None:
        if url in self.processed_urls:
            return None
        text = normalize_text(text)
        if not text:
            return None
        mh = self.compute_minhash(text)
        matches = self.lsh.query(mh) if self.lsh else []
        if matches:
            self.processed_urls.add(url)
            return matches[0]
        try:
            self.lsh.insert(url, mh)
            self.minhashes[url] = mh
            self.processed_urls.add(url)
        except Exception:
            pass
        return None

    def process_new_corpus(self, corpus_file: str | Path,
                           on_progress: Callable | None = None) -> dict[str, str]:
        corpus_file = Path(corpus_file)
        duplicates: dict[str, str] = {}
        new_count = 0
        total = 0
        with MmapJsonlReader(corpus_file) as reader:
            total = reader.count_lines()
        with MmapJsonlReader(corpus_file) as reader:
            for record in reader.iter_records():
                url = record.get("source_url", "")
                if not url or url in self.processed_urls:
                    continue
                content = record.get("content") or ""
                if not content:
                    continue
                original = self.add(url, content)
                if original:
                    duplicates[url] = original
                else:
                    new_count += 1
                if on_progress and (new_count + len(duplicates)) % 100 == 0:
                    on_progress(new_count + len(duplicates), total)
        self.save()
        return duplicates

    def clear(self) -> None:
        self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        self.minhashes = {}
        self.processed_urls = set()
        try:
            self.index_path.unlink()
        except FileNotFoundError:
            pass
