"""Диалог авто-поиска источников для config.yaml.

Позволяет пользователю выбрать темы/категории и автоматически
найти источники на GitHub, StackExchange и Wikipedia.

Также содержит предустановленные наборы тем (presets) для быстрого старта:
  - electronics_general
  - analog_design
  - microcontrollers
  - power_electronics
  - rf_microwave
  - russian_electronics
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .auto_discover import AutoDiscover
from .gui_improvements import tr
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


class AutoDiscoverWorker(QThread):
    """Запускает авто-поиск в отдельном потоке."""
    progress = Signal(int, int, str)
    url_found = Signal(dict)
    finished_result = Signal(list, dict)
    error = Signal(str)

    def __init__(self, topics=None, se_tags=None, se_site="electronics",
                 wiki_categories=None, wiki_lang="en", wiki_langs=None,
                 seed_urls=None, max_per_source=50):
        super().__init__()
        self.topics = topics or []
        self.se_tags = se_tags or []
        self.se_site = se_site
        self.wiki_categories = wiki_categories or []
        self.wiki_lang = wiki_lang
        self.wiki_langs = wiki_langs
        self.seed_urls = seed_urls or []
        self.max_per_source = max_per_source

    def run(self):
        try:
            discover = AutoDiscover()
            sources = discover.discover(
                topics=self.topics if self.topics else None,
                se_tags=self.se_tags if self.se_tags else None,
                se_site=self.se_site,
                wiki_categories=self.wiki_categories if self.wiki_categories else None,
                wiki_lang=self.wiki_lang,
                wiki_langs=self.wiki_langs,
                seed_urls=self.seed_urls if self.seed_urls else None,
                max_per_source=self.max_per_source,
                on_progress=self._on_progress,
            )
            stats = discover.get_stats()
            for s in sources:
                self.url_found.emit(s)
            self.finished_result.emit(sources, stats)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def _on_progress(self, current, total, msg):
        self.progress.emit(current, total, msg)


class AutoDiscoverDialog(QDialog):
    """Диалог авто-поиска источников.

    Пользователь выбирает темы, теги и категории — программа
    автоматически ищет источники на 3 платформах, дедуплицирует
    и сохраняет в config.yaml.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("ad_title"))
        self.resize(700, 600)
        self.sources_count = 0
        self.config_path = None
        self.worker = None
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        title = QLabel(tr("ad_title"))
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {ACCENT};")
        outer.addWidget(title)

        subtitle = QLabel(
            "Программа автоматически найдёт источники на GitHub, StackExchange и Wikipedia\n"
            "по заданным темам/категориям, дедуплицирует их и создаст config.yaml."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px;")
        outer.addWidget(subtitle)

        # Предустановки
        preset_group = QGroupBox(tr("ad_presets"))
        preset_layout = QHBoxLayout(preset_group)
        preset_layout.addWidget(QLabel(tr("ad_select_preset")))

        self.combo_preset = QComboBox()
        presets = AutoDiscover.get_preset_topics()
        self.combo_preset.addItem("(custom — настроить вручную)")
        for name, preset in presets.items():
            label = name.replace("_", " ").title()
            self.combo_preset.addItem(label, preset)
        self.combo_preset.currentIndexChanged.connect(self._on_preset_selected)
        preset_layout.addWidget(self.combo_preset, stretch=1)

        self.btn_apply_preset = QPushButton(tr("ad_apply"))
        self.btn_apply_preset.clicked.connect(self._apply_preset)
        preset_layout.addWidget(self.btn_apply_preset)
        outer.addWidget(preset_group)

        # Ручная настройка
        manual_group = QGroupBox(tr("ad_manual"))
        manual_layout = QFormLayout(manual_group)

        # GitHub topics
        self.edit_github_topics = QLineEdit()
        self.edit_github_topics.setPlaceholderText("kicad, pcb, embedded, electronics")
        manual_layout.addRow(tr("ad_github_topics"), self.edit_github_topics)

        # StackExchange tags
        se_row = QHBoxLayout()
        self.edit_se_tags = QLineEdit()
        self.edit_se_tags.setPlaceholderText("kicad, stm32, pcb")
        se_row.addWidget(self.edit_se_tags)
        se_row.addWidget(QLabel("Сайт:"))
        self.combo_se_site = QComboBox()
        self.combo_se_site.addItems(["electronics", "stackoverflow", "serverfault"])
        se_row.addWidget(self.combo_se_site)
        manual_layout.addRow(tr("ad_se_tags"), se_row)

        # Wikipedia categories
        wiki_row = QHBoxLayout()
        self.edit_wiki_categories = QLineEdit()
        self.edit_wiki_categories.setPlaceholderText("Electronics, Printed circuit boards, Operational amplifiers")
        wiki_row.addWidget(self.edit_wiki_categories, stretch=1)
        wiki_row.addWidget(QLabel("Языки:"))
        # Мультиязычный выбор через чекбоксы
        self.chk_wiki_en = QCheckBox("EN")
        self.chk_wiki_en.setChecked(True)
        wiki_row.addWidget(self.chk_wiki_en)
        self.chk_wiki_ru = QCheckBox("RU")
        wiki_row.addWidget(self.chk_wiki_ru)
        self.chk_wiki_de = QCheckBox("DE")
        wiki_row.addWidget(self.chk_wiki_de)
        self.chk_wiki_fr = QCheckBox("FR")
        wiki_row.addWidget(self.chk_wiki_fr)
        manual_layout.addRow("Wikipedia:", wiki_row)

        # Подсказка
        wiki_hint = QLabel(
            "💡 Отметьте языки для мультиязычного поиска.\n"
            "  Категории задаются один раз — программа ищет их на всех выбранных языках.\n"
            "  Например: 'Electronics' на EN + 'Электроника' на RU = оба результата."
        )
        wiki_hint.setWordWrap(True)
        wiki_hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        manual_layout.addRow(wiki_hint)

        # Max per source
        self.spin_max_per_source = QSpinBox()
        self.spin_max_per_source.setRange(1, 500)
        self.spin_max_per_source.setValue(50)
        manual_layout.addRow(tr("ad_max_per_source"), self.spin_max_per_source)

        outer.addWidget(manual_group)

        # Прогресс
        prog_group = QGroupBox(tr("ad_progress"))
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        prog_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("Готов к поиску")
        self.progress_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        prog_layout.addWidget(self.progress_label)
        outer.addWidget(prog_group)

        # Таблица найденных URL
        outer.addWidget(QLabel(tr("ad_found_sources")))
        self.results_table = QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["URL", "Тип", "Категории"])
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        outer.addWidget(self.results_table, stretch=1)

        # Кнопки
        btn_row = QHBoxLayout()
        self.btn_search = QPushButton(tr("ad_search"))
        self.btn_search.setStyleSheet(
            f"background-color: {ACCENT}; color: white; font-weight: bold; padding: 8px 18px; min-height: 28px;"
        )
        self.btn_search.clicked.connect(self._on_search)
        btn_row.addWidget(self.btn_search)

        self.btn_stop = QPushButton(tr("ad_stop"))
        self.btn_stop.setProperty("danger", True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()

        self.btn_save = QPushButton(tr("ad_save"))
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)

        outer.addLayout(btn_row)

    def _on_preset_selected(self, idx):
        """При выборе пресета — показываем его параметры."""
        if idx == 0:
            return
        preset = self.combo_preset.itemData(idx)
        if not preset:
            return
        self.edit_github_topics.setText(", ".join(preset.get("github_topics", [])))
        self.edit_se_tags.setText(", ".join(preset.get("se_tags", [])))
        self.combo_se_site.setCurrentText(preset.get("se_site", "electronics"))
        self.edit_wiki_categories.setText(", ".join(preset.get("wiki_categories", [])))
        # Поддержка мультиязычных пресетов
        wiki_langs = preset.get("wiki_langs")
        if wiki_langs:
            self.chk_wiki_en.setChecked("en" in wiki_langs)
            self.chk_wiki_ru.setChecked("ru" in wiki_langs)
            self.chk_wiki_de.setChecked("de" in wiki_langs)
            self.chk_wiki_fr.setChecked("fr" in wiki_langs)
        else:
            lang = preset.get("wiki_lang", "en")
            self.chk_wiki_en.setChecked(lang == "en")
            self.chk_wiki_ru.setChecked(lang == "ru")
            self.chk_wiki_de.setChecked(lang == "de")
            self.chk_wiki_fr.setChecked(lang == "fr")

    def _apply_preset(self):
        """Применить выбранный пресет."""
        self._on_preset_selected(self.combo_preset.currentIndex())

    def _on_search(self):
        """Запустить авто-поиск."""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Занято", "Поиск уже идёт.")
            return

        topics_str = self.edit_github_topics.text().strip()
        se_tags_str = self.edit_se_tags.text().strip()
        wiki_cats_str = self.edit_wiki_categories.text().strip()
        wiki_langs = []
        if self.chk_wiki_en.isChecked():
            wiki_langs.append("en")
        if self.chk_wiki_ru.isChecked():
            wiki_langs.append("ru")
        if self.chk_wiki_de.isChecked():
            wiki_langs.append("de")
        if self.chk_wiki_fr.isChecked():
            wiki_langs.append("fr")
        if not wiki_langs:
            wiki_langs = ["en"]  # default

        if not topics_str and not se_tags_str and not wiki_cats_str:
            QMessageBox.warning(self, "Нет данных",
                "Укажите хотя бы одну тему/тег/категорию.\\n"
                "Или выберите пресет из списка.")
            return

        topics = [t.strip() for t in topics_str.replace(";", ",").split(",") if t.strip()]
        se_tags = [t.strip() for t in se_tags_str.replace(";", ",").split(",") if t.strip()]
        wiki_cats = [c.strip() for c in wiki_cats_str.replace(";", ",").split(",") if c.strip()]

        self.btn_search.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_save.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Запуск...")
        self.results_table.setRowCount(0)
        self.sources_count = 0

        self.worker = AutoDiscoverWorker(
            topics=topics,
            se_tags=se_tags,
            se_site=self.combo_se_site.currentText(),
            wiki_categories=wiki_cats,
            wiki_langs=wiki_langs if len(wiki_langs) > 1 else None,
            wiki_lang=wiki_langs[0] if len(wiki_langs) == 1 else "en",
            max_per_source=self.spin_max_per_source.value(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.url_found.connect(self._on_url_found)
        self.worker.finished_result.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current, total, msg):
        if total > 0:
            self.progress_bar.setValue(int(current * 100 / total))
        self.progress_label.setText(msg)

    def _on_url_found(self, source: dict):
        self.sources_count += 1
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        self.results_table.setItem(row, 0, QTableWidgetItem(source.get("url", "")[:80]))
        self.results_table.setItem(row, 1, QTableWidgetItem(source.get("type", "")))
        cats = source.get("categories", [])
        self.results_table.setItem(row, 2, QTableWidgetItem(", ".join(cats)[:60] if cats else ""))
        self.results_table.scrollToBottom()

    def _on_finished(self, sources: list, stats: dict):
        self.btn_search.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_save.setEnabled(len(sources) > 0)
        self.progress_bar.setValue(100)

        stats_text = (
            f"Найдено источников: {stats.get('total', 0)}\\n"
            f"Уникальных URL: {stats.get('unique_urls', 0)}\\n"
            f"По платформам:"
        )
        for platform, count in stats.get("by_platform", {}).items():
            stats_text += f"\\n  {platform}: {count}"
        self.progress_label.setText(stats_text)

        if sources:
            QMessageBox.information(self, "Поиск завершён",
                f"Найдено {len(sources)} источников.\\n"
                f"Нажмите «Сохранить config.yaml» для создания файла."
            )

    def _on_error(self, err: str):
        self.btn_search.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_label.setText(f"Ошибка: {err}")
        QMessageBox.critical(self, "Ошибка", err)

    def _on_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.btn_stop.setEnabled(False)
            self.progress_label.setText("Останавливаю...")

    def _on_save(self):
        """Сохранить найденные источники в config.yaml."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Куда сохранить config.yaml",
            "config.auto.yaml",
            "YAML (*.yaml *.yml)"
        )
        if not path:
            return

        try:
            from .auto_discover import AutoDiscover
            discover = AutoDiscover()
            # Используем источники из worker
            sources = []
            for row in range(self.results_table.rowCount()):
                url_item = self.results_table.item(row, 0)
                type_item = self.results_table.item(row, 1)
                cats_item = self.results_table.item(row, 2)
                if not url_item:
                    continue
                from .config_generator import make_source
                from .models import SOURCE_TYPES
                url = url_item.text().strip()
                if not url.startswith("http"):
                    url = "https://" + url
                cats = []
                if cats_item and cats_item.text():
                    cats = [c.strip() for c in cats_item.text().split(",")]
                # тип из таблицы обязан сохраняться: иначе вики/arXiv-строки
                # уезжают в config как «html» и специализированный краулер
                # никогда не вызывается (C2)
                stype = (type_item.text().strip() if type_item else "") or None
                if stype not in SOURCE_TYPES:
                    stype = None
                sources.append(make_source(url, stype, categories=cats or None))

            discover.save_config(sources, path)
            self.config_path = path
            self.sources_count = len(sources)

            QMessageBox.information(self, "Сохранено",
                f"Создан config.yaml с {len(sources)} источниками.\\n"
                f"Файл: {path}"
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
        QPushButton[danger="true"] {{ background-color: #f44747; }}
        QLineEdit, QComboBox, QSpinBox {{
            background-color: {DARKER_BG};
            border: 1px solid {BORDER};
            border-radius: 4px;
            padding: 4px 8px;
            color: {TEXT_PRIMARY};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {ACCENT};
        }}
        QProgressBar {{
            background-color: {DARKER_BG};
            border: 1px solid {BORDER};
            border-radius: 4px;
            text-align: center;
            color: {TEXT_PRIMARY};
            min-height: 22px;
        }}
        QProgressBar::chunk {{
            background-color: {ACCENT};
            border-radius: 3px;
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
        """)
