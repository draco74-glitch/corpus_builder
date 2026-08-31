"""Хранение настроек приложения в JSON-файле."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Iterable, get_type_hints


def _coerce_settings_value(value, tp, default):
    """Привести значение из JSON к объявленному типу поля dataclass.

    Возвращает default, если тип неизвестен и значение «не похоже» на поле.
    Исключение поднято, если приведение невозможно совсем (см. _from_dict).
    """
    origin = getattr(tp, "__origin__", None)
    if origin is not None:                       # list[str] | None и т.п.
        args = [a for a in getattr(tp, "__args__", ()) if a is not type(None)]
        tp = args[0] if args else type(default)

    if tp is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "да")
        return bool(value)
    if tp is int:
        return int(value)                        # ValueError/TypeError — наверх
    if tp is float:
        return float(value)
    if tp is str:
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return ",".join(str(v) for v in value)
        return str(value)
    if tp is list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [value]
    if isinstance(value, (bool, int, float, str, list, dict, type(None))):
        return value
    raise TypeError(f"unsupported type {tp!r} for value {value!r}")


#: поля диалога, которые хранятся строкой «a,b,c», а движку нужны списком
_CSV_FIELDS = {"image_extensions", "download_files_ext", "include_files",
               "languages_allowed"}


def _split_csv(value: str | list | None) -> list[str]:
    """Поля диалога настроек хранятся строкой «a,b,c» — движку нужен список."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


@dataclass
class CrawlSettings:
    user_agent: str = "CorpusBuilder/0.2 (research)"
    request_timeout: int = 30
    request_delay: float = 2.0
    max_file_size_mb: int = 50
    use_cache: bool = True
    cache_ttl_hours: int = 168
    #: сверять размер кэшированных файлов с сервером (HEAD на файл)
    revalidate_cached_files: bool = True
    use_proxy: bool = False
    proxy_list: str = ""
    use_browser_headers: bool = True
    respect_robots_txt: bool = True
    #: контакт для «polite» API (Crossref/Wikipedia требуют mailto в UA)
    contact_email: str = ""
    save_checkpoint_every: int = 50
    progress_bar: bool = True
    per_url_timeout_minutes: float = 10.0  # таймаут на один URL


@dataclass
class AsyncSettings:
    enabled: bool = False
    max_concurrent_total: int = 8
    max_concurrent_per_domain: int = 1


@dataclass
class HtmlCrawlerSettings:
    extract_mode: str = "trafilatura"
    download_images: bool = True
    image_extensions: str = "svg,png,jpg,jpeg,webp"
    download_files_ext: str = "pdf,kicad_sch,kicad_pcb,zip,sch,brd"
    #: потолок HTML-страниц на один источник (0 = без ограничения)
    max_html_pages: int = 0


@dataclass
class PdfCrawlerSettings:
    ocr_enabled: bool = True
    ocr_lang: str = "rus+eng"
    ocr_min_chars_per_page: int = 50
    #: сколько страниц гонять через tesseract параллельно (1 = последовательно)
    ocr_parallel_workers: int = 4
    image_min_width: int = 300
    image_min_height: int = 200
    extract_tables: bool = False
    two_column_detection: bool = True
    filter_schematic_images: bool = True
    use_toc_as_structure: bool = True


@dataclass
class GithubCrawlerSettings:
    token: str = ""
    branch: str = ""
    crawl_issues: bool = False
    crawl_issues_max: int = 50
    crawl_wiki: bool = False
    crawl_docs_dir: bool = True
    include_files: str = "*.md,*.kicad_sch,*.kicad_pcb,*.csv,*.dcm,*.lib"
    #: сколько комментариев подтягивать к каждому issue/PR (0 = только тело)
    issues_comments_max: int = 20
    #: максимальный размер ZIP-архива репозитория, МБ
    max_archive_mb: int = 250


@dataclass
class StackExchangeSettings:
    api_key: str = ""
    site: str = "electronics"
    min_score: int = 5
    max_questions: int = 100


@dataclass
class QualitySettings:
    min_chars: int = 200
    max_chars: int = 200000
    max_non_alpha_ratio: float = 0.30
    max_dup_line_ratio: float = 0.50
    max_code_ratio: float = 0.50
    spam_check: bool = True
    language: str = "bilingual"
    languages_allowed: str = "ru,en"
    perplexity_check: bool = False
    max_perplexity: float = 1000.0
    perplexity_model_path: str = ""


