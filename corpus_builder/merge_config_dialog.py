"""Диалог объединения нескольких config.yaml в один.

С умной дедупликацией:
  - Точное совпадение URL
  - Канонизированный URL (удаление utm_*, сортировка query, trailing slash)
  - Слияние категорий из дубликатов

Поддерживает drag-and-drop нескольких файлов.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QCheckBox, QGroupBox,
)

from .logging_setup import get_logger

log = get_logger(__name__)

DARK_BG = "#1e1e1e"
DARKER_BG = "#252526"
LIGHTER_BG = "#2d2d30"
ACCENT = "#007acc"
ACCENT_HOVER = "#1f8ad2"
SUCCESS = "#4ec9b0"
TEXT_PRIMARY = "#d4d4d4"
TEXT_SECONDARY = "#858585"
BORDER = "#3c3c3c"


class MergeConfigDialog(QDialog):
    """Диалог объединения config.yaml файлов с дедупликацией."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔗 Объединить config.yaml")
        self.resize(800, 600)
        self._files: list[str] = []
        self._merged_sources: list[dict] = []
        self._merge_stats: dict = {}
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Заголовок
        title = QLabel("🔗 Объединение config.yaml")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {ACCENT};")
        outer.addWidget(title)

        subtitle = QLabel(
            "Добавьте несколько config.yaml файлов — программа объединит их в один\n"
            "с автоматическим удалением дубликатов (по точному и канонизированному URL).\n"
            "Категории из дубликатов сливаются в первую запись."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        outer.addWidget(subtitle)

        # Список файлов
        files_group = QGroupBox("📎 Файлы для объединения")
        files_layout = QVBoxLayout(files_group)

        self.files_list = QListWidget()
        self.files_list.setAcceptDrops(True)
        self.files_list.setSelectionMode(QListWidget.ExtendedSelection)
        files_layout.addWidget(self.files_list)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("➕  Добавить файлы...")
        btn_add.setProperty("secondary", True)
        btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("➖  Удалить выбранные")
        btn_remove.setProperty("secondary", True)
        btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(btn_remove)

        btn_clear = QPushButton("🗑  Очистить список")
        btn_clear.setProperty("secondary", True)
        btn_clear.clicked.connect(self._clear_files)
        btn_row.addWidget(btn_clear)

        files_layout.addLayout(btn_row)
        outer.addWidget(files_group)

        # Опции
        opts_group = QGroupBox("⚙ Опции дедупликации")
        opts_layout = QHBoxLayout(opts_group)

        self.chk_exact = QCheckBox("Точное совпадение URL")
        self.chk_exact.setChecked(True)
        self.chk_exact.setToolTip("Удалять дубликаты с точным совпадением URL")
        opts_layout.addWidget(self.chk_exact)

        self.chk_canonical = QCheckBox("Канонизированный URL")
        self.chk_canonical.setChecked(True)
        self.chk_canonical.setToolTip(
            "Сравнивать URL после канонизации:\\n"
            "  - удаление utm_* параметров\\n"
            "  - сортировка query-string\\n"
            "  - приведение к https://\\n"
            "  - удаление trailing slash"
        )
        opts_layout.addWidget(self.chk_canonical)

        self.chk_merge_cats = QCheckBox("Сливать категории из дубликатов")
        self.chk_merge_cats.setChecked(True)
        self.chk_merge_cats.setToolTip(
            "Если URL повторяется — категории из дубликата\\n"
            "добавляются в первую запись"
        )
        opts_layout.addWidget(self.chk_merge_cats)

        outer.addWidget(opts_group)

        # Кнопка объединения
        self.btn_merge = QPushButton("🔗  Объединить")
        self.btn_merge.setStyleSheet(
            f"background-color: {ACCENT}; color: white; font-weight: bold; "
            f"padding: 8px 20px; min-height: 28px;"
        )
        self.btn_merge.clicked.connect(self._on_merge)
        outer.addWidget(self.btn_merge)

        # Результат
        result_group = QGroupBox("📊 Результат")
        result_layout = QVBoxLayout(result_group)

        self.stats_label = QLabel("Объединение ещё не выполнено")
        self.stats_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        result_layout.addWidget(self.stats_label)

        # Таблица с дубликатами
        result_layout.addWidget(QLabel("Статистика по файлам:"))
        self.files_table = QTableWidget(0, 3)
        self.files_table.setHorizontalHeaderLabels(["Файл", "Источников", "Уникальных"])
        header = self.files_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setEditTriggers(QTableWidget.NoEditTriggers)
        result_layout.addWidget(self.files_table)

        outer.addWidget(result_group)

        # Кнопки сохранения
        save_row = QHBoxLayout()
        save_row.addStretch()

        self.btn_save = QPushButton("💾  Сохранить объединённый config.yaml")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        save_row.addWidget(self.btn_save)

        outer.addLayout(save_row)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите config.yaml файлы", "",
            "YAML (*.yaml *.yml);;Все файлы (*)"
        )
        for path in paths:
            if path not in self._files:
                self._files.append(path)
                item = QListWidgetItem(f"📄 {Path(path).name}")
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                self.files_list.addItem(item)

    def _remove_selected(self):
        for item in self.files_list.selectedItems():
            path = item.data(Qt.UserRole)
            if path in self._files:
                self._files.remove(path)
            self.files_list.takeItem(self.files_list.row(item))

    def _clear_files(self):
        self._files.clear()
        self.files_list.clear()
        self.files_table.setRowCount(0)
        self.stats_label.setText("Список очищен")
        self.btn_save.setEnabled(False)

    def _on_merge(self):
        if len(self._files) < 2:
            QMessageBox.warning(self, "Недостаточно файлов",
                "Добавьте минимум 2 файла для объединения.")
            return

        try:
            from .config_generator import merge_sources_with_stats, build_config
            import yaml

            sources, stats = merge_sources_with_stats(self._files)
            self._merged_sources = sources
            self._merge_stats = stats

            # Обновить статистику
            self.stats_label.setText(
                f"✅ Объединено: {stats['total_output']} уникальных источников\n"
                f"   Входных записей: {stats['total_input']}\n"
                f"   Дубликатов удалено: {stats['duplicates_removed']}"
            )

            # Заполнить таблицу
            self.files_table.setRowCount(0)
            for fname, count in stats.get("by_file", {}).items():
                row = self.files_table.rowCount()
                self.files_table.insertRow(row)
                self.files_table.setItem(row, 0, QTableWidgetItem(fname))
                self.files_table.setItem(row, 1, QTableWidgetItem(str(count)))
                # Уникальных — вычислим пропорцию
                unique = int(count * stats['total_output'] / max(stats['total_input'], 1))
                self.files_table.setItem(row, 2, QTableWidgetItem(f"~{unique}"))

            self.btn_save.setEnabled(len(sources) > 0)

            QMessageBox.information(self, "Объединение завершено",
                f"Уникальных источников: {stats['total_output']}\n"
                f"Дубликатов удалено: {stats['duplicates_removed']}\n\n"
                f"Нажмите «Сохранить» для создания файла."
            )

        except Exception as e:
            import traceback
            QMessageBox.critical(self, "Ошибка объединения",
                f"{e}\n\n{traceback.format_exc()[:500]}")

    def _on_save(self):
        if not self._merged_sources:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить объединённый config.yaml",
            "config.merged.yaml",
            "YAML (*.yaml *.yml)"
        )
        if not path:
            return

        try:
            from .config_generator import build_config
            build_config(self._merged_sources, path)
            QMessageBox.information(self, "Сохранено",
                f"Файл сохранён: {path}\n\n"
                f"Источников: {len(self._merged_sources)}"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QDialog, QWidget {{
            background-color: {DARK_BG};
            color: {TEXT_PRIMARY};
            font-family: 'Segoe UI', 'SF Pro', 'DejaVu Sans';
            font-size: 13px;
        }}
        QGroupBox {{
            background-color: {DARKER_BG};
            border: 1px solid {BORDER};
            border-radius: 6px;
            margin-top: 14px;
            padding-top: 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {ACCENT};
            font-weight: bold;
        }}
        QPushButton {{
            background-color: {ACCENT};
            color: white;
            border: none;
            padding: 6px 14px;
            border-radius: 4px;
            min-height: 22px;
        }}
        QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
        QPushButton:disabled {{ background-color: #555; color: #aaa; }}
        QPushButton[secondary="true"] {{
            background-color: #3a3a3a;
            color: {TEXT_PRIMARY};
        }}
        QListWidget {{
            background-color: {DARKER_BG};
            border: 1px solid {BORDER};
            border-radius: 4px;
            color: {TEXT_PRIMARY};
        }}
        QTableWidget {{
            background-color: {DARKER_BG};
            gridline-color: {BORDER};
            color: {TEXT_PRIMARY};
        }}
        QHeaderView::section {{
            background-color: {LIGHTER_BG};
            color: {TEXT_PRIMARY};
            padding: 4px;
            border: none;
        }}
        QLabel {{ color: {TEXT_PRIMARY}; }}
        QCheckBox {{ color: {TEXT_PRIMARY}; }}
        """)
