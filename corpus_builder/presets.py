"""Пресеты настроек (В5): готовые профили под частые сценарии.

Пресет — это набор значений `AppSettings` по путям «секция.поле» (та же
система координат, что у `mapping()` и режима приоритета), поэтому:

* применение пресета = обычные правки настроек: поля помечаются «тронутыми» и
  честно перекрывают config.yaml (режим «touched») вместо тихой подмены;
* пресет не может сослаться на несуществующую настройку — это проверяется и
  тестом, и при загрузке (`apply_preset` бросает `KeyError`);
* секреты (токены, ключи) в пресеты не попадают физически: список полей-
  секретов исключается при сохранении своего пресета.

Четыре встроенных профиля закрывают ~90 % случаев запуска (замысел из ревью):
polite, own_site, academic, big_corpus.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .app_settings import AppSettings, is_secret_field
from .logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    values: dict[str, object] = field(default_factory=dict)
    #: пресет меняет только перечисленное — остальное остаётся как было
    builtin: bool = True

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "description": self.description,
                "values": copy.deepcopy(self.values), "builtin": self.builtin}


BUILTIN_PRESETS: tuple[Preset, ...] = (
    Preset(
        "polite", "Вежливый", "Медленно и по правилам: для чужих сайтов и долгих прогонов.",
        values={
            "crawl.request_delay": 5.0,
            "crawl.respect_robots_txt": True,
            "crawl.revalidate_cached_files": True,
            "crawl.use_cache": True,
            "crawl.request_timeout": 60,
            "crawl.per_url_timeout_minutes": 10.0,
            "crawl.use_browser_headers": False,
            "crawl.use_proxy": False,
            "async_crawl.enabled": False,
            "html.max_html_pages": 200,
            "github.crawl_issues": False,
        },
    ),
    Preset(
        "own_site", "Свой сайт", "Задержки не нужны, кэш полезен: источник наш и терпеть спам не от кого.",
        values={
            "crawl.request_delay": 0.0,
            "crawl.respect_robots_txt": True,      # на своём можно снять вручную
            "crawl.use_cache": True,
            "crawl.cache_ttl_hours": 24,
            "crawl.revalidate_cached_files": True,
            "crawl.request_timeout": 30,
            "crawl.save_checkpoint_every": 200,
            "crawl.min_checkpoint_seconds": 30.0,
            "async_crawl.enabled": True,
            "async_crawl.max_concurrent_total": 8,
            "async_crawl.max_concurrent_per_domain": 4,
        },
    ),
    Preset(
        "academic", "Научные источники", "arXiv/Crossref/DOAJ/Wikipedia/GitHub-доки: тексты + OCR, требования к качеству мягче.",
        values={
            "crawl.request_delay": 1.0,
            "crawl.respect_robots_txt": True,
            "crawl.request_timeout": 60,
            "crawl.per_url_timeout_minutes": 15.0,
            "html.download_images": False,
            "pdf.ocr_enabled": True,
            "pdf.extract_tables": True,
            "pdf.two_column_detection": True,
            "github.crawl_docs_dir": True,
            "quality.min_chars": 400,
            "quality.max_code_ratio": 0.9,
            "quality.spam_check": False,
            "stackexchange.min_score": 1,
        },
    ),
    Preset(
        "big_corpus", "Большой корпус", "Сотни тысяч URL: стриминг, инкрементальный дедуп, параллельный пост-процесс, редкие чекпойнты.",
        values={
            "crawl.save_checkpoint_every": 200,
            "crawl.min_checkpoint_seconds": 30.0,
            "crawl.max_file_size_mb": 200,
            "dedup.use_streaming": True,
            "dedup.auto_streaming": "auto",
            "dedup.auto_streaming_threshold_mb": 256,
            "dedup.use_incremental": True,
            "dedup.dedup_images": False,
            "export.gzip_output": True,
            "export.parallel_postproc": True,
            "export.parallel_workers": 4,
            "async_crawl.enabled": True,
            "async_crawl.max_concurrent_total": 16,
            "async_crawl.max_concurrent_per_domain": 1,
            "gui.log_level": "WARNING",
        },
    ),
)


def builtin_presets() -> tuple[Preset, ...]:
    return BUILTIN_PRESETS


def preset_by_key(key: str) -> Preset | None:
    for preset in BUILTIN_PRESETS:
        if preset.key == key:
            return preset
    return load_user_presets().get(key)


def all_presets() -> list[Preset]:
    from_user = [p for p in load_user_presets().values()]
    return list(BUILTIN_PRESETS) + sorted(from_user, key=lambda p: p.title)


# ------------------------------------------------------------------ пользоватес
def user_presets_file() -> Path:
    """Пользовательские пресеты — рядом с файлом настроек (и там же в frozen-сборке)."""
    if getattr(sys, "frozen", False):                  # pragma: no cover
        return Path(sys.executable).parent / "corpus_builder_presets.json"
    return Path.home() / ".corpus_builder_presets.json"


def load_user_presets() -> dict[str, Preset]:
    path = user_presets_file()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"Файл пользовательских пресетов не читается: {e}")
        return {}
    out: dict[str, Preset] = {}
    for key, item in (raw or {}).items():
        if not isinstance(item, dict):
            continue
        values = {k: v for k, v in (item.get("values") or {}).items()
                  if not is_secret_field(k.split(".")[-1])}
        out[key] = Preset(key=key, title=str(item.get("title") or key),
                          description=str(item.get("description") or ""),
                          values=values, builtin=False)
    return out


def save_user_preset(preset: Preset) -> Path:
    if preset.builtin:
        raise ValueError("встроенный пресет не перезаписывается")
    path = user_presets_file()
    data = {k: p.as_dict() for k, p in load_user_presets().items()}
    data[preset.key] = preset.as_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def delete_user_preset(key: str) -> bool:
    data = load_user_presets()
    if key not in data:
        return False
    data.pop(key)
    path = user_presets_file()
    payload = {k: p.as_dict() for k, p in data.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


_PROBE_UNSET = object()
_probe_cache = _PROBE_UNSET


def _probe_config():
    """Минимальный AppConfig — «полигон» для проверки значений пресетов."""
    global _probe_cache
    if _probe_cache is _PROBE_UNSET:
        from .models import AppConfig, SourceItem
        _probe_cache = AppConfig(
            sources=[SourceItem(url="http://probe.invalid/", type="html")],
            output={"corpus_file": "probe/raw.jsonl", "download_dir": "probe/dl"})
    return _probe_cache


def _engine_rejects(setting_path: str, value: object) -> str | None:
    """None — движок принимает; иначе текст причины."""
    import copy

    from .app_settings import AppSettings

    targets = dict(AppSettings().mapping())
    config_path = targets.get(setting_path)
    if config_path is None:
        return None                      # чисто UI-настройка — проверять негде
    cfg = copy.deepcopy(_probe_config())
    obj = cfg
    for part in config_path.split(".")[:-1]:
        obj = getattr(obj, part)
    try:
        setattr(obj, config_path.split(".")[-1], value)
    except Exception as e:                       # noqa: BLE001 — показываем причину
        return f"движок не принимает «{config_path}» = {value!r}: " \
               f"{str(e).splitlines()[0][:120]}"
    return None


# ------------------------------------------------------------------ применение
def validate_preset(preset: Preset) -> list[str]:
    """Проблемы пресета: [] — применим. Проверяет пути, секреты и типы."""
    from dataclasses import fields, is_dataclass

    settings = AppSettings()
    problems: list[str] = []
    for path, value in preset.values.items():
        section_name, _, name = path.partition(".")
        section = getattr(settings, section_name, None)
        if section is None or not is_dataclass(section):
            problems.append(f"{preset.key}: нет секции «{section_name}»")
            continue
        available = {f.name for f in fields(section)}
        if name not in available:
            problems.append(f"{preset.key}: нет настройки «{path}»")
            continue
        if is_secret_field(name):
            problems.append(f"{preset.key}: секретное поле «{path}» в пресет не кладём")
            continue
        original = getattr(section, name)
        if original is not None and type(value) is not type(original) \
                and not (isinstance(original, bool) == isinstance(value, bool)):
            problems.append(
                f"{preset.key}: «{path}» = {value!r} не того типа "
                f"(ожидается {type(original).__name__})")
            continue
        # значение обязан принять движок: у части полей в AppConfig стоит
        # Literal/диапазон, и «extract_mode: readability» выглядело бы
        # применённым пресетом, который роняет запуск
        problem = _engine_rejects(path, value)
        if problem:
            problems.append(f"{preset.key}: {problem}")
    if not preset.values:
        problems.append(f"{preset.key}: пустой пресет")
    return problems


def apply_preset(settings: AppSettings, key: str, *, mark_touched: bool = True) -> list[str]:
    """Применить пресет к настройкам; вернуть список изменённых путей.

    Изменённые поля помечаются «тронутыми» — иначе режим приоритета «touched»
    не пропустит их в движок, и «применить пресет» станет обещанием.
    """
    preset = preset_by_key(key)
    if preset is None:
        raise KeyError(f"неизвестный пресет: {key}")
    problems = validate_preset(preset)
    if problems:
        raise ValueError("; ".join(problems))

    changed: list[str] = []
    for path, value in preset.values.items():
        section_name, _, name = path.partition(".")
        section = getattr(settings, section_name)
        if getattr(section, name) != value:
            setattr(section, name, value)
            changed.append(path)
    if mark_touched and changed:
        settings.mark_touched(changed)
    log.info(f"Пресет «{preset.title}»: применено {len(changed)} полей")
    return changed


def capture_preset(settings: AppSettings, key: str, title: str,
                   description: str = "") -> Preset:
    """Снять текущие настройки в пресет (без секретов и UI-мусора)."""
    skip_sections = {"gui"}
    skip_fields = {"ui_overridden", "override_mode", "override_migrated",
                   "last_config_path", "last_output_dir", "last_excel_path",
                   "recent_configs"}
    values: dict[str, object] = {}
    defaults = AppSettings().snapshot()         # дефолты не тащим в пресет
    snap = settings.snapshot()
    for path, current in snap.items():
        section_name, _, name = path.partition(".")
        if section_name in skip_sections or name in skip_fields or is_secret_field(name):
            continue
        if defaults.get(path) == current:
            continue
        values[path] = copy.deepcopy(current)
    return Preset(key=key, title=title or key, description=description,
                  values=values, builtin=False)
