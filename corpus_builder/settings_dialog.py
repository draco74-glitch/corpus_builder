"""Единое окно настроек CorpusBuilder с вкладками для всех функций программы.

Вкладки:
  1. General — общие настройки (тема, язык, пути по умолчанию)
  2. Crawling — краулинг (user_agent, timeout, delay, proxy, cache)
  3. HTML — HTML-краулер (extract_mode, images, files)
  4. PDF — PDF-краулер (OCR, двухколоночная вёрстка, таблицы, схемы)
  5. GitHub — GitHub-краулер (токен, issues, wiki, docs)
  6. StackExchange — SE API (ключ, сайт, score)
  7. Quality — фильтр качества (длина, alpha, code, спам, perplexity)
  8. Dedup — дедупликация (exact, MinHash, streaming, incremental)
  9. Performance — производительность (async, workers, gzip, mmap)
  10. GUI — настройки интерфейса (тема, лог, прогресс)
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_settings import AppSettings, _split_csv
from .presets import (all_presets, apply_preset, capture_preset, delete_user_preset,
                      preset_by_key, save_user_preset, validate_preset)
from .gui_improvements import tr, trl
from .logging_setup import get_logger

log = get_logger(__name__)

DARK_BG = "#1e1e1e"
DARKER_BG = "#252526"
LIGHTER_BG = "#2d2d30"
ACCENT = "#007acc"
ACCENT_HOVER = "#1f8ad2"
SUCCESS = "#4ec9b0"
WARN = "#dcdcaa"
ERROR = "#f44747"
TEXT_PRIMARY = "#d4d4d4"
TEXT_SECONDARY = "#858585"
BORDER = "#3c3c3c"


#: режимы приоритета над config.yaml (Б); подписи — ключи перевода
OVERRIDE_MODE_CHOICES = (
    ("changed", "st_override_touched"),
    ("file", "st_override_file"),
    ("all", "st_override_all"),
)

def _slug(text: str) -> str:
    """Ключ пользовательского пресета из названия (латиница/цифры/подчёркивание)."""
    import unicodedata
    import zlib

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    if not clean:
        # название целиком кириллическое: ключ должен быть стабильным и юниксальным
        clean = f"preset_{zlib.crc32(text.encode('utf-8')) & 0xFFFF:04x}"
    return clean


#: варианты авто-стриминга дедупа (А4): «auto» включается сам на крупном корпусе
AUTO_STREAMING_CHOICES = (
    ("off", "off — всегда грузить целиком"),
    ("auto", "auto — стримить, если корпус крупнее порога"),
    ("force", "force — всегда стримить (экономия RAM)"),
)

#: Привязки «путь настройки → виджет»: один список на загрузку и сохранение.
#: В3: пути — те же, что у `AppConfig` (`quality.min_chars`,
#: `crawlers.pdf.ocr_enabled`, `output.request_delay`): диалог и движок смотрят
#: в один объект, поэтому поле не может «исчезнуть по дороге в движок».
SETTING_BINDINGS: tuple[tuple[str, str, str], ...] = (
    ("crawlers.github.branch", "edit_github_branch", "text"),
    ("crawlers.github.crawl_docs_dir", "chk_crawl_docs", "check"),
    ("crawlers.github.crawl_issues", "chk_crawl_issues", "check"),
    ("crawlers.github.crawl_issues_max", "spin_issues_max", "spin"),
    ("crawlers.github.crawl_issues_state", "combo_issues_state", "combo_text"),
    ("crawlers.github.crawl_wiki", "chk_crawl_wiki", "check"),
    ("crawlers.github.docs_extensions", "edit_docs_ext", "csv_text"),
    ("crawlers.github.include_files", "edit_include_files", "csv_text"),
    ("crawlers.html.download_files_ext", "edit_files_ext", "csv_text"),
    ("crawlers.html.download_images", "chk_download_images", "check"),
    ("crawlers.html.extract_mode", "combo_html_mode", "combo_text"),
    ("crawlers.html.image_extensions", "edit_image_ext", "csv_text"),
    ("crawlers.pdf.extract_tables", "chk_extract_tables", "check"),
    ("crawlers.pdf.filter_schematic_images", "chk_filter_schematics", "check"),
    ("crawlers.pdf.image_min_height", "spin_img_min_height", "spin"),
    ("crawlers.pdf.image_min_width", "spin_img_min_width", "spin"),
    ("crawlers.pdf.ocr_enabled", "chk_ocr", "check"),
    ("crawlers.pdf.ocr_lang", "edit_ocr_lang", "text"),
    ("crawlers.pdf.ocr_min_chars_per_page", "spin_ocr_min_chars", "spin"),
    ("crawlers.pdf.ocr_parallel_workers", "spin_ocr_workers", "spin"),
    ("crawlers.pdf.two_column_detection", "chk_two_column", "check"),
    ("crawlers.pdf.use_toc_as_structure", "chk_use_toc", "check"),
    ("crawlers.stackexchange.max_list_questions", "spin_se_max_questions", "spin"),
    ("crawlers.stackexchange.min_score", "spin_se_min_score", "spin"),
    ("crawlers.stackexchange.site", "combo_se_site", "combo_text"),
    ("dedup.auto_streaming", "combo_auto_streaming", "combo_data"),
    ("dedup.auto_streaming_threshold_mb", "spin_streaming_mb", "spin"),
    ("dedup.dedup_images", "chk_dedup_images", "check"),
    ("dedup.exact", "chk_exact", "check"),
    ("dedup.incremental", "chk_incremental", "check"),
    ("dedup.minhash", "chk_minhash", "check"),
    ("dedup.minhash_num_perm", "spin_minhash_perm", "spin"),
    ("dedup.minhash_threshold", "spin_minhash_threshold", "spin"),
    ("dedup.streaming", "chk_streaming", "check"),
    ("export.write_gzip", "chk_gzip", "check"),
    ("output.cache_ttl_hours", "spin_cache_ttl", "spin"),
    ("output.contact_email", "edit_contact_email", "text"),
    ("output.max_file_size_mb", "spin_max_file_size", "spin"),
    ("output.request_delay", "spin_delay", "spin"),
    ("output.request_timeout", "spin_timeout", "spin"),
    ("output.respect_robots_txt", "chk_robots", "check"),
    ("output.revalidate_cached_files", "chk_revalidate", "check"),
    ("output.use_browser_headers", "chk_browser_headers", "check"),
    ("output.use_http_cache", "chk_use_cache", "check"),
    ("output.use_proxy", "chk_use_proxy", "check"),
    ("output.user_agent", "edit_user_agent", "text"),
    ("pipeline.max_concurrent_per_domain", "spin_max_per_domain", "spin"),
    ("pipeline.max_concurrent_total", "spin_max_concurrent", "spin"),
    ("pipeline.min_checkpoint_seconds", "spin_min_checkpoint", "spin"),
    ("pipeline.parallel_postproc", "chk_parallel_postproc", "check"),
    ("pipeline.parallel_workers", "spin_parallel_workers", "spin"),
    ("pipeline.per_url_timeout_minutes", "spin_url_timeout", "spin"),
    ("pipeline.save_checkpoint_every", "spin_checkpoint", "spin"),
    ("pipeline.use_async", "chk_async", "check"),
    ("quality.language", "combo_language", "combo_text"),
    ("quality.languages_allowed", "edit_langs_allowed", "csv_text"),
    ("quality.max_chars", "spin_max_chars", "spin"),
    ("quality.max_code_ratio", "spin_max_code", "spin"),
    ("quality.max_dup_line_ratio", "spin_max_dup_lines", "spin"),
    ("quality.max_non_alpha_ratio", "spin_max_non_alpha", "spin"),
    ("quality.max_perplexity", "spin_max_perplexity", "spin"),
    ("quality.min_chars", "spin_min_chars", "spin"),
    ("quality.perplexity_check", "chk_perplexity", "check"),
    ("quality.perplexity_model_path", "edit_perplexity_model", "text"),
    ("quality.spam_check", "chk_spam", "check"),
    ("secrets.github_token", "edit_github_token", "text"),
    ("secrets.proxy_list", "edit_proxy_list", "plain"),
    ("secrets.stackexchange_api_key", "edit_se_key", "text"),
    ("ui.last_config_path", "edit_last_config", "text"),
    ("ui.last_output_dir", "edit_last_output", "text"),
    ("ui.log_level", "combo_log_level", "combo_text"),
    ("ui.override_mode", "combo_override_mode", "combo_data"),
    ("pipeline.progress_bar", "chk_show_progress", "check"),
    ("ui.theme", "combo_theme", "combo_text"),
    ("ui.window_height", "spin_window_height", "spin"),
    ("ui.window_width", "spin_window_width", "spin"),
)


def _is_empty(value) -> bool:
    """«Не задано» и для виджета, и для модели — одно и то же."""
    return value is None or value == "" or value == [] or value == ()


class SettingsDialog(QDialog):
    """Диалоговое окно настроек с вкладками."""

    settings_changed = Signal()
    #: В5: применён пресет (ключ) — главное окно может обновить индикатор
    preset_applied = Signal(str)

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("settings_title"))
        self.resize(700, 600)
        self._build_ui()
        self._load_values()
        self._apply_styles()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Заголовок
        title = QLabel(tr("settings_header"))
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {ACCENT};")
        outer.addWidget(title)

        subtitle = QLabel(tr("settings_subtitle"))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        outer.addWidget(subtitle)

        # Вкладки
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "📋  Общие")
        self.tabs.addTab(self._build_crawl_tab(), "🌐  Краулинг")
        self.tabs.addTab(self._build_html_tab(), "📄  HTML")
        self.tabs.addTab(self._build_pdf_tab(), "📕  PDF")
        self.tabs.addTab(self._build_github_tab(), "🐙  GitHub")
        self.tabs.addTab(self._build_stackexchange_tab(), "💬  StackExchange")
        self.tabs.addTab(self._build_quality_tab(), "✅  Качество")
        self.tabs.addTab(self._build_dedup_tab(), "🔄  Дедупликация")
        self.tabs.addTab(self._build_performance_tab(), "⚡  Производительность")
        self.tabs.addTab(self._build_gui_tab(), "🎨  Интерфейс")
        outer.addWidget(self.tabs, stretch=1)

        # Кнопки
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton(tr("settings_reset"))
        self.btn_reset.setProperty("secondary", True)
        self.btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(self.btn_reset)

        self.btn_export = QPushButton(tr("settings_export"))
        self.btn_export.setProperty("secondary", True)
        self.btn_export.clicked.connect(self._on_export)
        btn_row.addWidget(self.btn_export)

        self.btn_import = QPushButton(tr("settings_import"))
        self.btn_import.setProperty("secondary", True)
        self.btn_import.clicked.connect(self._on_import)
        btn_row.addWidget(self.btn_import)

        btn_row.addStretch()

        self.btn_cancel = QPushButton(tr("btn_cancel"))
        self.btn_cancel.setProperty("secondary", True)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton(tr("btn_save"))
        self.btn_save.setStyleSheet(
            f"background-color: {ACCENT}; color: white; font-weight: bold; padding: 8px 20px; min-height: 28px;"
        )
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)

        outer.addLayout(btn_row)

    # ============================================================
    # Вкладка: Общие
    # ============================================================

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        # Тема
        self.combo_theme = QComboBox()
        # B5: список тем брался «из головы» (2 из 5) — берём из THEMES
        from .gui_improvements import THEMES
        self._theme_ids = list(THEMES.keys())
        self.combo_theme.addItems(self._theme_ids)
        layout.addRow("Тема оформления:", self.combo_theme)

        # Уровень логирования
        self.combo_log_level = QComboBox()
        self.combo_log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        layout.addRow("Уровень логирования:", self.combo_log_level)

        # Б: явный выбор «кто важнее» — config.yaml или эти настройки
        self.combo_override_mode = QComboBox()
        for value, key in OVERRIDE_MODE_CHOICES:
            self.combo_override_mode.addItem(tr(key), value)
        self.combo_override_mode.setToolTip(tr("st_override_mode_hint"))
        layout.addRow(tr("st_override_mode"), self.combo_override_mode)

        # В5: готовые профили настроек + «сохранить как пресет»
        preset_row = QHBoxLayout()
        self.combo_preset = QComboBox()
        self.combo_preset.setToolTip(tr("st_preset_hint"))
        preset_row.addWidget(self.combo_preset, stretch=1)
        self.btn_preset_apply = QPushButton(tr("st_preset_apply"))
        self.btn_preset_apply.clicked.connect(self._on_apply_preset)
        preset_row.addWidget(self.btn_preset_apply)
        self.btn_preset_save = QPushButton(tr("st_preset_save"))
        self.btn_preset_save.setProperty("secondary", True)
        self.btn_preset_save.clicked.connect(self._on_save_preset)
        preset_row.addWidget(self.btn_preset_save)
        self.btn_reset_overrides = QPushButton(tr("st_reset_overrides"))
        self.btn_reset_overrides.setProperty("secondary", True)
        self.btn_reset_overrides.setToolTip(tr("st_reset_overrides_hint"))
        self.btn_reset_overrides.clicked.connect(self._on_reset_overrides)
        preset_row.addWidget(self.btn_reset_overrides)

        self.btn_preset_delete = QPushButton(tr("st_preset_delete"))
        self.btn_preset_delete.setProperty("secondary", True)
        self.btn_preset_delete.clicked.connect(self._on_delete_preset)
        preset_row.addWidget(self.btn_preset_delete)
        layout.addRow(tr("st_presets"), preset_row)
        self._reload_presets()
        self.combo_preset.currentIndexChanged.connect(self._refresh_preset_buttons)

        # Путь по умолчанию для config.yaml
        self.edit_last_config = QLineEdit()
        self.edit_last_config.setPlaceholderText("Путь к последнему config.yaml")
        btn_config = QPushButton(tr("btn_browse"))
        btn_config.setProperty("secondary", True)
        btn_config.clicked.connect(lambda: self._browse_file(self.edit_last_config, "config.yaml", "YAML (*.yaml *.yml)"))
        config_row = QHBoxLayout()
        config_row.addWidget(self.edit_last_config)
        config_row.addWidget(btn_config)
        layout.addRow("Последний config.yaml:", config_row)

        # Папка вывода по умолчанию
        self.edit_last_output = QLineEdit()
        self.edit_last_output.setPlaceholderText("Папка для корпуса по умолчанию")
        btn_output = QPushButton(tr("btn_browse"))
        btn_output.setProperty("secondary", True)
        btn_output.clicked.connect(lambda: self._browse_dir(self.edit_last_output))
        output_row = QHBoxLayout()
        output_row.addWidget(self.edit_last_output)
        output_row.addWidget(btn_output)
        layout.addRow("Папка корпуса:", output_row)

        # Размер окна
        size_group = QGroupBox(tr("st_window_size"))
        size_layout = QHBoxLayout(size_group)
        size_layout.addWidget(QLabel(tr("st_width")))
        self.spin_window_width = QSpinBox()
        self.spin_window_width.setRange(800, 3840)
        size_layout.addWidget(self.spin_window_width)
        size_layout.addWidget(QLabel(tr("st_height")))
        self.spin_window_height = QSpinBox()
        self.spin_window_height.setRange(600, 2160)
        size_layout.addWidget(self.spin_window_height)
        layout.addRow(size_group)

        return tab

    # ============================================================
    # Вкладка: Краулинг
    # ============================================================

    def _build_crawl_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.edit_user_agent = QLineEdit()
        self.edit_user_agent.setPlaceholderText("User-Agent для HTTP-запросов")
        layout.addRow("User-Agent:", self.edit_user_agent)

        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(5, 300)
        self.spin_timeout.setSuffix(" сек")
        layout.addRow("Таймаут запросов:", self.spin_timeout)

        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(0.0, 30.0)
        self.spin_delay.setSuffix(" сек")
        self.spin_delay.setToolTip(
            "Минимальный интервал МЕЖДУ ЗАПРОСАМИ К ОДНОМУ ДОМЕНУ (не между "
            "записями: разные домены не мешают друг другу). Crawl-delay и "
            "Request-rate из robots.txt этого домена имеют приоритет выше. "
            "Ответ из HTTP-кэша задержку не ждёт.")
        layout.addRow("Задержка между запросами (на домен):", self.spin_delay)

        self.spin_max_file_size = QSpinBox()
        self.spin_max_file_size.setRange(1, 1000)
        self.spin_max_file_size.setSuffix(" МБ")
        layout.addRow("Макс. размер файла:", self.spin_max_file_size)

        self.edit_contact_email = QLineEdit()
        self.edit_contact_email.setPlaceholderText(
            "you@example.org — просят Crossref/Wikipedia (mailto в User-Agent)")
        layout.addRow("Контакт для API:", self.edit_contact_email)

        self.chk_use_cache = QCheckBox(tr("st_use_cache"))
        self.chk_use_cache.setChecked(True)
        layout.addRow(self.chk_use_cache)

        self.chk_revalidate = QCheckBox("Сверять размер кэшированных файлов с сервером (HEAD)")
        self.chk_revalidate.setToolTip(
            "Выключено: ранее скачанный PDF используется вечно, даже если на сервере "
            "лежит новая версия того же URL")
        layout.addRow(self.chk_revalidate)

        self.spin_cache_ttl = QSpinBox()
        self.spin_cache_ttl.setRange(1, 720)
        self.spin_cache_ttl.setSuffix(" часов")
        layout.addRow("Срок жизни кэша:", self.spin_cache_ttl)

        self.edit_contact_email = QLineEdit()
        self.edit_contact_email.setPlaceholderText(
            "you@example.org — нужен для Crossref/Wikipedia («polite pool»)")
        layout.addRow("Контакт для API:", self.edit_contact_email)

        self.chk_robots = QCheckBox(tr("st_robots"))
        self.chk_robots.setChecked(True)
        layout.addRow(self.chk_robots)

        self.chk_browser_headers = QCheckBox(tr("st_browser_headers"))
        self.chk_browser_headers.setChecked(True)
        layout.addRow(self.chk_browser_headers)

        # Прокси
        proxy_group = QGroupBox(tr("st_proxy"))
        proxy_layout = QVBoxLayout(proxy_group)
        self.chk_use_proxy = QCheckBox(tr("st_use_proxy"))
        proxy_layout.addWidget(self.chk_use_proxy)
        proxy_layout.addWidget(QLabel(tr("st_proxy_list")))
        self.edit_proxy_list = QTextEdit()
        self.edit_proxy_list.setMaximumHeight(80)
        self.edit_proxy_list.setPlaceholderText("http://user:pass@host:port, socks5://host:port, ...")
        proxy_layout.addWidget(self.edit_proxy_list)
        layout.addRow(proxy_group)

        self.spin_checkpoint = QSpinBox()
        self.spin_checkpoint.setRange(1, 1000)
        layout.addRow("Сохранять чекпойнт каждые:", self.spin_checkpoint)

        self.spin_min_checkpoint = QDoubleSpinBox()
        self.spin_min_checkpoint.setRange(0.0, 600.0)
        self.spin_min_checkpoint.setSingleStep(0.5)
        self.spin_min_checkpoint.setDecimals(1)
        self.spin_min_checkpoint.setSuffix(" с")
        self.spin_min_checkpoint.setToolTip(
            "Не переписывать state.json чаще этого интервала (0 = на каждом чекпойnte)")
        layout.addRow("Минимальный интервал чекпойнта:", self.spin_min_checkpoint)

        return tab

    # ============================================================
    # Вкладка: HTML
    # ============================================================

    def _build_html_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.combo_html_mode = QComboBox()
        self.combo_html_mode.addItems(["trafilatura", "bs4"])
        layout.addRow("Режим извлечения:", self.combo_html_mode)

        self.chk_download_images = QCheckBox(tr("st_download_images"))
        self.chk_download_images.setChecked(True)
        layout.addRow(self.chk_download_images)

        self.edit_image_ext = QLineEdit()
        self.edit_image_ext.setPlaceholderText("svg,png,jpg,jpeg,webp")
        layout.addRow("Расширения изображений:", self.edit_image_ext)

        self.edit_files_ext = QLineEdit()
        self.edit_files_ext.setPlaceholderText("pdf,kicad_sch,kicad_pcb,zip,sch,brd")
        layout.addRow("Расширения файлов:", self.edit_files_ext)

        return tab

    # ============================================================
    # Вкладка: PDF
    # ============================================================

    def _build_pdf_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.chk_ocr = QCheckBox(tr("st_ocr"))
        self.chk_ocr.setChecked(True)
        layout.addRow(self.chk_ocr)

        self.edit_ocr_lang = QLineEdit()
        self.edit_ocr_lang.setPlaceholderText("rus+eng")
        layout.addRow("Язык OCR:", self.edit_ocr_lang)

        self.spin_ocr_min_chars = QSpinBox()
        self.spin_ocr_min_chars.setRange(1, 5000)
        layout.addRow("Мин. символов на страницу для OCR:", self.spin_ocr_min_chars)

        self.spin_ocr_workers = QSpinBox()
        self.spin_ocr_workers.setRange(1, 16)
        layout.addRow("Параллельных потоков OCR:", self.spin_ocr_workers)

        self.spin_img_min_width = QSpinBox()
        self.spin_img_min_width.setRange(10, 5000)
        self.spin_img_min_width.setSuffix(" px")
        layout.addRow("Мин. ширина изображения:", self.spin_img_min_width)

        self.spin_img_min_height = QSpinBox()
        self.spin_img_min_height.setRange(10, 5000)
        self.spin_img_min_height.setSuffix(" px")
        layout.addRow("Мин. высота изображения:", self.spin_img_min_height)

        self.chk_extract_tables = QCheckBox(tr("st_extract_tables"))
        layout.addRow(self.chk_extract_tables)

        self.chk_two_column = QCheckBox(tr("st_two_column"))
        self.chk_two_column.setChecked(True)
        layout.addRow(self.chk_two_column)

        self.chk_filter_schematics = QCheckBox(tr("st_filter_schematics"))
        self.chk_filter_schematics.setChecked(True)
        layout.addRow(self.chk_filter_schematics)

        self.chk_use_toc = QCheckBox(tr("st_use_toc"))
        self.chk_use_toc.setChecked(True)
        layout.addRow(self.chk_use_toc)

        return tab

    # ============================================================
    # Вкладка: GitHub
    # ============================================================

    def _build_github_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.edit_github_token = QLineEdit()
        self.edit_github_token.setEchoMode(QLineEdit.Password)
        self.edit_github_token.setPlaceholderText("ghp_xxxxxxxxxxxxxxxxxxxx")
        layout.addRow("GitHub Token:", self.edit_github_token)

        layout.addRow(QLabel(
            "⚠ Токен хранится в настройках приложения (не в config.yaml).\n"
            "Получить: github.com/settings/tokens → Fine-grained token"
        ))

        self.edit_github_branch = QLineEdit()
        self.edit_github_branch.setPlaceholderText("(пусто = auto-detect default branch)")
        layout.addRow("Ветка (пусто = авто):", self.edit_github_branch)

        self.chk_crawl_issues = QCheckBox(tr("st_crawl_issues"))
        layout.addRow(self.chk_crawl_issues)

        self.spin_issues_max = QSpinBox()
        self.spin_issues_max.setRange(1, 500)
        layout.addRow("Макс. Issues/PR:", self.spin_issues_max)

        self.combo_issues_state = QComboBox()
        self.combo_issues_state.addItems(["all", "open", "closed"])
        layout.addRow("Состояние Issues:", self.combo_issues_state)

        self.edit_docs_ext = QLineEdit()
        self.edit_docs_ext.setPlaceholderText(".md, .rst, .txt")
        self.edit_docs_ext.setToolTip("Расширения файлов из docs/, которые считаем документацией")
        layout.addRow("Расширения docs/:", self.edit_docs_ext)

        self.chk_crawl_wiki = QCheckBox(tr("st_crawl_wiki"))
        layout.addRow(self.chk_crawl_wiki)

        self.chk_crawl_docs = QCheckBox(tr("st_crawl_docs"))
        self.chk_crawl_docs.setChecked(True)
        layout.addRow(self.chk_crawl_docs)

        self.edit_include_files = QLineEdit()
        self.edit_include_files.setPlaceholderText("*.md,*.kicad_sch,*.kicad_pcb,*.csv,*.dcm,*.lib")
        layout.addRow("Шаблоны файлов:", self.edit_include_files)

        return tab

    # ============================================================
    # Вкладка: StackExchange
    # ============================================================

    def _build_stackexchange_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.edit_se_key = QLineEdit()
        self.edit_se_key.setEchoMode(QLineEdit.Password)
        self.edit_se_key.setPlaceholderText("опционально, для повышенного лимита")
        layout.addRow("StackExchange API Key:", self.edit_se_key)

        self.combo_se_site = QComboBox()
        self.combo_se_site.addItems([
            "electronics", "stackoverflow", "serverfault", "superuser",
            "mathoverflow", "askubuntu",
        ])
        layout.addRow("Сайт по умолчанию:", self.combo_se_site)

        self.spin_se_min_score = QSpinBox()
        self.spin_se_min_score.setRange(0, 10000)
        layout.addRow("Мин. score вопроса:", self.spin_se_min_score)

        self.spin_se_max_questions = QSpinBox()
        self.spin_se_max_questions.setRange(1, 1000)
        layout.addRow("Макс. вопросов на тег:", self.spin_se_max_questions)

        return tab

    # ============================================================
    # Вкладка: Качество
    # ============================================================

    def _build_quality_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.spin_min_chars = QSpinBox()
        self.spin_min_chars.setRange(0, 1000000)
        layout.addRow("Мин. длина текста (chars):", self.spin_min_chars)

        self.spin_max_chars = QSpinBox()
        self.spin_max_chars.setRange(1000, 10000000)
        layout.addRow("Макс. длина текста (chars):", self.spin_max_chars)

        self.spin_max_non_alpha = QDoubleSpinBox()
        self.spin_max_non_alpha.setRange(0.0, 1.0)
        self.spin_max_non_alpha.setSingleStep(0.05)
        layout.addRow("Макс. доля не-букв (0-1):", self.spin_max_non_alpha)

        self.spin_max_dup_lines = QDoubleSpinBox()
        self.spin_max_dup_lines.setRange(0.0, 1.0)
        self.spin_max_dup_lines.setSingleStep(0.05)
        layout.addRow("Макс. доля дубл. строк (0-1):", self.spin_max_dup_lines)

        self.spin_max_code = QDoubleSpinBox()
        self.spin_max_code.setRange(0.0, 1.0)
        self.spin_max_code.setSingleStep(0.05)
        layout.addRow("Макс. доля кода (0-1):", self.spin_max_code)

        self.chk_spam = QCheckBox(tr("st_spam"))
        self.chk_spam.setChecked(True)
        layout.addRow(self.chk_spam)

        self.combo_language = QComboBox()
        self.combo_language.addItems(["bilingual", "ru", "en", "multi"])
        layout.addRow("Языковой режим:", self.combo_language)

        self.edit_langs_allowed = QLineEdit()
        self.edit_langs_allowed.setPlaceholderText("ru,en")
        layout.addRow("Разрешённые языки:", self.edit_langs_allowed)

        # Perplexity (опционально)
        perplexity_group = QGroupBox(tr("st_perplexity_group"))
        perplexity_layout = QVBoxLayout(perplexity_group)
        self.chk_perplexity = QCheckBox(tr("st_perplexity"))
        perplexity_layout.addWidget(self.chk_perplexity)
        perplexity_layout.addWidget(QLabel(tr("st_max_perplexity")))
        self.spin_max_perplexity = QDoubleSpinBox()
        self.spin_max_perplexity.setRange(10.0, 100000.0)
        self.spin_max_perplexity.setValue(1000.0)
        perplexity_layout.addWidget(self.spin_max_perplexity)
        perplexity_layout.addWidget(QLabel(tr("st_perplexity_model")))
        perplexity_path_row = QHBoxLayout()
        self.edit_perplexity_model = QLineEdit()
        self.edit_perplexity_model.setPlaceholderText("/path/to/model.binary")
        perplexity_path_row.addWidget(self.edit_perplexity_model)
        btn_perp_model = QPushButton(tr("btn_browse"))
        btn_perp_model.setProperty("secondary", True)
        btn_perp_model.clicked.connect(lambda: self._browse_file(self.edit_perplexity_model, "kenlm model", "KenLM model (*.binary *.arpa);;All files (*)"))
        perplexity_path_row.addWidget(btn_perp_model)
        perplexity_layout.addLayout(perplexity_path_row)
        layout.addRow(perplexity_group)

        return tab

    # ============================================================
    # Вкладка: Дедупликация
    # ============================================================

    def _build_dedup_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.chk_exact = QCheckBox(tr("st_dedup_exact"))
        self.chk_exact.setChecked(True)
        layout.addRow(self.chk_exact)

        self.chk_minhash = QCheckBox(tr("st_dedup_minhash"))
        self.chk_minhash.setChecked(True)
        layout.addRow(self.chk_minhash)

        self.spin_minhash_perm = QSpinBox()
        self.spin_minhash_perm.setRange(16, 512)
        layout.addRow("MinHash permutations:", self.spin_minhash_perm)

        self.spin_minhash_threshold = QDoubleSpinBox()
        self.spin_minhash_threshold.setRange(0.5, 1.0)
        self.spin_minhash_threshold.setSingleStep(0.01)
        layout.addRow("Порог Jaccard (0.5-1.0):", self.spin_minhash_threshold)

        self.chk_dedup_images = QCheckBox(tr("st_dedup_images"))
        self.chk_dedup_images.setChecked(True)
        layout.addRow(self.chk_dedup_images)

        self.chk_streaming = QCheckBox(tr("st_streaming"))
        layout.addRow(self.chk_streaming)

        self.combo_auto_streaming = QComboBox()
        for value, label in AUTO_STREAMING_CHOICES:
            self.combo_auto_streaming.addItem(label, value)
        layout.addRow(tr("st_auto_streaming"), self.combo_auto_streaming)

        self.spin_streaming_mb = QSpinBox()
        self.spin_streaming_mb.setRange(16, 65536)
        self.spin_streaming_mb.setSingleStep(64)
        self.spin_streaming_mb.setSuffix(" МБ")
        layout.addRow(tr("st_streaming_threshold"), self.spin_streaming_mb)

        self.chk_incremental = QCheckBox(tr("st_incremental"))
        layout.addRow(self.chk_incremental)

        return tab

    # ============================================================
    # Вкладка: Производительность
    # ============================================================

    def _build_performance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.chk_async = QCheckBox(tr("st_async"))
        layout.addRow(self.chk_async)

        self.spin_max_concurrent = QSpinBox()
        self.spin_max_concurrent.setRange(1, 100)
        self.spin_max_concurrent.setValue(8)
        layout.addRow("Макс. одновременных запросов:", self.spin_max_concurrent)

        self.spin_max_per_domain = QSpinBox()
        self.spin_max_per_domain.setRange(1, 20)
        self.spin_max_per_domain.setValue(1)
        layout.addRow("Макс. на домен (1 = вежливо):", self.spin_max_per_domain)

        self.spin_url_timeout = QDoubleSpinBox()
        self.spin_url_timeout.setDecimals(2)
        self.spin_url_timeout.setRange(0.05, 720)
        self.spin_url_timeout.setSuffix(" мин")
        self.spin_url_timeout.setValue(10)
        self.spin_url_timeout.setSuffix(" мин")
        self.spin_url_timeout.setToolTip(
            "Если обработка одного URL занимает больше этого времени —\n"
            "0.05 мин = 3 с; удобно для проверок.\n"
            "URL пропускается и помечается как ошибочный.\n"
            "Защищает от зависания на медленных/больших ресурсах."
        )
        layout.addRow("Таймаут на один URL:", self.spin_url_timeout)

        self.chk_gzip = QCheckBox(tr("st_gzip"))
        layout.addRow(self.chk_gzip)

        self.chk_parallel_postproc = QCheckBox(tr("st_parallel"))
        layout.addRow(self.chk_parallel_postproc)

        self.spin_parallel_workers = QSpinBox()
        self.spin_parallel_workers.setRange(0, 32)
        self.spin_parallel_workers.setValue(0)
        layout.addRow("Workers для пост-обработки (0 = auto):", self.spin_parallel_workers)

        layout.addRow(QLabel(
            "💡 Рекомендации:\n"
            "• Для 100+ источников: async-crawl с max_concurrent=8\n"
            "• Для больших корпусов (>1 ГБ): gzip + parallel_postproc + streaming MinHash\n"
            "• Для повторных прогонов: incremental dedup + HTTP cache"
        ))

        return tab

    # ============================================================
    # Вкладка: Интерфейс
    # ============================================================

    def _build_gui_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(10)

        self.chk_show_progress = QCheckBox(tr("st_show_progress"))
        self.chk_show_progress.setChecked(True)
        layout.addRow(self.chk_show_progress)

        info = QLabel(
            "🎨 Тема настраивается во вкладке «Общие».\n"
            "📝 Уровень логирования — во вкладке «Общие».\n"
            "💾 Настройки сохраняются в ~/.corpus_builder_settings.json"
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addRow(info)

        return tab

    # ============================================================
    # Загрузка/сохранение значений
    # ============================================================

    def _load_values(self) -> None:
        """Разложить значения настроек по виджетам (см. SETTING_BINDINGS)."""
        for path, widget, kind in SETTING_BINDINGS:
            w = getattr(self, widget)
            value = self.settings.get(path)
            if kind == "spin":
                w.setValue(value)
            elif kind == "check":
                w.setChecked(bool(value))
            elif kind == "combo_text":
                w.setCurrentText(value or "")
            elif kind == "combo_data":
                idx = w.findData(value)
                w.setCurrentIndex(idx if idx >= 0 else 0)
            elif kind == "csv_text":
                w.setText(", ".join(value or []))
            elif kind == "plain":
                w.setPlainText(value or "")
            else:
                w.setText("" if value is None else str(value))

    def _save_values(self) -> None:
        """Собрать значения из виджетов в настройки.

        Записывается только реально изменившееся (`_set_if_changed`). Иначе любое
        «Сохранить» сделало бы все ~70 полей явно заданными — и настройки снова
        начали бы молча перекрывать config.yaml (В1), из-за чего схема и менялась.
        """
        for path, widget, kind in SETTING_BINDINGS:
            w = getattr(self, widget)
            if kind == "spin":
                value = w.value()
            elif kind == "check":
                value = w.isChecked()
            elif kind == "combo_text":
                value = w.currentText()
            elif kind == "combo_data":
                value = w.currentData()
            elif kind == "csv_text":
                value = _split_csv(w.text())
            elif kind == "plain":
                value = w.toPlainText()
            else:
                value = w.text()
            self._set_if_changed(path, value)

    def _set_if_changed(self, path: str, value) -> None:
        """Записать поле, только если пользователь его правда поменял.

        Два нюанса, без которых «Сохранить» снова стало бы «явно задал всё»:
        • пустое поле виджета («») и None в модели — одно и то же «не задано»;
        • значение, совпавшее с дефолтом движка, перекрывающую отметку СНИМАЕТ:
          вернул поле к общему значению — и оно снова берётся из config.yaml.
        """
        current = self.settings.get(path)
        if _is_empty(current) and _is_empty(value):
            if current is not None:
                return                              # "" и [] — то же «пусто»
            self.settings.reset(path)               # виджет очистил заданное поле
            return
        if current == value:
            if self.settings.is_default(path):
                self.settings.reset(path)
            return
        try:
            self.settings.set(path, value)
        except (ValueError, KeyError) as e:
            # виджет выдал то, что движок не примет: не роняем диалог и не
            # записываем — поле останется взятым из config.yaml
            log.warning(f"настройка «{path}» не сохранена: {e}")
            return
        if self.settings.is_default(path):
            self.settings.reset(path)

    # ============================================================
    # Обработчики кнопок
    # ============================================================

    # ------------------------------------------------------------- пресеты (В5)
    def _reload_presets(self, keep: str | None = None) -> None:
        """Обновить список: встроенные профили + пользовательские."""
        current = keep or self.combo_preset.currentData()
        self.combo_preset.blockSignals(True)
        self.combo_preset.clear()
        for preset in all_presets():
            label = preset.title if preset.builtin else f"{preset.title} ★"
            self.combo_preset.addItem(label, preset.key)
            self.combo_preset.setItemData(
                self.combo_preset.count() - 1, preset.description,
                Qt.ItemDataRole.ToolTipRole)
        idx = self.combo_preset.findData(current) if current else -1
        self.combo_preset.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_preset.blockSignals(False)
        self._refresh_preset_buttons()

    def _refresh_preset_buttons(self) -> None:
        key = self.combo_preset.currentData()
        preset = preset_by_key(key) if key else None
        self.btn_preset_delete.setEnabled(bool(preset and not preset.builtin))

    def _on_apply_preset(self) -> None:
        key = self.combo_preset.currentData()
        if not key:
            return
        try:
            changed = apply_preset(self.settings, key)
        except (KeyError, ValueError) as e:
            QMessageBox.warning(self, tr("st_preset_apply"), str(e))
            return
        self._load_values()             # виджеты обязаны показать значения пресета
        self.preset_applied.emit(key)
        QMessageBox.information(
            self, tr("st_preset_apply"),
            tr("st_preset_applied").replace("{n}", str(len(changed)))
            + ("\n" + ", ".join(sorted(changed)[:6]) if changed else ""))

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("st_preset_save"),
                                        tr("st_preset_name_ask"))
        if not ok or not name.strip():
            return
        key = _slug(name)
        preset = capture_preset(self.settings, key, name.strip())
        problems = validate_preset(preset)
        if problems:
            QMessageBox.warning(self, tr("st_preset_save"), "\n".join(problems))
            return
        try:
            save_user_preset(preset)
        except OSError as e:
            QMessageBox.critical(self, tr("st_preset_save"), str(e))
            return
        self._reload_presets(keep=key)
        QMessageBox.information(self, tr("st_preset_save"),
                                tr("st_preset_saved").replace("{name}", name.strip())
                                .replace("{n}", str(len(preset.values))))

    def _on_delete_preset(self) -> None:
        key = self.combo_preset.currentData()
        preset = preset_by_key(key) if key else None
        if preset is None or preset.builtin:
            return
        if QMessageBox.question(self, tr("st_preset_delete"),
                                tr("st_preset_delete_ask").replace("{name}", preset.title)
                                ) != QMessageBox.StandardButton.Yes:
            return
        delete_user_preset(key)
        self._reload_presets()

    def _on_reset_overrides(self) -> None:
        """«Взять из config.yaml»: снять со всех полей отметку «задавал в GUI» (В1).

        До В3 такой кнопки не могло быть: «не перекрывать» приходилось бы
        вычислять сравнением со вторым набором дефолтов. Здесь это просто
        очищенный провенанс.
        """
        n = self.settings.reset_all()
        self._load_values()
        if n:
            QMessageBox.information(self, tr("st_reset_overrides"),
                                    tr("st_reset_overrides_done").replace("{n}", str(n)))
        else:
            QMessageBox.information(self, tr("st_reset_overrides"), tr("st_reset_none"))

    def _on_save(self) -> None:
        """Сохранить настройки и закрыть окно."""
        self._save_values()
        self.settings.save()
        self.settings.setup_env_vars()
        self.settings_changed.emit()
        QMessageBox.information(self, trl('Сохранено'),
            trl('Настройки сохранены и будут применены к следующим запускам.'))
        self.accept()

    def _on_reset(self) -> None:
        """Сбросить все настройки к значениям по умолчанию."""
        reply = QMessageBox.question(
            self, trl('Сброс настроек'),
            trl('Сбросить все настройки к значениям по умолчанию?'),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            from .app_settings import AppSettings
            self.settings = AppSettings()
            self._load_values()
            QMessageBox.information(self, trl('Сброшено'),
                trl('Настройки сброшены. Нажмите «Сохранить» для применения.'))

    def _on_export(self) -> None:
        """Экспорт настроек в JSON-файл."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт настроек", "corpus_builder_settings.json",
            "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._save_values()
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.settings.to_export_dict(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, trl('Экспортировано'), trl('Настройки сохранены в:\n{0}').format(path))
        except Exception as e:
            QMessageBox.critical(self, trl('Ошибка'), str(e))

    def _on_import(self) -> None:
        """Импорт настроек из JSON-файла."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт настроек", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            import json

            from .app_settings import AppSettings
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.settings = AppSettings.from_dict(data)
            self._load_values()
            QMessageBox.information(self, trl('Импортировано'),
                trl('Настройки загружены. Нажмите «Сохранить» для применения.'))
        except Exception as e:
            QMessageBox.critical(self, trl('Ошибка'), str(e))

    def _browse_file(self, edit: QLineEdit, title: str, filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, "", filter)
        if path:
            edit.setText(path)

    def _browse_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if path:
            edit.setText(path)

    # ============================================================
    # Стилизация
    # ============================================================

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        QDialog, QWidget {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY};
            font-family: 'Segoe UI', 'SF Pro', 'DejaVu Sans'; font-size: 13px; }}
        QGroupBox {{ background-color: {DARKER_BG}; border: 1px solid {BORDER};
            border-radius: 6px; margin-top: 14px; padding-top: 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
            color: {ACCENT}; font-weight: bold; }}
        QPushButton {{ background-color: {ACCENT}; color: white; border: none;
            padding: 6px 14px; border-radius: 4px; min-height: 22px; }}
        QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        QPushButton[secondary="true"] {{ background-color: #3a3a3a; color: {TEXT_PRIMARY}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {DARKER_BG};
            border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px;
            color: {TEXT_PRIMARY}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {ACCENT}; }}
        QTabWidget::pane {{ border: 1px solid {BORDER}; background: {DARK_BG}; }}
        QTabBar::tab {{ background: {DARKER_BG}; color: {TEXT_SECONDARY};
            padding: 6px 14px; border: 1px solid {BORDER}; border-bottom: none; }}
        QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}
        QTextEdit {{ background-color: {DARKER_BG}; border: 1px solid {BORDER};
            border-radius: 4px; color: {TEXT_PRIMARY}; }}
        QLabel {{ color: {TEXT_PRIMARY}; }}
        QCheckBox {{ color: {TEXT_PRIMARY}; }}
        """)
