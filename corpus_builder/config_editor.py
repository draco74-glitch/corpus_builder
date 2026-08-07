"""Расширенный редактор config.yaml с поддержкой:
  1. Drag-and-drop Excel-файла прямо в окно
  2. Встроенный YAML-редактор с подсветкой синтаксиса
  3. Профили запуска — пресеты для типовых задач

Используется как альтернатива базовому ConfigGeneratorDialog, расширяя его
возможности без переписывания всей логики.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import (
    QColor, QFont, QDragEnterEvent, QDropEvent, QTextCursor, QSyntaxHighlighter,
    QTextCharFormat, QPalette,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTextEdit,
    QFileDialog, QMessageBox, QComboBox, QGroupBox, QSplitter, QTabWidget,
    QListWidget, QListWidgetItem, QInputDialog, QApplication,
)

from .logging_setup import get_logger

log = get_logger(__name__)


# ============================================================
# Drag-and-drop виджет
# ============================================================

class FileDropArea(QTextEdit):
    """Текстовое поле, принимающее перетаскиваемые файлы.

    Используется для drag-and-drop загрузки Excel/CSV-файлов.
    При drop-событии вызывает сигнал file_dropped(path).
    """
    file_dropped = Signal(str)

    def __init__(self, accepted_extensions: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setReadOnly(True)
        self.accepted_extensions = accepted_extensions or [".xlsx", ".xls", ".csv", ".yaml", ".yml"]
        self.setPlainText(
            "Перетащите файл сюда\n\n"
            "Поддерживаемые форматы: " + ", ".join(self.accepted_extensions) + "\n\n"
            "Или используйте кнопку «Обзор...» ниже."
        )
        self.setStyleSheet(
            "QTextEdit { "
            "  border: 2px dashed #5B8DB8; "
            "  border-radius: 6px; "
            "  background-color: #2d2d30; "
            "  color: #858585; "
            "  font-size: 13px; "
            "  padding: 20px; "
            "}"
        )
        self.setMinimumHeight(120)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    ext = os.path.splitext(path)[1].lower()
                    if ext in self.accepted_extensions:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                ext = os.path.splitext(path)[1].lower()
                if ext in self.accepted_extensions:
                    self.setPlainText(f"Загружен файл:\n{path}")
                    self.file_dropped.emit(path)
                    event.acceptProposedAction()
                    return
        event.ignore()


# ============================================================
# YAML-редактор с подсветкой синтаксиса
# ============================================================

class YamlHighlighter(QSyntaxHighlighter):
    """Простая подсветка синтаксиса YAML."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_formats()

    def _setup_formats(self) -> None:
        # Ключи (до двоеточия)
        self.key_format = QTextCharFormat()
        self.key_format.setForeground(QColor("#569CD6"))  # синий
        self.key_format.setFontWeight(QFont.Bold)

        # Строковые значения
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(QColor("#CE9178"))  # оранжевый

        # Числа и булевы
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(QColor("#B5CEA8"))  # зелёный

        # Комментарии
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(QColor("#6A9955"))  # зелёный italic
        self.comment_format.setFontItalic(True)

        # Спецсимволы YAML: ---, [], {}
        self.special_format = QTextCharFormat()
        self.special_format.setForeground(QColor("#FFD700"))  # золотой

    def highlightBlock(self, text: str) -> None:
        # Комментарии
        if text.strip().startswith("#"):
            self.setFormat(0, len(text), self.comment_format)
            return

        # Ключ: значение
        if ":" in text and not text.strip().startswith("-"):
            colon_idx = text.find(":")
            if colon_idx > 0:
                # Ключ — до двоеточия
                self.setFormat(0, colon_idx, self.key_format)
                # Значение — после двоеточия
                value = text[colon_idx + 1:].strip()
                if value:
                    start = colon_idx + 1 + (len(text[colon_idx + 1:]) - len(value))
                    # Определяем тип значения
                    if value.startswith('"') or value.startswith("'"):
                        self.setFormat(start, len(value), self.string_format)
                    elif value.lower() in ("true", "false", "null", "none"):
                        self.setFormat(start, len(value), self.number_format)
                    elif value.replace(".", "").replace("-", "").replace("+", "").isdigit():
                        self.setFormat(start, len(value), self.number_format)

        # --- разделитель документов
        if text.strip() == "---":
            self.setFormat(0, len(text), self.special_format)


