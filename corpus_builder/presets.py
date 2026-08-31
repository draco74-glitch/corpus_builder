"""Пресеты настроек (В5): готовые профили под частые сценарии.

Значения пресета — это пути в координатах `AppConfig` (`output.request_delay`,
`quality.min_chars`, `dedup.streaming`, `ui.log_level`), то есть ровно то же
пространство имён, в котором живут настройки GUI (В3) и сам config.yaml.
следствия:

* пресет нельзя «потерять по дороге»: `AppSettings.set()` принимает только
  существующее поле движка и валидирует значение его же валидаторами;
* применение пресеста = обычные правки настроек, поэтому провенанс
  (`model_fields_set`) сам помечает эти поля как «задаваемые GUI» — и они
  честно перекрывают config.yaml в режиме `changed`;
* секретов в пресетах не бывает физически: они живут в `AppSettings.secrets`,
  а не в движке.

Четыре встроенных профиля закрывают ~90 % случаев: polite, own_site,
academic, big_corpus. Свои профили — `capture_preset` +
`save_user_preset` (`~/.corpus_builder_presets.json`).
"""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .app_settings import AppSettings
from .logging_setup import get_logger

log = get_logger(__name__)

#: пути, которые в пресет не попадают никогда
SKIP_PREFIXES = ("secrets.",)
#: то, что имеет смысл только в ран-конфиге, а не в профиле
SKIP_PATHS = frozenset({"output.corpus_file", "output.download_dir", "output.error_log",
                        "output.state_file", "output.log_file", "sources"})


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    values: dict[str, Any] = field(default_factory=dict)
    builtin: bool = True

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "description": self.description,
                "values": copy.deepcopy(self.values), "builtin": self.builtin}


BUILTIN_PRESETS: tuple[Preset, ...] = (
    Preset(
        "polite", "Вежливый", "Медленно и по правилам: чужие сайты и долгие прогоны.",
        values={
            "output.request_delay": 5.0,
            "output.respect_robots_txt": True,
            "output.revalidate_cached_files": True,
            "output.use_http_cache": True,
            "output.request_timeout": 60,
            "output.use_browser_headers": False,
            "output.use_proxy": False,
            "pipeline.per_url_timeout_minutes": 10.0,
            "pipeline.use_async": False,
            "crawlers.html.max_html_pages": 200,
            "crawlers.github.crawl_issues": False,
        },
    ),
    Preset(
        "own_site", "Свой сайт",
        "Задержки не нужны, кэш полезен: источник свой и терпеть спам не от кого.",
        values={
            "output.request_delay": 0.0,
            "output.respect_robots_txt": True,     # на своём снимается вручную
            "output.use_http_cache": True,
            "output.cache_ttl_hours": 24,
            "output.revalidate_cached_files": True,
            "output.request_timeout": 30,
            "pipeline.save_checkpoint_every": 200,
            "pipeline.min_checkpoint_seconds": 30.0,
            "pipeline.use_async": True,
            "pipeline.max_concurrent_total": 8,
            "pipeline.max_concurrent_per_domain": 4,
        },
    ),
    Preset(
        "academic", "Научные источники",
        "arXiv/Crossref/DOAJ/Wikipedia/GitHub-доки: тексты + OCR, к качеству — свои требования.",
        values={
            "output.request_delay": 1.0,
            "output.respect_robots_txt": True,
            "output.request_timeout": 60,
            "pipeline.per_url_timeout_minutes": 15.0,
            "crawlers.html.download_images": False,
            "crawlers.pdf.ocr_enabled": True,
            "crawlers.pdf.extract_tables": True,
            "crawlers.pdf.two_column_detection": True,
            "crawlers.github.crawl_docs_dir": True,
            "crawlers.stackexchange.min_score": 1,
            "quality.min_chars": 400,
            "quality.max_code_ratio": 0.9,
            "quality.spam_check": False,
        },
    ),
    Preset(
        "big_corpus", "Большой корпус",
        "Сотни тысяч URL: стриминг, инкрементальный дедуп, параллельный пост-процесс, редкие чекпойнты.",
        values={
            "output.max_file_size_mb": 200,
            "pipeline.save_checkpoint_every": 200,
            "pipeline.min_checkpoint_seconds": 30.0,
            "pipeline.use_async": True,
            "pipeline.max_concurrent_total": 16,
            "pipeline.max_concurrent_per_domain": 1,
            "pipeline.parallel_postproc": True,
            "pipeline.parallel_workers": 4,
            "dedup.streaming": True,
            "dedup.auto_streaming": "auto",
            "dedup.auto_streaming_threshold_mb": 256,
            "dedup.incremental": True,
            "dedup.dedup_images": False,
            "export.write_gzip": True,
            "ui.log_level": "WARNING",
        },
    ),
)


