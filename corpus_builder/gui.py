"""PySide6 GUI для corpus-builder.

Запуск:
    python -m corpus_builder.gui
или после сборки exe:
    CorpusBuilder.exe
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_settings import AppSettings
from .auto_updater import CommitUpdater

# В собранной через PyInstaller версии __package__ может быть пустым
from .config import ensure_output_dirs, load_config
from .config_generator_dialog import ConfigGeneratorDialog
from .gui_improvements import (
    DashboardDialog,
    DiffCorpusDialog,
    FirstRunWizard,
    KicadPreviewDialog,
    ProgressBarWithETA,
    RecentConfigsManager,
    SplitterStateSaver,
    ToastNotification,
    YamlEditorDialog,
    apply_theme,
    get_language,
    get_theme_qss,
    set_language,
    tr,
)
from .logging_setup import get_logger
from .merge_config_dialog import MergeConfigDialog
from .models import AppConfig
from .pipeline import estimate_crawl_minutes, run_crawl, run_postprocess
from .postproc.export import compute_statistics, export_huggingface, export_parquet
from .settings_dialog import SettingsDialog
from .startup_dialog import StartupDialog
from .state import State

log = get_logger(__name__)

#: границы «разрастания» UI (A7): до них окно оставалось линейно медленным и
#: жрало память на длинных ранах (10k+ записей)
MAX_LOG_BLOCKS = 3000          # строк в виджете лога (полный лог — в crawl.log)
MAX_TABLE_ROWS = 500           # строк в таблице «Последние записи»


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
                if getattr(self.config.pipeline, "use_async", False):
                    import asyncio

                    from .async_pipeline import run_async_crawl
                    stats = asyncio.run(run_async_crawl(
                        self.config,
                        resume=self.resume,
                        retry_errors=self.retry_errors,
                        limit=self.limit,
                        source_type=self.source_type,
                        on_progress=self._on_progress,
                        on_record=self._on_record,
                        on_log=self.log_message.emit,
                        should_stop=self.should_stop,
                    ))
                    self.finished_stats.emit(stats)
                    return
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
                    should_stop=self.should_stop,
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

class StatsWorker(QThread):
    """Считает статистику корпуса вне GUI-потока (A7).

    `compute_statistics()` обходит весь JSONL; на корпусах в сотни тысяч записей
    это вешало окно на несколько секунд при каждом обновлении вкладке / Ctrl+S.
    """
    ready = Signal(dict, str)
    failed = Signal(str)

    def __init__(self, corpus_file: Path, parent=None):
        super().__init__(parent)
        self.corpus_file = corpus_file

    def run(self) -> None:
        try:
            stats = compute_statistics(self.corpus_file)
            self.ready.emit(stats, str(self.corpus_file))
        except Exception as e:                       # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config: AppConfig | None = None
        self.config_path: str | None = None
        self.output_dir: str = ""
        self.worker: CrawlWorker | None = None
        self.recent_records: deque[dict] = deque(maxlen=20)

        self.setWindowTitle(tr("window_title"))
        self.resize(1280, 820)
        self._apply_dark_theme()

        # Tray icon (опционально, не падает если система без tray)
        self.tray = None
        try:
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_DriveHDIcon), self)
            self.tray.setToolTip("Corpus Builder")
            menu = QMenu()
            act_show = menu.addAction(tr("tray_show"))
            act_show.triggered.connect(self.showNormal)
            act_quit = menu.addAction(tr("tray_quit"))
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
        set_language(getattr(self.app_settings.gui, 'language', 'ru'))

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(2000)

        # Создаём state только один раз и периодически перезагружаем без логов
        self._state_for_status: State | None = None
        self._state_mtime: float | None = None

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
        """Create menus with i18n support."""
        menubar = self.menuBar()
        self._menus = {}
        self._menu_actions = {}

        def _m(key, title):
            menu = menubar.addMenu(title)
            self._menus[key] = menu
            return menu

        def _a(menu, key, text, handler=None, shortcut=None):
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if handler:
                act.triggered.connect(handler)
            menu.addAction(act)
            self._menu_actions[key] = act
            return act

        # File
        file_menu = _m("file", tr("menu_file_title"))
        _a(file_menu, "menu_open_config", tr("menu_open_config"), self._menu_open_config, "Ctrl+O")
        self.recent_menu = file_menu.addMenu(tr("menu_recent"))
        file_menu.addSeparator()
        _a(file_menu, "menu_export_hf", tr("menu_export_hf"), self._on_export_hf)
        _a(file_menu, "menu_export_parquet", tr("menu_export_parquet"), self._on_export_parquet)
        file_menu.addSeparator()
        _a(file_menu, "menu_save_config", tr("menu_save_config"),
           self._save_config_as, "Ctrl+S")
        _a(file_menu, "menu_quit", tr("menu_quit"), self._quit_app, "Ctrl+Q")

        # Settings
        settings_menu = _m("settings", tr("menu_settings_title"))
        _a(settings_menu, "menu_all_settings", tr("menu_all_settings"), self._open_settings, "Ctrl+,")
        settings_menu.addSeparator()
        _a(settings_menu, "menu_export_settings", tr("menu_export_settings"), self._export_settings)
        _a(settings_menu, "menu_import_settings", tr("menu_import_settings"), self._import_settings)
        settings_menu.addSeparator()
        _a(settings_menu, "menu_reset_settings", tr("menu_reset_settings"), self._reset_settings)

        # View
        view_menu = _m("view", tr("menu_view_title"))
        self._theme_menu = view_menu.addMenu(tr("menu_theme_title"))
        self.theme_group = {}
        self._theme_keys = ["theme_dark", "theme_light", "theme_material_blue",
                           "theme_material_green", "theme_material_purple"]
        for theme_name, tkey in zip(
            ["dark", "light", "material_blue", "material_green", "material_purple"],
            self._theme_keys
        ):
            act = self._theme_menu.addAction(tr(tkey))
            act.setCheckable(True)
            act.setChecked(self.app_settings.gui.theme == theme_name)
            act.triggered.connect(lambda checked, t=theme_name: self._change_theme(t))
            self.theme_group[theme_name] = act
            self._menu_actions[tkey] = act

        view_menu.addSeparator()
        _a(view_menu, "menu_toggle_log", tr("menu_toggle_log"), self._toggle_log_visibility, "Ctrl+L")
        _a(view_menu, "menu_search_log", tr("menu_search_log"), self._toggle_log_search, "Ctrl+F")

        # Actions
        actions_menu = _m("actions", tr("menu_actions_title"))
        _a(actions_menu, "menu_crawl", tr("menu_crawl"), self._on_start_crawl, "Ctrl+R")
        _a(actions_menu, "menu_postprocess", tr("menu_postprocess"), self._on_postprocess)
        _a(actions_menu, "menu_stop", tr("menu_stop"), self._on_stop)
        actions_menu.addSeparator()
        _a(actions_menu, "menu_generate_config", tr("menu_generate_config"), self._on_open_config_generator)
        _a(actions_menu, "menu_auto_discover", tr("menu_auto_discover"), self._on_auto_discover, "Ctrl+Shift+A")
        _a(actions_menu, "menu_merge_config", tr("menu_merge_config"), self._on_merge_config, "Ctrl+Shift+M")

        # Help
        help_menu = _m("help", tr("menu_help_title"))
        _a(help_menu, "menu_check_update", tr("menu_check_update"), self._check_for_updates_manual, "Ctrl+U")
        _a(help_menu, "menu_about", tr("menu_about"), self._show_about)
        _a(help_menu, "menu_docs", tr("menu_docs"), self._open_documentation)
        act = _a(help_menu, "menu_stats", tr("menu_stats"), self._refresh_stats_charts, "F5")
        self._stats_action = act

        # Tools
        tools_menu = _m("tools", tr("menu_tools_title"))
        _a(tools_menu, "menu_diff", tr("menu_diff"), self._show_diff_dialog)
        _a(tools_menu, "menu_yaml", tr("menu_yaml"), self._show_yaml_editor, "Ctrl+E")
        _a(tools_menu, "menu_dashboard", tr("menu_dashboard"), self._show_dashboard, "Ctrl+D")
        _a(tools_menu, "menu_effective_config", tr("menu_effective_config"),
           self._show_effective_config, "Ctrl+Shift+E")
        _a(tools_menu, "menu_validate_config", tr("menu_validate_config"),
           self._validate_current_config, "Ctrl+Shift+V")
        _a(tools_menu, "menu_run_history", tr("menu_run_history"),
           self._show_run_history, "Ctrl+H")
        _a(tools_menu, "menu_last_metrics", tr("menu_last_metrics"),
           self._show_last_metrics, "F4")
        _a(tools_menu, "menu_shortcuts", tr("menu_shortcuts"),
           self._show_shortcuts, "F1")
        tools_menu.addSeparator()

        self._lang_menu = tools_menu.addMenu(tr("menu_language"))
        self._lang_act_ru = self._lang_menu.addAction(tr("menu_lang_ru"))
        self._lang_act_ru.setCheckable(True)
        self._lang_act_ru.setChecked(get_language() == "ru")
        self._lang_act_ru.triggered.connect(lambda: self._change_language("ru"))
        self._lang_act_en = self._lang_menu.addAction(tr("menu_lang_en"))
        self._lang_act_en.setCheckable(True)
        self._lang_act_en.setChecked(get_language() == "en")
        self._lang_act_en.triggered.connect(lambda: self._change_language("en"))

    def _change_language(self, lang: str) -> None:
        """Switch language and rebuild entire UI for full translation."""
        set_language(lang)
        self.app_settings.gui.language = lang
        self.app_settings.save()

        # Clear old menu bar
        menubar = self.menuBar()
        menubar.clear()

        # Clear old central widget
        old_widget = self.centralWidget()

        # Rebuild everything fresh
        self._build_ui()
        self._build_menu()
        self._connect_signals()

        # Delete old widget after new one is set
        if old_widget:
            old_widget.deleteLater()

        # Re-apply theme + styles
        self._apply_dark_theme()
        self._apply_theme(self.app_settings.gui.theme)

        # Restore state
        if self.config_path:
            self.config_edit.setText(self.config_path)
        if self.output_dir:
            self.output_edit.setText(self.output_dir)
        self.chk_resume.setChecked(True)

        # Update recent menu
        self._update_recent_menu()

        # Update language checkboxes
        self._lang_act_ru.setChecked(lang == "ru")
        self._lang_act_en.setChecked(lang == "en")

    def _menu_open_config(self) -> None:
        """Открыть config.yaml через меню Файл."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu_open_config"), "", "YAML (*.yaml *.yml);;All files (*)"
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
        path, _ = QFileDialog.getSaveFileName(
            self, tr("menu_export_settings"), "corpus_builder_settings.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            import json
            from .app_settings import secret_paths

            self.app_settings.save()
            secrets = secret_paths(self.app_settings)
            redact = True
            if secrets:
                # секреты по умолчанию НЕ пишем: файл настроек обычно куда-то
                # пересылают. Спрашиваем явно, No = безопасный вариант.
                answer = QMessageBox.question(
                    self, tr("menu_export_settings"),
                    tr("export_secrets_ask").replace("{n}", str(len(secrets)))
                    + "\n" + ", ".join(secrets[:6]),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                redact = answer != QMessageBox.StandardButton.Yes
            data = self.app_settings.to_export_dict(redact=redact)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            msg = tr("exported_to").replace("{path}", path)
            if redact and secrets:
                msg += "\n" + tr("export_secrets_hidden").replace(
                    "{fields}", ", ".join(secrets[:6]))
            QMessageBox.information(self, "Экспортировано", msg)
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
            QMessageBox.information(self, tr("export_ok"), tr("settings_reset_ok"))

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
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://github.com/draco74-glitch/corpus_builder"))

    def _restore_window_geometry(self) -> None:
        """Восстановить размер и позицию окна из настроек."""
        w = self.app_settings.gui.window_width
        h = self.app_settings.gui.window_height
        self.resize(w, h)
        # Проверка обновлений при старте (через 2 секунды, чтобы не блокировать UI)
        if getattr(self.app_settings.gui, "check_updates_on_start", True):
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
            QMessageBox.information(self, tr("menu_check_update"), tr("update_none"))
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
            self.status.showMessage(tr("status_ready"))

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # --- Конфигурация ---
        cfg_group = QGroupBox(tr("group_config"))
        self.cfg_group = cfg_group
        cfg_layout = QGridLayout(cfg_group)
        cfg_layout.setHorizontalSpacing(8)
        cfg_layout.setVerticalSpacing(6)

        # config.yaml
        cfg_layout.addWidget(QLabel(tr("label_config")), 0, 0)
        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText("Выберите файл конфигурации...")
        cfg_layout.addWidget(self.config_edit, 0, 1)
        btn_browse_config = QPushButton(tr("btn_browse"))
        btn_browse_config.setProperty("secondary", True)
        btn_browse_config.clicked.connect(self._browse_config)
        cfg_layout.addWidget(btn_browse_config, 0, 2)

        # output dir
        cfg_layout.addWidget(QLabel(tr("label_output")), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Куда сохранять корпус (перекрывает config.yaml)")
        cfg_layout.addWidget(self.output_edit, 1, 1)
        btn_browse_output = QPushButton(tr("btn_browse"))
        btn_browse_output.setProperty("secondary", True)
        btn_browse_output.clicked.connect(self._browse_output)
        cfg_layout.addWidget(btn_browse_output, 1, 2)

        btn_open_folder = QPushButton(tr("btn_open_folder"))
        btn_open_folder.setProperty("secondary", True)
        btn_open_folder.clicked.connect(self._open_output_folder)
        cfg_layout.addWidget(btn_open_folder, 2, 2)

        # Опции запуска
        opts_row = QHBoxLayout()
        self.chk_resume = QCheckBox(tr("chk_resume"))
        self.chk_resume.setChecked(True)
        self.chk_resume.setToolTip("Не начинать заново, продолжить с чекпойнта")
        self.chk_retry = QCheckBox(tr("chk_retry"))
        self.chk_retry.setToolTip("Повторно обработать URL, помеченные как ошибки")
        opts_row.addWidget(self.chk_resume)
        opts_row.addWidget(self.chk_retry)
        opts_row.addStretch()
        cfg_layout.addWidget(QLabel(tr("label_options")), 2, 0)
        cfg_layout.addLayout(opts_row, 2, 1)

        outer.addWidget(cfg_group)

        # --- Действия ---
        actions_group = QGroupBox(tr("group_actions"))
        self.actions_group = actions_group
        actions_layout = QHBoxLayout(actions_group)
        self.btn_merge_config = QPushButton(tr("btn_merge_config"))
        self.btn_merge_config.setToolTip("Объединить несколько config.yaml в один с удалением дубликатов")
        self.btn_merge_config.clicked.connect(self._on_merge_config)
        actions_layout.addWidget(self.btn_merge_config)

        self.btn_auto_discover = QPushButton(tr("btn_auto_discover"))
        self.btn_auto_discover.setToolTip(
            "Автоматический поиск источников на GitHub, StackExchange и Wikipedia\n"
            "по заданным темам/категориям"
        )
        self.btn_auto_discover.clicked.connect(self._on_auto_discover)
        actions_layout.addWidget(self.btn_auto_discover)

        self.btn_generate_config = QPushButton(tr("btn_generate_config"))
        self.btn_generate_config.setProperty("secondary", True)
        self.btn_generate_config.setToolTip(
            "Открыть мастер генерации config.yaml из Excel/CSV, GitHub topics или StackExchange tags"
        )
        self.btn_generate_config.clicked.connect(self._on_open_config_generator)
        self.btn_crawl = QPushButton(tr("btn_crawl"))
        self.btn_crawl.setStyleSheet("font-weight: bold; min-height: 28px;")
        self.btn_crawl.clicked.connect(self._on_start_crawl)
        self.btn_postprocess = QPushButton(tr("btn_postprocess"))
        self.btn_postprocess.setProperty("secondary", True)
        self.btn_postprocess.clicked.connect(self._on_postprocess)
        self.btn_stop = QPushButton(tr("btn_stop"))
        self.btn_stop.setProperty("danger", True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_export_hf = QPushButton(tr("btn_export_hf"))
        self.btn_export_hf.setProperty("secondary", True)
        self.btn_export_hf.clicked.connect(self._on_export_hf)
        self.btn_export_parquet = QPushButton(tr("btn_export_parquet"))
        self.btn_export_parquet.setProperty("secondary", True)
        self.btn_export_parquet.clicked.connect(self._on_export_parquet)
        actions_layout.addWidget(self.btn_generate_config)
        actions_layout.addWidget(self.btn_crawl)
        actions_layout.addWidget(self.btn_postprocess)
        actions_layout.addWidget(self.btn_stop)
        actions_layout.addStretch()
        actions_layout.addWidget(QLabel(tr("label_export")))
        actions_layout.addWidget(self.btn_export_hf)
        actions_layout.addWidget(self.btn_export_parquet)
        outer.addWidget(actions_group)

        # --- Прогресс ---
        prog_group = QGroupBox(tr("group_progress"))
        self.prog_group = prog_group
        prog_layout = QVBoxLayout(prog_group)
        # B3: ProgressBarWithETAExistовал в gui_improvements, но в главное окно
        # не был встроен — «Progress bar with ETA» из README не показывался.
        self.progress_bar = ProgressBarWithETA()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        prog_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel(tr("progress_ready"))
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
        # A7: виджет лога не должен расти бесконечно — старые строки вытесняются,
        # полная история остаётся в файле crawl.log
        self.log_view.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        log_layout.addWidget(self.log_view)
        log_buttons = QHBoxLayout()
        btn_clear_log = QPushButton(tr("btn_clear_log"))
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
        btn_refresh_stats = QPushButton(tr("btn_refresh_stats"))
        btn_refresh_stats.setProperty("secondary", True)
        btn_refresh_stats.clicked.connect(self._refresh_stats_charts)
        stats_layout.addWidget(btn_refresh_stats)

        # Текстовая сводка
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(180)
        stats_layout.addWidget(self.stats_text)

        tabs = QTabWidget()
        tabs.addTab(log_tab, tr("tab_log"))
        tabs.addTab(records_tab, tr("tab_records"))
        tabs.addTab(stats_tab, tr("tab_stats"))
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 1)
        outer.addWidget(splitter, stretch=1)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(tr("status_ready"))

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
        """Конфиг с перекрытым output dir (без мутации self.config — I9).

        Раньше метод возвращал ТОТ ЖЕ объект, что лежит в self.config, и
        подменял в нём пути навсегда: «выбрать папку» для одного запуска
        ломало следующую загрузку config.yaml, а worker-поток читал объект,
        который GUI мог изменить в любой момент.
        """
        if self.config is None:
            QMessageBox.warning(self, "Нет конфигурации",
                                 "Сначала выберите config.yaml")
            return None
        cfg = copy.deepcopy(self.config)
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
                tr("busy_crawl"))
            return
        try:
            # Передаём текущую папку вывода как начальную
            default_dir = self.output_edit.text().strip() or os.path.expanduser("~")
            dialog = ConfigGeneratorDialog(self, default_output_dir=default_dir)
            if dialog.exec() == QDialog.Accepted:
                self._log("INFO", tr("config_generator_done"))
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
            QMessageBox.warning(self, tr("busy"), tr("thread_busy_start"))
            return

        resume = self.chk_resume.isChecked()
        retry = self.chk_retry.isChecked()

        if not self.chk_resume.isChecked() and not self._confirm_fresh_overwrite(cfg):
            return

        self._last_task_mode = "crawl"
        self._records_total = 0
        self._set_running_state(True)
        eta = estimate_crawl_minutes(cfg.sources, cfg.output.request_delay)
        if eta >= 1:
            self._log("INFO", tr("eta_log_hint").replace("{minutes}", f"{eta:.0f}"))
        self._log("INFO", tr("crawl_started"))
        self.progress_bar.setValue(0)
        if hasattr(self.progress_bar, "reset_timer"):
            self.progress_bar.reset_timer()
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
            QMessageBox.warning(self, tr("busy"), tr("busy_task"))
            return

        corpus_file = Path(cfg.output.corpus_file)
        if not corpus_file.exists() or corpus_file.stat().st_size == 0:
            QMessageBox.warning(self, tr("no_data"),
                                f"{tr('no_corpus_desc')}: {corpus_file}")
            return

        self._last_task_mode = "postprocess"
        self._records_total = 0
        self._set_running_state(True)
        self._log("INFO", tr("postprocess_started"))
        self.progress_bar.setValue(0)
        if hasattr(self.progress_bar, "reset_timer"):
            self.progress_bar.reset_timer()

        self.worker = CrawlWorker(cfg, mode="postprocess")
        self._connect_worker_signals(self.worker)
        self.worker.start()

    def _confirm_fresh_overwrite(self, cfg: AppConfig) -> bool:
        """B2: запуск без resume затирает собранный корпус — спрашиваем разрешение."""
        corpus = Path(cfg.output.corpus_file)
        if not corpus.exists() or corpus.stat().st_size == 0:
            return True
        try:
            with open(corpus, "r", encoding="utf-8") as f:
                n = sum(1 for line in f if line.strip())
        except OSError:
            n = 0
        eta = estimate_crawl_minutes(cfg.sources, cfg.output.request_delay)
        box = QMessageBox(self)
        box.setWindowTitle(tr("resume_warn_title"))
        box.setText(tr("resume_warn_text")
                    .replace("{file}", str(corpus))
                    .replace("{n}", str(n)))
        box.setInformativeText(tr("resume_warn_eta").replace("{minutes}", f"{eta:.0f}"))
        run_btn = box.addButton(tr("resume_warn_run"), QMessageBox.ButtonRole.AcceptRole)
        keep_btn = box.addButton(tr("resume_warn_resume_instead"),
                                 QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn)          # дефолт — НЕ «затереть»
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep_btn:
            self.chk_resume.setChecked(True)     # дописываем к существующему
            return True
        return clicked is run_btn

    # ============================================================
    # B6/B8/B9/B10: сохранение, «эффективный конфиг», валидация, история
    # ============================================================

    def _save_config_as(self) -> None:
        """Ctrl+S: сохранить эффективный конфиг в config.yaml (B6)."""
        cfg = self._build_effective_config(warn=False)
        if cfg is None:
            return
        suggested = self.config_path or "config.yaml"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("menu_save_config"), suggested, "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            import yaml
            payload = cfg.model_dump(mode="json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# CorpusBuilder config — сохранён из GUI\n")
                yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
            self.config_edit.setText(path)
            self._log("INFO", f"{tr('save_config_ok')}: {path}")
            self._show_toast(tr("save_config_ok"), path, ToastNotification.SUCCESS)
        except Exception as e:                        # noqa: BLE001
            self._log("ERROR", f"{tr('save_config_fail')}: {e}")
            QMessageBox.critical(self, tr("error"), str(e))

    def _diff_against_file(self, effective: AppConfig) -> list[str]:
        """Чем эффективный конфиг отличается от того, что в файле (B8)."""
        diffs: list[str] = []
        if not self.config_path or not Path(self.config_path).exists():
            return [tr("cfg_no_file_loaded")]
        try:
            from .config import load_config
            base = load_config(self.config_path)
        except Exception as e:                        # noqa: BLE001
            return [f"{tr('cfg_file_broken')}: {e}"]
        for section in ("output", "quality", "dedup", "pipeline", "export", "finetune"):
            base_obj = getattr(base, section, None)
            eff_obj = getattr(effective, section, None)
            if base_obj is None or eff_obj is None:
                continue
            for key in base_obj.model_fields:
                b, e = getattr(base_obj, key), getattr(eff_obj, key)
                if b != e:
                    diffs.append(f"{section}.{key}: {b!r} → {e!r}")
        for i, (b_s, e_s) in enumerate(zip(base.crawlers.model_dump().items(),
                                           effective.crawlers.model_dump().items())):
            if b_s != e_s:
                diffs.append(f"crawlers.{b_s[0]}: {b_s[1]} → {e_s[1]}")
        return diffs

    def _show_effective_config(self) -> None:
        """Ctrl+Shift+E: что реально поедет в движок, и кто это перекрыл (B8)."""
        cfg = self._build_effective_config(warn=False)
        if cfg is None:
            return
        import yaml
        diffs = self._diff_against_file(cfg)
        text = ("# " + tr("cfg_effective_note") + "\n"
                + yaml.safe_dump(cfg.model_dump(mode="json"),
                                 allow_unicode=True, sort_keys=False))
        box = QMessageBox(self)
        box.setWindowTitle(tr("menu_effective_config"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        if diffs:
            box.setText(tr("cfg_overridden").replace("{n}", str(len(diffs)))
                        + "\n" + "\n".join(diffs[:40]))
            box.setInformativeText(tr("cfg_where"))
        else:
            box.setText(tr("cfg_no_overrides"))
        box.setDetailedText(text)
        box.exec()

    def _validate_current_config(self) -> None:
        """Ctrl+Shift+V: проверить config.yaml и показать человекочитаемо (B9)."""
        path = self.config_edit.text().strip()
        if not path:
            QMessageBox.warning(self, tr("menu_validate_config"), tr("no_config_desc"))
            return
        from .cli import validate_config_file
        problems = validate_config_file(path)
        if problems:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(tr("cfg_invalid_title"))
            box.setText(tr("cfg_invalid_found").replace("{n}", str(len(problems))))
            box.setDetailedText("\n".join(f"• {x}" for x in problems))
            box.exec()
            for line in problems[:20]:
                self._log("ERROR", line)
        else:
            self._log("INFO", tr("cfg_valid"))
            QMessageBox.information(self, tr("cfg_valid_title"),
                                    f"{path}\n{tr('cfg_valid')}")

    def _run_history_file(self) -> Path:
        base = Path.cwd()
        if self.config is not None:
            base = Path(self.config.output.corpus_file).parent
        return base / "run_history.jsonl"

    def _record_run(self, mode: str, stats: dict) -> None:
        """B10: журнал запусков — когда, что и сколько собрано."""
        try:
            path = self._run_history_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "mode": mode,
                "config": self.config_path or "",
                "sources": len(self.config.sources) if self.config else 0,
                "stats": {k: v for k, v in stats.items()
                          if isinstance(v, (int, float, str, bool))},
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            log.debug(f"run history not written: {e}")

    def _show_run_history(self) -> None:
        """Ctrl+H: журнал последних прогонов и их метрики (B10)."""
        path = self._run_history_file()
        if not path.exists():
            QMessageBox.information(self, tr("menu_run_history"),
                                    tr("history_empty").replace("{p}", str(path)))
            return
        try:
            rows = [json.loads(l) for l in
                    path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, tr("menu_run_history"), str(e))
            return
        lines = []
        for r in rows[-50:]:
            st = r.get("stats") or {}
            counts = " ".join(f"{k}={v}" for k, v in list(st.items())[:6])
            lines.append(f"{r.get('ts','')}  {r.get('mode',''):11}  {counts}")
        box = QMessageBox(self)
        box.setWindowTitle(tr("menu_run_history"))
        box.setText(tr("history_text").replace("{n}", str(len(rows))) + "\n"
                    + "\n".join(lines[-25:]))
        box.setDetailedText(path.read_text(encoding="utf-8"))
        box.exec()

    def _show_last_metrics(self) -> None:
        """F4: метрики последней задачи (вместо модалки с JSON на каждом финале)."""
        stats = getattr(self, "_last_stats", None)
        if not stats:
            QMessageBox.information(self, tr("menu_last_metrics"), tr("no_metrics"))
            return
        box = QMessageBox(self)
        box.setWindowTitle(tr("menu_last_metrics"))
        box.setText(self._format_postprocess_summary(stats)
                    if "dedup" in stats else
                    tr("metrics_short").replace(
                        "{p}", str(stats.get("processed", 0))).replace(
                        "{e}", str(stats.get("errors", 0))))
        box.setDetailedText(json.dumps(stats, ensure_ascii=False, indent=2))
        box.exec()

    def _show_shortcuts(self) -> None:
        """F1: список горячих клавиш — из самих действий меню (B-доступность)."""
        rows = [f"{a.text()}\t{a.shortcut().toString()}"
                for a in getattr(self, "_menu_actions", {}).values()
                if a.shortcut().toString()]
        QMessageBox.information(
            self, tr("menu_shortcuts"),
            tr("shortcuts_text") + "\n" + "\n".join(sorted(set(rows))))


    def _on_stop(self) -> None:
        """Стоп: сначала graceful, повторная кнопка — жёсткий обрыв (B4)."""
        if not (self.worker and self.worker.isRunning()):
            return
        if not getattr(self, "_stop_armed", False):
            self._stop_armed = True
            self._log("WARN", tr("crawl_stopped"))
            self.worker.request_stop()
            mode = getattr(self.worker, "mode", "crawl")
            if mode == "postprocess":
                msg, hint = tr("stop_waiting_stage"), tr("stop_hint_stage")
            else:
                timeout = (self.config.pipeline.per_url_timeout_minutes
                           if self.config is not None else 10)
                msg = tr("stop_waiting").replace("{minutes}", str(timeout))
                hint = tr("stop_hint").replace("{minutes}", str(timeout))
            self.btn_stop.setText(tr("btn_stop_forced"))
            self.btn_stop.setToolTip(hint)
            self.status.showMessage(msg)
            return
        # второе нажатие — жёстко прерываем (с предупреждением о риске)
        reply = QMessageBox.question(
            self, tr("stop_force_title"), tr("stop_force_text"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.worker.request_stop()
            if not self.worker.wait(1000):
                self.worker.terminate()
                self._log("ERROR", tr("stop_terminated"))
        finally:
            self.worker = None
            self._stop_armed = False
            self._set_running_state(False)

    def _on_export_hf(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        corpus_file = Path(cfg.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            QMessageBox.warning(self, tr("no_corpus"),
                                f"{tr('no_corpus_final')}\n{corpus_file}")
            return
        target = QFileDialog.getExistingDirectory(self, "Куда сохранить HuggingFace dataset")
        if not target:
            return
        try:
            stats = export_huggingface(corpus_file, Path(target) / "corpus_hf_dataset")
            self._log("INFO", f"HF экспорт: {stats['records']} записей → {stats['path']}")
            QMessageBox.information(self, tr("info"),
                                     f"Записей: {stats['records']}\nПапка: {stats['path']}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))

    def _on_export_parquet(self) -> None:
        cfg = self._build_effective_config()
        if cfg is None:
            return
        corpus_file = Path(cfg.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            QMessageBox.warning(self, tr("no_corpus"),
                                f"{tr('no_corpus_final')}\n{corpus_file}")
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
            if hasattr(self.progress_bar, "set_progress"):
                self.progress_bar.set_progress(current, total)   # % + ETA + URL/s
            else:
                self.progress_bar.setValue(int(current * 100 / total))
            self.progress_label.setText(f"{current}/{total} — {msg}")
        else:
            self.progress_label.setText(msg)

    def _on_worker_record(self, record: dict) -> None:
        self.recent_records.append(record)
        self._records_total = getattr(self, "_records_total", 0) + 1
        # A7: показываем последние MAX_TABLE_ROWS, а не все N записей
        while self.records_table.rowCount() >= MAX_TABLE_ROWS:
            self.records_table.removeRow(0)
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
        if self._records_total > MAX_TABLE_ROWS:
            self.records_table.setHorizontalHeaderLabels(
                [tr("col_index"),
                 f"{tr('col_url')} (1..{self._records_total})",
                 tr("col_type"), tr("col_length"), tr("col_language"), tr("col_quality")])

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

        # B1: раньше в конце каждого запуска всплывала модальная QMessageBox с
        # дампом JSON — она блокировала окно и требовала клика. Теперь toast +
        # запись в лог; полный JSON доступен по кнопке в сводке.
        self._last_stats = stats
        processed = stats.get("processed", stats.get("kept", 0)) or 0
        self._show_toast(tr("toast_complete"),
                         f"{tr('crawl_finished')}: {processed} | "
                         f"{tr('col_errors')}: {stats.get('errors', 0)}",
                         ToastNotification.SUCCESS if not stats.get("errors")
                         else ToastNotification.WARNING)
        # Tray notification
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(
                "Corpus Builder",
                f"{tr('toast_complete')}: {processed}",
                QSystemTrayIcon.Information, 5000
            )

        self._record_run(getattr(self, "_last_task_mode", "task"), stats)
        # подробный дамп — не модалкой, а в лог; F4 открывает диалог «подробно»
        self._log("INFO", "metrics: " + json.dumps(stats, ensure_ascii=False)[:600])

    def _on_worker_error(self, err: str) -> None:
        self._log("ERROR", f"Критическая ошибка: {err}")
        self._set_running_state(False)
        QMessageBox.critical(self, "Критическая ошибка", err)

    # ----------------- Хелперы UI -----------------

    def _set_running_state(self, running: bool) -> None:
        self.btn_crawl.setEnabled(not running)
        self.btn_postprocess.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if not running:
            # B4: вернуть кнопку «Стоп» в исходное состояние после остановки
            self._stop_armed = False
            self.btn_stop.setText(tr("btn_stop"))
            self.btn_stop.setToolTip("")
            if hasattr(self.progress_bar, "set_progress"):
                self.progress_bar.set_format("%p%")
        self.btn_export_hf.setEnabled(not running)
        self.btn_export_parquet.setEnabled(not running)
        if running:
            self.status.showMessage(tr("status_working"))
        else:
            self.status.showMessage(tr("status_ready"))

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
            state_path = Path(self.config.output.state_file)
            mtime = state_path.stat().st_mtime if state_path.exists() else None
            if self._state_for_status is None or \
                    self._state_for_status.state_file != state_path:
                self._state_for_status = State(state_path)
                self._state_mtime = mtime
            elif mtime != getattr(self, "_state_mtime", None):
                # A7: перечитываем только если файл реально изменился (2 секунды
                # на полный JSON-разбор 200k URL — это заметная нагрузка)
                self._state_for_status.reload_silent()
            self._state_mtime = mtime
            done = self._state_for_status.done_count
            err = self._state_for_status.error_count
            self.status.showMessage(f"Готов | Обработано: {done} | Ошибок: {err}")
        except Exception:
            pass

    def _refresh_stats_charts(self) -> None:
        """Обновить вкладку статистики — расчёт уводится в фон (A7)."""
        if self.config is None:
            return
        corpus_file = Path(self.config.output.corpus_file).parent / "corpus_final.jsonl"
        if not corpus_file.exists():
            # fallback на raw_corpus
            corpus_file = Path(self.config.output.corpus_file)
        if not corpus_file.exists():
            return

        worker = getattr(self, "_stats_worker", None)
        if worker is not None and worker.isRunning():
            self._stats_pending = True        # догоним после текущего расчёта
            return
        self._stats_pending = False
        self.status.showMessage(tr("stats_calculating"))
        self._stats_worker = StatsWorker(corpus_file, self)
        self._stats_worker.ready.connect(self._on_stats_ready)
        self._stats_worker.failed.connect(
            lambda err: self._log("WARNING", f"Статистика: {err}"))
        self._stats_worker.start()

    def _on_stats_ready(self, stats: dict, source: str) -> None:
        self.status.showMessage("")
        self._draw_stats(stats, source)
        if getattr(self, "_stats_pending", False):
            self._stats_pending = False
            self._refresh_stats_charts()      # пересчитаем «пока ждали»

    def _draw_stats(self, stats: dict, source: str = "") -> None:
        # Текстовая сводка
        summary = (
            f"Всего записей: {stats['total']}\n"
            f"Дубликатов: {stats['duplicates']}\n"
            f"Суммарно символов: {stats['total_chars']:,}\n"
            f"Средняя длина: {stats['avg_chars']:,} символов\n\n"
            f"По типам:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_type", {}).items()) + "\n\n"
            "По языкам:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_language", {}).items()) + "\n\n"
            "По лицензиям:\n" + "\n".join(f"  {k}: {v}" for k, v in stats.get("by_license", {}).items())
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
            "Дедупликация:",
            f"  Всего: {d.get('total', 0)}",
            f"  Оставлено: {d.get('kept', 0)}",
            f"  Удалено дубликатов: {d.get('removed', 0)}",
            f"  Дубликатов изображений: {d.get('image_duplicates', 0)}",
            "",
            "Фильтр качества:",
            f"  Всего: {q.get('total', 0)}",
            f"  Прошло: {q.get('kept', 0)}",
            f"  Отброшено: {q.get('rejected_total', 0)}",
        ]
        for reason, count in (q.get("rejected_by_reason") or {}).items():
            lines.append(f"    {reason}: {count}")
        lines.extend([
            "",
            "Пары для instruction-tuning:",
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
                self, tr("confirm_title"), tr("quit_running_text"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
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
            act_clear = QAction(tr("menu_recent_clear"), self)
            act_clear.setToolTip(tr("menu_recent_clear"))
            act_clear.triggered.connect(self.recent_configs.clear)
            self.recent_menu.addAction(act_clear)

    def _show_toast(self, title: str, message: str, toast_type: str = "info") -> None:
        """Показать toast-уведомление (Улучшение E)."""
        ToastNotification.display(self, title, message, toast_type)

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
            # Прежний набор `QMessageBox.Cancel | QMessageBox.Yes` не давал
            # осмысленного выбора: окно показывало «Отмена» как дефолтную кнопку,
            # а крестик окна интерпретировался как «свернуть в трей» (I9).
            box = QMessageBox(self)
            box.setWindowTitle(tr("confirm_title"))
            box.setText(tr("close_running_text"))
            hide_btn = box.addButton(tr("close_to_tray"), QMessageBox.ButtonRole.AcceptRole)
            quit_btn = box.addButton(tr("close_stop_quit"),
                                     QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(hide_btn)
            box.exec()
            clicked = box.clickedButton()

            if clicked is cancel_btn or clicked is None:
                event.ignore()                # пользователь передумал
                return

            if clicked is quit_btn:
                self.worker.request_stop()
                if not self.worker.wait(5000):
                    # поток daemon, процесс всё равно завершится; честно пишем в лог
                    log.warning("Worker поток не остановился за 5s — "
                                "он daemon и будет убит при выходе")
                self.worker = None
            else:                             # «свернуть в трей»
                self.hide()
                if self.tray:
                    self.tray.show()
                    self.tray.showMessage(
                        "Corpus Builder", tr("tray_running_msg"),
                        QSystemTrayIcon.Information, 3000
                    )
                event.ignore()
                return

        self._save_session()
        self._save_splitters()
        event.accept()


def main() -> int:
    """Точка входа GUI. Показывает startup dialog, затем открывает нужное окно."""
    app = QApplication(sys.argv)
    app.setApplicationName("Corpus Builder")
    app.setOrganizationName("draco74-glitch")
    app.setQuitOnLastWindowClosed(True)

    # Show startup dialog — select mode
    mode = StartupDialog.ask_mode()

    if mode == StartupDialog.MODE_FINETUNING:
        from .finetune_window import FinetuneWindow
        window = FinetuneWindow()
    else:
        window = MainWindow()

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
