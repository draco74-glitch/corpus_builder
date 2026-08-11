"""Хранение настроек приложения в JSON-файле."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class CrawlSettings:
    user_agent: str = "CorpusBuilder/0.2 (research)"
    request_timeout: int = 30
    request_delay: float = 2.0
    max_file_size_mb: int = 50
    use_cache: bool = True
    cache_ttl_hours: int = 168
    use_proxy: bool = False
    proxy_list: str = ""
    use_browser_headers: bool = True
    respect_robots_txt: bool = True
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
    log_level: str = "INFO"
    show_progress_bar: bool = True
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
    def load(cls) -> "AppSettings":
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
    def _from_dict(cls, data: dict) -> "AppSettings":
        settings = cls()
        for section_name, section_data in data.items():
            if not isinstance(section_data, dict):
                continue
            section = getattr(settings, section_name, None)
            if section is None:
                continue
            for key, value in section_data.items():
                if hasattr(section, key):
                    setattr(section, key, value)
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
        """Применить настройки к AppConfig."""
        config.output.user_agent = self.crawl.user_agent
        config.output.request_timeout = self.crawl.request_timeout
        config.output.request_delay = self.crawl.request_delay
        config.output.max_file_size_mb = self.crawl.max_file_size_mb
        config.pipeline.save_checkpoint_every = self.crawl.save_checkpoint_every
        config.pipeline.progress_bar = self.crawl.progress_bar
        config.pipeline.per_url_timeout_minutes = self.crawl.per_url_timeout_minutes

        config.crawlers.html.extract_mode = self.html.extract_mode
        config.crawlers.html.download_images = self.html.download_images
        config.crawlers.html.image_extensions = [e.strip() for e in self.html.image_extensions.split(",") if e.strip()]
        config.crawlers.html.download_files_ext = [e.strip() for e in self.html.download_files_ext.split(",") if e.strip()]

        config.crawlers.pdf.ocr_enabled = self.pdf.ocr_enabled
        config.crawlers.pdf.ocr_lang = self.pdf.ocr_lang
        config.crawlers.pdf.ocr_min_chars_per_page = self.pdf.ocr_min_chars_per_page
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
        config.crawlers.github.include_files = [p.strip() for p in self.github.include_files.split(",") if p.strip()]

        config.crawlers.stackexchange.site = self.stackexchange.site

        config.quality.min_chars = self.quality.min_chars
        config.quality.max_chars = self.quality.max_chars
        config.quality.max_non_alpha_ratio = self.quality.max_non_alpha_ratio
        config.quality.max_dup_line_ratio = self.quality.max_dup_line_ratio
        config.quality.max_code_ratio = self.quality.max_code_ratio
        config.quality.spam_check = self.quality.spam_check
        config.quality.language = self.quality.language
        config.quality.languages_allowed = [l.strip() for l in self.quality.languages_allowed.split(",") if l.strip()]
        config.quality.perplexity_check = self.quality.perplexity_check
        config.quality.max_perplexity = self.quality.max_perplexity
        config.quality.perplexity_model_path = self.quality.perplexity_model_path or None

        config.dedup.exact = self.dedup.exact
        config.dedup.minhash = self.dedup.minhash
        config.dedup.minhash_num_perm = self.dedup.minhash_num_perm
        config.dedup.minhash_threshold = self.dedup.minhash_threshold
        config.dedup.dedup_images = self.dedup.dedup_images

    def setup_env_vars(self) -> None:
        """Установить переменные окружения из настроек."""
        if self.github.token:
            os.environ["GITHUB_TOKEN"] = self.github.token
        if self.stackexchange.api_key:
            os.environ["STACKEXCHANGE_KEY"] = self.stackexchange.api_key
        if self.crawl.proxy_list:
            os.environ["CORPUS_BUILDER_PROXIES"] = self.crawl.proxy_list
