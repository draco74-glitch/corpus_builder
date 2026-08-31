"""Pydantic-модели для валидации конфигурации и записей корпуса."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

# Единственный источник истины по типам источников.
# Любое изменение здесь должно совпадать с реестром `crawlers.REGISTRY`
# (проверяется тестом tests/test_models_source_types.py).
SOURCE_TYPES = (
    "html", "pdf", "github_repo", "stackexchange", "forum",
    "doaj", "arxiv", "crossref", "wikipedia",
)


# ---------- Конфигурация ----------

class _ValidatingModel(BaseModel):
    """Базовая модель: проверять типы при ПрИСВАИВАНИИ, а не только при создании.

    Без этого `config.pipeline.per_url_timeout_minutes = "ten"` (например из
    импортированного файла настроек) уходил в рантайм и падал внутри краулинга
    (I5).
    """
    model_config = ConfigDict(validate_assignment=True)


class SourceItem(_ValidatingModel):
    url: str
    type: Literal[
        "html", "pdf", "github_repo", "stackexchange", "forum",
        "doaj", "arxiv", "crossref", "wikipedia",
    ]
    categories: list[str] = Field(default_factory=list)
    include_files: list[str] | None = None
    download_files: bool = True
    #: обойти robots.txt для ИСТОЧНИКА (по умолчанию False = уважать).
    #: Нужен, когда краулер ходит в API другого домена (StackExchange →
    #: api.stackexchange.com), а robots.txt проверляется по URL страницы.
    ignore_robots: bool = False


class OutputConfig(_ValidatingModel):
    corpus_file: str
    download_dir: str
    error_log: str = "corpus_output/errors.jsonl"
    state_file: str = "corpus_output/state.json"
    log_file: str = "corpus_output/crawl.log"
    max_file_size_mb: int = 50
    request_delay: float = 2.0
    request_timeout: int = 30
    user_agent: str = "CorpusBuilder/0.2.1"
    #: контакт для «polite» API (Crossref/arXiv просят mailto в User-Agent)
    contact_email: str = ""
    #: вежливость: уважать robots.txt (настройка GUI имела смысл раньше — I4)
    respect_robots_txt: bool = True
    #: fail-closed при недоступности robots.txt (см. RobotsCache)
    robots_fail_open: bool = False
    #: сверять размер кэшированного файла с сервером (HEAD-запрос на файл).
    #: False — прежнее поведение «файл есть → не трогаем» (быстрее, но век
    #: жизни устаревших PDF), True — перезакачивать при смене размера (I5).
    revalidate_cached_files: bool = True
    #: HTTP-кэш ответов (requests-cache + SQLite WAL)
    use_http_cache: bool = True
    cache_ttl_hours: int = 168
    #: брать прокси из CORPUS_BUILDER_PROXIES и ротирировать их
    use_proxy: bool = False
    #: брать Sec-Fetch-* / Accept-* заголовки «как у браузера» (User-Agent из конфига)
    use_browser_headers: bool = False


    @model_validator(mode="after")
    def _derive_sibling_paths(self) -> OutputConfig:
        """state.json/errors.jsonl/crawl.log — рядом с corpus_file по умолчанию.

        Иначе `output.corpus_file: /data/run1/raw.jsonl` без явного state_file
        писал состояние в ./corpus_output/, и «resume» находило чужой state
        (или не находило свой) — та же классика рассогласования путей, что и с
        output-папкой в GUI (I9).
        """
        from pathlib import Path as _P
        parent = _P(self.corpus_file).parent
        defaults = {
            "state_file": OutputConfig.model_fields["state_file"].default,
            "error_log": OutputConfig.model_fields["error_log"].default,
            "log_file": OutputConfig.model_fields["log_file"].default,
        }
        for field, default in defaults.items():
            current = getattr(self, field)
            if current == default and str(parent) != _P(default).parent.as_posix():
                object.__setattr__(self, field, str(parent / _P(default).name))
        return self


class CrawlerHTMLConfig(_ValidatingModel):
    extract_mode: Literal["trafilatura", "bs4"] = "trafilatura"
    download_images: bool = True
    image_extensions: list[str] = Field(default_factory=lambda: ["svg", "png", "jpg", "jpeg", "webp"])
    download_files_ext: list[str] = Field(default_factory=lambda: ["pdf", "kicad_sch", "kicad_pcb", "zip", "sch", "brd"])
    #: потолок скачанных вложений на один источник (0 = без ограничения)
    max_html_pages: int = 0


class CrawlerPDFConfig(_ValidatingModel):
    ocr_enabled: bool = True
    ocr_lang: str = "rus+eng"
    ocr_min_chars_per_page: int = 50
    #: параллельный OCR страниц (tesseract — внешний процесс, GIL не мешает)
    ocr_parallel_workers: int = 1
    image_min_width: int = 300
    image_min_height: int = 200
    extract_tables: bool = False
    # Расширенные опции (Этап 3)
    two_column_detection: bool = True          # авто-определение двухколоночной вёрстки
    two_column_x_threshold: float = 0.35       # если блоков с x < page_width*0.35 > 30% — двухколоночный
    filter_schematic_images: bool = True       # OCR на наличие слов Figure/Circuit/Diagram
    schematic_keywords: list[str] = Field(
        default_factory=lambda: ["figure", "circuit", "diagram", "schematic", "pinout", "block diagram"]
    )
    use_toc_as_structure: bool = True          # использовать TOC для разметки разделов


class CrawlerGitHubConfig(_ValidatingModel):
    token_env: str = "GITHUB_TOKEN"
    branch: str | None = None
    include_files: list[str] = Field(
        default_factory=lambda: ["*.md", "*.kicad_sch", "*.kicad_pcb", "*.csv", "*.dcm", "*.lib"]
    )
    # Расширенные опции (Этап 2)
    crawl_issues: bool = False
    crawl_issues_max: int = 50          # ограничение на число issues/PR
    crawl_issues_state: str = "all"     # "open" | "closed" | "all"
    crawl_wiki: bool = False            # клонировать {repo}.wiki.git
    crawl_docs_dir: bool = True         # парсить docs/ directory из ZIP-архива
    docs_extensions: list[str] = Field(
        default_factory=lambda: [".md", ".rst", ".txt"]
    )
    #: максимальный размер архива репозитория, скачиваемого в память/на диск
    max_archive_mb: int = 250
    #: сколько комментариев подтягивать к каждому issue/PR (0 = только тело)
    issues_comments_max: int = 20


class CrawlerSEConfig(_ValidatingModel):
    api_key_env: str = "STACKEXCHANGE_KEY"
    site: str = "electronics"
    #: сколько вопросов брать из списка (/questions/tagged/<tag>)
    max_list_questions: int = 10
    #: минимальный рейтинг вопроса в списке (0 = без фильтра)
    min_score: int = 0


class CrawlersConfig(_ValidatingModel):
    html: CrawlerHTMLConfig = Field(default_factory=CrawlerHTMLConfig)
    pdf: CrawlerPDFConfig = Field(default_factory=CrawlerPDFConfig)
    github: CrawlerGitHubConfig = Field(default_factory=CrawlerGitHubConfig)
    stackexchange: CrawlerSEConfig = Field(default_factory=CrawlerSEConfig)


class QualityConfig(_ValidatingModel):
    min_chars: int = 200
    max_chars: int = 200_000
    max_non_alpha_ratio: float = 0.30
    max_dup_line_ratio: float = 0.50
    language: Literal["bilingual", "ru", "en", "multi"] = "bilingual"
    languages_allowed: list[str] = Field(default_factory=lambda: ["ru", "en"])
    # Расширенные фильтры (Этап 1)
    max_code_ratio: float = 0.50
    spam_check: bool = True
    perplexity_check: bool = False   # opt-in — требует kenlm модель
    max_perplexity: float = 1000.0
    perplexity_model_path: str | None = None


class DedupConfig(_ValidatingModel):
    exact: bool = True
    minhash: bool = True
    minhash_num_perm: int = 128
    minhash_threshold: float = 0.85
    dedup_images: bool = True
    # Потоковые режимы для больших корпусов (то, что настройки GUI обещали, но
    # никуда не передавались — I3/I4).
    streaming: bool = False
    #: авто-выбор потоковой стратегии для больших корпусов (A4).
    #: "off" — никогда; "auto" — включать по порогу; "force" — всегда streaming
    auto_streaming: Literal["off", "auto", "force"] = "auto"
    auto_streaming_threshold_mb: int = 256
    incremental: bool = False
    incremental_index_file: str = "corpus_output/.dedup_index.pkl"
    incremental_score_threshold: int = 0


class ExportConfig(_ValidatingModel):
    """Настройки экспорта финального корпуса (впервые подключены к движению)."""
    write_gzip: bool = False          # corpus_final.jsonl.gz
    keep_intermediate: bool = True    # deduped.jsonl / filtered.jsonl


class PipelineConfig(_ValidatingModel):
    resume: bool = True
    save_checkpoint_every: int = 50
    #: не чаще этого интервала (сек) писать полный state — см. A5
    min_checkpoint_seconds: float = 5.0
    progress_bar: bool = True
    # дробное значение: 10 минут по умолчанию, но для тестов/быстрых прогонов
    # нужна и гранулярность в секунды (0.5 мин); целое число это исключало
    per_url_timeout_minutes: float = 10.0
    #: использовать async-путь (aiohttp-краулер + семафоры по домену)
    use_async: bool = False
    max_concurrent_total: int = 8
    max_concurrent_per_domain: int = 1
    #: параллельная пост-обработка (несколько process worker'ов)
    parallel_postproc: bool = False
    parallel_workers: int = 0


class FineTuneConfig(_ValidatingModel):
    """Настройки для fine-tuning режима."""
    max_per_type: int = 1000
    min_prompt_chars: int = 20
    max_prompt_chars: int = 8000
    min_completion_chars: int = 20
    max_completion_chars: int = 16000
    balance_classes: bool = True
    remove_pii: bool = True
    #: вырезать также числа, «похожие на телефон/IP» (прежнее поведение).
    #: По умолчанию False: в техническом корпусе это задержки и парт-номера.
    pii_aggressive: bool = False
    formats: list[str] = Field(default_factory=lambda: ["jsonl", "chatml", "alpaca", "sharegpt"])


class AppConfig(_ValidatingModel):
    #: значения, которые были в YAML-файле по-настоящему (заполняет
    #`config.load_config). Нужны индикатору «чем перекрыли»: после наложения
    #: настроек на этот же объект fields_set врёт, а файл надо показать как есть.
    _from_file: dict = PrivateAttr(default_factory=dict)

    def values_from_file(self) -> dict:
        return dict(self._from_file)

    sources: list[SourceItem]
    output: OutputConfig
    crawlers: CrawlersConfig = Field(default_factory=CrawlersConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    finetune: FineTuneConfig = Field(default_factory=FineTuneConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)


# ---------- Запись корпуса ----------

class DownloadedFile(_ValidatingModel):
    type: str                              # image | pdf | kicad | csv | attachment
    original_url: str | None = None
    original_file: str | None = None
    local_path: str
    sha1: str | None = None
    size_bytes: int | None = None


class CorpusRecord(_ValidatingModel):
    source_url: str
    source_type: str
    content: str
    content_sha1: str | None = None
    downloaded_files: list[DownloadedFile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list)
    date_accessed: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    language: str | None = None
    license: str | None = None
    quality_score: float | None = None
    #: контент уже нормализован (краулером или стадией normalize) — см. A2
    content_normalized: bool = False
    is_duplicate: bool = False
    duplicate_of: str | None = None  # source_url оригинала
    status: Literal["ok", "error", "skipped"] = "ok"


class ErrorRecord(_ValidatingModel):
    source_url: str
    source_type: str
    reason: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------- Настройки приложения (В3) — одни и те же модели с AppConfig ----------

class OutputSettings(OutputConfig):
    """`OutputConfig` без обязательных путей.

    Пути (`corpus_file`, `download_dir`) принадлежат конкретному run-конфигу,
    а не глобальным настройкам GUI: в них нет смысла «на все проекты». Всё
    остальное — те же поля, та же валидация, те же дефолты, что у движка:
    второго списка настроек больше нет (В3).
    """
    corpus_file: str = ""
    download_dir: str = ""


class GuiEngineConfig(_ValidatingModel):
    """Значения движка, заданные в диалоге настроек.

    Повторно объявлять поля не нужно: секции — те же классы, что у `AppConfig`
    (и `OutputSettings` для output), поэтому дефолт/тип/диапазон физически не
    могут разойтись с движком, а «что пользователь менял» — это
    `model_fields_set`, а не сравнение с дефолтом.
    """
    output: OutputSettings = Field(default_factory=OutputSettings)
    crawlers: CrawlersConfig = Field(default_factory=CrawlersConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    finetune: FineTuneConfig = Field(default_factory=FineTuneConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)


def explicit_paths(model: BaseModel, prefix: str = "") -> set[str]:
    """Пути leaf-полей, заданных **явно** (а не подставленных дефолтом).

    pydantic ведёт `model_fields_set` на каждой вложенной модели, и важно, что
    запись `cfg.quality.min_chars = 800` помечает поле внутри `quality`, но НЕ
    внутри родителя: поэтому дерево обходится целиком, а не только по
    `model_fields_set` верхнего уровня. Провенанс получается честный и для
    конфигу из YAML (ключи, реально написанные в файле), и для настроек GUI.
    """
    out: set[str] = set()
    for name in type(model).model_fields:
        path = name if not prefix else f"{prefix}.{name}"
        value = getattr(model, name, None)
        if isinstance(value, BaseModel):
            out |= explicit_paths(value, path)
        elif name in model.__pydantic_fields_set__:
            out.add(path)
    return out


def get_by_path(model: BaseModel, dotted: str) -> Any:
    obj: Any = model
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def set_by_path(model: BaseModel, dotted: str, value: Any) -> None:
    """Значение по пути с валидацией типов (модели наследуют `_ValidatingModel`)."""
    parts = dotted.split(".")
    obj: Any = model
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)
