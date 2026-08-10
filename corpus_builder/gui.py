"""PySide6 GUI для corpus-builder.

Запуск:
    python -m corpus_builder.gui
или после сборки exe:
    CorpusBuilder.exe
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QTextCursor, QPalette, QPixmap, QKeySequence
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QProgressBar, QTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QCheckBox, QSpinBox, QComboBox,
    QMessageBox, QSystemTrayIcon, QMenu, QGroupBox, QSplitter, QHeaderView,
    QStatusBar, QStyle, QFrame, QToolButton, QSizePolicy, QDialog
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .app_settings import AppSettings
from .settings_dialog import SettingsDialog
from .auto_updater import AutoUpdater, CommitUpdater
from .auto_discover import AutoDiscover
from .merge_config_dialog import MergeConfigDialog
from .gui_improvements import (
    ConfigDropArea, RecordsTableContextMenu, LogSearchBar,
    SplitterStateSaver, ToastNotification, apply_theme, get_theme_qss, THEMES,
    KicadPreviewDialog, RecentConfigsManager, ProgressBarWithETA,
    DiffCorpusDialog, YamlEditorDialog, DashboardDialog, FirstRunWizard,
    set_language, get_language, tr,
)

# В打包анной версии через PyInstaller __package__ может быть пустым
from .config import load_config, ensure_output_dirs
from .config_generator_dialog import ConfigGeneratorDialog
from .models import AppConfig
from .pipeline import run_crawl, run_postprocess
from .postproc.export import compute_statistics, export_huggingface, export_parquet
from .state import State


# ---------- Цветовая палитра (тёмная тема, как VS Code Dark+) ----------

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


# ---------- Worker для запуска краулинга в отдельном потоке ----------

class CrawlWorker(QThread):
    """Запускает run_crawl в отдельном потоке, сигналами обновляет GUI."""
    progress = Signal(int, int, str)      # current, total, message
    record_added = Signal(dict)            # dict-record
    log_message = Signal(str, str)         # level, message
    finished_stats = Signal(dict)
    error = Signal(str)

    def __init__(self, config: AppConfig, mode: str = "crawl",
                 resume: bool = True, retry_errors: bool = False,
                 limit: int | None = None, source_type: str | None = None):
        super().__init__()
        self.config = config
        self.mode = mode
        self.resume = resume
        self.retry_errors = retry_errors
        self.limit = limit
        self.source_type = source_type
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def should_stop(self) -> bool:
        return self._stop_requested

    def run(self) -> None:
        try:
            if self.mode == "crawl":
                stats = run_crawl(
                    self.config,
                    resume=self.resume,
                    retry_errors=self.retry_errors,
                    limit=self.limit,
                    source_type=self.source_type,
                    on_progress=self._on_progress,
                    on_record=self._on_record,
                    on_log=self.log_message.emit,
                    should_stop=self.should_stop,
                )
            elif self.mode == "postprocess":
                stats = run_postprocess(
                    self.config,
                    on_progress=self._on_progress,
                    on_log=self.log_message.emit,
                )
            else:
                stats = {"error": f"Unknown mode: {self.mode}"}
            self.finished_stats.emit(stats)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        self.progress.emit(current, total, msg)

    def _on_record(self, record: dict) -> None:
        self.record_added.emit(record)


# ---------- Главное окно ----------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config: AppConfig | None = None
        self.config_path: str | None = None
        self.output_dir: str = ""
        self.worker: CrawlWorker | None = None
        self.recent_records: deque[dict] = deque(maxlen=20)

        self.setWindowTitle("Corpus Builder — сбор корпуса для LLM")
        self.resize(1280, 820)
        self._apply_dark_theme()

        # Tray icon (опционально, не падает если система без tray)
        self.tray = None
        try:
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_DriveHDIcon), self)
            self.tray.setToolTip("Corpus Builder")
            menu = QMenu()
            act_show = menu.addAction("Показать окно")
            act_show.triggered.connect(self.showNormal)
            act_quit = menu.addAction("Выход")
            act_quit.triggered.connect(self._quit_app)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
        except Exception:
            pass

        # Загружаем настройки приложения
        self.app_settings = AppSettings.load()
        self.app_settings.setup_env_vars()

        self._build_ui()
        self._build_menu()
        self._connect_signals()
        self._restore_last_session()
        self._restore_window_geometry()

        # Recent configs manager (Улучшение H)
        self.recent_configs = RecentConfigsManager()

        # Splitter state saver (Улучшение D)
        self.splitter_saver = SplitterStateSaver(
            Path.home() / ".corpus_builder_splitter.json"
        )

        # Проверка первого запуска (Улучшение M)
        first_run_file = Path.home() / ".corpus_builder_first_run"
        if not first_run_file.exists():
            QTimer.singleShot(500, self._show_first_run_wizard)
            first_run_file.touch()

        # Язык интерфейса (Улучшение N)
        set_language(self.app_settings.gui.theme if hasattr(self.app_settings.gui, 'language') else 'ru')

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(2000)

        # Создаём state только один раз и периодически перезагружаем без логов
        self._state_for_status: State | None = None

    # ----------------- UI -----------------

    def _apply_dark_theme(self) -> None:
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(DARK_BG))
        palette.setColor(QPalette.WindowText, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.Base, QColor(DARKER_BG))
        palette.setColor(QPalette.AlternateBase, QColor(LIGHTER_BG))
        palette.setColor(QPalette.Text, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.Button, QColor(LIGHTER_BG))
        palette.setColor(QPalette.ButtonText, QColor(TEXT_PRIMARY))
        palette.setColor(QPalette.Highlight, QColor(ACCENT))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ToolTipBase, QColor(LIGHTER_BG))
        palette.setColor(QPalette.ToolTipText, QColor(TEXT_PRIMARY))
        QApplication.setPalette(palette)

        # Дополнительные QSS-стили
        qss = f"""
        QMainWindow, QWidget {{ background-color: {DARK_BG}; color: {TEXT_PRIMARY}; font-family: 'Segoe UI', 'SF Pro', 'DejaVu Sans'; font-size: 13px; }}
        QGroupBox {{ background-color: {DARKER_BG}; border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding-top: 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT}; font-weight: bold; }}
        QPushButton {{ background-color: {ACCENT}; color: white; border: none; padding: 6px 14px; border-radius: 4px; min-height: 22px; }}
        QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        QPushButton:disabled {{ background-color: #555; color: #aaa; }}
        QPushButton[secondary="true"] {{ background-color: #3a3a3a; color: {TEXT_PRIMARY}; }}
        QPushButton[danger="true"] {{ background-color: {ERROR}; }}
        QLineEdit, QComboBox, QSpinBox {{ background-color: {DARKER_BG}; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px; color: {TEXT_PRIMARY}; }}
        QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
        QProgressBar {{ background-color: {DARKER_BG}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; color: {TEXT_PRIMARY}; min-height: 22px; }}
        QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}
        QTabWidget::pane {{ border: 1px solid {BORDER}; background: {DARK_BG}; }}
        QTabBar::tab {{ background: {DARKER_BG}; color: {TEXT_SECONDARY}; padding: 6px 14px; border: 1px solid {BORDER}; border-bottom: none; }}
        QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}
        QTableWidget {{ background-color: {DARKER_BG}; gridline-color: {BORDER}; color: {TEXT_PRIMARY}; }}
        QHeaderView::section {{ background-color: {LIGHTER_BG}; color: {TEXT_PRIMARY}; padding: 4px; border: none; }}
        QTextEdit {{ background-color: {DARKER_BG}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER}; border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono'; font-size: 12px; }}
        QStatusBar {{ background-color: {DARKER_BG}; color: {TEXT_SECONDARY}; }}
        QSplitter::handle {{ background-color: {BORDER}; }}
        QLabel {{ color: {TEXT_PRIMARY}; }}
        QCheckBox {{ color: {TEXT_PRIMARY}; }}
        """
        self.setStyleSheet(qss)

    def _build_menu(self) -> None:
        """Создать меню: Файл, Настройки, Вид, Справка."""
        menubar = self.menuBar()

        # === Меню Файл ===
        file_menu = menubar.addMenu("Файл")

        act_open_config = QAction("Открыть config.yaml...", self)
        act_open_config.setShortcut(QKeySequence("Ctrl+O"))
        act_open_config.triggered.connect(self._menu_open_config)
        file_menu.addAction(act_open_config)

        act_open_output = QAction("Открыть папку корпуса", self)
        act_open_output.triggered.connect(self._open_output_folder)
        file_menu.addAction(act_open_output)

        # Недавние файлы (Улучшение H)
        self.recent_menu = file_menu.addMenu("Недавние config.yaml")
        file_menu.addSeparator()

        act_export_hf = QAction("Экспорт в HuggingFace...", self)
        act_export_hf.triggered.connect(self._on_export_hf)
        file_menu.addAction(act_export_hf)

        act_export_parquet = QAction("Экспорт в Parquet...", self)
        act_export_parquet.triggered.connect(self._on_export_parquet)
        file_menu.addAction(act_export_parquet)

        file_menu.addSeparator()

        act_quit = QAction("Выход", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self._quit_app)
        file_menu.addAction(act_quit)

        # === Меню Настройки ===
        settings_menu = menubar.addMenu("Настройки")

        act_settings = QAction("⚙  Все настройки...", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(act_settings)

        settings_menu.addSeparator()

        act_export_settings = QAction("📤  Экспорт настроек...", self)
        act_export_settings.triggered.connect(self._export_settings)
        settings_menu.addAction(act_export_settings)

        act_import_settings = QAction("📥  Импорт настроек...", self)
        act_import_settings.triggered.connect(self._import_settings)
        settings_menu.addAction(act_import_settings)

        settings_menu.addSeparator()

        act_reset_settings = QAction("↺  Сбросить к defaults", self)
        act_reset_settings.triggered.connect(self._reset_settings)
        settings_menu.addAction(act_reset_settings)

        # === Меню Вид ===
        view_menu = menubar.addMenu("Вид")

        # Тема (Улучшения F + O)
        theme_menu = view_menu.addMenu("Тема")
        self.theme_group = {}
        for theme_name in ["dark", "light", "material_blue", "material_green", "material_purple"]:
            label = {"dark": "Тёмная", "light": "Светлая",
                     "material_blue": "Material Blue", "material_green": "Material Green",
                     "material_purple": "Material Purple"}[theme_name]
            act = theme_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.app_settings.gui.theme == theme_name)
            act.triggered.connect(lambda checked, t=theme_name: self._change_theme(t))
            self.theme_group[theme_name] = act

        view_menu.addSeparator()

        act_toggle_log = QAction("Показать/скрыть лог", self)
        act_toggle_log.setShortcut(QKeySequence("Ctrl+L"))
        act_toggle_log.triggered.connect(self._toggle_log_visibility)
        view_menu.addAction(act_toggle_log)

        act_search_log = QAction("🔍  Поиск по логу", self)
        act_search_log.setShortcut(QKeySequence("Ctrl+F"))
        act_search_log.triggered.connect(self._toggle_log_search)
        view_menu.addAction(act_search_log)

        # === Меню Действия ===
        actions_menu = menubar.addMenu("Действия")

        act_crawl = QAction("▶  Запустить краулинг", self)
        act_crawl.setShortcut(QKeySequence("Ctrl+R"))
        act_crawl.triggered.connect(self._on_start_crawl)
        actions_menu.addAction(act_crawl)

        act_postprocess = QAction("⚙  Пост-обработка", self)
        act_postprocess.triggered.connect(self._on_postprocess)
        actions_menu.addAction(act_postprocess)

        act_stop = QAction("⏹  Остановить", self)
        act_stop.triggered.connect(self._on_stop)
        actions_menu.addAction(act_stop)

        actions_menu.addSeparator()

        act_generate_config = QAction("✨  Создать config.yaml...", self)
        act_generate_config.triggered.connect(self._on_open_config_generator)
        actions_menu.addAction(act_generate_config)

        act_auto_discover = QAction("🔄  Авто-поиск источников...", self)
        act_auto_discover.setShortcut(QKeySequence("Ctrl+Shift+A"))
        act_auto_discover.triggered.connect(self._on_auto_discover)
        actions_menu.addAction(act_auto_discover)

        act_merge_config = QAction("🔗  Объединить config.yaml...", self)
        act_merge_config.setShortcut(QKeySequence("Ctrl+Shift+M"))
        act_merge_config.triggered.connect(self._on_merge_config)
        actions_menu.addAction(act_merge_config)

        # === Меню Справка ===
        help_menu = menubar.addMenu("Справка")

        act_check_update = QAction("🔄  Проверить обновления", self)
        act_check_update.setShortcut(QKeySequence("Ctrl+U"))
        act_check_update.triggered.connect(self._check_for_updates_manual)
        help_menu.addAction(act_check_update)

        act_about = QAction("О программе", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        act_help = QAction("Документация", self)
        act_help.triggered.connect(self._open_documentation)
        help_menu.addAction(act_help)

        act_stats = QAction("Статистика корпуса", self)
        act_stats.setShortcut(QKeySequence("Ctrl+S"))
        act_stats.triggered.connect(self._refresh_stats_charts)
        help_menu.addAction(act_stats)

        # Новые инструменты (Улучшения J, K, L, N)
        tools_menu = menubar.addMenu("Инструменты")

        act_diff = QAction("📊  Сравнить корпуса...", self)
        act_diff.triggered.connect(self._show_diff_dialog)
        tools_menu.addAction(act_diff)

        act_yaml = QAction("📝  Редактор YAML...", self)
        act_yaml.setShortcut(QKeySequence("Ctrl+E"))
        act_yaml.triggered.connect(self._show_yaml_editor)
        tools_menu.addAction(act_yaml)

        act_dashboard = QAction("📈  Dashboard...", self)
        act_dashboard.setShortcut(QKeySequence("Ctrl+D"))
        act_dashboard.triggered.connect(self._show_dashboard)
        tools_menu.addAction(act_dashboard)

        tools_menu.addSeparator()

        # Язык (Улучшение N)
        lang_menu = tools_menu.addMenu("🌐  Язык / Language")
        act_ru = lang_menu.addAction("Русский")
        act_ru.setCheckable(True)
        act_ru.setChecked(get_language() == "ru")
        act_ru.triggered.connect(lambda: set_language("ru"))
        act_en = lang_menu.addAction("English")
        act_en.setCheckable(True)
        act_en.setChecked(get_language() == "en")
        act_en.triggered.connect(lambda: set_language("en"))

    def _menu_open_config(self) -> None:
        """Открыть config.yaml через меню Файл."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть config.yaml", "", "YAML (*.yaml *.yml);;Все файлы (*)"
        )
        if path:
            self.config_edit.setText(path)

    def _open_settings(self) -> None:
        """Открыть диалог настроек."""
        dialog = SettingsDialog(self.app_settings, self)
        dialog.settings_changed.connect(self._on_settings_changed)
        dialog.exec()

    def _on_settings_changed(self) -> None:
        """Настройки изменились — применяем."""
        self.app_settings = AppSettings.load()
        self.app_settings.setup_env_vars()
        # Если есть загруженный config — применяем настройки к нему
        if self.config:
            self.app_settings.apply_to_config(self.config)
        self._log("INFO", "Настройки применены")

    def _export_settings(self) -> None:
        """Экспорт настроек в JSON."""
        from pathlib import Path
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт настроек", "corpus_builder_settings.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            import json
            self.app_settings.save()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.app_settings.to_dict(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Экспортировано", f"Настройки сохранены в:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _import_settings(self) -> None:
        """Импорт настроек из JSON."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт настроек", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            from .app_settings import AppSettings
            self.app_settings = AppSettings._from_dict(data)
            self.app_settings.save()
            self.app_settings.setup_env_vars()
            self._on_settings_changed()
            QMessageBox.information(self, "Импортировано", "Настройки загружены и применены.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _reset_settings(self) -> None:
        """Сбросить настройки к defaults."""
        reply = QMessageBox.question(
            self, "Сброс настроек",
            "Сбросить все настройки к значениям по умолчанию?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            from .app_settings import AppSettings
            self.app_settings = AppSettings()
            self.app_settings.save()
            self.app_settings.setup_env_vars()
            self._on_settings_changed()
            QMessageBox.information(self, "Сброшено", "Настройки сброшены к defaults.")

    def _change_theme(self, theme: str) -> None:
        """Сменить тему оформления."""
        self.app_settings.gui.theme = theme
        self.app_settings.save()
        QMessageBox.information(self, "Тема изменена",
            f"Тема изменена на \"{theme}\". Перезапустите приложение для применения.")

    def _toggle_log_visibility(self) -> None:
        """Показать/скрыть панель лога."""
        # Лог находится во вкладках, переключаемся на него
        if hasattr(self, "tabs"):
            # Найти индекс вкладки с логом
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "Лог":
                    self.tabs.setCurrentIndex(i)
                    return

    def _show_about(self) -> None:
        """Показать окно 'О программе'."""
        QMessageBox.about(self, "О программе",
            "<h3>CorpusBuilder</h3>"
            "<p>Сборщик сырого корпуса для pretraining LLM</p>"
            "<p>Версия: 0.2.0</p>"
            "<p>GitHub: <a href=\"https://github.com/draco74-glitch/corpus_builder\">"
            "github.com/draco74-glitch/corpus_builder</a></p>"
            "<p>Поддерживаемые источники: HTML, PDF, GitHub, StackExchange, "
            "DOAJ, arXiv, Crossref, Wikipedia</p>"
        )

    def _open_documentation(self) -> None:
        """Открыть документацию в браузере."""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl("https://github.com/draco74-glitch/corpus_builder"))

    def _restore_window_geometry(self) -> None:
        """Восстановить размер и позицию окна из настроек."""
        w = self.app_settings.gui.window_width
        h = self.app_settings.gui.window_height
        self.resize(w, h)
        # Проверка обновлений при старте (через 2 секунды, чтобы не блокировать UI)
        QTimer.singleShot(2000, self._check_for_updates)

    def _check_for_updates(self) -> None:
        """Проверить наличие новых коммитов на GitHub (без релизов)."""
        try:
            updater = CommitUpdater(
                repo="draco74-glitch/corpus_builder",
                branch="main",
            )
            commit_info = updater.check_for_commit_updates()
            if commit_info:
                short_sha = commit_info.get("short_sha", "?")
                message = commit_info.get("message", "")[:100]
                author = commit_info.get("author", "")
                self._show_toast(
                    "Доступно обновление",
                    f"Коммит {short_sha} от {author}\n{message}",
                    "info"
                )
                self._has_update = commit_info
                self._updater = updater
            else:
                self._has_update = None
                self._updater = None
        except Exception as e:
            log.debug(f"Update check failed: {e}")
            self._has_update = None
            self._updater = None

    def _apply_update(self) -> None:
        """Применить обновление (скачать .py файлы из последнего коммита)."""
        if not hasattr(self, "_updater") or not self._updater:
            QMessageBox.information(self, "Обновление", "Нет доступных обновлений.")
            return

        commit_info = getattr(self, "_has_update", {}) or {}
        short_sha = commit_info.get("short_sha", "?")
        message = commit_info.get("message", "")[:200]
        author = commit_info.get("author", "")

        reply = QMessageBox.question(
            self, "Обновление из коммита",
            f"Коммит: {short_sha}\n"
            f"Автор: {author}\n"
            f"Сообщение: {message}\n\n"
            f"Применить обновление?\n"
            f"Будут скачаны и заменены .py файлы.\n"
            f"Программу нужно будет перезапустить.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.status.showMessage("Скачивание обновления...")
            QApplication.processEvents()

            result = self._updater.apply_commit_update(
                on_progress=lambda d, t, msg: self.status.showMessage(
                    f"[{d}/{t}] {msg}"
                )
            )

            if result.get("success"):
                updated = result.get("files_updated", 0)
                failed = result.get("files_failed", 0)
                self.status.showMessage(f"Обновлено {updated} файлов")
                QMessageBox.information(
                    self, "Обновление применено",
                    f"Успешно обновлено .py файлов: {updated}\n"
                    f"Ошибок: {failed}\n\n"
                    f"Пожалуйста, перезапустите CorpusBuilder\n"
                    f"для применения изменений."
                )
                QApplication.quit()
            else:
                error = result.get("error", "Неизвестная ошибка")
                self.status.showMessage("Обновление не удалось")
                QMessageBox.warning(
                    self, "Ошибка обновления",
                    f"Не удалось применить обновление:\n{error}"
                )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _check_for_updates_manual(self) -> None:
        """Ручная проверка обновлений (по коммитам)."""
        self.status.showMessage("Проверка коммитов на GitHub...")
        QApplication.processEvents()
        try:
            updater = CommitUpdater(
                repo="draco74-glitch/corpus_builder",
                branch="main",
            )
            commit_info = updater.check_for_commit_updates()
            if commit_info:
                short_sha = commit_info.get("short_sha", "?")
                message = commit_info.get("message", "")[:300]
                author = commit_info.get("author", "")
                date = commit_info.get("date", "")

                reply = QMessageBox.question(
                    self, "Доступно обновление",
                    f"Новый коммит: {short_sha}\n"
                    f"Автор: {author}\n"
                    f"Дата: {date}\n"
                    f"Сообщение: {message}\n\n"
                    f"Применить обновление?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self._updater = updater
                    self._has_update = commit_info
                    self._apply_update()
            else:
                QMessageBox.information(
                    self, "Обновления",
                    "У вас последняя версия (все коммиты применены)."
                )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка проверки", str(e))
        finally:
            self.status.showMessage("Готов")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # --- Конфигурация ---
        cfg_group = QGroupBox("1. Конфигурация")
        cfg_layout = QGridLayout(cfg_group)
        cfg_layout.setHorizontalSpacing(8)
        cfg_layout.setVerticalSpacing(6)

        # config.yaml
        cfg_layout.addWidget(QLabel("config.yaml:"), 0, 0)
        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText("Выберите файл конфигурации...")
        cfg_layout.addWidget(self.config_edit, 0, 1)
        btn_browse_config = QPushButton("Обзор...")
        btn_browse_config.setProperty("secondary", True)
        btn_browse_config.clicked.connect(self._browse_config)
        cfg_layout.addWidget(btn_browse_config, 0, 2)

        # output dir
        cfg_layout.addWidget(QLabel("Папка корпуса:"), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Куда сохранять корпус (перекрывает config.yaml)")
        cfg_layout.addWidget(self.output_edit, 1, 1)
        btn_browse_output = QPushButton("Обзор...")
        btn_browse_output.setProperty("secondary", True)
        btn_browse_output.clicked.connect(self._browse_output)
        cfg_layout.addWidget(btn_browse_output, 1, 2)

        btn_open_folder = QPushButton("Открыть папку")
        btn_open_folder.setProperty("secondary", True)
        btn_open_folder.clicked.connect(self._open_output_folder)
        cfg_layout.addWidget(btn_open_folder, 2, 2)

        # Опции запуска
        opts_row = QHBoxLayout()
        self.chk_resume = QCheckBox("Продолжить (resume)")
        self.chk_resume.setChecked(True)
        self.chk_resume.setToolTip("Не начинать заново, продолжить с чекпойнта")
        self.chk_retry = QCheckBox("Повторить упавшие")
        self.chk_retry.setToolTip("Повторно обработать URL, помеченные как ошибки")
        opts_row.addWidget(self.chk_resume)
        opts_row.addWidget(self.chk_retry)
        opts_row.addStretch()
        cfg_layout.addWidget(QLabel("Опции:"), 2, 0)
        cfg_layout.addLayout(opts_row, 2, 1)

        outer.addWidget(cfg_group)

        # --- Действия ---
        actions_group = QGroupBox("2. Действия")
        actions_layout = QHBoxLayout(actions_group)
        self.btn_merge_config = QPushButton("🔗  Объединить config")
        self.btn_merge_config.setToolTip("Объединить несколько config.yaml в один с удалением дубликатов")
        self.btn_merge_config.clicked.connect(self._on_merge_config)
        actions_layout.addWidget(self.btn_merge_config)

        self.btn_auto_discover = QPushButton("🔄  Авто-поиск источников")
        self.btn_auto_discover.setToolTip(
            "Автоматический поиск источников на GitHub, StackExchange и Wikipedia\n"
            "по заданным темам/категориям"
        )
        self.btn_auto_discover.clicked.connect(self._on_auto_discover)
        actions_layout.addWidget(self.btn_auto_discover)

        self.btn_generate_config = QPushButton("✨  Создать config.yaml")
        self.btn_generate_config.setProperty("secondary", True)
        self.btn_generate_config.setToolTip(
            "Открыть мастер генерации config.yaml из Excel/CSV, GitHub topics или StackExchange tags"
        )
        self.btn_generate_config.clicked.connect(self._on_open_config_generator)
        self.btn_crawl = QPushButton("▶  Запустить краулинг")
        self.btn_crawl.setStyleSheet(f"font-weight: bold; min-height: 28px;")
        self.btn_crawl.clicked.connect(self._on_start_crawl)
        self.btn_postprocess = QPushButton("⚙  Пост-обработка")
        self.btn_postprocess.setProperty("secondary", True)
        self.btn_postprocess.clicked.connect(self._on_postprocess)
        self.btn_stop = QPushButton("⏹  Остановить")
        self.btn_stop.setProperty("danger", True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_export_hf = QPushButton("⬇  Экспорт HF")
        self.btn_export_hf.setProperty("secondary", True)
        self.btn_export_hf.clicked.connect(self._on_export_hf)
        self.btn_export_parquet = QPushButton("⬇  Экспорт Parquet")
        self.btn_export_parquet.setProperty("secondary", True)
        self.btn_export_parquet.clicked.connect(self._on_export_parquet)
        actions_layout.addWidget(self.btn_generate_config)
        actions_layout.addWidget(self.btn_crawl)
        actions_layout.addWidget(self.btn_postprocess)
        actions_layout.addWidget(self.btn_stop)
        actions_layout.addStretch()
        actions_layout.addWidget(QLabel("Экспорт:"))
        actions_layout.addWidget(self.btn_export_hf)
        actions_layout.addWidget(self.btn_export_parquet)
        outer.addWidget(actions_group)

        # --- Прогресс ---
        prog_group = QGroupBox("3. Прогресс")
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        prog_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("Готов к запуску")
        self.progress_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        prog_layout.addWidget(self.progress_label)
        outer.addWidget(prog_group)

        # --- Вкладки: лог / записи / статистика ---
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {BORDER}; }}")
        splitter.setHandleWidth(2)

        # Лог
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_view)
        log_buttons = QHBoxLayout()
        btn_clear_log = QPushButton("Очистить лог")
        btn_clear_log.setProperty("secondary", True)
        btn_clear_log.clicked.connect(self.log_view.clear)
        log_buttons.addStretch()
        log_buttons.addWidget(btn_clear_log)
        log_layout.addLayout(log_buttons)

        # Записи
        records_tab = QWidget()
        records_layout = QVBoxLayout(records_tab)
        records_layout.setContentsMargins(0, 0, 0, 0)
        self.records_table = QTableWidget(0, 6)
        self.records_table.setHorizontalHeaderLabels(
            ["#", "URL", "Тип", "Длина", "Язык", "Quality"]
        )
        header = self.records_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.records_table.verticalHeader().setVisible(False)
        self.records_table.setEditTriggers(QTableWidget.NoEditTriggers)
        records_layout.addWidget(self.records_table)

        # Статистика
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.setContentsMargins(0, 0, 0, 0)

        # Создаём matplotlib фигуры для графиков
        self._build_stats_charts(stats_layout)

        # Обновить кнопку
        btn_refresh_stats = QPushButton("⟳ Обновить статистику")
        btn_refresh_stats.setProperty("secondary", True)
        btn_refresh_stats.clicked.connect(self._refresh_stats_charts)
        stats_layout.addWidget(btn_refresh_stats)

        # Текстовая сводка
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(180)
        stats_layout.addWidget(self.stats_text)

        tabs = QTabWidget()
        tabs.addTab(log_tab, "Лог")
        tabs.addTab(records_tab, "Последние записи")
        tabs.addTab(stats_tab, "Статистика")
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        outer.addWidget(splitter, stretch=1)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Готов")

    def _build_stats_charts(self, parent_layout: QVBoxLayout) -> None:
        # 2x2 grid: типы, языки, длины, качество
        self.fig = Figure(figsize=(8, 6), facecolor=DARKER_BG)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setStyleSheet("background: transparent;")
        parent_layout.addWidget(self.canvas)

        # Тёмная тема matplotlib
        import matplotlib as mpl
        mpl.rcParams.update({
            "figure.facecolor": DARKER_BG,
            "axes.facecolor": DARKER_BG,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": TEXT_PRIMARY,
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "text.color": TEXT_PRIMARY,
            "axes.titlecolor": ACCENT,
            "grid.color": "#3a3a3a",
        })

        self.ax_type = self.fig.add_subplot(2, 2, 1)
        self.ax_lang = self.fig.add_subplot(2, 2, 2)
        self.ax_len = self.fig.add_subplot(2, 2, 3)
        self.ax_qual = self.fig.add_subplot(2, 2, 4)
        self.fig.tight_layout(pad=1.5)

    # ----------------- Сигналы -----------------

    def _connect_signals(self) -> None:
        # автоматически загружаем конфиг, когда путь указан
        self.config_edit.textChanged.connect(self._on_config_path_changed)

    # ----------------- Хранение сессии -----------------

    def _settings_file(self) -> Path:
        # Сохраняем настройки рядом с exe или в home
        home = Path(os.path.expanduser("~"))
        return home / ".corpus_builder_gui.json"

    def _restore_last_session(self) -> None:
        f = self._settings_file()
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("config_path"):
                self.config_edit.setText(data["config_path"])
            if data.get("output_dir"):
                self.output_edit.setText(data["output_dir"])
        except Exception:
            pass

    def _save_session(self) -> None:
        try:
            data = {
                "config_path": self.config_edit.text(),
                "output_dir": self.output_edit.text(),
            }
            self._settings_file().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    # ----------------- Обработчики -----------------

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите config.yaml", "", "YAML (*.yaml *.yml);;Все файлы (*)"
        )
        if path:
            self.config_edit.setText(path)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Выберите папку для корпуса", ""
        )
        if path:
            self.output_edit.setText(path)

    def _on_config_path_changed(self) -> None:
        path = self.config_edit.text().strip()
        if not path or not Path(path).exists():
            self.config = None
            self.config_path = None
            return
        try:
            self.config = load_config(path)
            self.config_path = path
            self._log("INFO", f"Конфигурация загружена: {path}")
            self._log("INFO", f"  источников: {len(self.config.sources)}")
            # Если output_edit пустой — берём из конфига
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(Path(self.config.output.corpus_file).parent.absolute()))
        except Exception as e:
            self.config = None
            self._log("ERROR", f"Не удалось загрузить конфиг: {e}")
            QMessageBox.critical(self, "Ошибка конфигурации", str(e))

    def _build_effective_config(self) -> AppConfig | None:
        """Возвращает конфиг с перекрытым output dir, если пользователь указал путь."""
        if self.config is None:
            QMessageBox.warning(self, "Нет конфигурации",
                                 "Сначала выберите config.yaml")
            return None
        cfg = self.config
        out_dir = self.output_edit.text().strip()
        if out_dir:
            out_dir_path = Path(out_dir)
            out_dir_path.mkdir(parents=True, exist_ok=True)
            cfg.output.corpus_file = str(out_dir_path / "raw_corpus.jsonl")
            cfg.output.download_dir = str(out_dir_path / "downloaded_files")
            cfg.output.error_log = str(out_dir_path / "errors.jsonl")
            cfg.output.state_file = str(out_dir_path / "state.json")
            cfg.output.log_file = str(out_dir_path / "crawl.log")
        # Применяем настройки приложения
        self.app_settings.apply_to_config(cfg)
        self.app_settings.setup_env_vars()
        ensure_output_dirs(cfg)
        self._save_session()
        return cfg

    def _on_merge_config(self) -> None:
        """Открыть диалог объединения config.yaml."""
        try:
            dialog = MergeConfigDialog(self)
            if dialog.exec() == QDialog.Accepted:
                self._log("INFO", "Конфиги объединены")
        except Exception as e:
            import traceback
            self._log("ERROR", f"Ошибка объединения: {e}")
            QMessageBox.critical(self, "Ошибка",
                f"Не удалось объединить конфиги:\n\n{e}")

    def _on_auto_discover(self) -> None:
        """Открыть диалог авто-поиска источников."""
        try:
            from .auto_discover_dialog import AutoDiscoverDialog
            dialog = AutoDiscoverDialog(self)
            if dialog.exec() == QDialog.Accepted:
                # Если найдены источники — подсказать пользователю
                if hasattr(dialog, "config_path") and dialog.config_path:
                    reply = QMessageBox.question(
                        self, "Источники найдены",
                        f"Создан config.yaml с {dialog.sources_count} источниками.\n\n"
                        f"Файл: {dialog.config_path}\n\n"
                        f"Загрузить его в главное окно?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        self.config_edit.setText(dialog.config_path)
                        self._log("INFO", f"Загружен авто-config: {dialog.config_path}")
        except Exception as e:
            import traceback
            self._log("ERROR", f"Ошибка авто-поиска: {e}")
            QMessageBox.critical(self, "Ошибка",
                f"Не удалось выполнить авто-поиск:\n\n{e}\n\n"
                f"{traceback.format_exc()[:500]}")

    def _on_open_config_generator(self) -> None:
        """Открыть мастер создания config.yaml из Excel/GitHub/StackExchange."""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Занято",
                "Дождитесь завершения краулинга перед открытием мастера.")
            return
        try:
            # Передаём текущую папку вывода как начальную
            default_dir = self.output_edit.text().strip() or os.path.expanduser("~")
            dialog = ConfigGeneratorDialog(self, default_output_dir=default_dir)
            if dialog.exec() == QDialog.Accepted:
                self._log("INFO", "Мастер создания config.yaml завершён")
        except Exception as e:
            import traceback
            error_msg = traceback.format_exc()
            self._log("ERROR", f"Ошибка при открытии мастера: {e}")
            QMessageBox.critical(self, "Ошибка",
                f"Не удалось открыть мастер создания config.yaml:\n\n{e}\n\n"
                f"Подробности:\n{error_msg[:500]}")

    def _on_start_crawl(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Занято", "Уже выполняется задача. Остановите её перед запуском новой.")
            return

        resume = self.chk_resume.isChecked()
        retry = self.chk_retry.isChecked()

        self._set_running_state(True)
        self._log("INFO", "Запуск краулинга...")
        self.progress_bar.setValue(0)
        self.records_table.setRowCount(0)
        self.recent_records.clear()

        self.worker = CrawlWorker(
            cfg, mode="crawl", resume=resume, retry_errors=retry,
        )
        self._connect_worker_signals(self.worker)
        self.worker.start()

    def _on_postprocess(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Занято", "Дождитесь завершения текущей задачи.")
            return

        corpus_file = Path(cfg.output.corpus_file)
        if not corpus_file.exists() or corpus_file.stat().st_size == 0:
            QMessageBox.warning(self, "Нет данных",
                                f"Сначала запустите краулинг. Файл корпуса пуст: {corpus_file}")
            return

        self._set_running_state(True)
        self._log("INFO", "Запуск пост-обработки...")
        self.progress_bar.setValue(0)

        self.worker = CrawlWorker(cfg, mode="postprocess")
        self._connect_worker_signals(self.worker)
        self.worker.start()

    def _on_stop(self) -> None:
        if self.worker and self.worker.isRunning():
            self._log("WARN", "Останавливаю после текущего URL...")
            self.worker.request_stop()
            self.btn_stop.setEnabled(False)

    def _on_export_hf(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        corpus_file = Path(cfg.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            QMessageBox.warning(self, "Нет корпуса",
                                f"Файл не найден: {corpus_file}\nСначала запустите пост-обработку.")
            return
        target = QFileDialog.getExistingDirectory(self, "Куда сохранить HuggingFace dataset")
        if not target:
            return
        try:
            stats = export_huggingface(corpus_file, Path(target) / "corpus_hf_dataset")
            self._log("INFO", f"HF экспорт: {stats['records']} записей → {stats['path']}")
            QMessageBox.information(self, "Экспорт завершён",
                                     f"Записей: {stats['records']}\nПапка: {stats['path']}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))

    def _on_export_parquet(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        corpus_file = Path(cfg.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            QMessageBox.warning(self, "Нет корпуса",
                                f"Файл не найден: {corpus_file}\nСначала запустите пост-обработку.")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить Parquet", "corpus.parquet", "Parquet (*.parquet)"
        )
        if not target:
            return
        try:
            stats = export_parquet(corpus_file, target)
            self._log("INFO", f"Parquet экспорт: {stats['records']} записей, {stats['size_bytes']} байт")
            QMessageBox.information(self, "Экспорт завершён",
                                     f"Записей: {stats['records']}\nРазмер: {stats['size_bytes']} байт\nФайл: {stats['path']}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))

    def _connect_worker_signals(self, worker: CrawlWorker) -> None:
        worker.progress.connect(self._on_worker_progress)
        worker.record_added.connect(self._on_worker_record)
        worker.log_message.connect(self._on_worker_log)
        worker.finished_stats.connect(self._on_worker_finished)
        worker.error.connect(self._on_worker_error)

    # ---- слоты для сигналов worker ----

    def _on_worker_progress(self, current: int, total: int, msg: str) -> None:
        if total > 0:
            pct = int(current * 100 / total)
            self.progress_bar.setValue(pct)
            self.progress_label.setText(f"{current}/{total} — {msg}")
        else:
            self.progress_label.setText(msg)

    def _on_worker_record(self, record: dict) -> None:
        self.recent_records.append(record)
        row = self.records_table.rowCount()
        self.records_table.insertRow(row)
        self.records_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.records_table.setItem(row, 1, QTableWidgetItem(record.get("source_url", "")[:80]))
        self.records_table.setItem(row, 2, QTableWidgetItem(record.get("source_type", "")))
        self.records_table.setItem(row, 3, QTableWidgetItem(str(len(record.get("content") or ""))))
        self.records_table.setItem(row, 4, QTableWidgetItem(record.get("language") or ""))
        qs = record.get("quality_score")
        self.records_table.setItem(row, 5, QTableWidgetItem(f"{qs:.2f}" if qs is not None else "-"))
        # авто-скролл к новой записи
        self.records_table.scrollToBottom()

    def _on_worker_log(self, level: str, message: str) -> None:
        self._log(level, message)

    def _on_worker_finished(self, stats: dict) -> None:
        self._log("INFO", f"Готово: {stats}")
        self._set_running_state(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText(
            f"Завершено. Обработано: {stats.get('processed', 0)}, "
            f"ошибок: {stats.get('errors', 0)}"
        )
        # Если есть postprocess-статистика — показать в stats-вкладке
        if "dedup" in stats:
            summary = self._format_postprocess_summary(stats)
            self.stats_text.setPlainText(summary)
        self._refresh_stats_charts()

        # Tray notification
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(
                "Corpus Builder",
                f"Задача завершена. Обработано: {stats.get('processed', 0)}",
                QSystemTrayIcon.Information, 5000
            )

        QMessageBox.information(self, "Готово",
                                f"Задача завершена.\n\n{json.dumps(stats, ensure_ascii=False, indent=2)}")

    def _on_worker_error(self, err: str) -> None:
        self._log("ERROR", f"Критическая ошибка: {err}")
        self._set_running_state(False)
        QMessageBox.critical(self, "Критическая ошибка", err)

    # ----------------- Хелперы UI -----------------

    def _set_running_state(self, running: bool) -> None:
        self.btn_crawl.setEnabled(not running)
        self.btn_postprocess.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_export_hf.setEnabled(not running)
        self.btn_export_parquet.setEnabled(not running)
        if running:
            self.status.showMessage("Работаю...")
        else:
            self.status.showMessage("Готов")

    def _log(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": TEXT_PRIMARY,
            "WARNING": WARN,
            "ERROR": ERROR,
            "DEBUG": TEXT_SECONDARY,
        }.get(level, TEXT_PRIMARY)
        # экранируем переносы
        safe = msg.replace("\n", " ").replace("\r", "")
        self.log_view.append(
            f'<span style="color:{TEXT_SECONDARY}">{ts}</span> '
            f'<span style="color:{color};font-weight:bold">[{level}]</span> '
            f'<span style="color:{TEXT_PRIMARY}">{safe}</span>'
        )
        # авто-скролл вниз
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    def _refresh_status(self) -> None:
        if self.config is None:
            return
        try:
            if self._state_for_status is None or \
                    self._state_for_status.state_file != Path(self.config.output.state_file):
                self._state_for_status = State(self.config.output.state_file)
            else:
                self._state_for_status.reload_silent()
            done = self._state_for_status.done_count
            err = self._state_for_status.error_count
            self.status.showMessage(f"Готов | Обработано: {done} | Ошибок: {err}")
        except Exception:
            pass

    def _refresh_stats_charts(self) -> None:
        if self.config is None:
            return
        corpus_file = Path(self.config.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            # fallback на raw_corpus
            corpus_file = Path(self.config.output.corpus_file)
        if not corpus_file.exists():
            return

        stats = compute_statistics(corpus_file)

        # Текстовая сводка
        summary = (
            f"Всего записей: {stats['total']}\n"
            f"Дубликатов: {stats['duplicates']}\n"
            f"Суммарно символов: {stats['total_chars']:,}\n"
            f"Средняя длина: {stats['avg_chars']:,} символов\n\n"
            f"По типам:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_type", {}).items()) + "\n\n"
            f"По языкам:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_language", {}).items()) + "\n\n"
            f"По лицензиям:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_license", {}).items())
        )
        self.stats_text.setPlainText(summary)

        # Графики
        for ax in (self.ax_type, self.ax_lang, self.ax_len, self.ax_qual):
            ax.clear()

        # По типам — pie
        bt = stats.get("by_type", {})
        if bt:
            self.ax_type.pie(
                list(bt.values()), labels=list(bt.keys()),
                colors=["#007acc", "#4ec9b0", "#dcdcaa", "#ce9178", "#c586c0"],
                autopct="%1.0f%%",
                textprops={"color": TEXT_PRIMARY, "fontsize": 9},
            )
            self.ax_type.set_title("Распределение по типам источников", fontsize=10)
        else:
            self.ax_type.text(0.5, 0.5, "Нет данных", ha="center", va="center", color=TEXT_SECONDARY)
            self.ax_type.set_axis_off()

        # По языкам — bar
        bl = stats.get("by_language", {})
        if bl:
            self.ax_lang.bar(
                list(bl.keys()), list(bl.values()),
                color=["#007acc", "#4ec9b0", "#dcdcaa", "#ce9178", "#c586c0"][:len(bl)]
            )
            self.ax_lang.set_title("По языкам", fontsize=10)
            self.ax_lang.tick_params(axis="x", rotation=0, labelsize=8)
        else:
            self.ax_lang.text(0.5, 0.5, "Нет данных", ha="center", va="center", color=TEXT_SECONDARY)
            self.ax_lang.set_axis_off()

        # Длины — histogram
        lengths = stats.get("content_lengths") or []
        if lengths:
            self.ax_len.hist(lengths, bins=30, color="#4ec9b0", edgecolor=BORDER)
            self.ax_len.set_title("Распределение длин текстов", fontsize=10)
            self.ax_len.set_xlabel("chars", fontsize=9)
            self.ax_len.set_ylabel("count", fontsize=9)
            self.ax_len.tick_params(labelsize=8)
        else:
            self.ax_len.text(0.5, 0.5, "Нет данных", ha="center", va="center", color=TEXT_SECONDARY)
            self.ax_len.set_axis_off()

        # Качество — histogram
        qs = stats.get("quality_scores") or []
        if qs:
            self.ax_qual.hist(qs, bins=20, range=(0, 1), color="#dcdcaa", edgecolor=BORDER)
            self.ax_qual.set_title("Распределение quality_score", fontsize=10)
            self.ax_qual.set_xlabel("score", fontsize=9)
            self.ax_qual.set_ylabel("count", fontsize=9)
            self.ax_qual.tick_params(labelsize=8)
        else:
            self.ax_qual.text(0.5, 0.5, "Нет данных", ha="center", va="center", color=TEXT_SECONDARY)
            self.ax_qual.set_axis_off()

        self.fig.tight_layout(pad=1.0)
        self.canvas.draw_idle()

    def _format_postprocess_summary(self, stats: dict) -> str:
        d = stats.get("dedup", {})
        q = stats.get("quality", {})
        p = stats.get("pairs", {})
        lines = [
            "=== ПОСТ-ОБРАБОТКА ЗАВЕРШЕНА ===",
            "",
            f"Дедупликация:",
            f"  Всего: {d.get('total', 0)}",
            f"  Оставлено: {d.get('kept', 0)}",
            f"  Удалено дубликатов: {d.get('removed', 0)}",
            f"  Дубликатов изображений: {d.get('image_duplicates', 0)}",
            "",
            f"Фильтр качества:",
            f"  Всего: {q.get('total', 0)}",
            f"  Прошло: {q.get('kept', 0)}",
            f"  Отброшено: {q.get('rejected_total', 0)}",
        ]
        for reason, count in (q.get("rejected_by_reason") or {}).items():
            lines.append(f"    {reason}: {count}")
        lines.extend([
            "",
            f"Пары для instruction-tuning:",
            f"  Всего: {p.get('total_pairs', 0)}",
        ])
        for ptype, count in (p.get("by_type") or {}).items():
            lines.append(f"    {ptype}: {count}")
        lines.extend([
            "",
            f"Финальный корпус: {stats.get('final_corpus', '')}",
            f"Пары: {stats.get('pairs_file', '')}",
        ])
        return "\n".join(lines)

    def _open_output_folder(self) -> None:
        path = self.output_edit.text().strip()
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Папка не найдена", f"Укажите существующую папку.\nТекущее значение: {path}")
            return
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ----------------- Tray -----------------

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _quit_app(self) -> None:
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Подтверждение",
                "Краулинг ещё идёт. Остановить и выйти?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
            self.worker.request_stop()
            self.worker.wait(3000)
        self._save_session()
        QApplication.quit()

    # ----------------- Close event -----------------

    def _show_first_run_wizard(self) -> None:
        """Показать мастер первого запуска (Улучшение M)."""
        wizard = FirstRunWizard(self.app_settings, self)
        if wizard.exec():
            self._log("INFO", "Мастер первого запуска завершён")
            self._on_settings_changed()

    def _show_diff_dialog(self) -> None:
        """Открыть диалог сравнения корпусов (Улучшение J)."""
        dialog = DiffCorpusDialog(self)
        dialog.exec()

    def _show_yaml_editor(self) -> None:
        """Открыть встроенный редактор YAML (Улучшение K)."""
        path = self.config_edit.text().strip() if hasattr(self, "config_edit") else None
        dialog = YamlEditorDialog(path, self)
        if dialog.exec() == QDialog.Accepted and path:
            # Перезагружаем конфиг
            self.config_edit.setText(path)

    def _show_dashboard(self) -> None:
        """Открыть dashboard с метриками (Улучшение L)."""
        corpus_file = None
        if self.config:
            corpus_file = str(Path(self.config.output.corpus_file).parent / "corpus_final.jsonl")
            if not Path(corpus_file).exists():
                corpus_file = self.config.output.corpus_file
        dialog = DashboardDialog(corpus_file, self.config.output.error_log if self.config else None, self)
        dialog.exec()

    def _apply_theme(self, theme_name: str) -> None:
        """Применить тему оформления (Улучшения F + O)."""
        colors = apply_theme(QApplication.instance(), theme_name)
        self.setStyleSheet(get_theme_qss(colors))
        self._log("INFO", f"Тема применена: {theme_name}")

    def _change_theme(self, theme: str) -> None:
        """Сменить тему оформления."""
        self.app_settings.gui.theme = theme
        self.app_settings.save()
        self._apply_theme(theme)

    def _add_to_recent(self, path: str) -> None:
        """Добавить config.yaml в список недавних (Улучшение H)."""
        self.recent_configs.add(path)
        self._update_recent_menu()

    def _update_recent_menu(self) -> None:
        """Обновить меню недавних файлов (Улучшение H)."""
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        recent = self.recent_configs.get_recent(5)
        for path in recent:
            action = QAction(Path(path).name, self)
            action.setToolTip(path)
            action.triggered.connect(lambda checked, p=path: self.config_edit.setText(p))
            self.recent_menu.addAction(action)
        if recent:
            self.recent_menu.addSeparator()
            act_clear = QAction("Очистить список", self)
            act_clear.triggered.connect(self.recent_configs.clear)
            self.recent_menu.addAction(act_clear)

    def _show_toast(self, title: str, message: str, toast_type: str = "info") -> None:
        """Показать toast-уведомление (Улучшение E)."""
        ToastNotification.show(self, title, message, toast_type)

    def _toggle_log_search(self) -> None:
        """Показать/скрыть поиск по логу (Улучшение C)."""
        if not hasattr(self, "log_search_bar"):
            return
        if self.log_search_bar.isVisible():
            self.log_search_bar.hide()
        else:
            self.log_search_bar.show()
            self.log_search_bar.search_edit.setFocus()

    def _save_splitters(self) -> None:
        """Сохранить позиции разделителей (Улучшение D)."""
        if hasattr(self, "splitter"):
            self.splitter_saver.save(self.splitter, "main")

    def _restore_splitters(self) -> None:
        """Восстановить позиции разделителей (Улучшение D)."""
        if hasattr(self, "splitter"):
            self.splitter_saver.restore(self.splitter, "main")

    def _preview_kicad(self, file_path: str) -> None:
        """Показать превью KiCad-файла (Улучшение G)."""
        dialog = KicadPreviewDialog(file_path, self)
        dialog.exec()

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Подтверждение",
                "Краулинг ещё идёт. Свернуть в трей или выйти?",
                QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Yes
            )
            if reply == QMessageBox.Cancel:
                self.hide()
                if self.tray:
                    self.tray.show()
                    self.tray.showMessage(
                        "Corpus Builder",
                        "Сбор продолжается в фоне. Двойной клик по иконке — показать окно.",
                        QSystemTrayIcon.Information, 3000
                    )
                event.ignore()
                return
            self.worker.request_stop()
            self.worker.wait(3000)
        self._save_session()
        self._save_splitters()
        event.accept()


def main() -> int:
    """Точка входа GUI. Используется в pyinstaller --windowed."""
    app = QApplication(sys.argv)
    app.setApplicationName("Corpus Builder")
    app.setOrganizationName("draco74-glitch")
    app.setQuitOnLastWindowClosed(True)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