def builtin_presets() -> tuple[Preset, ...]:
    return BUILTIN_PRESETS


def all_presets() -> list[Preset]:
    return list(BUILTIN_PRESETS) + sorted(load_user_presets().values(),
                                          key=lambda p: p.title)


def preset_by_key(key: str) -> Preset | None:
    for preset in BUILTIN_PRESETS:
        if preset.key == key:
            return preset
    return load_user_presets().get(key)


def validate_preset(preset: Preset) -> list[str]:
    """Проблемы пресета; [] — применим.

    Проверка типов и допустимых значений — та же, что у движка: значение
    пробуется на чистой копии `GuiEngineConfig` через `AppSettings.set()`. Отдель-
    ного списка «какие значения вообще разрешены» поэтому нет (В3).
    """
    problems: list[str] = []
    if not preset.values:
        return [f"{preset.key}: пустой пресет"]
    probe = AppSettings()
    for path, value in preset.values.items():
        if path.startswith(SKIP_PREFIXES) or path in SKIP_PATHS:
            problems.append(f"{preset.key}: «{path}» в пресет не кладём")
            continue
        try:
            probe.set(path, value)
        except KeyError:
            problems.append(f"{preset.key}: нет настройки «{path}»")
        except ValueError as e:
            problems.append(f"{preset.key}: {str(e).splitlines()[0][:120]}")
    return problems


def apply_preset(settings: AppSettings, key: str, *, ignore_errors: bool = False) -> list[str]:
    """Применить пресет; вернуть список реально изменённых путей.

    Ничего не применяется, если пресет невалиден: «применить и получить
    половину профиля» — тот же класс сюрприза, что молча не сработавший чекбокс.
    """
    preset = preset_by_key(key)
    if preset is None:
        raise KeyError(f"неизвестный пресет: {key}")
    problems = validate_preset(preset)
    if problems and not ignore_errors:
        raise ValueError("; ".join(problems))

    changed: list[str] = []
    for path, value in preset.values.items():
        if path.startswith(SKIP_PREFIXES) or path in SKIP_PATHS:
            continue
        if settings.get(path, _MISSING) == value:
            continue
        settings.set(path, value)
        changed.append(path)
    log.info(f"Пресет «{preset.title}»: применено {len(changed)} полей")
    return changed


_MISSING = object()


def capture_preset(settings: AppSettings, key: str, title: str,
                   description: str = "") -> Preset:
    """Снять текущие настройки в пресет.

    Берётся только то, что пользователь задал явно (`AppSettings.changed()` →
    разреженный документ движка): дефолты и секреты в профиль не попадают.
    """
    values = {path: copy.deepcopy(settings.get(path))
              for path in sorted(settings.changed())}
    if settings.ui.log_level != AppSettings().ui.log_level:
        values["ui.log_level"] = settings.ui.log_level
    return Preset(key=key, title=title or key, description=description,
                  values=values, builtin=False)


# ------------------------------------------------------------- свои пресеты
def user_presets_file() -> Path:
    """Пользовательские профили — рядом с файлом настроек (там же в frozen-сборке)."""
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
                  if not k.startswith(SKIP_PREFIXES) and k not in SKIP_PATHS}
        out[key] = Preset(key=key, title=str(item.get("title") or key),
                          description=str(item.get("description") or ""),
                          values=values, builtin=False)
    return out


def save_user_preset(preset: Preset) -> Path:
    if preset.builtin:
        raise ValueError("встроенный пресет не перезаписывается")
    data = {k: p.as_dict() for k, p in load_user_presets().items()}
    data[preset.key] = preset.as_dict()
    path = user_presets_file()
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
    payload = {k: p.as_dict() for k, p in data.items()}
    user_presets_file().write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return True


def preset_to_yaml(preset: Preset) -> str:
    """Поля пресета как YAML-накидка на config.yaml (CLI `preset --yaml`)."""
    import yaml

    tree: dict[str, Any] = {}
    skipped: list[str] = []
    for path, value in preset.values.items():
        if path.startswith("ui.") or path in SKIP_PATHS or path.startswith(SKIP_PREFIXES):
            skipped.append(path)
            continue
        parts = path.split(".")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    header = (f"# Накладка пресета «{preset.title}»: {preset.description}\n"
              f"# Пропущено полей вне config.yaml: {len(skipped) or 'нет'}\n")
    return header + yaml.safe_dump(tree, allow_unicode=True, sort_keys=False)
