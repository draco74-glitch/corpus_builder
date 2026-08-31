"""Настройки приложения — одна pydantic-модель поверх `models.py` (В3).

Раньше конфигурация жила в двух местах: dataclass'ы `CrawlSettings`,
`QualitySettings`, … (9 штук, 68 полей) и pydantic-модели движка, плюс таблица
соответствия «настройка GUI → путь в AppConfig». Следствия были предсказуемы:
дефолты расходились (устаревший User-Agent, `use_browser_headers`, workers OCR),
имена — тем более, а «каждая настройка должна доходить до движка» приходилось
защищать тестом, потому что молча отвалившийся чекбокс был нормальным исходом.

Теперь:

* `AppSettings.engine` собран из Тех ЖЕ секций, что и `AppConfig`
  (`OutputSettings`/`CrawlersConfig`/`QualityConfig`/`DedupConfig`/
  `PipelineConfig`/`FineTuneConfig`/`ExportConfig`) — расхождение невозможно по
  конструкции, таблицы соответствия нет;
* «что пользователь менял» = `model_fields_set` (провенанс), а не сравнение с
 第二个 набором дефолтов: поле, которого нет в документе настроек, не имеет
  права перекрывать config.yaml;
* UI-состояние и секреты — отдельные модели (`ui`, `secrets`): в движок они не
  едут, секреты уходят в переменные окружения и не попадают ни в пресеты, ни в
  экспорт настроек.

Файл `~/.corpus_builder_settings.json` пишется с правами 0600; файлы формата v1
(плоские секции `crawl`/`html`/…) читаются и мигрируются один раз —
`migrate_legacy_v1()`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from .logging_setup import get_logger
from .models import (GuiEngineConfig, _ValidatingModel, explicit_paths, get_by_path,
                     set_by_path)

log = get_logger(__name__)

#: значение-заглушка для секретов при экспорте настроек (файл обычно куда-то
#: пересылают, поэтому токены по умолчанию наружу не едут)
REDACTED_SECRET = "***redacted***"

_SECRET_NAMES = {"token", "api_key", "password", "secret", "access_token", "client_secret"}
_SECRET_SUFFIXES = ("_token", "_api_key", "_password", "_secret")

#: режимы приоритета над config.yaml
OVERRIDE_FILE = "file"        # настройки GUI ничего не перекрывают
OVERRIDE_CHANGED = "changed"  # только явно заданные (провенанс)
OVERRIDE_ALL = "all"          # легаси: накладывается всё, включая дефолты GUI
OVERRIDE_MODES = (OVERRIDE_FILE, OVERRIDE_CHANGED, OVERRIDE_ALL)

#: куда ложится CORPUS_BUILDER_* — см. setup_env_vars
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_SE_API_KEY = "STACKEXCHANGE_KEY"
ENV_PROXIES = "CORPUS_BUILDER_PROXIES"


def is_secret_field(name: str) -> bool:
    """Имя поля похоже на секрет (для маскирования экспорта и миграции v1)."""
    n = name.lower()
    return n in _SECRET_NAMES or n.endswith(_SECRET_SUFFIXES)


class UiSettings(_ValidatingModel):
    """Только состояние интерфейса — в движок не уезжает ничего."""
    theme: str = "dark"
    language: str = "ru"
    log_level: str = "INFO"
    check_updates_on_start: bool = True
    #: см. OVERRIDE_*
    override_mode: str = OVERRIDE_CHANGED
    window_width: int = 1280
    window_height: int = 820
    last_config_path: str = ""
    last_output_dir: str = ""
    last_excel_path: str = ""
    recent_configs: list[str] = []


class SecretSettings(_ValidatingModel):
    """Секреты: живут в файле настроек 0600 и передаются движку через окружение.

    В `AppConfig` для них полей нет намеренно (`token_env`/`api_key_env` — это
    ИМЕНА переменных): секрет не может случайно уехать в config.yaml или в
    пресет.
    """
    github_token: str = ""
    stackexchange_api_key: str = ""
    proxy_list: str = ""


def _split_csv(value: str | list | None) -> list[str]:
    """CSV-строка виджета → список (для полей, которые движок хранит списками)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _engine_leaf_paths(engine: GuiEngineConfig) -> list[str]:
    """Все пути листьев движка (без имён секций и без paths-полей output)."""
    out: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        for name in type(node).model_fields:
            path = f"{prefix}.{name}" if prefix else name
            if path in _NEVER_IN_GUI:
                continue
            value = getattr(node, name)
            if isinstance(value, _ValidatingModel):
                walk(value, path)
            else:
                out.append(path)

    walk(engine, "")
    return out


