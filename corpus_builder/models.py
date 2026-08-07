"""Pydantic-модели для валидации конфигурации и записей корпуса."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


# ---------- Конфигурация ----------

class SourceItem(BaseModel):
    url: str
    type: Literal["html", "pdf", "github_repo", "stackexchange", "forum"]
    categories: list[str] = Field(default_factory=list)
    include_files: list[str] | None = None
    download_files: bool = True


class OutputConfig(BaseModel):
    corpus_file: str
    download_dir: str
    error_log: str = "corpus_output/errors.jsonl"
    state_file: str = "corpus_output/state.json"
    log_file: str = "corpus_output/crawl.log"
    max_file_size_mb: int = 50
    request_delay: float = 2.0
    request_timeout: int = 30
    user_agent: str = "CorpusBuilder/0.2"


class CrawlerHTMLConfig(BaseModel):
    extract_mode: Literal["trafilatura", "bs4"] = "trafilatura"
    download_images: bool = True
    image_extensions: list[str] = Field(default_factory=lambda: ["svg", "png", "jpg", "jpeg", "webp"])
    download_files_ext: list[str] = Field(default_factory=lambda: ["pdf", "kicad_sch", "kicad_pcb", "zip", "sch", "brd"])


class CrawlerPDFConfig(BaseModel):
    ocr_enabled: bool = True
    ocr_lang: str = "rus+eng"
    ocr_min_chars_per_page: int = 50
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


class CrawlerGitHubConfig(BaseModel):
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


class CrawlerSEConfig(BaseModel):
    api_key_env: str = "STACKEXCHANGE_KEY"
    site: str = "electronics"


class CrawlersConfig(BaseModel):
    html: CrawlerHTMLConfig = Field(default_factory=CrawlerHTMLConfig)
    pdf: CrawlerPDFConfig = Field(default_factory=CrawlerPDFConfig)
    github: CrawlerGitHubConfig = Field(default_factory=CrawlerGitHubConfig)
    stackexchange: CrawlerSEConfig = Field(default_factory=CrawlerSEConfig)


class QualityConfig(BaseModel):
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


class DedupConfig(BaseModel):
    exact: bool = True
    minhash: bool = True
    minhash_num_perm: int = 128
    minhash_threshold: float = 0.85
    dedup_images: bool = True


class PipelineConfig(BaseModel):
    resume: bool = True
    save_checkpoint_every: int = 50
    progress_bar: bool = True


class AppConfig(BaseModel):
    sources: list[SourceItem]
    output: OutputConfig
    crawlers: CrawlersConfig = Field(default_factory=CrawlersConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


# ---------- Запись корпуса ----------

class DownloadedFile(BaseModel):
    type: str                              # image | pdf | kicad | csv | attachment
    original_url: str | None = None
    original_file: str | None = None
    local_path: str
    sha1: str | None = None
    size_bytes: int | None = None


class CorpusRecord(BaseModel):
    source_url: str
    source_type: str
    content: str
    content_sha1: str | None = None
    downloaded_files: list[DownloadedFile] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list)
    date_accessed: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    language: str | None = None
    license: str | None = None
    quality_score: float | None = None
    is_duplicate: bool = False
    duplicate_of: str | None = None  # source_url оригинала
    status: Literal["ok", "error", "skipped"] = "ok"


class ErrorRecord(BaseModel):
    source_url: str
    source_type: str
    reason: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
