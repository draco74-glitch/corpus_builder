"""Incremental dedup: LSH-индекс сохраняется между запусками.

Индекс — это ПАМЯТЬ о решениях, а не множество «эти URL уже видали». Прежняя
реализация пропускала запись, если её URL уже был в индексе, и вторая
обработка того же корпуса возвращала пустой список дублей: `removed=0` вместо
40 из 60 записей, то есть на выход уезжал недодедуплированный корпус, и ни
статистика, ни лог об этом не говорили. Теперь решение вычисляется для каждой
записи, а экономика достигается за счёт кэша сигнатур и решений по хешу
контента (В: повторный прогон того же файла не считает MinHash заново).
"""
from __future__ import annotations

import os
import pickle
from collections.abc import Callable
from pathlib import Path

from datasketch import MinHash, MinHashLSH

from .logging_setup import get_logger
from .mmap_reader import MmapJsonlReader
from .text_utils import normalize_text, text_sha1

log = get_logger(__name__)

#: «оригинал» для кэша решений: пустая строка = запись не дубль
ORIGINAL = ""


def _key(url: str, content_sha1: str) -> str:
    return f"{url}\x00{content_sha1}"


class IncrementalDedup:
    def __init__(self, index_path: str | Path, threshold: float = 0.85, num_perm: int = 128):
        self.index_path = Path(index_path)
        self.threshold = threshold
        self.num_perm = num_perm
        self.lsh: MinHashLSH | None = None
        self.minhashes: dict[str, MinHash] = {}
        self.processed_urls: set[str] = set()
        #: «url + хеш текста» → original url ('' — не дубль). Переживает перезапуск.
        self.decisions: dict[str, str] = {}
        #: порядок вставки — нужен, чтобы «оригиналом» оставалась первая запись,
        #: как в неразыменном (холодном) прогоне
        self.order: dict[str, int] = {}
        #: хеш нормализованного текста → MinHash (одинаковый текст не хешем дважды)
        self._mh_cache: dict[str, MinHash] = {}
        self._load()

    # ------------------------------------------------------------ индекс
    def _load(self) -> None:
        if not self.index_path.exists():
            self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
            return
        try:
            with open(self.index_path, "rb") as f:
                data = pickle.load(f)
            self.lsh = data.get("lsh")
            self.minhashes = dict(data.get("minhashes", {}))
            self.processed_urls = set(data.get("processed_urls", self.minhashes))
            self.decisions = dict(data.get("decisions", {}))
            self.order = dict(data.get("order", {}))
            if not self.order:
                # старый индекс: порядок восстановления = порядок словаря (pickle
                # сохраняет порядок вставки dict)
                self.order = {u: i for i, u in enumerate(self.minhashes)}
            if self.lsh is None:
                self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        except Exception as e:
            log.warning(f"LSH-индекс не читается, начинаю с чистого: {e}")
            self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
            self.minhashes = {}
            self.processed_urls = set()
            self.decisions = {}
            self.order = {}

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp_path = str(self.index_path) + ".tmp"
            with open(tmp_path, "wb") as f:
                pickle.dump({
                    "lsh": self.lsh, "minhashes": self.minhashes,
                    "processed_urls": list(self.processed_urls),
                    "decisions": self.decisions, "order": self.order,
                    "threshold": self.threshold, "num_perm": self.num_perm,
                }, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, self.index_path)
        except Exception as e:
            log.warning(f"Failed to save LSH index: {e}")

    # ------------------------------------------------------------ дедуп
    def compute_minhash(self, text: str) -> MinHash:
        """MinHash одной записи, пакетом если datasketch умеет (A1)."""
        from .postproc.dedup import _make_minhash, shingles_bytes
        return _make_minhash(shingles_bytes(text, k=5), self.num_perm)

    def _minhash_for(self, content_sha1: str, text: str) -> MinHash:
        cached = self._mh_cache.get(content_sha1)
        if cached is not None:
            return cached
        mh = self.compute_minhash(text)
        self._mh_cache[content_sha1] = mh
        return mh

    def _find_original(self, url: str, mh: MinHash) -> str | None:
        """Самая ранняя по вставке запись с похожим содержимым (не сама себя)."""
        best: str | None = None
        for cand in (self.lsh.query(mh) if self.lsh else []):
            if cand == url:
                continue
            cmh = self.minhashes.get(cand)
            if cmh is None:
                continue
            if mh.jaccard(cmh) < self.threshold:
                continue
            if best is None or self.order.get(cand, 0) < self.order.get(best, 0):
                best = cand
        return best

    def add(self, url: str, text: str, *, normalized: bool = False) -> str | None:
        """Вернуть «оригинал», если запись — дубль; иначе None и индексация.

        `normalized=True` — текст уже нормализован (поле content_normalized),
        повторный normalize_text на той же строке не нужен (A2).
        """
        if not url:
            return None
        text = text if normalized else normalize_text(text)
        if not text:
            return None
        chash = text_sha1(text)
        key = _key(url, chash)
        cached = self.decisions.get(key)
        if cached is not None:
            return cached or None

        if url in self.minhashes:
            # тот же URL с другим содержимым: старую сигнатуру убираем из
            # «оригиналов», иначе она навсегда останется «первым вхождением»
            self._drop(url)
        mh = self._minhash_for(chash, text)
        original = self._find_original(url, mh)
        if original is not None:
            self.decisions[key] = original
            return original
        try:
            self.lsh.insert(url, mh)
        except Exception as e:                     # ключ уже в LSH и т.п.
            log.debug(f"LSH insert({url[:60]}): {e}")
            if url not in self.minhashes:
                return None
        self.minhashes[url] = mh
        self.order[url] = len(self.order)
        self.processed_urls.add(url)
        self.decisions[key] = ORIGINAL
        return None

    def _drop(self, url: str) -> None:
        try:
            self.lsh.remove(url)
        except Exception:
            pass
        self.minhashes.pop(url, None)

    def process_new_corpus(self, corpus_file: str | Path,
                           on_progress: Callable | None = None) -> dict[str, str]:
        """Пройти по корпусу и вернуть {url дубля: url оригинала}.

        «Новых» записей больше не существует как отдельной категории: решение
        принимается для каждой, иначе повторный прогон молча отдаёт пустой
        список дублей.
        """
        corpus_file = Path(corpus_file)
        duplicates: dict[str, str] = {}
        originals = 0
        total = 0
        with MmapJsonlReader(corpus_file) as reader:
            total = reader.count_lines()
        seen_here: set[str] = set()
        with MmapJsonlReader(corpus_file) as reader:
            for record in reader.iter_records():
                url = record.get("source_url", "")
                if not url:
                    continue
                content = record.get("content_normalized") or record.get("content") or ""
                if not content:
                    continue
                original = self.add(url, content,
                                    normalized=bool(record.get("content_normalized")))
                if original:
                    duplicates[url] = original
                elif url in seen_here:
                    # один и тот же URL дважды в файле — второй не «оригинал»
                    duplicates[url] = url
                else:
                    originals += 1
                    seen_here.add(url)
                if on_progress and (originals + len(duplicates)) % 200 == 0:
                    on_progress(originals + len(duplicates), total)
        self.save()
        self._mh_cache.clear()
        log.info(f"Incremental dedup: {len(duplicates)} duplicates, "
                 f"{originals} originals, index size {len(self.processed_urls)}")
        return duplicates

    def clear(self) -> None:
        self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        self.minhashes = {}
        self.processed_urls = set()
        self.decisions = {}
        self.order = {}
        self._mh_cache = {}
        try:
            self.index_path.unlink()
        except FileNotFoundError:
            pass