#: пути, которые принадлежат конкретному прогону, а не глобальным настройкам
NeverInGui = ("output.corpus_file", "output.download_dir", "output.error_log",
              "output.state_file", "output.log_file", "sources")
_NEVER_IN_GUI = frozenset(NeverInGui)


class AppSettings(_ValidatingModel):
    """Один документ настроек: `ui` + `secrets` + `engine` (тот же AppConfig)."""

    ui: UiSettings = UiSettings()
    secrets: SecretSettings = SecretSettings()
    engine: GuiEngineConfig = GuiEngineConfig()

    #: поля, которые миграция v1 сочла «изменёнными» (для предупреждения в GUI;
    #: в файл не пишется — это состояние одного запуска)
    legacy_notice: list[str] = Field(default_factory=list, exclude=True)

    # ------------------------------------------------------------- пути файла
    @classmethod
    def _settings_file(cls) -> Path:
        if getattr(sys, "frozen", False):                  # pragma: no cover
            base = Path(sys.executable).parent
        else:
            base = Path.home()
        return base / ".corpus_builder_settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        path = cls._settings_file()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"Файл настроек не читается, начинаю с чистого: {e}")
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        """Собрать настройки из документа (v2) или мигрировать плоский v1."""
        if not isinstance(data, dict):
            return cls()
        if "engine" in data or "ui" in data or data.get("format") == 2:
            return cls._from_v2(data)
        return cls._from_v1(data)

    @classmethod
    def _from_v2(cls, data: dict) -> "AppSettings":
        secrets = {k: ("" if v == REDACTED_SECRET else v)
                   for k, v in (data.get("secrets") or {}).items()}
        # «***redacted***» из нашего же экспорта — не токен: возврат файла не
        # должен затирать настоящий секрет заглушкой
        try:
            return cls(ui=data.get("ui") or {}, secrets=secrets,
                       engine=data.get("engine") or {})
        except ValidationError as e:
            log.warning(f"Настройки v2 невалидны: {e.errors()[:3]}")
            return cls()

    # --------------------------------------------------- запись и сохранение
    def sparse_engine(self) -> dict[str, Any]:
        """Только явно заданные значения, в форме YAML-документа AppConfig.

        Полностью «развёрнутый» дамп превратил бы каждый дефолт в «выбранное
        пользователем» при следующем прочтении — и настройки снова молча
        перекрывали бы config.yaml.
        """
        doc: dict[str, Any] = {}
        for path in sorted(self.changed()):
            _set_dotted(doc, path, get_by_path(self.engine, path))
        return doc

    def filled_secrets(self) -> list[str]:
        """Имена непустых секретов — их показываем, когда спрашиваем про экспорт."""
        return [name for name, value in self.secrets.model_dump().items()
                if is_secret_field(name) and isinstance(value, str) and value.strip()]

    def to_export_dict(self, redact: bool = True) -> dict:
        """Снимок для «Экспорта настроек»: секреты — под заглушкой."""
        engine: dict[str, Any] = self.sparse_engine()
        secrets = self.secrets.model_dump(mode="json")
        if redact:
            for name, value in list(secrets.items()):
                if is_secret_field(name) and isinstance(value, str) and value.strip():
                    secrets[name] = REDACTED_SECRET
        doc = {"format": 2, "ui": self.ui.model_dump(mode="json"),
               "secrets": secrets, "engine": engine}
        if not redact:
            doc["_note"] = "файл содержит токены — не публикуйте его"
        return doc

    def to_overlay_yaml(self) -> str:
        """Те же настройки как YAML-накидка на config.yaml (`preset --yaml`)."""
        import yaml
        return yaml.safe_dump(self.sparse_engine(), allow_unicode=True, sort_keys=False)

    def save(self) -> None:
        """Атомарно записать файл настроек с правами 0600 (В2).

        Права выставляются и на существующем файле: он мог быть создан раньше
        (или другим процессом) с 0644, а внутри лежат токены.
        """
        path = self._settings_file()
        doc = {"format": 2, "ui": self.ui.model_dump(mode="json"),
               "secrets": self.secrets.model_dump(mode="json"),
               "engine": self.sparse_engine()}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError:
            # ФС без прав (FAT) или отказ chmod: сохраняем как умеем, не роняя
            # диалог настроек
            try:
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            except OSError as e:
                log.warning(f"Настройки не сохранены: {e}")

    # ------------------------------------------------------------ доступ
    def holder_for(self, path: str):
        """Модель-владелец пути: `ui.*`, `secrets.*` или движок."""
        alias = _secret_path_alias(path)
        if alias:
            return self.secrets, alias
        if path.startswith("ui."):
            return self.ui, path[3:]
        if path.startswith("secrets."):
            return self.secrets, path[len("secrets."):]
        return self.engine, path

    def get(self, path: str, default: Any = None) -> Any:
        """Значение настройки по пути (`quality.min_chars`, `ui.theme`, ...)."""
        holder, local = self.holder_for(path)
        try:
            return get_by_path(holder, local)
        except AttributeError:
            return default

    def set(self, path: str, value: Any) -> None:
        """Запись по пути — с валидацией типа (модели движка те же, что у AppConfig).

        KeyError — путём не существует (красный флаг для таблицы привязок
        диалога); ValueError — путь верный, но значение движок не примет.
        """
        holder, local = self.holder_for(path)
        if not self._path_exists(holder, local):
            raise KeyError(f"нет настройки с путём «{path}»")
        try:
            set_by_path(holder, local, value)
        except ValidationError as e:
            raise ValueError(f"значение для «{path}» не подходит: "
                             f"{e.errors()[0]['msg']}") from e
        except (AttributeError, KeyError) as e:
            raise KeyError(f"нет настройки с путём «{path}»") from e

    @staticmethod
    def _path_exists(holder: Any, local: str) -> bool:
        obj: Any = holder
        for part in local.split("."):
            if part not in getattr(type(obj), "model_fields", {}):
                return False
            obj = getattr(obj, part)
        return True

    def changed(self) -> set[str]:
        """Листья настроек движка, заданные в этом документе явно (провенанс)."""
        return {p for p in explicit_paths(self.engine) if p not in _NEVER_IN_GUI}

    def engine_default(self, path: str) -> Any:
        """Дефолт движка для пути (тот же класс модели — второй копии нет)."""
        holder, local = self.holder_for(path)
        if holder is not self.engine:
            return None
        try:
            return get_by_path(GuiEngineConfig(), local)
        except AttributeError:
            return None

    def is_default(self, path: str) -> bool:
        """Значение равно дефолту движка → перекрывать config.yaml незачем."""
        holder, _local = self.holder_for(path)
        if holder is not self.engine or path not in self.changed():
            return False
        return self.get(path) == self.engine_default(path)

    def reset(self, path: str) -> bool:
        """«Взять из config.yaml»: снять с настройки отметку «задано в GUI».

        Поле возвращается к дефолту движка и больше не перекрывает файл — ровно
        то, чего не хватало в п.1 (кнопка «взять из config.yaml»).
        """
        if path not in self.changed():
            return False
        obj_name, _, leaf = path.rpartition(".")
        holder = self.engine if not obj_name else get_by_path(self.engine, obj_name)
        field = type(holder).model_fields[leaf]
        if field.default_factory is not None:
            default = field.default_factory()                 # type: call
        else:
            default = field.default                           # PydanticUndefined → ниже
        setattr(holder, leaf, default)
        # setattr помечает поле заданным — отметку снимаем ПОСЛЕ
        holder.__pydantic_fields_set__.discard(leaf)
        return True

    def reset_all(self) -> int:
        paths = sorted(self.changed())
        for path in paths:
            self.reset(path)
        return len(paths)

    # ------------------------------------------------------- наложение на конфиг
    def apply_to_config(self, config: Any) -> list[str]:
        """Вернуть список путей, которые настройки применили на `AppConfig`."""
        return merge_into_config(self, config)

    def conflicts_with(self, config: Any) -> list[tuple[str, Any, Any]]:
        """Где настройки перекрывают ЯВНО написанное в config.yaml."""
        return config_conflicts(config, self)

    def setup_env_vars(self) -> None:
        """Секреты → переменные окружения (единственный канал в движок)."""
        setup_env_vars(self)


