"""Состояние краулинга: какие URL уже обработаны, чтобы поддержать resume.

Хранение — снимок (`state.json`) + append-only журнал (`state.journal`).

Причина: прежний `save()` на КАЖДЫЙ чекпойнт заново сериализовал всё множество
URL — O(n) на запись и O(n²/шаг) за ран. Замер: 4 мс на чекпойнт при 10 000 URL
и 8.6 мс при 20 000; при `save_checkpoint_every: 50` на 500 000 URL это часы
чистой перезаписи одного и того же файла (A5). Теперь промежуточный чекпойнт
дописывает в журнал только новые события (O(1) на запись), а снимок
переписывается периодически (компакция) и один раз в финале рана.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .logging_setup import get_logger

log = get_logger(__name__)

#: После скольких событий журнала делать компакцию (перепись снимка).
DEFAULT_COMPACT_AFTER = 5000


def journal_path_of(state_file: str | Path) -> Path:
    return Path(state_file).with_name(Path(state_file).name + ".journal")


def disk_signature(state_file: str | Path) -> tuple:
    """(mtime_ns, размер) снимка и журнала. Дешёвая проверка «изменилось ли».

    Отдельная функция, потому что строить State ради опроса раз в 2 секунды —
    значит перечитывать и разбирать всё состояние (A7).
    """
    sig = []
    for path in (Path(state_file), journal_path_of(state_file)):
        try:
            st = path.stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((0, 0))
    return tuple(sig)


class State:
    """Множества done/error URL: снимок + журнал событий поверх него.

    `state.json` — человекочитаемый снимок (можно открыть и посмотреть, что
    уже собрано), `state.journal` — JSONL событий поверх снимка:
      {"d": url} — done, {"e": url} — error,
      {"c": "done" | "errors" | "all"} — очистка множества.
    При загрузке применяется снимок, затем журнал поверх. Оборванная последняя
    строка журнала (процесс упосили посреди записи) отбрасывается и обрезается,
    чтобы не применить полусобытие.
    """

    def __init__(self, state_file: str | Path, compact_after: int = DEFAULT_COMPACT_AFTER):
        self.state_file = Path(state_file)
        self.journal_file = journal_path_of(self.state_file)
        self._done: set[str] = set()
        self._errors: set[str] = set()
        self._lock = threading.Lock()
        self._pending: list[bytes] = []        # ещё не записанные строки журнала
        self._journal_bytes = 0                # размер журнала на диске
        self._journal_events = 0               # событий в журнале с последней компакции
        self._compact_after = max(1, int(compact_after))
        self._load()

    # ------------------------------------------------------------- загрузка
    def _load(self, silent: bool = False) -> None:
        with self._lock:
            done: set[str] = set()
            errors: set[str] = set()
            self._read_snapshot(done, errors)
            events, size = self._replay_journal(done, errors)
            self._done, self._errors = done, errors
            self._journal_bytes = size
            self._journal_events = events
            self._pending = []
        if not silent:
            log.info(f"State loaded: {len(done)} done, {len(errors)} errors "
                     f"(+{events} событий журнала)")

    def _read_snapshot(self, done: set[str], errors: set[str]) -> None:
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            done.update(data.get("done", []))
            errors.update(data.get("errors", []))
        except Exception as e:
            log.warning(f"Failed to load state snapshot, starting fresh: {e}")

    def _replay_journal(self, done: set[str], errors: set[str]) -> tuple[int, int]:
        """Применить журнал; вернуть (число событий, число валидных байтов)."""
        try:
            raw = self.journal_file.read_bytes()
        except FileNotFoundError:
            return 0, 0
        except OSError as e:
            log.warning(f"Не удалось прочитать журнал состояния: {e}")
            return 0, 0
        applied = 0
        pos = 0
        valid = 0
        while True:
            nl = raw.find(b"\n", pos)
            if nl < 0:
                break                            # оборванная строка в хвосте
            line = raw[pos:nl].strip()
            if not line:
                pos = nl + 1
                valid = pos
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                break                            # мусор: остановаемся и обрезаем
            if isinstance(event, dict):
                self._apply(event, done, errors)
                applied += 1
            pos = nl + 1
            valid = pos
        if valid < len(raw):
            try:
                with open(self.journal_file, "r+b") as f:
                    f.truncate(valid)
            except OSError:
                pass
        return applied, valid

    @staticmethod
    def _apply(ev: dict, done: set[str], errors: set[str]) -> None:
        target = ev.get("c")
        if target is not None:
            if target in ("done", "all"):
                done.clear()
            if target in ("errors", "all"):
                errors.clear()
            return
        url = ev.get("d") or ev.get("e")
        if not url:
            return
        if "d" in ev:
            done.add(url)
            errors.discard(url)                  # «стал успешно» ≠ «ошибка»
        else:
            errors.add(url)

    def reload_silent(self) -> None:
        """Перечитать state без логирования — для периодических опросов в GUI."""
        self._load(silent=True)

    def disk_signature(self) -> tuple:
        """См. модульную disk_signature(): подписка и на журнал тоже (A5)."""
        return disk_signature(self.state_file)

    # ------------------------------------------------------------- изменение
    def reset(self) -> None:
        """Забыть всё состояние (запуск без resume)."""
        with self._lock:
            self._done.clear()
            self._errors.clear()
            self._pending.append(self._encode({"c": "all"}))

    def clear_errors(self) -> None:
        """Разрешить повторную обработку ранее упавших URL (retry-errors)."""
        with self._lock:
            self._errors.clear()
            self._pending.append(self._encode({"c": "errors"}))

    def is_done(self, url: str) -> bool:
        with self._lock:
            return url in self._done

    def is_error(self, url: str) -> bool:
        with self._lock:
            return url in self._errors

    def mark_done(self, url: str) -> None:
        with self._lock:
            if url in self._done:
                return
            self._done.add(url)
            self._errors.discard(url)
            self._pending.append(self._encode({"d": url}))

    def mark_error(self, url: str) -> None:
        with self._lock:
            if url in self._errors:
                return
            self._errors.add(url)
            self._pending.append(self._encode({"e": url}))

    @staticmethod
    def _encode(ev: dict) -> bytes:
        return (json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    # ------------------------------------------------------------ сохранение
    def save(self, compact: bool = False) -> int:
        """Записать состояние.

        `compact=True` (промежуточный чекпойнт) — только дописать новые события
        в журнал: O(новых URL) вместо O(всех URL). Когда журнал перерастает
        `compact_after` событий, сама собой происходит компакция (как иначе
        перечитывать состояние стало бы дороже, чем переписать снимок).

        `compact=False` (финальная запись рана) — полный отсортированный снимок,
        журнал стирается, `state.json` снова самодостаточен.
        """
        with self._lock:
            if (not compact) or (self._journal_events + len(self._pending)) >= self._compact_after:
                done, errors = set(self._done), set(self._errors)
                self._write_snapshot_locked(done, errors)
                return len(done) + len(errors)
            pending = self._pending
            self._pending = []
        ok = self._append_journal(pending)
        if not ok:                                # диск/права: события не потеряем
            with self._lock:
                self._pending = pending + self._pending
            return 0
        with self._lock:
            self._journal_events += len(pending)
            self._journal_bytes += sum(len(line) for line in pending)
        return self.done_count + self.error_count

    def _append_journal(self, lines: list[bytes]) -> bool:
        try:
            self.journal_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_file, "ab") as f:
                f.write(b"".join(lines))
                f.flush()
                os.fsync(f.fileno())              # журнал — гарантия resume после падения
            return True
        except OSError as e:
            log.warning(f"Failed to write state journal: {e}")
            return False

    def _write_snapshot_locked(self, done: set[str], errors: set[str]) -> None:
        """Атомарная запись снимка (tmp + fsync + os.replace) и обнуление журнала.

        Вызывается под self._lock: иначе mark_done между копированием множеств
        и стиранием журнала потерял бы событие (URL пропал бы из resume).
        """
        data = {"done": sorted(done), "errors": sorted(errors), "sorted": True}
        tmp = str(self.state_file) + ".tmp"
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
            try:
                self.journal_file.unlink(missing_ok=True)
            except OSError as e:
                log.warning(f"Не удалось стереть журнал состояния: {e}")
            self._journal_bytes = 0
            self._journal_events = 0
            self._pending = []
        except OSError as e:
            log.warning(f"Failed to save state: {e}")
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------- прочее
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

    @property
    def pending_events(self) -> int:
        """Событий, ещё не записанных на диск (для тестов и диагностики)."""
        with self._lock:
            return len(self._pending)
