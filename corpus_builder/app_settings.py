"""Хранение настроек приложения в JSON-файле."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import get_type_hints


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
    per_url_timeout_minutes: int = 10  # таймаут на один URL


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
    window_width: int = 1280
    window_height: int = 820
    last_config_path: str = ""
    last_output_dir: str = ""
    last_excel_path: str = ""
    recent_configs: list = field(default_factory=list)


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

    def to_dict(self) -> dict:
        return asdict(self)

    def apply_to_config(self, config) -> None:
        """Перенести настройки приложения в AppConfig (движок).

        Список полей обязан быть полным: чекбокс в диалоге настроек, который
        никуда не попадает, — это обещание, которое программа не выполняет (I4).
        Проверка — `test_app_settings.py::test_every_setting_reaches_engine`.
        """
        out = config.output
        out.user_agent = self.crawl.user_agent
        out.request_timeout = self.crawl.request_timeout
        out.request_delay = self.crawl.request_delay
        out.max_file_size_mb = self.crawl.max_file_size_mb
        out.respect_robots_txt = self.crawl.respect_robots_txt
        out.use_http_cache = self.crawl.use_cache
        out.revalidate_cached_files = self.crawl.revalidate_cached_files
        out.cache_ttl_hours = self.crawl.cache_ttl_hours
        out.use_proxy = self.crawl.use_proxy
        out.use_browser_headers = self.crawl.use_browser_headers
        out.contact_email = self.crawl.contact_email.strip()

        pipe = config.pipeline
        pipe.save_checkpoint_every = self.crawl.save_checkpoint_every
        pipe.progress_bar = self.crawl.progress_bar
        pipe.per_url_timeout_minutes = self.crawl.per_url_timeout_minutes
        pipe.use_async = self.async_crawl.enabled
        pipe.max_concurrent_total = self.async_crawl.max_concurrent_total
        pipe.max_concurrent_per_domain = self.async_crawl.max_concurrent_per_domain
        pipe.parallel_postproc = self.export.parallel_postproc
        pipe.parallel_workers = self.export.parallel_workers

        config.export.write_gzip = self.export.gzip_output

        config.dedup.exact = self.dedup.exact
        config.dedup.minhash = self.dedup.minhash
        config.dedup.minhash_num_perm = self.dedup.minhash_num_perm
        config.dedup.minhash_threshold = self.dedup.minhash_threshold
        config.dedup.dedup_images = self.dedup.dedup_images
        config.dedup.streaming = self.dedup.use_streaming
        config.dedup.incremental = self.dedup.use_incremental

        config.crawlers.html.extract_mode = self.html.extract_mode
        config.crawlers.html.download_images = self.html.download_images
        config.crawlers.html.image_extensions = _split_csv(self.html.image_extensions)
        config.crawlers.html.download_files_ext = _split_csv(self.html.download_files_ext)

        config.crawlers.pdf.ocr_enabled = self.pdf.ocr_enabled
        config.crawlers.pdf.ocr_lang = self.pdf.ocr_lang
        config.crawlers.pdf.ocr_min_chars_per_page = self.pdf.ocr_min_chars_per_page
        config.crawlers.pdf.ocr_parallel_workers = self.pdf.ocr_parallel_workers
        config.crawlers.pdf.image_min_width = self.pdf.image_min_width
        config.crawlers.pdf.image_min_height = self.pdf.image_min_height
        config.crawlers.pdf.extract_tables = self.pdf.extract_tables
        config.crawlers.pdf.two_column_detection = self.pdf.two_column_detection
        config.crawlers.pdf.filter_schematic_images = self.pdf.filter_schematic_images
        config.crawlers.pdf.use_toc_as_structure = self.pdf.use_toc_as_structure

        config.crawlers.github.branch = self.github.branch or None
        config.crawlers.github.crawl_issues = self.github.crawl_issues
        config.crawlers.github.crawl_issues_max = self.github.crawl_issues_max
        config.crawlers.github.crawl_wiki = self.github.crawl_wiki
        config.crawlers.github.crawl_docs_dir = self.github.crawl_docs_dir
        config.crawlers.github.include_files = _split_csv(self.github.include_files)

        config.crawlers.stackexchange.site = self.stackexchange.site
        config.crawlers.stackexchange.min_score = self.stackexchange.min_score
        config.crawlers.stackexchange.max_list_questions = self.stackexchange.max_questions

        config.quality.min_chars = self.quality.min_chars
        config.quality.max_chars = self.quality.max_chars
        config.quality.max_non_alpha_ratio = self.quality.max_non_alpha_ratio
        config.quality.max_dup_line_ratio = self.quality.max_dup_line_ratio
        config.quality.max_code_ratio = self.quality.max_code_ratio
        config.quality.spam_check = self.quality.spam_check
        config.quality.language = self.quality.language
        config.quality.languages_allowed = _split_csv(self.quality.languages_allowed)
        config.quality.perplexity_check = self.quality.perplexity_check
        config.quality.max_perplexity = self.quality.max_perplexity
        config.quality.perplexity_model_path = self.quality.perplexity_model_path or None

    def setup_env_vars(self) -> None:
        """Установить переменные окружения из настроек."""
        if self.github.token:
            os.environ["GITHUB_TOKEN"] = self.github.token
        if self.stackexchange.api_key:
            os.environ["STACKEXCHANGE_KEY"] = self.stackexchange.api_key
        if self.crawl.proxy_list:
            os.environ["CORPUS_BUILDER_PROXIES"] = self.crawl.proxy_list