#: старые (v1) имена секретов → новое место в `SecretSettings`
_SECRET_ALIASES = {"github.token": "github_token",
                   "stackexchange.api_key": "stackexchange_api_key",
                   "crawl.proxy_list": "proxy_list"}


def _secret_path_alias(path: str) -> str | None:
    return _SECRET_ALIASES.get(path)


def _set_dotted(tree: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# ------------------------------------------------------------------ применение
def merge_into_config(settings: "AppSettings", config: Any) -> list[str]:
    """Наложить настройки на уже собранный `AppConfig`."""
    mode = settings.ui.override_mode
    if mode == OVERRIDE_FILE:
        return []
    paths = (_engine_leaf_paths(settings.engine) if mode == OVERRIDE_ALL
             else sorted(settings.changed()))
    applied: list[str] = []
    for path in paths:
        if path in _NEVER_IN_GUI:
            continue
        try:
            set_by_path(config, path, get_by_path(settings.engine, path))
        except (AttributeError, KeyError, ValidationError) as e:
            log.warning(f"настройка «{path}» не легла на конфиг: "
                        f"{str(e).splitlines()[0][:90]}")
            continue
        applied.append(path)
    return applied





def config_conflicts(config: Any, settings: AppSettings) -> list[tuple[str, Any, Any]]:
    """Где настройки GUI перекрывают ЯВНО написанное в config.yaml.

    Провенанс берётся из `model_fields_set` обоих документов, поэтому это
    «вы и в файле задали, и в GUI задали», а не «отличается от дефолта».
    """
    getter = getattr(config, "values_from_file", None)
    file_values = getter() if getter else {p: get_by_path(config, p)
                                           for p in explicit_paths(config)}
    out = []
    for path in sorted(settings.changed() & {p for p in file_values if "." in p}):
        if path in _NEVER_IN_GUI:
            continue
        try:
            gui_value = get_by_path(settings.engine, path)
        except AttributeError:
            continue
        if file_values[path] != gui_value:
            out.append((path, file_values[path], gui_value))
    return out





def setup_env_vars(settings: "AppSettings") -> None:
    """Секреты → переменные окружения (единственный канал в движок)."""
    if settings.secrets.github_token:
        os.environ[ENV_GITHUB_TOKEN] = settings.secrets.github_token
    if settings.secrets.stackexchange_api_key:
        os.environ[ENV_SE_API_KEY] = settings.secrets.stackexchange_api_key
    if settings.secrets.proxy_list.strip():
        os.environ[ENV_PROXIES] = settings.secrets.proxy_list





# ------------------------------------------------------------------ миграция v1
#: отдельные имена, которые в движке называются иначе (только здесь, один раз)
_V1_RENAMES = {
    "crawl.request_timeout": "output.request_timeout",
    "crawl.request_delay": "output.request_delay",
    "crawl.user_agent": "output.user_agent",
    "crawl.max_file_size_mb": "output.max_file_size_mb",
    "crawl.use_cache": "output.use_http_cache",
    "crawl.cache_ttl_hours": "output.cache_ttl_hours",
    "crawl.revalidate_cached_files": "output.revalidate_cached_files",
    "crawl.use_proxy": "output.use_proxy",
    "crawl.use_browser_headers": "output.use_browser_headers",
    "crawl.respect_robots_txt": "output.respect_robots_txt",
    "crawl.contact_email": "output.contact_email",
    "crawl.save_checkpoint_every": "pipeline.save_checkpoint_every",
    "crawl.min_checkpoint_seconds": "pipeline.min_checkpoint_seconds",
    "crawl.progress_bar": "pipeline.progress_bar",
    "crawl.per_url_timeout_minutes": "pipeline.per_url_timeout_minutes",
    "async_crawl.enabled": "pipeline.use_async",
    "async_crawl.max_concurrent_total": "pipeline.max_concurrent_total",
    "async_crawl.max_concurrent_per_domain": "pipeline.max_concurrent_per_domain",
    "export.gzip_output": "export.write_gzip",
    "export.parallel_postproc": "pipeline.parallel_postproc",
    "export.parallel_workers": "pipeline.parallel_workers",
    "dedup.use_streaming": "dedup.streaming",
    "dedup.use_incremental": "dedup.incremental",
    "html.extract_mode": "crawlers.html.extract_mode",
    "html.download_images": "crawlers.html.download_images",
    "html.image_extensions": "crawlers.html.image_extensions",
    "html.download_files_ext": "crawlers.html.download_files_ext",
    "html.max_html_pages": "crawlers.html.max_html_pages",
    "pdf.ocr_enabled": "crawlers.pdf.ocr_enabled",
    "pdf.ocr_lang": "crawlers.pdf.ocr_lang",
    "pdf.ocr_min_chars_per_page": "crawlers.pdf.ocr_min_chars_per_page",
    "pdf.ocr_parallel_workers": "crawlers.pdf.ocr_parallel_workers",
    "pdf.image_min_width": "crawlers.pdf.image_min_width",
    "pdf.image_min_height": "crawlers.pdf.image_min_height",
    "pdf.extract_tables": "crawlers.pdf.extract_tables",
    "pdf.two_column_detection": "crawlers.pdf.two_column_detection",
    "pdf.filter_schematic_images": "crawlers.pdf.filter_schematic_images",
    "pdf.use_toc_as_structure": "crawlers.pdf.use_toc_as_structure",
    "github.branch": "crawlers.github.branch",
    "github.crawl_issues": "crawlers.github.crawl_issues",
    "github.crawl_issues_max": "crawlers.github.crawl_issues_max",
    "github.issues_comments_max": "crawlers.github.issues_comments_max",
    "github.crawl_wiki": "crawlers.github.crawl_wiki",
    "github.crawl_docs_dir": "crawlers.github.crawl_docs_dir",
    "github.max_archive_mb": "crawlers.github.max_archive_mb",
    "github.include_files": "crawlers.github.include_files",
    "stackexchange.site": "crawlers.stackexchange.site",
    "stackexchange.min_score": "crawlers.stackexchange.min_score",
    "stackexchange.max_questions": "crawlers.stackexchange.max_list_questions",
}
#: Значения, которые в v1 были дефолтами GUI и расходились с движком (их
#: починили отдельно: устаревший User-Agent, browser-заголовки, workers OCR,
#`min_score`, max_questions). Переносить их как «выбор пользователя» нельзя:
#: иначе миграция вернёт ровно тот баг, из-за которого всё переделывали.
_V1_STALE_DEFAULTS = {
    "output.user_agent": "CorpusBuilder/0.2 (research)",
    "output.use_browser_headers": True,
    "crawlers.pdf.ocr_parallel_workers": 4,
    "crawlers.stackexchange.min_score": 5,
    "crawlers.stackexchange.max_list_questions": 100,
}

#: поля v1, значения которых движок хранит списками
_V1_CSV = {"crawlers.html.image_extensions", "crawlers.html.download_files_ext",
           "crawlers.github.include_files", "quality.languages_allowed"}



# --------------------------------------------------------- чтение плоского v1
def _from_v1(cls, data: dict) -> "AppSettings":
    """Прочести плоский файл настроек формата v1 (`crawl`/`html`/`pdf`/…).

    Правила переноса — таблицей выше; это единственный текст, где старые имена
    вообще упоминаются. Из всего v1-снапка в настройки попадают только поля,
    которые (а) помечены в `ui_overridden` или (б) отличаются от дефолта
    движка — при отсутствии `ui_overridden`. Всё остальное остаётся правом
    config.yaml, за что и боролись в п.1.
    """
    settings = cls()
    legacy_gui = data.get("gui") or {}
    # v1 писал список в СВОИХ именах («crawl.request_delay») — переводим в пути
    # движка, иначе отметка «пользователь трогал это» теряется при миграции
    marked = []
    for x in (legacy_gui.get("ui_overridden") or []):
        if x == APPLY_ALL_MARKER:
            continue
        marked.append(_V1_RENAMES.get(x, x if x.split(".")[0] in
                                      ("quality", "dedup", "export") else
                                      f"crawlers.{x}" if x.split(".")[0] in
                                      ("html", "pdf", "github", "stackexchange") else x))
    apply_all = APPLY_ALL_MARKER in (legacy_gui.get("ui_overridden") or [])

    engine_src: dict[str, Any] = {}
    for section in ("crawl", "async_crawl", "html", "pdf", "github", "stackexchange",
                    "quality", "dedup", "export"):
        for field, value in (data.get(section) or {}).items():
            path = _v1_path(section, field)
            if path is None:
                continue
            if path in _NEVER_IN_GUI:
                continue
            engine_src[path] = _v1_value(path, value)

    defaults = GuiEngineConfig()
    reference = {path: get_by_path(defaults, path) for path in engine_src}
    #: «угадывали, а не читали»: old-файлы без ui_overridden помечают перенесённое
    guessing = apply_all or not marked
    notice: list[str] = []
    for path, value in engine_src.items():
        if apply_all:
            wanted = True
        elif path in marked:
            wanted = True
        elif path in _V1_STALE_DEFAULTS and value == _V1_STALE_DEFAULTS[path]:
            continue                                  # старый дефолт, не выбор
        else:
            wanted = value != reference[path]        # отличается от дефолта движка
        if not wanted:
            continue
        try:
            set_by_path(settings.engine, path, value)
        except (AttributeError, ValidationError) as e:
            log.warning(f"v1-настройка «{path}» не перенесена: "
                        f"{str(e).splitlines()[0][:80]}")
            continue
        if guessing:
            notice.append(path)

    ui_map = {"theme": "theme", "language": "language", "log_level": "log_level",
              "check_updates_on_start": "check_updates_on_start",
              "window_width": "window_width", "window_height": "window_height",
              "last_config_path": "last_config_path", "last_output_dir": "last_output_dir",
              "last_excel_path": "last_excel_path", "recent_configs": "recent_configs"}
    for old, new in ui_map.items():
        if old in legacy_gui and new in type(settings.ui).model_fields:
            setattr(settings.ui, new, legacy_gui[old])
    legacy_mode = legacy_gui.get("override_mode")
    if legacy_mode in ("touched", "changed"):
        settings.ui.override_mode = OVERRIDE_CHANGED
    elif legacy_mode in OVERRIDE_MODES:
        settings.ui.override_mode = legacy_mode
    # «все поля» в v1 включали и то, что пользователь не трогал: переносим
    # режим как есть, но про перенесённых «по отличием от дефолта» предупреждаем
    if legacy_gui.get("ui_overridden") == [APPLY_ALL_MARKER]:
        settings.ui.override_mode = OVERRIDE_ALL

    for old_path, attr in _SECRET_ALIASES.items():
        section, _, field = old_path.partition(".")
        value = (data.get(section) or {}).get(field)
        if isinstance(value, str) and value.strip():
            setattr(settings.secrets, attr, value)

    if notice or apply_all:
        settings.legacy_notice = sorted(notice) or ["*"]
        log.info(f"Файл настроек v1 мигрирован: перенесено полей — "
                 f"{len(settings.changed())}")
    return settings


def _v1_path(section: str, field: str) -> str | None:
    """Старый путь `секция.поле` → путь настроек v2 (или None — не переносим)."""
    dotted = f"{section}.{field}"
    if dotted in _V1_RENAMES:
        return _V1_RENAMES[dotted]
    if section in ("quality", "dedup", "export"):
        return f"{section}.{field}"
    return None


def _v1_value(path: str, value: Any) -> Any:
    if path in _V1_CSV:
        return _split_csv(value)
    if path in ("crawlers.github.branch", "quality.perplexity_model_path"):
        return value or None
    return value


APPLY_ALL_MARKER = "*"
AppSettings._from_v1 = classmethod(_from_v1)
