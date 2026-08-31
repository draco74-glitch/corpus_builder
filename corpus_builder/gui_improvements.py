"""Улучшения интерфейса (A-O):
  A. Drag-and-drop config.yaml
  B. Контекстное меню на таблице записей
  C. Поиск по логу (Ctrl+F)
  D. Сохранение позиции разделителей
  E. Toast-уведомления
  F. Реальное переключение тёмной/светлой темы
  G. Превью KiCad-файлов
  H. История последних config.yaml
  I. Прогресс с ETA в статус-баре
  J. Сравнение корпусов в GUI
  K. Встроенный редактор YAML
  L. Dashboard с метриками
  M. Мастер первого запуска
  N. Локализация RU/EN
  O. Темы оформления (Material Design)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from .logging_setup import get_logger

log = get_logger(__name__)


# ============================================================
# A. Drag-and-Drop config.yaml
# ============================================================

class ConfigDropArea(QFrame):
    """Зона для drag-and-drop config.yaml файлов."""
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(40)
        self._label = QLabel("💡 Перетащите config.yaml сюда", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #858585; font-size: 12px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.addWidget(self._label)
        self._set_normal_style()

    def _set_normal_style(self):
        self.setStyleSheet("""
            QFrame { border: 2px dashed #3c3c3c; border-radius: 6px; background: #252526; }
        """)

    def _set_hover_style(self):
        self.setStyleSheet("""
            QFrame { border: 2px dashed #007acc; border-radius: 6px; background: #2a3a4a; }
        """)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.endswith((".yaml", ".yml")):
                        event.acceptProposedAction()
                        self._set_hover_style()
                        return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_normal_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_normal_style()
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path.endswith((".yaml", ".yml")):
                    self.file_dropped.emit(path)
                    event.acceptProposedAction()
                    return
        event.ignore()


# ============================================================
# B. Контекстное меню на таблице записей
# ============================================================

class RecordsTableContextMenu:
    """Контекстное меню для таблицы последних записей.

    Правый клик → Открыть URL, Скопировать URL, Удалить из корпуса
    """

    @staticmethod
    def setup(table: QTableWidget, on_open_url=None, on_copy_url=None,
              on_delete=None) -> None:
        """Настроить контекстное меню для таблицы."""
        table.setContextMenuPolicy(Qt.CustomContextMenu)

        def show_menu(pos):
            item = table.itemAt(pos)
            if not item:
                return
            row = item.row()
            url_item = table.item(row, 1)  # колонка URL
            if not url_item:
                return
            url = url_item.text()

            menu = QMenu(table)

            act_open = menu.addAction("🔗  Открыть URL в браузере")
            act_copy = menu.addAction("📋  Скопировать URL")
            menu.addSeparator()
            act_delete = menu.addAction("🗑  Удалить из корпуса")

            action = menu.exec(table.mapToGlobal(pos))
            if action == act_open and on_open_url:
                on_open_url(url)
            elif action == act_copy and on_copy_url:
                on_copy_url(url)
            elif action == act_delete and on_delete:
                on_delete(row)

        table.customContextMenuRequested.connect(show_menu)

    @staticmethod
    def open_url_in_browser(url: str) -> None:
        """Открыть URL в системном браузере."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def copy_url_to_clipboard(url: str) -> None:
        """Скопировать URL в буфер обмена."""
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(url)


# ============================================================
# C. Поиск по логу (Ctrl+F)
# ============================================================

class LogSearchBar(QWidget):
    """Панель поиска по логу с подсветкой результатов.

    Использование:
        search_bar = LogSearchBar(log_view)
        layout.addWidget(search_bar)
    """

    def __init__(self, log_view: QTextEdit, parent=None):
        super().__init__(parent)
        self.log_view = log_view
        self._build_ui()
        self._highlights: list[QTextCursor] = []
        self._current_highlight = 0

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Поиск по логу... (Ctrl+F)")
        self.search_edit.textChanged.connect(self._on_search)
        layout.addWidget(self.search_edit)

        self.btn_prev = QPushButton("↑")
        self.btn_prev.setFixedWidth(30)
        self.btn_prev.setToolTip("Предыдущее совпадение")
        self.btn_prev.clicked.connect(self._prev_match)
        layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("↓")
        self.btn_next.setFixedWidth(30)
        self.btn_next.setToolTip("Следующее совпадение")
        self.btn_next.clicked.connect(self._next_match)
        layout.addWidget(self.btn_next)

        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #858585; font-size: 11px;")
        self.result_label.setFixedWidth(80)
        layout.addWidget(self.result_label)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(30)
        self.btn_close.setToolTip("Закрыть поиск (Esc)")
        self.btn_close.clicked.connect(self.hide)
        layout.addWidget(self.btn_close)

        self.hide()

    def _on_search(self, text: str) -> None:
        """Найти все совпадения и подсветить."""
        self._highlights = []
        self._current_highlight = 0

        if not text:
            self.result_label.setText("")
            return

        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.Start)

        # Поиск всех совпадений
        while True:
            cursor = self.log_view.document().find(text, cursor)
            if cursor.isNull():
                break
            self._highlights.append(QTextCursor(cursor))

        if self._highlights:
            self._goto_match(0)
            self.result_label.setText(f"1 / {len(self._highlights)}")
        else:
            self.result_label.setText("Не найдено")

    def _goto_match(self, idx: int) -> None:
        """Перейти к совпадению по индексу."""
        if not self._highlights:
            return
        idx = idx % len(self._highlights)
        self._current_highlight = idx
        cursor = self._highlights[idx]
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        self.result_label.setText(f"{idx + 1} / {len(self._highlights)}")

    def _next_match(self) -> None:
        if self._highlights:
            self._goto_match(self._current_highlight + 1)

    def _prev_match(self) -> None:
        if self._highlights:
            self._goto_match(self._current_highlight - 1)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
        elif event.key() == Qt.Key_Return:
            if event.modifiers() & Qt.ShiftModifier:
                self._prev_match()
            else:
                self._next_match()
        else:
            super().keyPressEvent(event)


# ============================================================
# D. Сохранение позиции разделителей
# ============================================================

class SplitterStateSaver:
    """Сохранение и восстановление позиций QSplitter.

    Использование:
        saver = SplitterStateSaver("corpus_builder/splitter_state.json")
        saver.save(splitter, "main")
        saver.restore(splitter, "main")
    """

    def __init__(self, settings_file: str | Path):
        self.settings_file = Path(settings_file)

    def save(self, splitter: QSplitter, name: str) -> None:
        """Сохранить позиции разделителя."""
        try:
            sizes = splitter.sizes()
            data = {}
            if self.settings_file.exists():
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[name] = sizes
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            log.debug(f"Cannot save splitter state: {e}")

    def restore(self, splitter: QSplitter, name: str) -> None:
        """Восстановить позиции разделителя."""
        try:
            if not self.settings_file.exists():
                return
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if name in data:
                sizes = data[name]
                if isinstance(sizes, list) and len(sizes) == splitter.count():
                    splitter.setSizes(sizes)
        except Exception as e:
            log.debug(f"Cannot restore splitter state: {e}")


# ============================================================
# E. Toast-уведомления
# ============================================================

class ToastNotification(QFrame):
    """Toast-уведомление — всплывающее окно в правом нижнем углу.

    Использование:
        ToastNotification.display(parent, "Заголовок", "Текст", ToastNotification.INFO)

    ВАЖНО (C5): фабричный метод НЕ может называться `show` — он перекрывал бы
    `QWidget.show()`, и собственный вызов `self.show()` внутри `__init__`
    падал с `TypeError: show() missing 3 required positional arguments`.
    """

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

    _colors = {
        "info":    {"bg": "#007acc", "border": "#0099e6"},
        "success": {"bg": "#4ec9b0", "border": "#5dd9c0"},
        "warning": {"bg": "#dcdcaa", "border": "#ece9ba"},
        "error":   {"bg": "#f44747", "border": "#ff5757"},
    }

    @classmethod
    def display(cls, parent: QWidget, title: str, message: str,
                toast_type: str = "info", duration: int = 4000) -> ToastNotification:
        """Создать toast и показать его (не путать с QWidget.show)."""
        toast = cls(parent, title, message, toast_type, duration)
        toast.raise_()
        return toast

    def __init__(self, parent: QWidget, title: str, message: str,
                 toast_type: str = "info", duration: int = 4000):
        super().__init__(parent)
        self.toast_type = toast_type
        self.duration = duration

        colors = self._colors.get(toast_type, self._colors["info"])
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {colors["bg"]};
                border: 1px solid {colors["border"]};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setStyleSheet("color: white; font-size: 12px;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        self.setFixedWidth(350)
        self.adjustSize()

        # Позиция: правый нижний угол родителя
        if parent:
            parent_rect = parent.rect()
            x = parent_rect.width() - self.width() - 20
            y = parent_rect.height() - self.height() - 20
            self.move(x, y)

        self.show()

        # Анимация появления
        self._fade_in()

        # Автоматическое скрытие
        QTimer.singleShot(duration, self._fade_out)

    def _fade_in(self) -> None:
        """Анимация плавного появления."""
        self.setWindowOpacity(0.0)
        # Простая анимация через QTimer
        for i in range(1, 11):
            QTimer.singleShot(i * 30, lambda v=i: self.setWindowOpacity(v / 10.0))

    def _fade_out(self) -> None:
        """Анимация плавного исчезновения."""
        for i in range(10, -1, -1):
            QTimer.singleShot((10 - i) * 30, lambda v=i: self.setWindowOpacity(v / 10.0))
        QTimer.singleShot(350, self.hide)


# ============================================================
# F. Реальное переключение тёмной/светлой темы
# ============================================================

THEMES = {
    "dark": {
        "window_bg": "#1e1e1e",
        "darker_bg": "#252526",
        "lighter_bg": "#2d2d30",
        "text_primary": "#d4d4d4",
        "text_secondary": "#858585",
        "accent": "#007acc",
        "accent_hover": "#1f8ad2",
        "success": "#4ec9b0",
        "warn": "#dcdcaa",
        "error": "#f44747",
        "border": "#3c3c3c",
    },
    "light": {
        "window_bg": "#ffffff",
        "darker_bg": "#f5f5f5",
        "lighter_bg": "#e8e8e8",
        "text_primary": "#1e1e1e",
        "text_secondary": "#666666",
        "accent": "#007acc",
        "accent_hover": "#005a9e",
        "success": "#107c10",
        "warn": "#bf8800",
        "error": "#d13438",
        "border": "#cccccc",
    },
    # Material Design themes (Улучшение O)
    "material_blue": {
        "window_bg": "#fafafa",
        "darker_bg": "#ffffff",
        "lighter_bg": "#e3f2fd",
        "text_primary": "#212121",
        "text_secondary": "#757575",
        "accent": "#2196f3",
        "accent_hover": "#1976d2",
        "success": "#4caf50",
        "warn": "#ff9800",
        "error": "#f44336",
        "border": "#bbdefb",
    },
    "material_green": {
        "window_bg": "#fafafa",
        "darker_bg": "#ffffff",
        "lighter_bg": "#e8f5e9",
        "text_primary": "#212121",
        "text_secondary": "#757575",
        "accent": "#4caf50",
        "accent_hover": "#388e3c",
        "success": "#66bb6a",
        "warn": "#ff9800",
        "error": "#f44336",
        "border": "#c8e6c9",
    },
    "material_purple": {
        "window_bg": "#fafafa",
        "darker_bg": "#ffffff",
        "lighter_bg": "#f3e5f5",
        "text_primary": "#212121",
        "text_secondary": "#757575",
        "accent": "#9c27b0",
        "accent_hover": "#7b1fa2",
        "success": "#4caf50",
        "warn": "#ff9800",
        "error": "#f44336",
        "border": "#e1bee7",
    },
}


def apply_theme(app: QApplication, theme_name: str) -> dict:
    """Применить тему к приложению и вернуть словарь цветов.

    Поддерживает: dark, light, material_blue, material_green, material_purple
    """
    colors = THEMES.get(theme_name, THEMES["dark"])

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(colors["window_bg"]))
    palette.setColor(QPalette.WindowText, QColor(colors["text_primary"]))
    palette.setColor(QPalette.Base, QColor(colors["darker_bg"]))
    palette.setColor(QPalette.AlternateBase, QColor(colors["lighter_bg"]))
    palette.setColor(QPalette.Text, QColor(colors["text_primary"]))
    palette.setColor(QPalette.Button, QColor(colors["lighter_bg"]))
    palette.setColor(QPalette.ButtonText, QColor(colors["text_primary"]))
    palette.setColor(QPalette.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipBase, QColor(colors["lighter_bg"]))
    palette.setColor(QPalette.ToolTipText, QColor(colors["text_primary"]))
    app.setPalette(palette)

    return colors


def get_theme_qss(colors: dict) -> str:
    """Сгенерировать QSS-стили на основе словаря цветов."""
    return f"""
    QMainWindow, QWidget {{
        background-color: {colors["window_bg"]};
        color: {colors["text_primary"]};
        font-family: 'Segoe UI', 'SF Pro', 'DejaVu Sans';
        font-size: 13px;
    }}
    QGroupBox {{
        background-color: {colors["darker_bg"]};
        border: 1px solid {colors["border"]};
        border-radius: 6px;
        margin-top: 14px;
        padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {colors["accent"]};
        font-weight: bold;
    }}
    QPushButton {{
        background-color: {colors["accent"]};
        color: white;
        border: none;
        padding: 6px 14px;
        border-radius: 4px;
        min-height: 22px;
    }}
    QPushButton:hover {{ background-color: {colors["accent_hover"]}; }}
    QPushButton:disabled {{ background-color: #555; color: #aaa; }}
    QPushButton[secondary="true"] {{
        background-color: {colors["lighter_bg"]};
        color: {colors["text_primary"]};
    }}
    QPushButton[danger="true"] {{ background-color: {colors["error"]}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {colors["darker_bg"]};
        border: 1px solid {colors["border"]};
        border-radius: 4px;
        padding: 4px 8px;
        color: {colors["text_primary"]};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {colors["accent"]};
    }}
    QProgressBar {{
        background-color: {colors["darker_bg"]};
        border: 1px solid {colors["border"]};
        border-radius: 4px;
        text-align: center;
        color: {colors["text_primary"]};
        min-height: 22px;
    }}
    QProgressBar::chunk {{
        background-color: {colors["accent"]};
        border-radius: 3px;
    }}
    QTabWidget::pane {{
        border: 1px solid {colors["border"]};
        background: {colors["window_bg"]};
    }}
    QTabBar::tab {{
        background: {colors["darker_bg"]};
        color: {colors["text_secondary"]};
        padding: 6px 14px;
        border: 1px solid {colors["border"]};
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: {colors["accent"]};
        color: white;
    }}
    QTableWidget {{
        background-color: {colors["darker_bg"]};
        gridline-color: {colors["border"]};
        color: {colors["text_primary"]};
    }}
    QHeaderView::section {{
        background-color: {colors["lighter_bg"]};
        color: {colors["text_primary"]};
        padding: 4px;
        border: none;
    }}
    QTextEdit {{
        background-color: {colors["darker_bg"]};
        color: {colors["text_primary"]};
        border: 1px solid {colors["border"]};
        border-radius: 4px;
        font-family: 'Cascadia Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono';
        font-size: 12px;
    }}
    QStatusBar {{
        background-color: {colors["darker_bg"]};
        color: {colors["text_secondary"]};
    }}
    QSplitter::handle {{ background-color: {colors["border"]}; }}
    QLabel {{ color: {colors["text_primary"]}; }}
    QCheckBox {{ color: {colors["text_primary"]}; }}
    QMenuBar {{ background-color: {colors["darker_bg"]}; color: {colors["text_primary"]}; }}
    QMenuBar::item:selected {{ background-color: {colors["accent"]}; color: white; }}
    QMenu {{ background-color: {colors["darker_bg"]}; color: {colors["text_primary"]};
            border: 1px solid {colors["border"]}; }}
    QMenu::item:selected {{ background-color: {colors["accent"]}; color: white; }}
    """


# ============================================================
# G. Превью KiCad-файлов
# ============================================================

class KicadPreviewDialog(QDialog):
    """Диалог превью KiCad-файла (.kicad_sch).

    Показывает структуру: компоненты, метки, соединения.
    """

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setWindowTitle(f"Превью KiCad: {Path(file_path).name}")
        self.resize(800, 600)
        self._build_ui()
        self._load_file()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Заголовок
        title = QLabel(f"📁 {Path(self.file_path).name}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        path_label = QLabel(f"Путь: {self.file_path}")
        path_label.setStyleSheet("color: #858585; font-size: 11px;")
        layout.addWidget(path_label)

        # Таблица компонентов
        layout.addWidget(QLabel("📦 Компоненты:"))
        self.components_table = QTableWidget(0, 4)
        self.components_table.setHorizontalHeaderLabels(
            ["Reference", "Value", "Footprint", "Datasheet"]
        )
        header = self.components_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.components_table, stretch=1)

        # Статистика
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #858585; font-size: 12px;")
        layout.addWidget(self.stats_label)

        # Кнопка закрытия
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def _load_file(self) -> None:
        """Парсить .kicad_sch файл и извлечь компоненты."""
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # KiCad v5/v6: ищем (symbol ...) блоки
            # Простая эвристика для v6: (symbol (lib_id "...") (at ...) (property "Reference" "...") ...)
            components = []

            # Pattern для KiCad v6 symbol blocks
            symbol_pattern = re.compile(
                r'\(symbol\s*\(lib_id\s*"([^"]*)"\)'
                r'.*?\(property\s*"Reference"\s*"([^"]*)"\)'
                r'.*?\(property\s*"Value"\s*"([^"]*)"\)'
                r'(?:.*?\(property\s*"Footprint"\s*"([^"]*)"\))?'
                r'(?:.*?\(property\s*"Datasheet"\s*"([^"]*)"\))?',
                re.DOTALL
            )
            for m in symbol_pattern.finditer(content):
                lib_id, ref, value, footprint, datasheet = m.groups()
                components.append({
                    "reference": ref or "",
                    "value": value or "",
                    "footprint": footprint or "",
                    "datasheet": datasheet or "",
                })

            # Fallback для KiCad v5
            if not components:
                v5_pattern = re.compile(
                    r'\(symbol\s+(\S+)\s+\(reference\s+"([^"]*)"\)\s+\(value\s+"([^"]*)"\)'
                )
                for m in v5_pattern.finditer(content):
                    lib_id, ref, value = m.groups()
                    components.append({
                        "reference": ref,
                        "value": value,
                        "footprint": lib_id,
                        "datasheet": "",
                    })

            # Заполняем таблицу
            self.components_table.setRowCount(0)
            for i, comp in enumerate(components):
                self.components_table.insertRow(i)
                self.components_table.setItem(i, 0, QTableWidgetItem(comp["reference"]))
                self.components_table.setItem(i, 1, QTableWidgetItem(comp["value"]))
                self.components_table.setItem(i, 2, QTableWidgetItem(comp["footprint"]))
                self.components_table.setItem(i, 3, QTableWidgetItem(comp["datasheet"]))

            # Статистика
            file_size = len(content)
            self.stats_label.setText(
                f"Всего компонентов: {len(components)} | "
                f"Размер файла: {file_size:,} байт | "
                f"Строк: {content.count(chr(10))}"
            )

        except Exception as e:
            self.stats_label.setText(f"Ошибка при чтении: {e}")


# ============================================================
# H. История последних config.yaml
# ============================================================

class RecentConfigsManager:
    """Управление списком последних config.yaml файлов.

    Использование:
        manager = RecentConfigsManager()
        manager.add("/path/to/config.yaml")
        recent = manager.get_recent(5)  # последние 5
    """

    MAX_ITEMS = 10

    def __init__(self, settings_file: str | Path | None = None):
        if settings_file is None:
            if getattr(sys, "frozen", False):
                settings_file = Path(sys.executable).parent / ".corpus_builder_recent.json"
            else:
                settings_file = Path.home() / ".corpus_builder_recent.json"
        self.settings_file = Path(settings_file)

    def add(self, path: str) -> None:
        """Добавить путь в начало списка."""
        recent = self.get_all()
        # Удаляем дубликат, если есть
        recent = [r for r in recent if r != path]
        recent.insert(0, path)
        recent = recent[:self.MAX_ITEMS]
        self._save(recent)

    def get_recent(self, count: int = 5) -> list[str]:
        """Вернуть последние N путей."""
        return self.get_all()[:count]

    def get_all(self) -> list[str]:
        """Вернуть все сохранённые пути (только существующие файлы)."""
        try:
            if not self.settings_file.exists():
                return []
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            paths = data.get("recent_configs", [])
            # Фильтруем только существующие файлы
            return [p for p in paths if Path(p).exists()]
        except Exception:
            return []

    def clear(self) -> None:
        """Очистить список."""
        try:
            self.settings_file.unlink()
        except FileNotFoundError:
            pass

    def _save(self, paths: list[str]) -> None:
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump({"recent_configs": paths}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.debug(f"Cannot save recent configs: {e}")


# ============================================================
# I. Прогресс с ETA в статус-баре
# ============================================================

class ProgressBarWithETA(QProgressBar):
    """Прогресс-бар с ETA.

    Показывает: 45% | 150/1000 | ETA: 5m 30s | 2.3 URL/s
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_time = time.time()
        self._done = 0
        self._total = 0

    def set_progress(self, current: int, total: int) -> None:
        """Обновить прогресс и пересчитать ETA."""
        self._done = current
        self._total = total
        if total > 0:
            self.setValue(int(current * 100 / total))
            elapsed = time.time() - self._start_time
            rate = current / elapsed if elapsed > 0 else 0
            remaining = (total - current) / rate if rate > 0 else 0
            eta_str = self._format_duration(remaining)
            rate_str = f"{rate:.1f}" if rate > 0 else "?"
            self.setFormat(f"{current}/{total} | ETA: {eta_str} | {rate_str} URL/s")
        else:
            self.setFormat("")

    def reset_timer(self) -> None:
        """Сбросить таймер."""
        self._start_time = time.time()
        self._done = 0
        self.reset()

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 0:
            return "?"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"


# ============================================================
# J. Сравнение корпусов в GUI (diff dialog)
# ============================================================

class DiffCorpusDialog(QDialog):
    """Диалог сравнения двух корпусов (JSONL)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение корпусов")
        self.resize(900, 600)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel("📊  Сравнение корпусов")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #007acc;")
        layout.addWidget(title)

        # Выбор файлов
        form = QFormLayout()

        self.old_file_edit = QLineEdit()
        self.old_file_edit.setPlaceholderText("Старый корпус (corpus_old.jsonl)")
        btn_old = QPushButton("Обзор...")
        btn_old.setProperty("secondary", True)
        btn_old.clicked.connect(lambda: self._browse(self.old_file_edit, "Старый корпус"))
        old_row = QHBoxLayout()
        old_row.addWidget(self.old_file_edit)
        old_row.addWidget(btn_old)
        form.addRow("Старый:", old_row)

        self.new_file_edit = QLineEdit()
        self.new_file_edit.setPlaceholderText("Новый корпус (corpus_new.jsonl)")
        btn_new = QPushButton("Обзор...")
        btn_new.setProperty("secondary", True)
        btn_new.clicked.connect(lambda: self._browse(self.new_file_edit, "Новый корпус"))
        new_row = QHBoxLayout()
        new_row.addWidget(self.new_file_edit)
        new_row.addWidget(btn_new)
        form.addRow("Новый:", new_row)
        layout.addLayout(form)

        # Кнопка сравнения
        self.btn_compare = QPushButton("🔄  Сравнить")
        self.btn_compare.clicked.connect(self._on_compare)
        layout.addWidget(self.btn_compare)

        # Результат
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text, stretch=1)

        # Кнопка сохранения HTML
        self.btn_save_html = QPushButton("💾  Сохранить HTML-отчёт")
        self.btn_save_html.setProperty("secondary", True)
        self.btn_save_html.clicked.connect(self._save_html)
        self.btn_save_html.setEnabled(False)
        layout.addWidget(self.btn_save_html, alignment=Qt.AlignRight)

        self._last_result = None

    def _browse(self, edit: QLineEdit, title: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, "", "JSONL (*.jsonl *.jsonl.gz);;All files (*)")
        if path:
            edit.setText(path)

    def _on_compare(self) -> None:
        old_path = self.old_file_edit.text().strip()
        new_path = self.new_file_edit.text().strip()
        if not old_path or not new_path:
            QMessageBox.warning(self, "Не выбраны файлы", "Укажите оба файла корпусов.")
            return
        if not Path(old_path).exists() or not Path(new_path).exists():
            QMessageBox.warning(self, "Файлы не найдены", "Один или оба файла не существуют.")
            return

        try:
            from .diff import diff_corpora
            self._last_result = diff_corpora(old_path, new_path)
            self._display_result(self._last_result, old_path, new_path)
            self.btn_save_html.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _display_result(self, result: dict, old_name: str, new_name: str) -> None:
        stats = result["stats"]
        html = f"""
        <h2>Результат сравнения</h2>
        <p><b>Старый:</b> {Path(old_name).name} ({stats['total_old']} записей)<br>
           <b>Новый:</b> {Path(new_name).name} ({stats['total_new']} записей)</p>
        <table border="1" cellpadding="8" style="border-collapse: collapse;">
        <tr><td style="color: green; font-size: 18px;">➕ Добавлено: {stats['total_added']}</td></tr>
        <tr><td style="color: red; font-size: 18px;">➖ Удалено: {stats['total_removed']}</td></tr>
        <tr><td style="color: orange; font-size: 18px;">~ Изменено: {stats['total_changed']}</td></tr>
        </table>
        """
        if result.get("added"):
            html += "<h3>Добавленные записи (топ-5):</h3><ul>"
            for r in result["added"][:5]:
                url = r.get("source_url", "")[:80]
                html += f"<li>{url}</li>"
            html += "</ul>"

        if result.get("removed"):
            html += "<h3>Удалённые записи (топ-5):</h3><ul>"
            for r in result["removed"][:5]:
                url = r.get("source_url", "")[:80]
                html += f"<li>{url}</li>"
            html += "</ul>"

        self.result_text.setHtml(html)

    def _save_html(self) -> None:
        if not self._last_result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить HTML-отчёт", "corpus_diff.html", "HTML (*.html)"
        )
        if not path:
            return
        try:
            from .diff import _generate_html_report
            old_name = Path(self.old_file_edit.text()).name
            new_name = Path(self.new_file_edit.text()).name
            html = _generate_html_report(self._last_result, old_name, new_name)
            Path(path).write_text(html, encoding="utf-8")
            QMessageBox.information(self, "Сохранено", f"HTML-отчёт сохранён:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))


# ============================================================
# K. Встроенный редактор YAML с подсветкой
# ============================================================

class YamlEditorDialog(QDialog):
    """Диалог с YAML-редактором и подсветкой синтаксиса.

    Использование:
        dialog = YamlEditorDialog("/path/to/config.yaml")
        if dialog.exec() == QDialog.Accepted:
            # файл сохранён
            pass
    """

    def __init__(self, file_path: str | None = None, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setWindowTitle("Редактор config.yaml")
        self.resize(900, 700)
        self._build_ui()
        if file_path and Path(file_path).exists():
            self._load_file(file_path)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Заголовок
        title = QLabel("📝  Редактор config.yaml")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #007acc;")
        layout.addWidget(title)

        # Текстовый редактор с подсветкой
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.highlighter = _YamlHighlighter(self.editor.document())
        layout.addWidget(self.editor, stretch=1)

        # Кнопки
        btn_row = QHBoxLayout()

        self.btn_load = QPushButton("📂  Открыть...")
        self.btn_load.setProperty("secondary", True)
        self.btn_load.clicked.connect(self._on_load)
        btn_row.addWidget(self.btn_load)

        self.btn_validate = QPushButton("✅  Проверить")
        self.btn_validate.setProperty("secondary", True)
        self.btn_validate.clicked.connect(self._on_validate)
        btn_row.addWidget(self.btn_validate)

        btn_row.addStretch()

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("💾  Сохранить")
        self.btn_save.setStyleSheet(
            "background-color: #007acc; color: white; font-weight: bold; padding: 8px 20px; min-height: 28px;"
        )
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

    def _load_file(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.setPlainText(f.read())
            self.file_path = path
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

    def _on_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть config.yaml", "", "YAML (*.yaml *.yml)"
        )
        if path:
            self._load_file(path)

    def _on_save(self) -> None:
        if not self.file_path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить config.yaml", "config.yaml", "YAML (*.yaml *.yml)"
            )
            if not path:
                return
            self.file_path = path

        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            QMessageBox.information(self, "Сохранено", f"Файл сохранён:\n{self.file_path}")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _on_validate(self) -> None:
        import yaml
        text = self.editor.toPlainText()
        try:
            data = yaml.safe_load(text)
            if data is None:
                QMessageBox.warning(self, "Проверка", "Конфиг пустой")
            else:
                keys = list(data.keys()) if isinstance(data, dict) else []
                QMessageBox.information(
                    self, "YAML валиден",
                    f"Синтаксис корректен.\nВерхнеуровневые ключи: {keys}"
                )
        except yaml.YAMLError as e:
            QMessageBox.critical(self, "Ошибка YAML", str(e))


class _YamlHighlighter(QSyntaxHighlighter):
    """Подсветка синтаксиса YAML (VS Code-like)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_formats()

    def _setup_formats(self) -> None:
        self.key_format = QTextCharFormat()
        self.key_format.setForeground(QColor("#569CD6"))
        self.key_format.setFontWeight(QFont.Bold)

        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor("#CE9178"))

        self.number_format = QTextCharFormat()
        self.number_format.setForeground(QColor("#B5CEA8"))

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor("#6A9955"))
        self.comment_format.setFontItalic(True)

        self.special_format = QTextCharFormat()
        self.special_format.setForeground(QColor("#FFD700"))

    def highlightBlock(self, text: str) -> None:
        if text.strip().startswith("#"):
            self.setFormat(0, len(text), self.comment_format)
            return

        if ":" in text and not text.strip().startswith("-"):
            colon_idx = text.find(":")
            if colon_idx > 0:
                self.setFormat(0, colon_idx, self.key_format)
                value = text[colon_idx + 1:].strip()
                if value:
                    start = colon_idx + 1 + (len(text[colon_idx + 1:]) - len(value))
                    if value.startswith('"') or value.startswith("'"):
                        self.setFormat(start, len(value), self.string_format)
                    elif value.lower() in ("true", "false", "null", "none") or value.replace(".", "").replace("-", "").replace("+", "").isdigit():
                        self.setFormat(start, len(value), self.number_format)

        if text.strip() == "---":
            self.setFormat(0, len(text), self.special_format)


# ============================================================
# L. Dashboard с метриками
# ============================================================

class DashboardDialog(QDialog):
    """Dashboard с метриками корпуса — графики и сводка.

    Использует AnalyticsWidget из analytics.py для отрисовки графиков.
    """

    def __init__(self, corpus_file: str | None = None,
                 errors_file: str | None = None, parent=None):
        super().__init__(parent)
        self.corpus_file = corpus_file
        self.errors_file = errors_file
        self.setWindowTitle("Dashboard — метрики корпуса")
        self.resize(1000, 700)
        self._build_ui()
        if corpus_file:
            self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel("📊  Dashboard")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #007acc;")
        layout.addWidget(title)

        # Кнопка обновления
        btn_refresh = QPushButton("⟳  Обновить метрики")
        btn_refresh.setProperty("secondary", True)
        btn_refresh.clicked.connect(self._refresh)
        layout.addWidget(btn_refresh)

        # Analytics widget
        try:
            from .analytics import AnalyticsWidget
            self.analytics = AnalyticsWidget()
            layout.addWidget(self.analytics.get_canvas(), stretch=1)
        except Exception as e:
            layout.addWidget(QLabel(f"Analytics недоступен: {e}"))

        # Текстовая сводка
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(150)
        layout.addWidget(self.stats_text)

    def _refresh(self) -> None:
        if not self.corpus_file or not Path(self.corpus_file).exists():
            QMessageBox.warning(self, "Нет данных", "Укажите файл корпуса.")
            return

        try:
            from .postproc.export import compute_statistics
            stats = compute_statistics(self.corpus_file)

            summary = (
                f"Всего записей: {stats['total']}\n"
                f"Дубликатов: {stats['duplicates']}\n"
                f"Суммарно символов: {stats['total_chars']:,}\n"
                f"Средняя длина: {stats['avg_chars']:,} символов\n\n"
            )
            if stats.get("by_type"):
                summary += "По типам:\n"
                for k, v in stats["by_type"].items():
                    summary += f"  {k}: {v}\n"
            if stats.get("by_language"):
                summary += "\nПо языкам:\n"
                for k, v in stats["by_language"].items():
                    summary += f"  {k}: {v}\n"

            self.stats_text.setPlainText(summary)

            # Обновляем графики
            if hasattr(self, "analytics"):
                self.analytics.refresh(self.corpus_file, self.errors_file)

        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))


# ============================================================
# M. Мастер первого запуска (wizard)
# ============================================================

class FirstRunWizard(QWizard):
    """Мастер первого запуска — пошаговая настройка.

    Шаги:
      1. Приветствие
      2. Выбор источников
      3. Настройка качества
      4. Токены (GitHub/StackExchange)
      5. Готово
    """

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Мастер первого запуска — CorpusBuilder")
        self.resize(600, 500)

        self.addPage(self._welcome_page())
        self.addPage(self._sources_page())
        self.addPage(self._quality_page())
        self.addPage(self._tokens_page())
        self.addPage(self._finish_page())

    def _welcome_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Добро пожаловать в CorpusBuilder!")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Этот мастер поможет настроить программу за 4 простых шага.\n\n"
            "CorpusBuilder — сборщик сырого корпуса для pretraining LLM\n"
            "с дедупликацией, нормализацией и фильтрацией качества.\n\n"
            "Нажмите «Далее» для продолжения."
        ))
        return page

    def _sources_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Шаг 1: Источники")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Какие типы источников вы планируете использовать?\n"
            "Выберите подходящие опции (можно изменить позже в настройках):"
        ))

        self.chk_html = QCheckBox("HTML (статьи, блоги)")
        self.chk_html.setChecked(True)
        layout.addWidget(self.chk_html)

        self.chk_pdf = QCheckBox("PDF (datasheet'ы, руководства)")
        self.chk_pdf.setChecked(True)
        layout.addWidget(self.chk_pdf)

        self.chk_github = QCheckBox("GitHub репозитории (KiCad, embedded)")
        self.chk_github.setChecked(True)
        layout.addWidget(self.chk_github)

        self.chk_stackexchange = QCheckBox("StackExchange (вопросы и ответы)")
        layout.addWidget(self.chk_stackexchange)

        self.chk_arxiv = QCheckBox("arXiv (научные статьи)")
        layout.addWidget(self.chk_arxiv)

        self.chk_wikipedia = QCheckBox("Wikipedia (энциклопедия)")
        layout.addWidget(self.chk_wikipedia)

        return page

    def _quality_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Шаг 2: Качество корпуса")
        layout = QFormLayout(page)

        self.spin_min_chars = QSpinBox()
        self.spin_min_chars.setRange(0, 100000)
        self.spin_min_chars.setValue(200)
        layout.addRow("Мин. длина текста:", self.spin_min_chars)

        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["bilingual (RU+EN)", "ru (только русский)", "en (только английский)", "multi (все языки)"])
        layout.addRow("Язык корпуса:", self.combo_lang)

        self.chk_spam = QCheckBox("Фильтровать спам/рекламу")
        self.chk_spam.setChecked(True)
        layout.addRow(self.chk_spam)

        self.chk_dedup = QCheckBox("Дедупликация (MinHash)")
        self.chk_dedup.setChecked(True)
        layout.addRow(self.chk_dedup)

        return page

    def _tokens_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Шаг 3: API токены (опционально)")
        layout = QFormLayout(page)

        layout.addRow(QLabel(
            "Токены нужны для повышенных лимитов API.\n"
            "Можно пропустить — программа будет работать, но с базовыми лимитами."
        ))

        self.edit_github_token = QLineEdit()
        self.edit_github_token.setEchoMode(QLineEdit.Password)
        self.edit_github_token.setPlaceholderText("ghp_xxx... (github.com/settings/tokens)")
        layout.addRow("GitHub Token:", self.edit_github_token)

        self.edit_se_key = QLineEdit()
        self.edit_se_key.setEchoMode(QLineEdit.Password)
        self.edit_se_key.setPlaceholderText("опционально (stackapps.com)")
        layout.addRow("StackExchange Key:", self.edit_se_key)

        return page

    def _finish_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Готово!")
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Настройки сохранены. Вы можете изменить их в любой момент\n"
            "через меню «Настройки → Все настройки...» (Ctrl+,)\n\n"
            "Нажмите «Готово» для запуска программы."
        ))
        return page

    def accept(self) -> None:
        """Применить настройки из мастера."""
        # Качество
        self.settings.quality.min_chars = self.spin_min_chars.value()
        lang_idx = self.combo_lang.currentIndex()
        lang_values = ["bilingual", "ru", "en", "multi"]
        self.settings.quality.language = lang_values[lang_idx]
        self.settings.quality.spam_check = self.chk_spam.isChecked()
        self.settings.dedup.minhash = self.chk_dedup.isChecked()

        # Токены
        if self.edit_github_token.text():
            self.settings.github.token = self.edit_github_token.text()
        if self.edit_se_key.text():
            self.settings.stackexchange.api_key = self.edit_se_key.text()

        self.settings.save()
        self.settings.setup_env_vars()
        super().accept()


# ============================================================
# N. Локализация RU/EN
# ============================================================

TRANSLATIONS = {
    "ru": {
        "col_index": "#",
        "col_url": "URL",
        "col_type": "Тип",
        "col_length": "Длина",
        "col_language": "Язык",
        "col_quality": "Quality",
        "menu_search_log": "Поиск по логу",
        # Меню Файл
        "menu_file": "Файл",
        "menu_open_config": "Открыть config.yaml...",
        "menu_open_output": "Открыть папку корпуса",
        "menu_export_hf": "Экспорт в HuggingFace...",
        "menu_export_parquet": "Экспорт в Parquet...",
        "menu_quit": "Выход",
        # Меню Настройки
        "menu_settings": "Настройки",
        "menu_all_settings": "⚙  Все настройки...",
        "menu_export_settings": "📤  Экспорт настроек...",
        "menu_import_settings": "📥  Импорт настроек...",
        "menu_reset_settings": "↺  Сбросить к defaults",
        # Меню Вид
        "menu_view": "Вид",
        "menu_theme": "Тема",
        "theme_dark": "Тёмная",
        "theme_light": "Светлая",
        "theme_material_blue": "Material Blue",
        "theme_material_green": "Material Green",
        "theme_material_purple": "Material Purple",
        "menu_toggle_log": "Показать/скрыть лог",
        # Меню Действия
        "menu_actions": "Действия",
        "menu_crawl": "▶  Запустить краулинг",
        "menu_postprocess": "⚙  Пост-обработка",
        "menu_stop": "⏹  Остановить",
        "menu_generate_config": "✨  Создать config.yaml...",
        # Меню Справка
        "menu_help": "Справка",
        "menu_about": "О программе",
        "menu_docs": "Документация",
        "menu_stats": "Статистика корпуса",
        # Кнопки
        "btn_generate": "⚙  Сгенерировать config.yaml",
        "btn_clear": "🗑  Очистить список",
        "btn_settings": "⚙  Настройки...",
        "btn_save": "💾  Сохранить",
        "btn_cancel": "Отмена",
        # Прочее
        "config_label": "config.yaml:",
        "output_label": "Папка корпуса:",
        "progress_label": "Готов к запуску",
        # Меню
        "menu_file_title": "Файл",
        "menu_settings_title": "Настройки",
        "menu_view_title": "Вид",
        "menu_actions_title": "Действия",
        "menu_help_title": "Справка",
        "menu_tools_title": "Инструменты",
        "menu_recent": "Недавние config.yaml",
        "menu_theme_title": "Тема",
        "menu_language": "🌐  Язык / Language",
        "menu_lang_ru": "Русский",
        "menu_lang_en": "English",
        "menu_recent_clear": "Очистить список",
        "menu_check_update": "🔄  Проверить обновления",
        "menu_diff": "📊  Сравнить корпуса...",
        "menu_yaml": "📝  Редактор YAML...",
        "menu_dashboard": "📈  Dashboard...",
        "menu_auto_discover": "🔄  Авто-поиск источников...",
        "menu_merge_config": "🔗  Объединить config.yaml...",
        # Tray
        "tray_show": "Показать окно",
        "tray_quit": "Выход",
        # Group boxes
        "group_config": "1. Конфигурация",
        "group_actions": "2. Действия",
        "group_progress": "3. Прогресс",
        # Labels
        "label_config": "config.yaml:",
        "label_output": "Папка корпуса:",
        "label_options": "Опции:",
        "label_export": "Экспорт:",
        # Buttons
        "btn_browse": "Обзор...",
        "btn_open_folder": "Открыть папку",
        "btn_merge_config": "🔗  Объединить config",
        "btn_auto_discover": "🔄  Авто-поиск источников",
        "btn_generate_config": "✨  Создать config.yaml",
        "btn_crawl": "▶  Запустить краулинг",
        "btn_postprocess": "⚙  Пост-обработка",
        "btn_stop": "⏹  Остановить",
        "btn_export_hf": "⬇  Экспорт HF",
        "btn_export_parquet": "⬇  Экспорт Parquet",
        "btn_clear_log": "Очистить лог",
        "btn_refresh_stats": "⟳ Обновить статистику",
        # Checkboxes
        "chk_resume": "Продолжить (resume)",
        "chk_retry": "Повторить упавшие",
        # Progress
        "progress_ready": "Готов к запуску",
        # Status
        "status_ready": "Готов",
        "status_working": "Работаю...",
        "status_downloading": "Скачивание обновления...",
        "status_updated": "Обновлено",
        "status_update_failed": "Обновление не удалось",
        "status_checking": "Проверка коммитов на GitHub...",
        "status_files_updated": "Обновлено файлов",
        # Tabs
        "tab_log": "Лог",
        "tab_records": "Последние записи",
        "tab_stats": "Статистика",
        # Window
        "window_title": "Corpus Builder — сбор корпуса для LLM",
        # Update dialog
        "update_title": "Обновление из коммита",
        "update_commit": "Коммит:",
        "update_author": "Автор:",
        "update_message": "Сообщение:",
        "update_apply": "Применить обновление?",
        "update_apply_desc": "Будут скачаны и заменены .py файлы.",
        "update_restart": "Программу нужно будет перезапустить.",
        "update_applied_title": "Обновление применено",
        "update_applied_msg": "Успешно обновлено .py файлов:",
        "update_restart_msg": "Пожалуйста, перезапустите CorpusBuilder для применения изменений.",
        "update_no_updates": "Нет доступных обновлений.",
        "update_latest": "У вас последняя версия (все коммиты применены).",
        "update_available": "Доступно обновление",
        "update_version": "Версия",
        "update_available_desc": "доступна.",
        "update_apply_q": "Применить обновление?",
        "update_error": "Ошибка обновления",
        "update_error_desc": "Не удалось применить обновление:",
        "update_download_full": "Скачайте полный дистрибутив с GitHub.",
        "update_check_error": "Ошибка проверки",
        # Toast
        "toast_update_title": "Доступно обновление",
        "toast_update_desc": "Нажмите для обновления.",
        # Other
        "ok": "OK",
        "cancel": "Отмена",
        "yes": "Да",
        "no": "Нет",
        "error": "Ошибка",
        "warning": "Предупреждение",
        "info": "Информация",
        "busy": "Занято",
        "busy_crawl": "Дождитесь завершения краулинга перед открытием мастера.",
        "busy_task": "Дождитесь завершения текущей задачи.",
        "no_config": "Нет конфигурации",
        "no_config_desc": "Сначала выберите config.yaml",
        "no_corpus": "Нет корпуса",
        "no_corpus_desc": "Сначала запустите краулинг.",
        "no_corpus_final": "Сначала запустите пост-обработку.",
        "file_not_found": "Файл не выбран",
        "file_not_found_desc": "Укажите путь к Excel/CSV-файлу на вкладке \"Excel / CSV\".",
        "no_data": "Нет данных",
        "no_data_desc": "Укажите хотя бы одну тему/тег/категорию.",
        "about_title": "О программе",
        "about_text": "CorpusBuilder — сборщик сырого корпуса для pretraining LLM\nВерсия: 0.2.0\nПоддерживаемые источники: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia",
        "config_loaded": "Конфигурация загружена",
        "crawl_started": "Запуск краулинга...",
        "crawl_finished": "Готово",
        "crawl_stopped": "Останавливаю после текущего URL...",
        "postprocess_started": "Запуск пост-обработки...",
        "config_generator_done": "Мастер создания config.yaml завершён",
        "auto_discover_done": "Мастер первого запуска завершён",
        "toast_complete": "Задача завершена",
        "first_run": "Мастер первого запуска",
        "lang_changed_title": "Язык изменён",
        "thread_busy_start": "Уже выполняется задача. Остановите её перед запуском новой.",
        "export_failed": "Ошибка экспорта",
        "export_ok": "Экспортировано",
        "export_ok_desc": "Файлы созданы:",
        "config_broken": "Ошибка конфигурации",
        "update_none": "Нет доступных обновлений.",
        "theme_changed": "Тема изменена",
        "theme_restart": "Перезапустите CorpusBuilder для полного применения темы.",
        "settings_reset_ok": "Настройки сброшены к defaults.",

        # Settings dialog
        # --- фиксы ревью: остановки, история, конфиг (B) ---
        "confirm_title": "Подтверждение",
        "stats_calculating": "Считаю статистику…",
        "eta_log_hint": "Оценка: минимум {minutes} мин на вежливые задержки (request_delay). Для ускорения — async-краулинг в Настройках → Crawling.",
        "stop_waiting_stage": "Останавливаюсь: дожидаюсь конца текущей стадии…",
        "stop_hint_stage": "Первое нажатие — корректная остановка на границе стадии. Повторное — прервать принудительно.",
        "btn_stop_forced": "⏹  Прервать сейчас",
        "stop_hint": "Первое нажатие — корректная остановка после текущего URL (до {minutes} мин). Повторное — прервать принудительно.",
        "stop_waiting": "Останавливаюсь: жду завершения текущего URL (таймаут {minutes} мин). Нажмите ещё раз, чтобы прервать принудительно.",
        "stop_force_title": "Прервать принудительно?",
        "stop_force_text": "Поток краулинга будет прерван немедленно. Последствия: незавершённая запись может попасть в конец JSONL (битые строки пост-обработка пропускает) и часть скачанного файла может остаться в .tmp.\n\nПрерывать?",
        "stop_terminated": "Поток прерван принудительно (terminate).",
        "col_errors": "Ошибки",
        "close_running_text": "Сбор ещё идёт. Что сделать?",
        "close_to_tray": "Свернуть в трей (сбор продолжится)",
        "close_stop_quit": "Остановить и выйти",
        "tray_running_msg": "Сбор продолжается в фоне. Двойной клик по иконке — показать окно.",
        "quit_running_text": "Краулинг ещё идёт. Остановить и выйти?",
        "resume_warn_title": "Внимание:_corpus_file будет перезаписан",
        "resume_warn_text": ("Флажок «Продолжить (resume)» снят, а в {file} уже лежит "
                             "{n} записей.\nЗапуск перезапишет этот файл.\n\n"
                             "Что сделать?"),
        "resume_warn_eta": "Ожидаемая нижняя граница времени: ~{minutes} мин (только вежливые задержки).",
        "resume_warn_run": "Перезаписать и запустить",
        "resume_warn_resume_instead": "Включить resume и дописать",
        "cfg_no_file_loaded": "config.yaml не загружен — показаны настройки по умолчанию",
        "cfg_file_broken": "config.yaml не читается",
        "cfg_effective_note": "Эффективный конфиг (то, что реально поедет в движок)",
        "cfg_overridden": "Настройки приложения перекрывают {n} полей(е) config.yaml:",
        "cfg_where": "«→» означает: значение из файла заменено значением из настроек приложения. Отключить: Настройки → «Не перекрывать config.yaml».",
        "cfg_no_overrides": " config.yaml не перекрывается — эффективный конфиг совпадает с файлом",
        "cfg_valid": "Конфигурация корректна",
        "export_secrets_ask": "В файле настроек есть заполненные секреты ({n}). "
                              "Экспортировать их как есть?",
        "export_secrets_hidden": "Скрыты поля с секретами: {fields}. "
                                 "Перенесите их вручную или экспортируйте ещё раз "
                                 "с подтверждением.",
        "exported_to": "Настройки сохранены в:\n{path}",
        "cfg_valid_title": "Проверка конфигурации",
        "cfg_invalid_title": "Ошибки конфигурации",
        "cfg_invalid_found": "Найдено проблем: {n}",
        "menu_save_config": "💾  Сохранить config.yaml…",
        "menu_effective_config": "🔍  Эффективный config (что реально применится)…",
        "menu_validate_config": "✔  Проверить config.yaml…",
        "menu_run_history": "🕘  Журнал прогонов…",
        "menu_last_metrics": "📊  Метрики последней задачи",
        "menu_shortcuts": "⌨  Горячие клавиши (F1)",
        "save_config_ok": "config.yaml сохранён",
        "save_config_fail": "Не удалось сохранить конфигурацию",
        "history_empty": "Журнал прогонов пуст ({p})",
        "history_text": "Последние прогоны (всего записей: {n}):",
        "no_metrics": "Метрик ещё нет — запустите краулинг или пост-обработку.",
        "metrics_short": "Обработано: {p}, ошибок: {e}. Полный JSON — в «подробностях».",
        "shortcuts_text": "Горячие клавиши (действия, у которых есть сочетания):",
        "settings_title": "Настройки CorpusBuilder",
        "settings_header": "⚙  Настройки программы",
        "settings_subtitle": "Все настройки сохраняются автоматически и применяются к следующим запускам.",
        "settings_reset": "↺  Сбросить к defaults",
        "settings_export": "📤  Экспорт настроек",
        "settings_import": "📥  Импорт настроек",
        "settings_saved": "Настройки сохранены и будут применены к следующим запускам.",
        "settings_applied": "Настройки применены",

        # Settings dialog — all checkboxes/labels
        "st_window_size": "Размер окна",
        "st_width": "Ширина:",
        "st_height": "Высота:",
        "st_use_cache": "Использовать HTTP-кэш (requests-cache)",
        "st_robots": "Уважать robots.txt",
        "st_browser_headers": "Использовать browser-like заголовки (Sec-Fetch-*)",
        "st_proxy": "Прокси (опционально)",
        "st_use_proxy": "Использовать прокси",
        "st_proxy_list": "Список прокси (через запятую):",
        "st_download_images": "Скачивать изображения",
        "st_ocr": "Включить OCR для скан-PDF (нужен tesseract)",
        "st_extract_tables": "Извлекать таблицы через pdfplumber",
        "st_two_column": "Авто-определение двухколоночной вёрстки",
        "st_filter_schematics": "Фильтровать схемы через OCR-ключевые слова",
        "st_use_toc": "Структурировать контент по TOC",
        "st_crawl_issues": "Извлекать Issues/PR",
        "st_crawl_wiki": "Клонировать Wiki",
        "st_crawl_docs": "Парсить директорию docs/",
        "st_spam": "Проверять на спам/рекламу",
        "st_perplexity_group": "Perplexity-фильтр (опционально, требует kenlm)",
        "st_perplexity": "Включить perplexity-фильтр",
        "st_max_perplexity": "Макс. perplexity:",
        "st_perplexity_model": "Путь к kenlm-модели (.binary):",
        "st_dedup_exact": "Точная дедупликация (sha1)",
        "st_dedup_minhash": "Нечёткая дедупликация (MinHash LSH)",
        "st_dedup_images": "Дедупликация изображений (sha1)",
        "st_streaming": "Streaming MinHash (для больших корпусов, экономит RAM)",
        "st_incremental": "Incremental dedup (сохранять LSH-индекс между прогонами)",
        "st_async": "Использовать асинхронный краулинг по умолчанию",
        "st_gzip": "Сжимать корпус в .jsonl.gz (экономия места 4-6x)",
        "st_parallel": "Параллельная пост-обработка (multiprocessing)",
        "st_show_progress": "Показывать прогресс-бар в терминале (tqdm)",
        # Wikipedia tab
        "wiki_lang_label": "Язык Wikipedia:",
        "wiki_categories_label": "Категории (через запятую):",
        "wiki_max_label": "Макс. статей на категорию:",
        "wiki_depth_label": "Глубина обхода подкатегорий:",
        "wiki_hint": "💡 Примеры категорий:\n  EN: Electronics, Printed circuit boards, Operational amplifiers\n  RU: Электроника, Печатные платы, Радиоэлектроника\n\nСм. полный список: en.wikipedia.org/wiki/Category:Electronics",

        # Auto-Discover dialog
        "ad_title": "🔄 Авто-поиск источников",
        "ad_presets": "🎯 Быстрый старт (пресеты)",
        "ad_select_preset": "Выбрать профиль:",
        "ad_apply": "Применить",
        "ad_manual": "⚙ Ручная настройка",
        "ad_github_topics": "GitHub topics:",
        "ad_se_tags": "StackExchange tags:",
        "ad_wiki_cats": "Wikipedia categories:",
        "ad_max_per_source": "Макс. источников с платформы:",
        "ad_progress": "Прогресс",
        "ad_search": "🔍  Начать поиск",
        "ad_stop": "⏹  Остановить",
        "ad_save": "💾  Сохранить config.yaml",
        "ad_found_sources": "Найденные источники:",
        # Merge Config dialog
        "mc_title": "🔗 Объединить config.yaml",
        "mc_files": "📎 Файлы для объединения",
        "mc_add_files": "➕  Добавить файлы...",
        "mc_remove": "➖  Удалить выбранные",
        "mc_clear": "🗑  Очистить список",
        "mc_options": "⚙ Опции дедупликации",
        "mc_exact": "Точное совпадение URL",
        "mc_canonical": "Канонизированный URL",
        "mc_merge_cats": "Сливать категории из дубликатов",
        "mc_merge": "🔗  Объединить",
        "mc_result": "📊 Результат",
        "mc_stats_by_file": "Статистика по файлам:",
        "mc_save": "💾  Сохранить объединённый config.yaml",

        # Fine-tuning window
        "ft_window_title": "CorpusBuilder — Fine-Tuning",
        "ft_generate": "🎯  Сгенерировать инструкции",
        "ft_export": "📤  Экспорт",
        "ft_settings": "Настройки Fine-Tuning",
        "ft_max_per_type": "Макс. пар на тип:",
        "ft_min_prompt": "Мин. prompt (chars):",
        "ft_max_prompt": "Макс. prompt (chars):",
        "ft_min_completion": "Мин. completion (chars):",
        "ft_max_completion": "Макс. completion (chars):",
        "ft_balance": "Балансировать по типам",
        "ft_pii": "Удалить PII (email/телефоны)",
        "ft_col_type": "Тип",
        "ft_col_prompt": "Prompt",
        "ft_col_completion": "Completion",
        "ft_col_source": "Источник",
        "ft_tab_preview": "Превью пар",
        "ft_tab_stats": "Статистика",
        "ft_no_pairs": "Сначала сгенерируйте инструкции",
        "ft_export_dir": "Куда экспортировать",
        "ft_token_limits": "Токен-лимиты (вместо chars)",
        "ft_token_limits_tooltip": "Использовать tiktoken для подсчёта токенов и фильтрации по token-лимитам вместо char-лимитов. Точнее для русского текста.",
        "ft_corpus_validation_warning": "Предупреждение по корпусу:",
        "ft_continue_anyway": "Продолжить всё равно?",
        "lang_changed_msg": "Язык интерфейса изменён. Перезапустите приложение для полного применения.",
        # --- подписи окна Fine-Tune (B7: раньше не было в RU) ---
        "ft_completion_full": "Полный completion",
        "ft_conversation": "Диалог",
        "ft_corpus_jsonl": "Файл корпуса (JSONL)",
        "ft_existing_corpus": "Существующий корпус:",
        "ft_exported_with_split": "Экспортировано с разбиением train/val",
        "ft_html_report": "HTML-отчёт",
        "ft_html_report_saved": "HTML-отчёт сохранён",
        "ft_menu_export_html": "Экспорт HTML-отчёта...",
        "ft_menu_lang_en": "Английский",
        "ft_menu_lang_ru": "Русский",
        "ft_menu_language": "🌐  Язык / Language",
        "ft_no_corpus_selected": "Корпус не выбран",
        "ft_no_corpus_selected_desc": "Укажите corpus_final.jsonl или сырой JSONL ниже.",
        "ft_pair_preview": "Предпросмотр пар",
        "ft_prompt_full": "Полный prompt",
        "ft_select_all": "Выбрать все",
        "ft_select_corpus_file": "Выбрать файл корпуса",
        "ft_select_none": "Снять выбор",
        "ft_split_train_val": "Разбить на train/val (90/10)",
        "ft_task_types": "Типы задач",
        "ft_use_existing_corpus": "Использовать существующий корпус",
        "ft_use_existing_corpus_desc": "Не запускать пост-обработку — взять JSONL как есть",

    },
    "en": {
        "col_index": "#",
        "col_url": "URL",
        "col_type": "Type",
        "col_length": "Length",
        "col_language": "Language",
        "col_quality": "Quality",
        "menu_search_log": "Search log",
        "menu_file": "File",
        "menu_open_config": "Open config.yaml...",
        "menu_open_output": "Open corpus folder",
        "menu_export_hf": "Export to HuggingFace...",
        "menu_export_parquet": "Export to Parquet...",
        "menu_quit": "Quit",
        "menu_settings": "Settings",
        "menu_all_settings": "⚙  All settings...",
        "menu_export_settings": "📤  Export settings...",
        "menu_import_settings": "📥  Import settings...",
        "menu_reset_settings": "↺  Reset to defaults",
        "menu_view": "View",
        "menu_theme": "Theme",
        "theme_dark": "Dark",
        "theme_light": "Light",
        "theme_material_blue": "Material Blue",
        "theme_material_green": "Material Green",
        "theme_material_purple": "Material Purple",
        "menu_toggle_log": "Show/hide log",
        "menu_actions": "Actions",
        "menu_crawl": "▶  Start crawling",
        "menu_postprocess": "⚙  Post-process",
        "menu_stop": "⏹  Stop",
        "menu_generate_config": "✨  Create config.yaml...",
        "menu_help": "Help",
        "menu_about": "About",
        "menu_docs": "Documentation",
        "menu_stats": "Corpus statistics",
        "btn_generate": "⚙  Generate config.yaml",
        "btn_clear": "🗑  Clear list",
        "btn_settings": "⚙  Settings...",
        "btn_save": "💾  Save",
        "btn_cancel": "Cancel",
        "config_label": "config.yaml:",
        "output_label": "Corpus folder:",
        "progress_label": "Ready to start",
        # Menus
        "menu_file_title": "File",
        "menu_settings_title": "Settings",
        "menu_view_title": "View",
        "menu_actions_title": "Actions",
        "menu_help_title": "Help",
        "menu_tools_title": "Tools",
        "menu_recent": "Recent config.yaml",
        "menu_theme_title": "Theme",
        "menu_language": "🌐  Language / Язык",
        "menu_lang_ru": "Русский",
        "menu_lang_en": "English",
        "menu_recent_clear": "Clear list",
        "menu_check_update": "🔄  Check for Updates",
        "menu_diff": "📊  Compare Corpora...",
        "menu_yaml": "📝  YAML Editor...",
        "menu_dashboard": "📈  Dashboard...",
        "menu_auto_discover": "🔄  Auto-Discover Sources...",
        "menu_merge_config": "🔗  Merge config.yaml...",
        # Tray
        "tray_show": "Show Window",
        "tray_quit": "Quit",
        # Group boxes
        "group_config": "1. Configuration",
        "group_actions": "2. Actions",
        "group_progress": "3. Progress",
        # Labels
        "label_config": "config.yaml:",
        "label_output": "Corpus folder:",
        "label_options": "Options:",
        "label_export": "Export:",
        # Buttons
        "btn_browse": "Browse...",
        "btn_open_folder": "Open Folder",
        "btn_merge_config": "🔗  Merge config",
        "btn_auto_discover": "🔄  Auto-Discover Sources",
        "btn_generate_config": "✨  Create config.yaml",
        "btn_crawl": "▶  Start Crawling",
        "btn_postprocess": "⚙  Post-Process",
        "btn_stop": "⏹  Stop",
        "btn_export_hf": "⬇  Export HF",
        "btn_export_parquet": "⬇  Export Parquet",
        "btn_clear_log": "Clear Log",
        "btn_refresh_stats": "⟳ Refresh Stats",
        # Checkboxes
        "chk_resume": "Resume",
        "chk_retry": "Retry failed",
        # Progress
        "progress_ready": "Ready to start",
        # Status
        "status_ready": "Ready",
        "status_working": "Working...",
        "status_downloading": "Downloading update...",
        "status_updated": "Updated",
        "status_update_failed": "Update failed",
        "status_checking": "Checking GitHub commits...",
        "status_files_updated": "Files updated",
        # Tabs
        "tab_log": "Log",
        "tab_records": "Recent Records",
        "tab_stats": "Statistics",
        # Window
        "window_title": "Corpus Builder — LLM Corpus Collection",
        # Update dialog
        "update_title": "Update from Commit",
        "update_commit": "Commit:",
        "update_author": "Author:",
        "update_message": "Message:",
        "update_apply": "Apply update?",
        "update_apply_desc": ".py files will be downloaded and replaced.",
        "update_restart": "The program needs to be restarted.",
        "update_applied_title": "Update Applied",
        "update_applied_msg": "Successfully updated .py files:",
        "update_restart_msg": "Please restart CorpusBuilder to apply changes.",
        "update_no_updates": "No updates available.",
        "update_latest": "You have the latest version (all commits applied).",
        "update_available": "Update Available",
        "update_version": "Version",
        "update_available_desc": "available.",
        "update_apply_q": "Apply update?",
        "update_error": "Update Error",
        "update_error_desc": "Failed to apply update:",
        "update_download_full": "Download the full distribution from GitHub.",
        "update_check_error": "Check Error",
        # Toast
        "toast_update_title": "Update Available",
        "toast_update_desc": "Click to update.",
        # Other
        "ok": "OK",
        "cancel": "Cancel",
        "yes": "Yes",
        "no": "No",
        "error": "Error",
        "warning": "Warning",
        "info": "Information",
        "busy": "Busy",
        "busy_crawl": "Wait for crawling to finish before opening the wizard.",
        "busy_task": "Wait for the current task to finish.",
        "no_config": "No Configuration",
        "no_config_desc": "Select a config.yaml first",
        "no_corpus": "No Corpus",
        "no_corpus_desc": "Run crawling first.",
        "no_corpus_final": "Run post-processing first.",
        "file_not_found": "File Not Selected",
        "file_not_found_desc": "Specify a path to an Excel/CSV file on the \"Excel / CSV\" tab.",
        "no_data": "No Data",
        "no_data_desc": "Specify at least one topic/tag/category.",
        "about_title": "About",
        "about_text": "CorpusBuilder — raw corpus builder for LLM pretraining\nVersion: 0.2.0\nSupported sources: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia",
        "config_loaded": "Configuration loaded",
        "crawl_started": "Starting crawl...",
        "crawl_finished": "Done",
        "crawl_stopped": "Stopping after current URL...",
        "postprocess_started": "Starting post-processing...",
        "config_generator_done": "Config generator wizard completed",
        "auto_discover_done": "First-run wizard completed",
        "toast_complete": "Task completed",
        "first_run": "First-Run Wizard",
        "lang_changed_title": "Language Changed",
        "thread_busy_start": "A task is already running. Stop it before starting another.",
        "export_failed": "Export failed",
        "export_ok": "Exported",
        "export_ok_desc": "Files created:",
        "config_broken": "Configuration error",
        "update_none": "No updates available.",
        "theme_changed": "Theme changed",
        "theme_restart": "Restart CorpusBuilder to fully apply the theme.",
        "settings_reset_ok": "Settings reset to defaults.",

        # Settings dialog
        # --- review fixes: stop, history, config (B) ---
        "confirm_title": "Confirmation",
        "stats_calculating": "Computing statistics…",
        "eta_log_hint": "Estimate: at least {minutes} min of polite delays (request_delay). Switch on async crawling in Settings → Crawling to speed it up.",
        "stop_waiting_stage": "Stopping: waiting for the current stage to finish…",
        "stop_hint_stage": "First press stops at the stage boundary. Press again to abort immediately.",
        "btn_stop_forced": "⏹  Abort now",
        "stop_hint": "First press stops gracefully after the current URL (up to {minutes} min). Press again to force-abort.",
        "stop_waiting": "Stopping: waiting for the current URL (timeout {minutes} min). Press again to abort immediately.",
        "stop_force_title": "Abort immediately?",
        "stop_force_text": "The crawl thread will be terminated at once. Consequences: a partial record may remain at the end of the JSONL (post-processing skips bad lines) and a partial download may be left as .tmp.\n\nAbort?",
        "stop_terminated": "Thread forcibly terminated.",
        "col_errors": "Errors",
        "close_running_text": "A run is still going. What should happen?",
        "close_to_tray": "Minimise to tray (run continues)",
        "close_stop_quit": "Stop and quit",
        "tray_running_msg": "Collection continues in background. Double-click the icon to show the window.",
        "quit_running_text": "Crawling is still running. Stop and quit?",
        "resume_warn_title": "Warning: the corpus file will be overwritten",
        "resume_warn_text": "“Resume” is unchecked, but {file} already contains {n} records.\nThe run will overwrite this file.\n\nWhat should happen?",
        "resume_warn_eta": "Expected lower bound: ~{minutes} min (polite delays only).",
        "resume_warn_run": "Overwrite and start",
        "resume_warn_resume_instead": "Enable resume and append",
        "cfg_no_file_loaded": "No config.yaml loaded — defaults are shown",
        "cfg_file_broken": "config.yaml cannot be read",
        "cfg_effective_note": "Effective config (what really goes into the engine)",
        "cfg_overridden": "App settings override {n} field(s) of config.yaml:",
        "cfg_where": "“→” means the file value was replaced by the app setting. Disable: Settings → “Don't override config.yaml”.",
        "cfg_no_overrides": "config.yaml is not overridden — the effective config matches the file",
        "cfg_valid": "Configuration is valid",
        "export_secrets_ask": "The settings file has {n} filled secret(s). "
                              "Export them as-is?",
        "export_secrets_hidden": "Secret field(s) hidden: {fields}. "
                                 "Move them manually or export again with confirmation.",
        "exported_to": "Settings saved to:\n{path}",
        "cfg_valid_title": "Configuration check",
        "cfg_invalid_title": "Configuration errors",
        "cfg_invalid_found": "Problems found: {n}",
        "menu_save_config": "💾  Save config.yaml…",
        "menu_effective_config": "🔍  Effective config (what really applies)…",
        "menu_validate_config": "✔  Validate config.yaml…",
        "menu_run_history": "🕘  Run history…",
        "menu_last_metrics": "📊  Last run metrics",
        "menu_shortcuts": "⌨  Keyboard shortcuts (F1)",
        "save_config_ok": "config.yaml saved",
        "save_config_fail": "Could not save the configuration",
        "history_empty": "Run history is empty ({p})",
        "history_text": "Recent runs ({n} records total):",
        "no_metrics": "No metrics yet — run crawling or post-processing.",
        "metrics_short": "Processed: {p}, errors: {e}. Full JSON in “Details”.",
        "shortcuts_text": "Keyboard shortcuts (actions that have one):",
        "settings_title": "CorpusBuilder Settings",
        "settings_header": "⚙  Program Settings",
        "settings_subtitle": "All settings are saved automatically and applied on next runs.",
        "settings_reset": "↺  Reset to defaults",
        "settings_export": "📤  Export settings",
        "settings_import": "📥  Import settings",
        "settings_saved": "Settings saved and will be applied on next runs.",
        "settings_applied": "Settings applied",

        # Settings dialog — all checkboxes/labels
        "st_window_size": "Window Size",
        "st_width": "Width:",
        "st_height": "Height:",
        "st_use_cache": "Use HTTP cache (requests-cache)",
        "st_robots": "Respect robots.txt",
        "st_browser_headers": "Use browser-like headers (Sec-Fetch-*)",
        "st_proxy": "Proxy (optional)",
        "st_use_proxy": "Use proxy",
        "st_proxy_list": "Proxy list (comma-separated):",
        "st_download_images": "Download images",
        "st_ocr": "Enable OCR for scanned PDFs (requires tesseract)",
        "st_extract_tables": "Extract tables via pdfplumber",
        "st_two_column": "Auto-detect two-column layout",
        "st_filter_schematics": "Filter schematics via OCR keywords",
        "st_use_toc": "Structure content by TOC",
        "st_crawl_issues": "Extract Issues/PR",
        "st_crawl_wiki": "Clone Wiki",
        "st_crawl_docs": "Parse docs/ directory",
        "st_spam": "Check for spam/ads",
        "st_perplexity_group": "Perplexity filter (optional, requires kenlm)",
        "st_perplexity": "Enable perplexity filter",
        "st_max_perplexity": "Max perplexity:",
        "st_perplexity_model": "Path to kenlm model (.binary):",
        "st_dedup_exact": "Exact deduplication (sha1)",
        "st_dedup_minhash": "Fuzzy deduplication (MinHash LSH)",
        "st_dedup_images": "Image deduplication (sha1)",
        "st_streaming": "Streaming MinHash (for large corpora, saves RAM)",
        "st_incremental": "Incremental dedup (persist LSH index between runs)",
        "st_async": "Use async crawling by default",
        "st_gzip": "Compress corpus to .jsonl.gz (4-6x space savings)",
        "st_parallel": "Parallel post-processing (multiprocessing)",
        "st_show_progress": "Show progress bar in terminal (tqdm)",
        # Wikipedia tab
        "wiki_lang_label": "Wikipedia Language:",
        "wiki_categories_label": "Categories (comma-separated):",
        "wiki_max_label": "Max articles per category:",
        "wiki_depth_label": "Subcategory depth:",
        "wiki_hint": "💡 Example categories:\n  EN: Electronics, Printed circuit boards, Operational amplifiers\n  RU: Электроника, Печатные платы\n\nSee full list: en.wikipedia.org/wiki/Category:Electronics",

        # Auto-Discover dialog
        "ad_title": "🔄 Auto-Discover Sources",
        "ad_presets": "🎯 Quick Start (presets)",
        "ad_select_preset": "Select profile:",
        "ad_apply": "Apply",
        "ad_manual": "⚙ Manual Configuration",
        "ad_github_topics": "GitHub topics:",
        "ad_se_tags": "StackExchange tags:",
        "ad_wiki_cats": "Wikipedia categories:",
        "ad_max_per_source": "Max sources per platform:",
        "ad_progress": "Progress",
        "ad_search": "🔍  Start Search",
        "ad_stop": "⏹  Stop",
        "ad_save": "💾  Save config.yaml",
        "ad_found_sources": "Found sources:",
        # Merge Config dialog
        "mc_title": "🔗 Merge config.yaml",
        "mc_files": "📎 Files to merge",
        "mc_add_files": "➕  Add files...",
        "mc_remove": "➖  Remove selected",
        "mc_clear": "🗑  Clear list",
        "mc_options": "⚙ Deduplication options",
        "mc_exact": "Exact URL match",
        "mc_canonical": "Canonicalized URL",
        "mc_merge_cats": "Merge categories from duplicates",
        "mc_merge": "🔗  Merge",
        "mc_result": "📊 Result",
        "mc_stats_by_file": "Statistics by file:",
        "mc_save": "💾  Save merged config.yaml",

        # Fine-tuning window
        "ft_window_title": "CorpusBuilder — Fine-Tuning",
        "ft_generate": "🎯  Generate Instructions",
        "ft_export": "📤  Export",
        "ft_settings": "Fine-Tuning Settings",
        "ft_max_per_type": "Max pairs per type:",
        "ft_min_prompt": "Min prompt (chars):",
        "ft_max_prompt": "Max prompt (chars):",
        "ft_min_completion": "Min completion (chars):",
        "ft_max_completion": "Max completion (chars):",
        "ft_balance": "Balance by type",
        "ft_pii": "Remove PII (email/phones)",
        "ft_col_type": "Type",
        "ft_col_prompt": "Prompt",
        "ft_col_completion": "Completion",
        "ft_col_source": "Source",
        "ft_tab_preview": "Pairs Preview",
        "ft_tab_stats": "Statistics",
        "ft_no_pairs": "Generate instructions first",
        "ft_export_dir": "Export to directory",
        "ft_task_types": "Instruction types:",
        "ft_select_all": "Select all",
        "ft_select_none": "Clear",
        "ft_use_existing_corpus": "Use existing corpus",
        "ft_use_existing_corpus_desc": "Select corpus_final.jsonl from pre-training mode",
        "ft_existing_corpus": "Existing corpus:",
        "ft_split_train_val": "Split into train/val (90/10)",
        "ft_html_report": "HTML report",
        "ft_html_report_saved": "HTML report saved",
        "ft_pair_preview": "Pair preview",
        "ft_prompt_full": "Full prompt:",
        "ft_completion_full": "Full completion:",
        "ft_conversation": "Conversation:",
        "ft_no_corpus_selected": "No corpus selected",
        "ft_no_corpus_selected_desc": "Select a config.yaml or an existing corpus",
        "ft_select_corpus_file": "Select corpus file",
        "ft_corpus_jsonl": "Corpus JSONL (*.jsonl)",
        "ft_exported_with_split": "Exported with train/val split",
        "ft_menu_language": "Language",
        "ft_menu_lang_ru": "Russian",
        "ft_menu_lang_en": "English",
        "ft_menu_export_html": "Export HTML report...",
        "ft_token_limits": "Token limits (instead of chars)",
        "ft_token_limits_tooltip": "Use tiktoken to count tokens and filter by token limits instead of char limits. More accurate for Russian text.",
        "ft_corpus_validation_warning": "Corpus validation warning:",
        "ft_continue_anyway": "Continue anyway?",
        "lang_changed_msg": "Interface language changed. Restart the application for full effect.",
    },
}

_current_lang = "ru"


def set_language(lang: str) -> None:
    """Установить язык интерфейса (ru или en)."""
    global _current_lang
    if lang in TRANSLATIONS:
        _current_lang = lang


def get_language() -> str:
    """Вернуть текущий язык."""
    return _current_lang


_missing_logged: set[str] = set()


def tr(key: str) -> str:
    """Перевести строку по ключу.

    Если перевода на текущий язык нет, показываем англоский вариант (B7): раньше
    возвращался сам ключ, и в русском интерфейсе окна Fine-Tune можно было
    увидеть «ft_task_types» вместо подписи. О пропуске сообщаем один раз в лог.
    """
    table = TRANSLATIONS.get(_current_lang, {})
    if key in table:
        return table[key]
    if key not in _missing_logged:
        _missing_logged.add(key)
        log.warning(f"Нет перевода для ключа '{key}' (язык '{_current_lang}') — "
                    f"показываю английскую строку")
    return TRANSLATIONS.get("en", {}).get(key, key)


# ============================================================
# O. Темы оформления (Material Design) — уже реализован в THEMES выше
# ============================================================

# THEMES dict определён в секции F и включает:
# dark, light, material_blue, material_green, material_purple