@dataclass
class DedupSettings:
    exact: bool = True
    minhash: bool = True
    minhash_num_perm: int = 128
    minhash_threshold: float = 0.85
    dedup_images: bool = True
    use_streaming: bool = False
    use_incremental: bool = False
    #: порог схожести для инкрементального дедупа (0 = как minhash_threshold)
    incremental_score_threshold: int = 0


@dataclass
class ExportSettings:
    gzip_output: bool = False
    parallel_postproc: bool = False
    parallel_workers: int = 0


@dataclass
class GuiSettings:
    theme: str = "dark"
    language: str = "ru"  # ru | en
    log_level: str = "INFO"
    show_progress_bar: bool = True
    #: проверять коммиты на GitHub при старте (Улучшение: отключается тут же)
    check_updates_on_start: bool = True
    #: C1: значения диалога настроек, которые реально трогали («section.field»).
    #: Пока список не «*», в движок уезжают ТОЛЬКО они: остальное берётся из
    #: config.yaml. Иначе GUI молча затирая весь YAML дефолтами (проверено:
    #: 6 из 6 полей, выставленных в файле, заменялись значениями по умолчанию).
    ui_overridden: list = field(default_factory=list)
    window_width: int = 1280
    window_height: int = 820
    last_config_path: str = ""
    last_output_dir: str = ""
    last_excel_path: str = ""
    recent_configs: list = field(default_factory=list)


#: «*» = применять все настройки (легаси-поведение до введения учёта)
APPLY_ALL_OVERRIDES = "*"