class YamlEditor(QWidget):
    """Виджет с текстовым редактором YAML и подсветкой синтаксиса."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Текстовый редактор
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Consolas", 10))
        self.highlighter = YamlHighlighter(self.editor.document())
        layout.addWidget(self.editor)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_load = QPushButton("Загрузить .yaml")
        btn_load.clicked.connect(self._load_file)
        btn_save = QPushButton("Сохранить .yaml")
        btn_save.clicked.connect(self._save_file)
        btn_validate = QPushButton("Проверить синтаксис")
        btn_validate.clicked.connect(self._validate_yaml)
        btn_layout.addWidget(btn_load)
        btn_layout.addWidget(btn_save)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_validate)
        layout.addLayout(btn_layout)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть config.yaml", "", "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.setPlainText(f.read())
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _save_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить config.yaml", "config.yaml", "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            QMessageBox.information(self, "Сохранено", f"Файл сохранён:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _validate_yaml(self) -> None:
        text = self.editor.toPlainText()
        try:
            data = yaml.safe_load(text)
            if data is None:
                QMessageBox.warning(self, "Пусто", "Конфиг пустой")
            else:
                QMessageBox.information(
                    self, "OK",
                    f"YAML валиден.\nВерхнеуровневые ключи: {list(data.keys())}"
                )
        except yaml.YAMLError as e:
            QMessageBox.critical(self, "Ошибка YAML", str(e))

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)

    def get_text(self) -> str:
        return self.editor.toPlainText()


# ============================================================
# Профили запуска — пресеты для типовых задач
# ============================================================

DEFAULT_PROFILES = {
    "Только Habr": {
        "description": "Только статьи с Habr по электронике",
        "sources_type_filter": "html",
        "categories_filter": ["habr", "ru"],
    },
    "Только GitHub (KiCad)": {
        "description": "Только репозитории с KiCad-проектами",
        "sources_type_filter": "github_repo",
        "categories_filter": ["kicad"],
    },
    "Datasheet'ы от TI": {
        "description": "PDF-документы от Texas Instruments",
        "sources_type_filter": "pdf",
        "categories_filter": ["ti", "datasheet"],
    },
    "StackExchange (топ-вопросы)": {
        "description": "Только топ-вопросы по электронике",
        "sources_type_filter": "stackexchange",
        "categories_filter": ["electronics"],
    },
    "arXiv (научные статьи)": {
        "description": "Только научные статьи из arXiv eess",
        "sources_type_filter": "arxiv",
        "categories_filter": [],
    },
    "Только русские источники": {
        "description": "Фильтр по языку — ru",
        "sources_type_filter": None,
        "categories_filter": ["ru"],
    },
    "Только английские источники": {
        "description": "Фильтр по языку — en",
        "sources_type_filter": None,
        "categories_filter": ["en"],
    },
}


class ProfileSelector(QWidget):
    """Виджет выбора профиля запуска.

    Позволяет быстро фильтровать sources из config.yaml по типу и категориям.
    """

    profile_selected = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Профиль запуска:"))
        self.combo = QComboBox()
        for name, cfg in DEFAULT_PROFILES.items():
            self.combo.addItem(f"{name} — {cfg['description']}", cfg)
        self.combo.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self.combo)

    def _on_profile_changed(self, idx: int) -> None:
        if idx < 0:
            return
        name = self.combo.itemText(idx).split(" — ")[0]
        cfg = self.combo.itemData(idx)
        if cfg:
            self.profile_selected.emit(name, cfg)

    def get_selected_profile(self) -> tuple[str, dict] | None:
        idx = self.combo.currentIndex()
        if idx < 0:
            return None
        name = self.combo.itemText(idx).split(" — ")[0]
        return name, self.combo.itemData(idx)


# ============================================================
# Расширенный редактор конфигурации
# ============================================================

class AdvancedConfigEditor(QWidget):
    """Расширенный редактор config.yaml с drag-and-drop и профилями."""

    config_loaded = Signal(str)  # путь к загруженному config.yaml

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Профиль запуска
        self.profile_selector = ProfileSelector()
        self.profile_selector.profile_selected.connect(self._on_profile)
        layout.addWidget(self.profile_selector)

        # Drag-and-drop для Excel
        drop_group = QGroupBox("Загрузить Excel/CSV")
        drop_layout = QVBoxLayout(drop_group)
        self.drop_area = FileDropArea(accepted_extensions=[".xlsx", ".xls", ".csv"])
        self.drop_area.file_dropped.connect(self._on_file_dropped)
        drop_layout.addWidget(self.drop_area)
        btn_browse = QPushButton("Обзор...")
        btn_browse.clicked.connect(self._browse_file)
        drop_layout.addWidget(btn_browse)
        layout.addWidget(drop_group)

        # YAML-редактор
        yaml_group = QGroupBox("Редактор config.yaml")
        yaml_layout = QVBoxLayout(yaml_group)
        self.yaml_editor = YamlEditor()
        yaml_layout.addWidget(self.yaml_editor)
        layout.addWidget(yaml_group, stretch=1)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить Excel/CSV", "",
            "Табличные файлы (*.xlsx *.xls *.csv);;Все файлы (*)"
        )
        if path:
            self._on_file_dropped(path)

    def _on_file_dropped(self, path: str) -> None:
        """При drop Excel-файла — конвертируем его в YAML и подставляем в редактор."""
        try:
            from .config_generator import from_excel, build_config_to_dict
            rows = from_excel(path)
            # Конвертируем в sources для YAML
            sources = [
                {"url": r[0], "type": "html", "depth": r[1],
                 "categories": r[2] if r[2] else None}
                for r in rows
            ]
            # Формируем полный конфиг
            config_dict = build_config_to_dict(sources)
            yaml_text = yaml.dump(config_dict, default_flow_style=False, sort_keys=False,
                                  allow_unicode=True, indent=2)
            self.yaml_editor.set_text(yaml_text)
            log.info(f"Loaded Excel: {path}, {len(rows)} rows → YAML editor")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{e}")

    def _on_profile(self, name: str, cfg: dict) -> None:
        """Применить выбранный профиль к YAML в редакторе."""
        log.info(f"Profile selected: {name}")
        # Просто показываем уведомление — реальная фильтрация применяется при краулинге
        QMessageBox.information(
            self, "Профиль выбран",
            f"Профиль: {name}\n"
            f"Тип: {cfg.get('sources_type_filter') or 'все'}\n"
            f"Категории: {', '.join(cfg.get('categories_filter') or []) or 'все'}\n\n"
            "Профиль будет применён при запуске краулинга."
        )


# Расширение config_generator.build_config, возвращающее dict вместо записи в файл
def build_config_to_dict(sources: list[dict]) -> dict:
    """Вернуть конфиг как dict (не записывая в файл)."""
    from .config_generator import DEFAULT_TEMPLATE
    config = dict(DEFAULT_TEMPLATE)
    config["sources"] = sources
    return config
