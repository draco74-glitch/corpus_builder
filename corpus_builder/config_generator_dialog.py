"""Диалог мастера генерации config.yaml.

Открывается из главного окна по кнопке «Создать config.yaml».
Содержит 3 вкладки:
  - Excel/CSV  — основной сценарий: загрузить файл, выбрать глубину, сгенерировать
  - GitHub     — поиск репозиториев по topics
  - StackExchange — топ вопросов по тегам

На каждой вкладке: прогресс-бар + таблица найденных URL + кнопка «Сгенерировать».
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QTextCursor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QProgressBar, QTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QCheckBox, QSpinBox, QComboBox,
    QMessageBox, QGroupBox, QSplitter, QHeaderView, QStatusBar, QStyle,
    QToolButton, QSizePolicy, QWidget, QListWidget, QListWidgetItem, QScrollArea,
    QButtonGroup, QRadioButton, QSlider, QFrame
)

from .app_settings import AppSettings
from .settings_dialog import SettingsDialog
from .config_generator import (
    build_config,
    crawl_excel_with_depth,
    from_excel,
    from_github_topics,
    from_stackexchange_tags,
    save_template_xlsx,
)
from .logging_setup import get_logger
from .gui_improvements import tr

log = get_logger(__name__)


# ---------- Цветовая палитра (должна совпадать с главным окном) ----------

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


# ============================================================
# Worker для запуска генерации в отдельном потоке
# ============================================================

class ExcelGenWorker(QThread):
    """Запускает crawl_excel_async (асинхронная версия, 10-30x быстрее).

    Поддерживает:
      - Асинхронный BFS через aiohttp (Улучшение 1)
      - Параллельную обработку нескольких seeds (Улучшение 2)
      - Skip crawl опцию — только URL из Excel, без сети (Улучшение 7)
    """
    progress = Signal(int, int, str)        # current, total, message
    url_found = Signal(dict)                # dict-source
    finished_result = Signal(list)         # list[dict] источников
    error = Signal(str)
    log_msg = Signal(str, str)              # level, message

    def __init__(self, excel_path: str, max_total_urls: int = 5000,
                 max_concurrent_seeds: int = 5,
                 skip_crawl: bool = False,
                 same_domain_only: bool = True,
                 include_subdomains: bool = False,
                 request_delay: float = 1.0):
        super().__init__()
        self.excel_path = excel_path
        self.max_total_urls = max_total_urls
        self.max_concurrent_seeds = max_concurrent_seeds
        self.skip_crawl = skip_crawl
        self.same_domain_only = same_domain_only
        self.include_subdomains = include_subdomains
        self.request_delay = request_delay
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def should_stop(self) -> bool:
        return self._stop_requested

    def run(self) -> None:
        try:
            if self.skip_crawl:
                # Skip crawl — мгновенно, без сети
                from .config_generator import from_excel, make_source
                rows = from_excel(self.excel_path)
                sources = []
                seen = set()
                for url, depth, cats in rows:
                    if url not in seen:
                        sources.append(make_source(url, categories=cats or None))
                        seen.add(url)
                if self._on_progress:
                    self._on_progress(len(sources), len(sources), f"skip_crawl: {len(sources)} URLs")
                for s in sources:
                    self.url_found.emit(s)
                self.finished_result.emit(sources)
                return

            # Пытаемся использовать асинхронную версию
            try:
                from .async_config_generator import crawl_excel_async_sync
                sources = crawl_excel_async_sync(
                    self.excel_path,
                    max_concurrent_seeds=self.max_concurrent_seeds,
                    max_total_urls=self.max_total_urls,
                    same_domain_only=self.same_domain_only,
                    include_subdomains=self.include_subdomains,
                    request_delay=self.request_delay,
                    on_progress=self._on_progress,
                    should_stop=self.should_stop,
                    skip_crawl=self.skip_crawl,
                )
            except Exception as async_err:
                # Fallback на синхронную версию если async не работает
                # (например, в PyInstaller frozen режиме asyncio может не работать)
                from .config_generator import crawl_excel_with_depth
                sources = crawl_excel_with_depth(
                    self.excel_path,
                    max_total_urls=self.max_total_urls,
                    on_progress=self._on_progress,
                    should_stop=self.should_stop,
                )
            for s in sources:
                self.url_found.emit(s)
            self.finished_result.emit(sources)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        self.progress.emit(current, total, msg)


class GitHubGenWorker(QThread):
    """Запускает from_github_topics."""
    progress = Signal(int, int, str)
    url_found = Signal(dict)
    finished_result = Signal(list)
    error = Signal(str)

    def __init__(self, topics: list[str], language: str | None, max_repos: int):
        super().__init__()
        self.topics = topics
        self.language = language
        self.max_repos = max_repos

    def run(self) -> None:
        try:
            self.progress.emit(0, len(self.topics), "Starting GitHub search...")
            sources = from_github_topics(
                self.topics,
                language=self.language or None,
                max_repos=self.max_repos,
            )
            for s in sources:
                self.url_found.emit(s)
            self.finished_result.emit(sources)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class StackExchangeGenWorker(QThread):
    """Запускает from_stackexchange_tags."""
    progress = Signal(int, int, str)
    url_found = Signal(dict)
    finished_result = Signal(list)
    error = Signal(str)

    def __init__(self, site: str, tags: list[str], max_questions: int, min_score: int):
        super().__init__()
        self.site = site
        self.tags = tags
        self.max_questions = max_questions
        self.min_score = min_score

    def run(self) -> None:
        try:
            self.progress.emit(0, len(self.tags), "Starting StackExchange search...")
            sources = from_stackexchange_tags(
                site=self.site,
                tags=self.tags,
                max_questions=self.max_questions,
                min_score=self.min_score,
            )
            for s in sources:
                self.url_found.emit(s)
            self.finished_result.emit(sources)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


# ============================================================
# Диалог мастера
# ============================================================

class ConfigGeneratorDialog(QDialog):
    """Диалоговое окно для генерации config.yaml из разных источников."""

    def __init__(self, parent=None, default_output_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Мастер создания config.yaml")
        self.resize(1100, 720)
        self.default_output_dir = default_output_dir
        self.sources: list[dict] = []
        self.worker: QThread | None = None

        self.app_settings = AppSettings.load()
        self._build_ui()
        self._connect_signals()
        self._apply_styles()
        self._build_menu()

    # ----------------- Menu -----------------

    def _build_menu(self) -> None:
        """Создать меню в мастере config.yaml (заглушка для совместимости)."""
        pass  # Меню создаётся через кнопки внизу диалога

    def _open_settings(self) -> None:
        """Открыть диалог настроек."""
        try:
            from .settings_dialog import SettingsDialog
            dialog = SettingsDialog(self.app_settings, self)
            if dialog.exec() == QDialog.Accepted:
                self.app_settings = AppSettings.load()
                self._log("INFO", "Настройки применены")
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "Ошибка настроек",
                f"Не удалось открыть настройки:\n\n{e}\n\n{traceback.format_exc()[:500]}")

    # ----------------- UI -----------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Заголовок
        title = QLabel("Создание config.yaml для corpus-builder")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {ACCENT};")
        outer.addWidget(title)

        subtitle = QLabel(
            "Загрузите Excel/CSV с URL и глубиной обхода, или используйте GitHub/StackExchange для поиска источников. "
            "Сгенерированный config.yaml будет сразу готов к запуску краулинга."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        outer.addWidget(subtitle)

        # Вкладки
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_excel_tab(), "📊  Excel / CSV")
        self.tabs.addTab(self._build_github_tab(), "🐙  GitHub topics")
        self.tabs.addTab(self._build_stackexchange_tab(), "💬  StackExchange")
        self.tabs.addTab(self._build_wikipedia_tab(), "📚  Wikipedia")
        outer.addWidget(self.tabs, stretch=1)

        # Общий прогресс-бар и лог
        prog_group = QGroupBox("Прогресс")
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        prog_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("Готов к генерации")
        self.progress_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        prog_layout.addWidget(self.progress_label)

        # Лог
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        prog_layout.addWidget(self.log_view)

        outer.addWidget(prog_group)

        # Кнопки снизу
        buttons_row = QHBoxLayout()

        self.btn_generate = QPushButton("⚙  Сгенерировать config.yaml")
        self.btn_generate.setStyleSheet(
            f"background-color: {ACCENT}; color: white; font-weight: bold; padding: 8px 18px; min-height: 26px;"
        )
        self.btn_generate.clicked.connect(self._on_generate)
        buttons_row.addWidget(self.btn_generate)

        self.btn_stop = QPushButton("⏹  Остановить")
        self.btn_stop.setProperty("danger", True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        buttons_row.addWidget(self.btn_stop)

        buttons_row.addStretch()

        self.btn_clear = QPushButton("🗑  Очистить список")
        self.btn_clear.setProperty("secondary", True)
        self.btn_clear.clicked.connect(self._on_clear)
        buttons_row.addWidget(self.btn_clear)

        self.btn_settings = QPushButton("⚙  Настройки...")
        self.btn_settings.setProperty("secondary", True)
        self.btn_settings.clicked.connect(self._open_settings)
        buttons_row.addWidget(self.btn_settings)

        outer.addLayout(buttons_row)

        # Статус снизу
        self.status_label = QLabel("Источников: 0")
        self.status_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        outer.addWidget(self.status_label)

    def _build_excel_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # Секция выбора файла
        file_group = QGroupBox("1. Загрузка файла")
        file_layout = QGridLayout(file_group)
        file_layout.setHorizontalSpacing(8)
        file_layout.setVerticalSpacing(6)

        file_layout.addWidget(QLabel("Файл:"), 0, 0)
        self.excel_path_edit = QLineEdit()
        self.excel_path_edit.setPlaceholderText("Выберите .xlsx, .xls или .csv файл...")
        file_layout.addWidget(self.excel_path_edit, 0, 1)
        btn_browse_excel = QPushButton("Обзор...")
        btn_browse_excel.setProperty("secondary", True)
        btn_browse_excel.clicked.connect(self._browse_excel)
        file_layout.addWidget(btn_browse_excel, 0, 2)

        btn_template = QPushButton("📥  Скачать шаблон .xlsx")
        btn_template.setProperty("secondary", True)
        btn_template.clicked.connect(self._download_template)
        file_layout.addWidget(btn_template, 1, 2)

        # Предпросмотр содержимого Excel
        file_layout.addWidget(QLabel("Содержимое файла:"), 2, 0, 1, 3)
        self.excel_table = QTableWidget(0, 3)
        self.excel_table.setHorizontalHeaderLabels(["URL", "Глубина", "Категории"])
        header = self.excel_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.excel_table.verticalHeader().setVisible(False)
        self.excel_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.excel_table.setMaximumHeight(180)
        file_layout.addWidget(self.excel_table, 3, 0, 1, 3)

        layout.addWidget(file_group)

        # Опции генерации
        opts_group = QGroupBox("2. Опции обхода")
        opts_layout = QGridLayout(opts_group)

        opts_layout.addWidget(QLabel("Макс. URL всего:"), 0, 0)
        self.max_total_urls_spin = QSpinBox()
        self.max_total_urls_spin.setRange(10, 50000)
        self.max_total_urls_spin.setValue(1000)
        self.max_total_urls_spin.setSuffix(" URL")
        opts_layout.addWidget(self.max_total_urls_spin, 0, 1)

        opts_layout.addWidget(QLabel("Задержка между запросами (сек):"), 0, 2)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 30)
        self.delay_spin.setValue(1)
        opts_layout.addWidget(self.delay_spin, 0, 3)

        opts_layout.addWidget(QLabel("Параллельных seeds:"), 1, 0)
        self.concurrent_seeds_spin = QSpinBox()
        self.concurrent_seeds_spin.setRange(1, 20)
        self.concurrent_seeds_spin.setValue(5)
        self.concurrent_seeds_spin.setToolTip("Сколько URL из Excel обрабатывать параллельно (5 = оптимально, 10-30x ускорение)")
        opts_layout.addWidget(self.concurrent_seeds_spin, 1, 1)

        # Улучшение 7: Skip crawl — только URL из Excel, без сетевых запросов
        self.chk_skip_crawl = QCheckBox("⚡ Пропустить обход (только URL из файла)")
        self.chk_skip_crawl.setToolTip(
            "Если включено — config.yaml будет создан мгновенно из URL в Excel, без сетевых запросов.\n"
            "Используйте эту опцию, если в Excel уже есть все нужные URL (depth=0 для всех).\n"
            "Если нужно обходить ссылки (depth > 0) — снимите галку."
        )
        self.chk_skip_crawl.setStyleSheet(f"color: {SUCCESS}; font-weight: bold;")
        opts_layout.addWidget(self.chk_skip_crawl, 1, 2, 1, 2)

        self.chk_same_domain = QCheckBox("Только same-domain ссылки")
        self.chk_same_domain.setChecked(True)
        self.chk_same_domain.setToolTip("Разрешать только ссылки с тем же доменом (например, для habr.com не идти на vk.com)")
        opts_layout.addWidget(self.chk_same_domain, 2, 0, 1, 2)

        self.chk_subdomains = QCheckBox("Включая поддомены")
        self.chk_subdomains.setToolTip("Разрешить blog.example.com для example.com")
        opts_layout.addWidget(self.chk_subdomains, 2, 2, 1, 2)

        layout.addWidget(opts_group)

        layout.addStretch()
        return tab

    def _build_github_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        opts_group = QGroupBox("Параметры поиска GitHub")
        opts_layout = QGridLayout(opts_group)

        opts_layout.addWidget(QLabel("Topics:"), 0, 0)
        self.gh_topics_edit = QLineEdit()
        self.gh_topics_edit.setPlaceholderText("kicad, pcb, embedded (через запятую)")
        opts_layout.addWidget(self.gh_topics_edit, 0, 1)

        opts_layout.addWidget(QLabel("Язык:"), 1, 0)
        self.gh_language_edit = QLineEdit()
        self.gh_language_edit.setPlaceholderText("Python, C, C++ (необязательно)")
        opts_layout.addWidget(self.gh_language_edit, 1, 1)

        opts_layout.addWidget(QLabel("Макс. репо на topic:"), 2, 0)
        self.gh_max_repos_spin = QSpinBox()
        self.gh_max_repos_spin.setRange(1, 1000)
        self.gh_max_repos_spin.setValue(100)
        opts_layout.addWidget(self.gh_max_repos_spin, 2, 1)

        hint = QLabel(
            "⚠ Без GITHUB_TOKEN лимит — 10 запросов/мин.\n"
            "Установите переменную окружения GITHUB_TOKEN для повышения лимита до 30/мин."
        )
        hint.setStyleSheet(f"color: {WARN}; font-size: 11px;")
        hint.setWordWrap(True)
        opts_layout.addWidget(hint, 3, 0, 1, 2)

        layout.addWidget(opts_group)

        layout.addStretch()
        return tab

    def _build_stackexchange_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        opts_group = QGroupBox("Параметры поиска StackExchange")
        opts_layout = QGridLayout(opts_group)

        opts_layout.addWidget(QLabel("Сайт:"), 0, 0)
        self.se_site_combo = QComboBox()
        self.se_site_combo.addItems([
            "electronics", "stackoverflow", "serverfault", "superuser",
            "mathoverflow", "askubuntu",
        ])
        opts_layout.addWidget(self.se_site_combo, 0, 1)

        opts_layout.addWidget(QLabel("Теги:"), 1, 0)
        self.se_tags_edit = QLineEdit()
        self.se_tags_edit.setPlaceholderText("kicad, pcb, stm32 (через запятую)")
        opts_layout.addWidget(self.se_tags_edit, 1, 1)

        opts_layout.addWidget(QLabel("Макс. вопросов на тег:"), 2, 0)
        self.se_max_questions_spin = QSpinBox()
        self.se_max_questions_spin.setRange(1, 1000)
        self.se_max_questions_spin.setValue(100)
        opts_layout.addWidget(self.se_max_questions_spin, 2, 1)

        opts_layout.addWidget(QLabel("Мин. score:"), 3, 0)
        self.se_min_score_spin = QSpinBox()
        self.se_min_score_spin.setRange(0, 10000)
        self.se_min_score_spin.setValue(5)
        opts_layout.addWidget(self.se_min_score_spin, 3, 1)

        hint = QLabel(
            "⚠ Без STACKEXCHANGE_KEY лимит — 300 запросов/день.\n"
            "Получите бесплатный ключ на stackapps.com → apps/oauth/register"
        )
        hint.setStyleSheet(f"color: {WARN}; font-size: 11px;")
        hint.setWordWrap(True)
        opts_layout.addWidget(hint, 4, 0, 1, 2)

        layout.addWidget(opts_group)
        layout.addStretch()
        return tab

    # ----------------- Стилизация -----------------

    def _build_wikipedia_tab(self) -> QWidget:
        """Wikipedia search tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # Language
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(tr("wiki_lang_label")))
        self.wiki_lang_combo = QComboBox()
        self.wiki_lang_combo.addItems(["en", "ru", "de", "fr", "es", "it", "ja", "zh"])
        lang_row.addWidget(self.wiki_lang_combo)
        lang_row.addStretch()
        layout.addLayout(lang_row)

        # Categories
        layout.addWidget(QLabel(tr("wiki_categories_label")))
        self.wiki_categories_edit = QLineEdit()
        self.wiki_categories_edit.setPlaceholderText("Electronics, Printed circuit boards, Operational amplifiers")
        layout.addWidget(self.wiki_categories_edit)

        # Max articles
        max_row = QHBoxLayout()
        max_row.addWidget(QLabel(tr("wiki_max_label")))
        self.wiki_max_spin = QSpinBox()
        self.wiki_max_spin.setRange(1, 500)
        self.wiki_max_spin.setValue(50)
        max_row.addWidget(self.wiki_max_spin)
        max_row.addStretch()
        layout.addLayout(max_row)

        # Depth
        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel(tr("wiki_depth_label")))
        self.wiki_depth_spin = QSpinBox()
        self.wiki_depth_spin.setRange(0, 3)
        self.wiki_depth_spin.setValue(1)
        depth_row.addWidget(self.wiki_depth_spin)
        depth_row.addStretch()
        layout.addLayout(depth_row)

        # Hint
        hint = QLabel(tr("wiki_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()
        return tab

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
        QPushButton:disabled {{ background-color: #555; color: #aaa; }}
        QPushButton[secondary="true"] {{ background-color: #3a3a3a; color: {TEXT_PRIMARY}; }}
        QPushButton[danger="true"] {{ background-color: {ERROR}; }}
        QLineEdit, QComboBox, QSpinBox {{ background-color: {DARKER_BG};
            border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px;
            color: {TEXT_PRIMARY}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
        QProgressBar {{ background-color: {DARKER_BG}; border: 1px solid {BORDER};
            border-radius: 4px; text-align: center; color: {TEXT_PRIMARY}; min-height: 22px; }}
        QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}
        QTabWidget::pane {{ border: 1px solid {BORDER}; background: {DARK_BG}; }}
        QTabBar::tab {{ background: {DARKER_BG}; color: {TEXT_SECONDARY};
            padding: 6px 14px; border: 1px solid {BORDER}; border-bottom: none; }}
        QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}
        QTableWidget {{ background-color: {DARKER_BG}; gridline-color: {BORDER};
            color: {TEXT_PRIMARY}; }}
        QHeaderView::section {{ background-color: {LIGHTER_BG}; color: {TEXT_PRIMARY};
            padding: 4px; border: none; }}
        QTextEdit {{ background-color: {DARKER_BG}; color: {TEXT_PRIMARY};
            border: 1px solid {BORDER}; border-radius: 4px;
            font-family: 'Cascadia Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono'; font-size: 12px; }}
        QLabel {{ color: {TEXT_PRIMARY}; }}
        QCheckBox {{ color: {TEXT_PRIMARY}; }}
        """)

    # ----------------- Сигналы -----------------

    def _connect_signals(self) -> None:
        self.excel_path_edit.textChanged.connect(self._on_excel_path_changed)

    # ----------------- Хендлеры -----------------

    def _browse_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл со списком URL",
            "",
            "Табличные файлы (*.xlsx *.xls *.csv);;Excel (*.xlsx *.xls);;CSV (*.csv);;Все файлы (*)"
        )
        if path:
            self.excel_path_edit.setText(path)

    def _on_excel_path_changed(self) -> None:
        path = self.excel_path_edit.text().strip()
        if not path or not Path(path).exists():
            self.excel_table.setRowCount(0)
            return
        try:
            rows = from_excel(path)
        except Exception as e:
            self._log("ERROR", f"Не удалось прочитать файл: {e}")
            QMessageBox.warning(self, "Ошибка чтения", str(e))
            return
        self.excel_table.setRowCount(0)
        for i, (url, depth, cats) in enumerate(rows):
            self.excel_table.insertRow(i)
            self.excel_table.setItem(i, 0, QTableWidgetItem(url))
            self.excel_table.setItem(i, 1, QTableWidgetItem(str(depth)))
            self.excel_table.setItem(i, 2, QTableWidgetItem(", ".join(cats)))
        self._log("INFO", f"Загружено строк: {len(rows)}")

    def _download_template(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить шаблон",
            "corpus_builder_sources.xlsx",
            "Excel (*.xlsx)"
        )
        if not target:
            return
        try:
            save_template_xlsx(target)
            self._log("INFO", f"Шаблон сохранён: {target}")
            QMessageBox.information(self, "Шаблон создан",
                f"Шаблон сохранён:\n{target}\n\n"
                "Откройте его в Excel, заполните колонки url и depth, сохраните и загрузите обратно.")
            # Открыть папку в проводнике
            self._open_in_explorer(Path(target).parent)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _open_in_explorer(self, path: Path) -> None:
        if not path.exists():
            return
        url = QUrl.fromLocalFile(str(path))
        QDesktopServices.openUrl(url)

    def _on_generate(self) -> None:
        try:
            if self.worker and self.worker.isRunning():
                QMessageBox.warning(self, "Занято", "Дождитесь завершения текущей задачи.")
                return

            # Определяем активную вкладку
            idx = self.tabs.currentIndex()
            if idx == 0:
                self._start_excel_generation()
            elif idx == 1:
                self._start_github_generation()
            elif idx == 2:
                self._start_stackexchange_generation()
        except Exception as e:
            import traceback
            self._log("ERROR", f"Ошибка генерации: {e}")
            QMessageBox.critical(self, "Ошибка генерации",
                f"Не удалось сгенерировать config.yaml:\n\n{e}\n\n"
                f"Подробности:\n{traceback.format_exc()[:500]}")

    def _start_excel_generation(self) -> None:
        path = self.excel_path_edit.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Файл не выбран",
                "Укажите путь к Excel/CSV-файлу на вкладке «Excel / CSV».")
            return
        self._set_running_state(True)
        skip_crawl = self.chk_skip_crawl.isChecked()
        if skip_crawl:
            self._log("INFO", f"⚡ Skip crawl: генерация из {path} без сетевых запросов...")
        else:
            self._log("INFO", f"Запуск асинхронного обхода из {path} (10-30x быстрее)...")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Запуск...")
        self.worker = ExcelGenWorker(
            excel_path=path,
            max_total_urls=self.max_total_urls_spin.value(),
            max_concurrent_seeds=self.concurrent_seeds_spin.value(),
            skip_crawl=skip_crawl,
            same_domain_only=self.chk_same_domain.isChecked(),
            include_subdomains=self.chk_subdomains.isChecked(),
            request_delay=float(self.delay_spin.value()),
        )
        self._connect_worker(self.worker)
        self.worker.start()

    def _start_github_generation(self) -> None:
        topics_str = self.gh_topics_edit.text().strip()
        if not topics_str:
            QMessageBox.warning(self, "Нет данных",
                "Укажите хотя бы один topic (например, kicad, pcb)")
            return
        topics = [t.strip() for t in topics_str.replace(";", ",").split(",") if t.strip()]
        language = self.gh_language_edit.text().strip() or None
        max_repos = self.gh_max_repos_spin.value()

        self._set_running_state(True)
        self._log("INFO", f"GitHub search: topics={topics}, language={language}, max={max_repos}")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Поиск репозиториев...")
        self.worker = GitHubGenWorker(
            topics=topics,
            language=language,
            max_repos=max_repos,
        )
        self._connect_worker(self.worker)
        self.worker.start()

    def _start_stackexchange_generation(self) -> None:
        tags_str = self.se_tags_edit.text().strip()
        if not tags_str:
            QMessageBox.warning(self, "Нет данных",
                "Укажите хотя бы один тег (например, kicad, pcb, stm32)")
            return
        tags = [t.strip() for t in tags_str.replace(";", ",").split(",") if t.strip()]
        site = self.se_site_combo.currentText()
        max_q = self.se_max_questions_spin.value()
        min_score = self.se_min_score_spin.value()

        self._set_running_state(True)
        self._log("INFO", f"SE search: site={site}, tags={tags}, max={max_q}, min_score={min_score}")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Поиск вопросов...")
        self.worker = StackExchangeGenWorker(
            site=site,
            tags=tags,
            max_questions=max_q,
            min_score=min_score,
        )
        self._connect_worker(self.worker)
        self.worker.start()

    def _start_wikipedia_generation(self) -> None:
        """Поиск статей Wikipedia по категориям."""
        categories_str = self.wiki_categories_edit.text().strip()
        if not categories_str:
            QMessageBox.warning(self, "Нет данных",
                "Укажите хотя бы одну категорию (например: Electronics)")
            return

        categories = [c.strip() for c in categories_str.replace(";", ",").split(",") if c.strip()]
        lang = self.wiki_lang_combo.currentText()
        max_articles = self.wiki_max_spin.value()
        depth = self.wiki_depth_spin.value()

        self._set_running_state(True)
        self._log("INFO", f"Wikipedia: поиск по категориям {categories} (lang={lang})")
        self.progress_bar.setValue(0)
        self.progress_label.setText("Поиск статей Wikipedia...")

        # Используем отдельный поток для поиска
        from .crawlers.base import BaseCrawler
        class WikiWorker(QThread):
            progress = Signal(int, int, str)
            url_found = Signal(dict)
            finished_result = Signal(list)
            error = Signal(str)

            def __init__(self, categories, lang, max_articles, depth):
                super().__init__()
                self.categories = categories
                self.lang = lang
                self.max_articles = max_articles
                self.depth = depth

            def run(self):
                try:
                    from .config_generator import from_wikipedia
                    sources = from_wikipedia(
                        categories=self.categories,
                        lang=self.lang,
                        max_articles=self.max_articles,
                        depth=self.depth,
                    )
                    for s in sources:
                        self.url_found.emit(s)
                    self.finished_result.emit(sources)
                except Exception as e:
                    self.error.emit(f"{type(e).__name__}: {e}")

        self.worker = WikiWorker(categories, lang, max_articles, depth)
        self._connect_worker(self.worker)
        self.worker.start()

    def _on_stop(self) -> None:
        if self.worker and self.worker.isRunning():
            if hasattr(self.worker, "request_stop"):
                self.worker.request_stop()
            self._log("WARN", "Останавливаю после текущего URL...")
            self.btn_stop.setEnabled(False)

    def _on_clear(self) -> None:
        self.sources.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("Готов к генерации")
        self.log_view.clear()
        self.status_label.setText("Источников: 0")

    def _connect_worker(self, worker: QThread) -> None:
        if hasattr(worker, "progress"):
            worker.progress.connect(self._on_worker_progress)
        if hasattr(worker, "url_found"):
            worker.url_found.connect(self._on_worker_url_found)
        if hasattr(worker, "finished_result"):
            worker.finished_result.connect(self._on_worker_finished)
        if hasattr(worker, "error"):
            worker.error.connect(self._on_worker_error)
        if hasattr(worker, "log_msg"):
            worker.log_msg.connect(self._on_worker_log)

    def _on_worker_progress(self, current: int, total: int, msg: str) -> None:
        if total > 0:
            pct = int(current * 100 / total)
            self.progress_bar.setValue(pct)
        self.progress_label.setText(msg)
        self._log("INFO", msg)

    def _on_worker_url_found(self, source: dict) -> None:
        self.sources.append(source)
        self.status_label.setText(f"Источников: {len(self.sources)}")

    def _on_worker_finished(self, sources: list) -> None:
        self._set_running_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText(f"Готово. Найдено источников: {len(sources)}")
        self._log("INFO", f"Сбор завершён: {len(sources)} источников")

        if not sources:
            QMessageBox.warning(self, "Пусто",
                "Не найдено ни одного источника. Проверьте параметры или файл.")
            return

        # Сохранить config.yaml
        target, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить config.yaml",
            "config.generated.yaml",
            "YAML (*.yaml *.yml)"
        )
        if not target:
            return

        try:
            build_config(sources, target)
            self._log("INFO", f"Config сохранён: {target}")
            QMessageBox.information(self, "Готово",
                f"Сохранено источников: {len(sources)}\n"
                f"Файл: {target}\n\n"
                f"Теперь можно загрузить этот config.yaml в главное окно и запустить краулинг."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))

    def _on_worker_error(self, err: str) -> None:
        self._set_running_state(False)
        self._log("ERROR", f"Ошибка: {err}")
        QMessageBox.critical(self, "Критическая ошибка", err)

    def _on_worker_log(self, level: str, msg: str) -> None:
        self._log(level, msg)

    # ----------------- Хелперы UI -----------------

    def _set_running_state(self, running: bool) -> None:
        self.btn_generate.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_clear.setEnabled(not running)

    def _log(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": TEXT_PRIMARY,
            "WARNING": WARN,
            "ERROR": ERROR,
            "DEBUG": TEXT_SECONDARY,
        }.get(level, TEXT_PRIMARY)
        safe = msg.replace("\n", " ").replace("\r", "")
        self.log_view.append(
            f'<span style="color:{TEXT_SECONDARY}">{ts}</span> '
            f'<span style="color:{color};font-weight:bold">[{level}]</span> '
            f'<span style="color:{TEXT_PRIMARY}">{safe}</span>'
        )
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)


# Точка входа для тестирования диалога отдельно
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = ConfigGeneratorDialog()
    dialog.exec()