@dataclass
class AppSettings:
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    async_crawl: AsyncSettings = field(default_factory=AsyncSettings)
    html: HtmlCrawlerSettings = field(default_factory=HtmlCrawlerSettings)
    pdf: PdfCrawlerSettings = field(default_factory=PdfCrawlerSettings)
    github: GithubCrawlerSettings = field(default_factory=GithubCrawlerSettings)
    stackexchange: StackExchangeSettings = field(default_factory=StackExchangeSettings)
    quality: QualitySettings = field(default_factory=QualitySettings)
    dedup: DedupSettings = field(default_factory=DedupSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    gui: GuiSettings = field(default_factory=GuiSettings)

    @classmethod
    def _settings_file(cls) -> Path:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path.home()
        return base / ".corpus_builder_settings.json"

    @classmethod
    def load(cls) -> AppSettings:
        path = cls._settings_file()
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls._from_dict(data)
        except Exception:
            return cls()

    @classmethod
    def _from_dict(cls, data: dict) -> AppSettings:
        """Восстановить настройки из JSON, соблюдая объявленные типы dataclass.

        Раньше шёл слепой `setattr`: импорт файла с `"per_url_timeout_minutes":
        "ten"` молча проходил, и взрывался уже в недрах краулинга
        (`TypeError: '>' not supported between instances of 'str' and 'int'`).
        Теперь значение приводится к типу поля; если привести нельзя —
        используется default и проблема логируется (I5).
        """
        settings = cls()
        for section_name, section_data in (data or {}).items():
            if not isinstance(section_data, dict):
                continue
            section = getattr(settings, section_name, None)
            if section is None:
                continue
            hints = get_type_hints(type(section))
            for f in dataclass_fields(section):
                if f.name not in section_data:
                    continue
                value = section_data[f.name]
                try:
                    setattr(section, f.name,
                            _coerce_settings_value(value, hints.get(f.name, str), f.default))
                except (TypeError, ValueError):
                    print(f"Warning: настройка {section_name}.{f.name}={value!r} "
                          f"не приводится к {hints.get(f.name)}, используем default",
                          file=sys.stderr)
                    setattr(section, f.name, f.default)
        return settings

    def save(self) -> None:
        path = self._settings_file()
        try:
            data = asdict(self)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Warning: cannot save settings: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # C1: что из настроек РЕАЛЬНО задано, а что осталось дефолтом
    # ------------------------------------------------------------------
    def _defaults(self) -> dict[str, object]:
        """Снимок значений по умолчанию (лениво, в не-pickle-атрибуте)."""
        cached = getattr(self, "_defaults_cache", None)
        if cached is None:
            cached = type(self)().snapshot()
            object.__setattr__(self, "_defaults_cache", cached)
        return cached

    def snapshot(self) -> dict[str, object]:
        """Плоский вид {«section.field»: значение}."""
        from dataclasses import fields, is_dataclass
        out: dict[str, object] = {}
        for f in fields(self):
            section = getattr(self, f.name)
            if f.name.startswith("_") or not is_dataclass(section):
                continue
            for sub in fields(section):
                if sub.name.startswith("_"):
                    continue
                out[f"{f.name}.{sub.name}"] = getattr(section, sub.name)
        return out

    def changed_fields(self) -> set[str]:
        """Поля, значение которых отличается от дефолта dataclass'а."""
        defaults = self._defaults()
        snap = self.snapshot()
        return {k for k, v in snap.items() if k in defaults and v != defaults[k]}

    def overridden_fields(self) -> set[str]:
        """Что имеет право перекрить config.yaml:
        явно заданное + то, что отметил диалог (`gui.ui_overridden`)."""
        cur = set(self.gui.ui_overridden or [])
        if APPLY_ALL_OVERRIDES in cur:
            return {APPLY_ALL_OVERRIDES}
        return self.changed_fields() | cur

    def mark_touched(self, keys: Iterable[str]) -> None:
        """Диалог настроек: какие поля пользователь правил вручную."""
        cur = set(self.gui.ui_overridden or [])
        if APPLY_ALL_OVERRIDES in cur:
            return
        cur.update(k for k in keys if k != "gui.ui_overridden")
        self.gui.ui_overridden = sorted(cur)

    def compute_overridden_from(self, previous: "AppSettings") -> list[str]:
        """Разница двух снимков (диалог вызывает её при сохранении)."""
        return self.gui.ui_overridden | []

    def clear_ui_overrides(self) -> None:
        self.gui.ui_overridden = []

    def to_dict(self) -> dict:
        return asdict(self)

    def mapping(self) -> list[tuple[str, str]]:
        """Таблица соответствия «настройка GUI → путь в AppConfig».

        Единственный источник истины и для применения, и для теста «все
        настройки имеют потребителя», и для индикатора эффективного конфига.
        Формат пути: «секция.[подсекция.]поле».
        """
        return [
            ("crawl.user_agent", "output.user_agent"),
            ("crawl.request_timeout", "output.request_timeout"),
            ("crawl.request_delay", "output.request_delay"),
            ("crawl.max_file_size_mb", "output.max_file_size_mb"),
            ("crawl.respect_robots_txt", "output.respect_robots_txt"),
            ("crawl.use_cache", "output.use_http_cache"),
            ("crawl.cache_ttl_hours", "output.cache_ttl_hours"),
            ("crawl.revalidate_cached_files", "output.revalidate_cached_files"),
            ("crawl.use_proxy", "output.use_proxy"),
            ("crawl.use_browser_headers", "output.use_browser_headers"),
            ("crawl.contact_email", "output.contact_email"),
            ("crawl.save_checkpoint_every", "pipeline.save_checkpoint_every"),
            ("crawl.progress_bar", "pipeline.progress_bar"),
            ("crawl.per_url_timeout_minutes", "pipeline.per_url_timeout_minutes"),
            ("async_crawl.enabled", "pipeline.use_async"),
            ("async_crawl.max_concurrent_total", "pipeline.max_concurrent_total"),
            ("async_crawl.max_concurrent_per_domain", "pipeline.max_concurrent_per_domain"),
            ("export.parallel_postproc", "pipeline.parallel_postproc"),
            ("export.parallel_workers", "pipeline.parallel_workers"),
            ("export.gzip_output", "export.write_gzip"),
            ("html.extract_mode", "crawlers.html.extract_mode"),
            ("html.download_images", "crawlers.html.download_images"),
            ("html.image_extensions", "crawlers.html.image_extensions"),
            ("html.download_files_ext", "crawlers.html.download_files_ext"),
            ("html.max_html_pages", "crawlers.html.max_html_pages"),
            ("pdf.ocr_enabled", "crawlers.pdf.ocr_enabled"),
            ("pdf.ocr_lang", "crawlers.pdf.ocr_lang"),
            ("pdf.ocr_min_chars_per_page", "crawlers.pdf.ocr_min_chars_per_page"),
            ("pdf.ocr_parallel_workers", "crawlers.pdf.ocr_parallel_workers"),
            ("pdf.image_min_width", "crawlers.pdf.image_min_width"),
            ("pdf.image_min_height", "crawlers.pdf.image_min_height"),
            ("pdf.extract_tables", "crawlers.pdf.extract_tables"),
            ("pdf.two_column_detection", "crawlers.pdf.two_column_detection"),
            ("pdf.filter_schematic_images", "crawlers.pdf.filter_schematic_images"),
            ("pdf.use_toc_as_structure", "crawlers.pdf.use_toc_as_structure"),
            ("github.branch", "crawlers.github.branch"),
            ("github.crawl_issues", "crawlers.github.crawl_issues"),
            ("github.crawl_issues_max", "crawlers.github.crawl_issues_max"),
            ("github.issues_comments_max", "crawlers.github.issues_comments_max"),
            ("github.crawl_wiki", "crawlers.github.crawl_wiki"),
            ("github.crawl_docs_dir", "crawlers.github.crawl_docs_dir"),
            ("github.max_archive_mb", "crawlers.github.max_archive_mb"),
            ("github.include_files", "crawlers.github.include_files"),
            ("stackexchange.site", "crawlers.stackexchange.site"),
            ("stackexchange.min_score", "crawlers.stackexchange.min_score"),
            ("stackexchange.max_questions", "crawlers.stackexchange.max_list_questions"),
            ("quality.min_chars", "quality.min_chars"),
            ("quality.max_chars", "quality.max_chars"),
            ("quality.max_non_alpha_ratio", "quality.max_non_alpha_ratio"),
            ("quality.max_dup_line_ratio", "quality.max_dup_line_ratio"),
            ("quality.max_code_ratio", "quality.max_code_ratio"),
            ("quality.spam_check", "quality.spam_check"),
            ("quality.language", "quality.language"),
            ("quality.languages_allowed", "quality.languages_allowed"),
            ("quality.perplexity_check", "quality.perplexity_check"),
            ("quality.max_perplexity", "quality.max_perplexity"),
            ("quality.perplexity_model_path", "quality.perplexity_model_path"),
            ("dedup.exact", "dedup.exact"),
            ("dedup.minhash", "dedup.minhash"),
            ("dedup.minhash_num_perm", "dedup.minhash_num_perm"),
            ("dedup.minhash_threshold", "dedup.minhash_threshold"),
            ("dedup.dedup_images", "dedup.dedup_images"),
            ("dedup.use_streaming", "dedup.streaming"),
            ("dedup.use_incremental", "dedup.incremental"),
            ("dedup.incremental_score_threshold", "dedup.incremental_score_threshold"),
        ]

    @staticmethod
    def _resolve(obj, dotted: str):
        for part in dotted.split(".")[:-1]:
            obj = getattr(obj, part)
        return obj, dotted.split(".")[-1]

    def _get(self, setting_path: str):
        obj, field = self._resolve(self, setting_path)
        value = getattr(obj, field)
        # CSV-поля диалога движку отдают списком
        if isinstance(value, str) and field in _CSV_FIELDS:
            return _split_csv(value)
        if field == "perplexity_model_path" and not value:
            return None
        if field == "contact_email" and isinstance(value, str):
            return value.strip()
        if field == "branch" and value == "":
            return None
        return value

    def apply_to_config(self, config, strict: bool = True) -> list[str]:
        """Перенести настройки приложения в AppConfig (движок).

        C1: по умолчанию применяются ТОЛЬКО поля, которые пользователь реально
        менял в диалоге настроек (`gui.ui_overridden`). Пока список пуст,
        config.yaml правит всем — раньше любой запуск из GUI молча подменял
        значения из файла дефолтами (проверено: 6 из 6 полей, выставленных в
        YAML, заменялись дефолтами AppSettings).

        `«*»` в списке или `strict=False` — прежнее поведение (наложить всё).
        Возвращает список применённых «путь в AppConfig» — его показывает
        индикатор «эффективного конфига» в GUI.
        """
        allowed = self.overridden_fields()
        apply_all = APPLY_ALL_OVERRIDES in allowed or not strict
        applied: list[str] = []
        unmarked: list[str] = []

        for setting_path, config_path in self.mapping():
            if setting_path.startswith("gui."):        # только UI-состояние
                continue
            if not apply_all and setting_path not in allowed:
                unmarked.append(config_path)
                continue
            target, field = self._resolve(config, config_path)
            setattr(target, field, self._get(setting_path))
            applied.append(config_path)

        if apply_all and not self.gui.ui_overridden:
            # миграция легаси: у пользователя уже есть полный снимок настроек,
            # считаем его «осознанным», иначе апгрейд молча обесценит файл
            self.gui.ui_overridden = [APPLY_ALL_OVERRIDES]
        self._unmarked = unmarked
        return applied

    def unapplied_fields(self) -> list[str]:
        """Что осталось из config.yaml (ценность для индикатора B8)."""
        return list(getattr(self, "_unmarked", []))

    def setup_env_vars(self) -> None:
        """Установить переменные окружения из настроек."""
        if self.github.token:
            os.environ["GITHUB_TOKEN"] = self.github.token
        if self.stackexchange.api_key:
            os.environ["STACKEXCHANGE_KEY"] = self.stackexchange.api_key
        if self.crawl.proxy_list:
            os.environ["CORPUS_BUILDER_PROXIES"] = self.crawl.proxy_list
