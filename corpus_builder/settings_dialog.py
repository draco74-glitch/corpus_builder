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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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

from .app_settings import AppSettings
from .gui_improvements import tr
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
    ("touched", "st_override_touched"),
    ("file", "st_override_file"),
    ("all", "st_override_all"),
)

#: варианты авто-стриминга дедупа (А4): «auto» включается сам на крупном корпусе
AUTO_STREAMING_CHOICES = (
    ("off", "off — всегда грузить целиком"),
    ("auto", "auto — стримить, если корпус крупнее порога"),
    ("force", "force — всегда стримить (экономия RAM)"),
)

class SettingsDialog(QDialog):
    """Диалоговое окно настроек с вкладками."""

    settings_changed = Signal()

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        #: Б: снимок «до», чтобы при сохранении отметить только тронутые поля
        self._open_snapshot = settings.snapshot()
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
        """Загрузить значения из AppSettings в виджеты."""
        s = self.settings

        # General
        self.combo_theme.setCurrentText(s.gui.theme)
        self.combo_log_level.setCurrentText(s.gui.log_level)
        self.edit_last_config.setText(s.gui.last_config_path)
        self.edit_last_output.setText(s.gui.last_output_dir)
        self.spin_window_width.setValue(s.gui.window_width)
        self.spin_window_height.setValue(s.gui.window_height)

        # Crawl
        self.edit_user_agent.setText(s.crawl.user_agent)
        self.spin_timeout.setValue(s.crawl.request_timeout)
        self.spin_delay.setValue(s.crawl.request_delay)
        self.spin_max_file_size.setValue(s.crawl.max_file_size_mb)
        self.edit_contact_email.setText(s.crawl.contact_email)
        self.chk_use_cache.setChecked(s.crawl.use_cache)
        self.edit_contact_email.setText(s.crawl.contact_email)
        self.chk_revalidate.setChecked(s.crawl.revalidate_cached_files)
        self.spin_cache_ttl.setValue(s.crawl.cache_ttl_hours)
        self.chk_robots.setChecked(s.crawl.respect_robots_txt)
        self.chk_browser_headers.setChecked(s.crawl.use_browser_headers)
        self.chk_use_proxy.setChecked(s.crawl.use_proxy)
        self.edit_proxy_list.setPlainText(s.crawl.proxy_list)
        idx = self.combo_override_mode.findData(s.override_mode())
        self.combo_override_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.spin_checkpoint.setValue(s.crawl.save_checkpoint_every)
        self.spin_min_checkpoint.setValue(s.crawl.min_checkpoint_seconds)
        idx = self.combo_auto_streaming.findData(s.dedup.auto_streaming)
        self.combo_auto_streaming.setCurrentIndex(idx if idx >= 0 else 1)
        self.spin_streaming_mb.setValue(s.dedup.auto_streaming_threshold_mb)

        # HTML
        self.combo_html_mode.setCurrentText(s.html.extract_mode)
        self.chk_download_images.setChecked(s.html.download_images)
        self.edit_image_ext.setText(s.html.image_extensions)
        self.edit_files_ext.setText(s.html.download_files_ext)

        # PDF
        self.chk_ocr.setChecked(s.pdf.ocr_enabled)
        self.edit_ocr_lang.setText(s.pdf.ocr_lang)
        self.spin_ocr_min_chars.setValue(s.pdf.ocr_min_chars_per_page)
        self.spin_ocr_workers.setValue(s.pdf.ocr_parallel_workers)
        self.spin_img_min_width.setValue(s.pdf.image_min_width)
        self.spin_img_min_height.setValue(s.pdf.image_min_height)
        self.chk_extract_tables.setChecked(s.pdf.extract_tables)
        self.chk_two_column.setChecked(s.pdf.two_column_detection)
        self.chk_filter_schematics.setChecked(s.pdf.filter_schematic_images)
        self.chk_use_toc.setChecked(s.pdf.use_toc_as_structure)

        # GitHub
        self.edit_github_token.setText(s.github.token)
        self.edit_github_branch.setText(s.github.branch)
        self.chk_crawl_issues.setChecked(s.github.crawl_issues)
        self.spin_issues_max.setValue(s.github.crawl_issues_max)
        self.chk_crawl_wiki.setChecked(s.github.crawl_wiki)
        self.chk_crawl_docs.setChecked(s.github.crawl_docs_dir)
        self.edit_include_files.setText(s.github.include_files)

        # StackExchange
        self.edit_se_key.setText(s.stackexchange.api_key)
        self.combo_se_site.setCurrentText(s.stackexchange.site)
        self.spin_se_min_score.setValue(s.stackexchange.min_score)
        self.spin_se_max_questions.setValue(s.stackexchange.max_questions)

        # Quality
        self.spin_min_chars.setValue(s.quality.min_chars)
        self.spin_max_chars.setValue(s.quality.max_chars)
        self.spin_max_non_alpha.setValue(s.quality.max_non_alpha_ratio)
        self.spin_max_dup_lines.setValue(s.quality.max_dup_line_ratio)
        self.spin_max_code.setValue(s.quality.max_code_ratio)
        self.chk_spam.setChecked(s.quality.spam_check)
        self.combo_language.setCurrentText(s.quality.language)
        self.edit_langs_allowed.setText(s.quality.languages_allowed)
        self.chk_perplexity.setChecked(s.quality.perplexity_check)
        self.spin_max_perplexity.setValue(s.quality.max_perplexity)
        self.edit_perplexity_model.setText(s.quality.perplexity_model_path)

        # Dedup
        self.chk_exact.setChecked(s.dedup.exact)
        self.chk_minhash.setChecked(s.dedup.minhash)
        self.spin_minhash_perm.setValue(s.dedup.minhash_num_perm)
        self.spin_minhash_threshold.setValue(s.dedup.minhash_threshold)
        self.chk_dedup_images.setChecked(s.dedup.dedup_images)
        self.chk_streaming.setChecked(s.dedup.use_streaming)
        self.chk_incremental.setChecked(s.dedup.use_incremental)

        # Performance
        self.chk_async.setChecked(s.async_crawl.enabled)
        self.spin_max_concurrent.setValue(s.async_crawl.max_concurrent_total)
        self.spin_max_per_domain.setValue(s.async_crawl.max_concurrent_per_domain)
        self.spin_url_timeout.setValue(getattr(s.crawl, 'per_url_timeout_minutes', 10))
        self.chk_gzip.setChecked(s.export.gzip_output)
        self.chk_parallel_postproc.setChecked(s.export.parallel_postproc)
        self.spin_parallel_workers.setValue(s.export.parallel_workers)

        # GUI
        self.chk_show_progress.setChecked(s.gui.show_progress_bar)

    def _save_values(self) -> None:
        """Сохранить значения из виджетов в AppSettings."""
        s = self.settings

        # General
        s.gui.theme = self.combo_theme.currentText()
        s.gui.log_level = self.combo_log_level.currentText()
        s.gui.last_config_path = self.edit_last_config.text()
        s.gui.last_output_dir = self.edit_last_output.text()
        s.gui.window_width = self.spin_window_width.value()
        s.gui.window_height = self.spin_window_height.value()

        # Crawl
        s.crawl.user_agent = self.edit_user_agent.text()
        s.crawl.request_timeout = self.spin_timeout.value()
        s.crawl.request_delay = self.spin_delay.value()
        s.crawl.max_file_size_mb = self.spin_max_file_size.value()
        s.crawl.use_cache = self.chk_use_cache.isChecked()
        s.crawl.revalidate_cached_files = self.chk_revalidate.isChecked()
        s.crawl.cache_ttl_hours = self.spin_cache_ttl.value()
        s.crawl.respect_robots_txt = self.chk_robots.isChecked()
        s.crawl.use_browser_headers = self.chk_browser_headers.isChecked()
        s.crawl.use_proxy = self.chk_use_proxy.isChecked()
        s.crawl.proxy_list = self.edit_proxy_list.toPlainText()
        s.crawl.contact_email = self.edit_contact_email.text().strip()
        s.crawl.contact_email = self.edit_contact_email.text().strip()
        s.set_override_mode(self.combo_override_mode.currentData())
        s.crawl.save_checkpoint_every = self.spin_checkpoint.value()
        s.crawl.min_checkpoint_seconds = self.spin_min_checkpoint.value()
        s.dedup.auto_streaming = self.combo_auto_streaming.currentData()
        s.dedup.auto_streaming_threshold_mb = self.spin_streaming_mb.value()

        # HTML
        s.html.extract_mode = self.combo_html_mode.currentText()
        s.html.download_images = self.chk_download_images.isChecked()
        s.html.image_extensions = self.edit_image_ext.text()
        s.html.download_files_ext = self.edit_files_ext.text()

        # PDF
        s.pdf.ocr_enabled = self.chk_ocr.isChecked()
        s.pdf.ocr_lang = self.edit_ocr_lang.text()
        s.pdf.ocr_min_chars_per_page = self.spin_ocr_min_chars.value()
        s.pdf.ocr_parallel_workers = self.spin_ocr_workers.value()
        s.pdf.image_min_width = self.spin_img_min_width.value()
        s.pdf.image_min_height = self.spin_img_min_height.value()
        s.pdf.extract_tables = self.chk_extract_tables.isChecked()
        s.pdf.two_column_detection = self.chk_two_column.isChecked()
        s.pdf.filter_schematic_images = self.chk_filter_schematics.isChecked()
        s.pdf.use_toc_as_structure = self.chk_use_toc.isChecked()

        # GitHub
        s.github.token = self.edit_github_token.text()
        s.github.branch = self.edit_github_branch.text()
        s.github.crawl_issues = self.chk_crawl_issues.isChecked()
        s.github.crawl_issues_max = self.spin_issues_max.value()
        s.github.crawl_wiki = self.chk_crawl_wiki.isChecked()
        s.github.crawl_docs_dir = self.chk_crawl_docs.isChecked()
        s.github.include_files = self.edit_include_files.text()

        # StackExchange
        s.stackexchange.api_key = self.edit_se_key.text()
        s.stackexchange.site = self.combo_se_site.currentText()
        s.stackexchange.min_score = self.spin_se_min_score.value()
        s.stackexchange.max_questions = self.spin_se_max_questions.value()

        # Quality
        s.quality.min_chars = self.spin_min_chars.value()
        s.quality.max_chars = self.spin_max_chars.value()
        s.quality.max_non_alpha_ratio = self.spin_max_non_alpha.value()
        s.quality.max_dup_line_ratio = self.spin_max_dup_lines.value()
        s.quality.max_code_ratio = self.spin_max_code.value()
        s.quality.spam_check = self.chk_spam.isChecked()
        s.quality.language = self.combo_language.currentText()
        s.quality.languages_allowed = self.edit_langs_allowed.text()
        s.quality.perplexity_check = self.chk_perplexity.isChecked()
        s.quality.max_perplexity = self.spin_max_perplexity.value()
        s.quality.perplexity_model_path = self.edit_perplexity_model.text()

        # Dedup
        s.dedup.exact = self.chk_exact.isChecked()
        s.dedup.minhash = self.chk_minhash.isChecked()
        s.dedup.minhash_num_perm = self.spin_minhash_perm.value()
        s.dedup.minhash_threshold = self.spin_minhash_threshold.value()
        s.dedup.dedup_images = self.chk_dedup_images.isChecked()
        s.dedup.use_streaming = self.chk_streaming.isChecked()
        s.dedup.use_incremental = self.chk_incremental.isChecked()

        # Performance
        s.async_crawl.enabled = self.chk_async.isChecked()
        s.async_crawl.max_concurrent_total = self.spin_max_concurrent.value()
        s.async_crawl.max_concurrent_per_domain = self.spin_max_per_domain.value()
        s.crawl.per_url_timeout_minutes = self.spin_url_timeout.value()
        s.export.gzip_output = self.chk_gzip.isChecked()
        s.export.parallel_postproc = self.chk_parallel_postproc.isChecked()
        s.export.parallel_workers = self.spin_parallel_workers.value()

        # GUI
        s.gui.show_progress_bar = self.chk_show_progress.isChecked()

    # ============================================================
    # Обработчики кнопок
    # ============================================================

    def _on_save(self) -> None:
        """Сохранить настройки и закрыть окно."""
        self._save_values()
        # Б: помечаем поля, которые пользователь правил В ЭТОМ диалоге. Только
        # они в режиме «touched» получают право перекрывать config.yaml.
        self.settings.mark_touched(self.settings.diff_from_snapshot(self._open_snapshot))
        self._open_snapshot = self.settings.snapshot()
        self.settings.save()
        self.settings.setup_env_vars()
        self.settings_changed.emit()
        QMessageBox.information(self, "Сохранено",
            "Настройки сохранены и будут применены к следующим запускам.")
        self.accept()

    def _on_reset(self) -> None:
        """Сбросить все настройки к значениям по умолчанию."""
        reply = QMessageBox.question(
            self, "Сброс настроек",
            "Сбросить все настройки к значениям по умолчанию?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            from .app_settings import AppSettings
            self.settings = AppSettings()
            self._load_values()
            QMessageBox.information(self, "Сброшено",
                "Настройки сброшены. Нажмите «Сохранить» для применения.")

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
                json.dump(self.settings.to_dict(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Экспортировано", f"Настройки сохранены в:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

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
            self.settings = AppSettings._from_dict(data)
            self._load_values()
            QMessageBox.information(self, "Импортировано",
                "Настройки загружены. Нажмите «Сохранить» для применения.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

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
