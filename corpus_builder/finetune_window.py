"""Полное GUI окно для режима Fine-Tuning.

Содержит:
  1. Загрузка config.yaml (как в pre-training)
  2. Кнопки: Краулинг → Генерация инструкций → Фильтр → Балансировка → Экспорт
  3. Превью пар (prompt/completion)
  4. Выбор формата экспорта (JSONL/ChatML/Alpaca/ShareGPT)
  5. Статистика по типам инструкций
  6. Настройки fine-tuning (max_per_type, quality filters, PII)
"""
from __future__ import annotations

import json
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog, QProgressBar, QTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QCheckBox, QSpinBox, QComboBox,
    QMessageBox, QGroupBox, QSplitter, QHeaderView, QStatusBar, QStyle,
    QDialog, QFormLayout,
)

from .config import load_config, ensure_output_dirs
from .gui_improvements import tr, set_language, get_language, THEMES, apply_theme, get_theme_qss
from .logging_setup import get_logger, setup_logging
from .models import AppConfig
from .app_settings import AppSettings
from .pipeline import run_crawl, run_postprocess
from .state import State

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


# ============================================================
# Worker для генерации инструкций
# ============================================================

class FinetuneWorker(QThread):
    """Запускает пайплайн fine-tuning в отдельном потоке."""
    progress = Signal(int, int, str)
    pair_found = Signal(dict)
    finished_result = Signal(list, dict)
    error = Signal(str)

    def __init__(self, corpus_file: str, max_per_type: int = 1000,
                 min_prompt: int = 20, max_prompt: int = 8000,
                 min_completion: int = 20, max_completion: int = 16000,
                 balance: bool = True, remove_pii: bool = True):
        super().__init__()
        self.corpus_file = corpus_file
        self.max_per_type = max_per_type
        self.min_prompt = min_prompt
        self.max_prompt = max_prompt
        self.min_completion = min_completion
        self.max_completion = max_completion
        self.balance = balance
        self.remove_pii = remove_pii

    def run(self):
        try:
            from .postproc.instruction_generator import InstructionGenerator
            from .postproc.quality_finetune import filter_pairs, dedup_pairs
            from .postproc.dataset_balancer import balance_by_type, get_balance_stats
            from .postproc.pii_filter import clean_pair
            from .postproc.prompt_variations import set_seed as set_prompt_seed

            # Seed the prompt variations RNG so that prompt selection is
            # reproducible across runs. Without this, the global _rng in
            # prompt_variations.py continues from wherever it left off,
            # making datasets non-deterministic.
            set_prompt_seed(42)

            # Per-stage counts by task_type — for detecting type collapse.
            # Each entry: stage_name → {task_type: count}
            stage_counts: dict[str, dict[str, int]] = {}
            warnings: list[str] = []

            def count_by_type(pairs_list):
                counts: dict[str, int] = {}
                for p in pairs_list:
                    t = p.get("task_type", "unknown")
                    counts[t] = counts.get(t, 0) + 1
                return counts

            def log_stage(stage: str, pairs_list):
                counts = count_by_type(pairs_list)
                stage_counts[stage] = counts
                log.info(f"[stage] {stage}: {len(pairs_list)} pairs, by_type={counts}")
                # Check for type collapse vs the previous stage
                prev_stages = [s for s in stage_counts if s != stage]
                if prev_stages:
                    prev = stage_counts[prev_stages[-1]]
                    for t, prev_n in prev.items():
                        curr_n = counts.get(t, 0)
                        if prev_n > 0 and curr_n == 0:
                            warnings.append(
                                f"{stage}: task_type '{t}' disappeared "
                                f"(had {prev_n} pairs in {prev_stages[-1]})"
                            )
                            log.warning(warnings[-1])
                        elif prev_n > 0 and curr_n < prev_n * 0.1:
                            warnings.append(
                                f"{stage}: task_type '{t}' lost {prev_n - curr_n}/{prev_n} pairs "
                                f"({100*(prev_n-curr_n)//prev_n}% drop)"
                            )
                            log.warning(warnings[-1])

            # Step 1: Generate instructions
            # Steps total = 5 (generate, filter, dedup, pii, balance)
            total_steps = 5
            self.progress.emit(1, total_steps, "Generating instructions...")
            gen = InstructionGenerator()
            pairs = gen.generate_from_corpus(
                self.corpus_file,
                max_per_type=self.max_per_type,
                on_progress=lambda c, t, m: self.progress.emit(
                    1, total_steps, f"[{c}/{t}] {m}"
                ),
            )
            log_stage("1_generate", pairs)

            # Step 2: Quality filter
            self.progress.emit(2, total_steps, f"Filtering {len(pairs)} pairs...")
            pairs, q_stats = filter_pairs(
                pairs,
                min_prompt=self.min_prompt,
                max_prompt=self.max_prompt,
                min_completion=self.min_completion,
                max_completion=self.max_completion,
            )
            log_stage("2_filter", pairs)

            # Step 3: Dedup (Bug 8)
            self.progress.emit(3, total_steps, f"Deduplicating {len(pairs)} pairs...")
            pairs, d_stats = dedup_pairs(pairs, mode="prompt+completion")
            log_stage("3_dedup", pairs)

            # Step 4: PII removal
            if self.remove_pii:
                self.progress.emit(4, total_steps, "Removing PII...")
                pairs = [clean_pair(p) for p in pairs]
            else:
                self.progress.emit(4, total_steps, "Skipping PII removal (disabled)")
            log_stage("4_pii", pairs)

            # Step 5: Balance
            if self.balance:
                self.progress.emit(5, total_steps, "Balancing dataset...")
                pairs = balance_by_type(pairs, max_per_type=self.max_per_type)
            else:
                self.progress.emit(5, total_steps, "Skipping balance (disabled)")
            log_stage("5_balance", pairs)

            stats = get_balance_stats(pairs)
            stats["quality"] = q_stats
            stats["dedup"] = d_stats
            stats["stage_counts"] = stage_counts
            stats["warnings"] = warnings

            for p in pairs[:100]:  # Emit first 100 for preview
                self.pair_found.emit(p)

            self.finished_result.emit(pairs, stats)

        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


# ============================================================
# Fine-Tuning Window
# ============================================================

class FinetuneWindow(QMainWindow):
    """Главное окно для режима Fine-Tuning."""

    def __init__(self):
        super().__init__()
        self.config: AppConfig | None = None
        self.config_path: str | None = None
        self.worker: FinetuneWorker | None = None
        self.app_settings = AppSettings.load()
        self.app_settings.setup_env_vars()
        set_language(getattr(self.app_settings.gui, 'language', 'ru'))

        self.setWindowTitle(tr("ft_window_title"))
        self.resize(1280, 820)
        self._apply_theme()

        self._build_ui()
        self._build_menu()
        self._connect_signals()
        self._restore_window_geometry()

    def _apply_theme(self):
        colors = apply_theme(QApplication.instance(), self.app_settings.gui.theme)
        self.setStyleSheet(get_theme_qss(colors))

    def _build_menu(self):
        menubar = self.menuBar()
        # File menu
        file_menu = menubar.addMenu(tr("menu_file_title"))
        act_open = QAction(tr("menu_open_config"), self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self._browse_config)
        file_menu.addAction(act_open)
        file_menu.addSeparator()
        act_quit = QAction(tr("menu_quit"), self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Actions menu
        actions_menu = menubar.addMenu(tr("menu_actions_title"))
        act_crawl = QAction(tr("menu_crawl"), self)
        act_crawl.setShortcut(QKeySequence("Ctrl+R"))
        act_crawl.triggered.connect(self._on_crawl)
        actions_menu.addAction(act_crawl)
        act_gen = QAction(tr("ft_generate"), self)
        act_gen.triggered.connect(self._on_generate)
        actions_menu.addAction(act_gen)
        act_export = QAction(tr("ft_export"), self)
        act_export.triggered.connect(self._on_export)
        actions_menu.addAction(act_export)

        # Help menu
        help_menu = menubar.addMenu(tr("menu_help_title"))
        act_about = QAction(tr("menu_about"), self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # === 1. Config ===
        cfg_group = QGroupBox(tr("group_config"))
        cfg_layout = QGridLayout(cfg_group)
        cfg_layout.addWidget(QLabel(tr("label_config")), 0, 0)
        self.config_edit = QLineEdit()
        self.config_edit.setPlaceholderText("config.yaml")
        cfg_layout.addWidget(self.config_edit, 0, 1)
        btn_browse = QPushButton(tr("btn_browse"))
        btn_browse.clicked.connect(self._browse_config)
        cfg_layout.addWidget(btn_browse, 0, 2)
        outer.addWidget(cfg_group)

        # === 2. Actions ===
        actions_group = QGroupBox(tr("group_actions"))
        actions_layout = QHBoxLayout(actions_group)
        self.btn_crawl = QPushButton(tr("btn_crawl"))
        self.btn_crawl.setStyleSheet("font-weight: bold; min-height: 28px;")
        self.btn_crawl.clicked.connect(self._on_crawl)
        actions_layout.addWidget(self.btn_crawl)
        self.btn_postprocess = QPushButton(tr("menu_postprocess"))
        self.btn_postprocess.clicked.connect(self._on_postprocess)
        actions_layout.addWidget(self.btn_postprocess)
        self.btn_generate = QPushButton(tr("ft_generate"))
        self.btn_generate.setStyleSheet(f"background-color: {SUCCESS}; color: white; font-weight: bold;")
        self.btn_generate.clicked.connect(self._on_generate)
        actions_layout.addWidget(self.btn_generate)
        self.btn_export = QPushButton(tr("ft_export"))
        self.btn_export.clicked.connect(self._on_export)
        actions_layout.addWidget(self.btn_export)
        outer.addWidget(actions_group)

        # === 3. Settings ===
        settings_group = QGroupBox(tr("ft_settings"))
        settings_layout = QGridLayout(settings_group)

        settings_layout.addWidget(QLabel(tr("ft_max_per_type")), 0, 0)
        self.spin_max_per_type = QSpinBox()
        self.spin_max_per_type.setRange(10, 10000)
        self.spin_max_per_type.setValue(1000)
        settings_layout.addWidget(self.spin_max_per_type, 0, 1)

        settings_layout.addWidget(QLabel(tr("ft_min_prompt")), 0, 2)
        self.spin_min_prompt = QSpinBox()
        self.spin_min_prompt.setRange(1, 1000)
        self.spin_min_prompt.setValue(20)
        settings_layout.addWidget(self.spin_min_prompt, 0, 3)

        settings_layout.addWidget(QLabel(tr("ft_max_prompt")), 1, 0)
        self.spin_max_prompt = QSpinBox()
        self.spin_max_prompt.setRange(100, 100000)
        self.spin_max_prompt.setValue(8000)
        settings_layout.addWidget(self.spin_max_prompt, 1, 1)

        settings_layout.addWidget(QLabel(tr("ft_min_completion")), 1, 2)
        self.spin_min_completion = QSpinBox()
        self.spin_min_completion.setRange(1, 1000)
        self.spin_min_completion.setValue(20)
        settings_layout.addWidget(self.spin_min_completion, 1, 3)

        settings_layout.addWidget(QLabel(tr("ft_max_completion")), 2, 0)
        self.spin_max_completion = QSpinBox()
        self.spin_max_completion.setRange(100, 100000)
        self.spin_max_completion.setValue(16000)
        settings_layout.addWidget(self.spin_max_completion, 2, 1)

        self.chk_balance = QCheckBox(tr("ft_balance"))
        self.chk_balance.setChecked(True)
        settings_layout.addWidget(self.chk_balance, 3, 0, 1, 2)
        self.chk_pii = QCheckBox(tr("ft_pii"))
        self.chk_pii.setChecked(True)
        settings_layout.addWidget(self.chk_pii, 3, 2, 1, 2)
        outer.addWidget(settings_group)

        # === 4. Progress ===
        prog_group = QGroupBox(tr("group_progress"))
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        prog_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel(tr("progress_ready"))
        self.progress_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        prog_layout.addWidget(self.progress_label)
        outer.addWidget(prog_group)

        # === 5. Tabs: Preview / Stats ===
        tabs = QTabWidget()

        # Preview tab
        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        self.pairs_table = QTableWidget(0, 4)
        self.pairs_table.setHorizontalHeaderLabels([
            tr("ft_col_type"), tr("ft_col_prompt"), tr("ft_col_completion"), tr("ft_col_source")
        ])
        header = self.pairs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.pairs_table.verticalHeader().setVisible(False)
        preview_layout.addWidget(self.pairs_table)
        tabs.addTab(preview_tab, tr("ft_tab_preview"))

        # Stats tab
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        stats_layout.addWidget(self.stats_text)
        tabs.addTab(stats_tab, tr("ft_tab_stats"))

        # Log tab
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.log_view)
        tabs.addTab(log_tab, tr("tab_log"))

        outer.addWidget(tabs, stretch=1)

        # Status
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(tr("status_ready"))

    def _connect_signals(self):
        pass

    def _restore_window_geometry(self):
        w = getattr(self.app_settings.gui, 'window_width', 1280)
        h = getattr(self.app_settings.gui, 'window_height', 820)
        self.resize(w, h)

    # === Handlers ===

    def _browse_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("menu_open_config"), "", "YAML (*.yaml *.yml)"
        )
        if path:
            self.config_edit.setText(path)
            self._load_config(path)

    def _load_config(self, path: str):
        try:
            self.config = load_config(path)
            self.config_path = path
            self._log("INFO", f"{tr('config_loaded')}: {path}")
        except Exception as e:
            self._log("ERROR", str(e))

    def _build_effective_config(self):
        if self.config is None:
            cfg_path = self.config_edit.text().strip()
            if not cfg_path or not Path(cfg_path).exists():
                QMessageBox.warning(self, tr("no_config"), tr("no_config_desc"))
                return None
            self._load_config(cfg_path)
        if self.config is None:
            return None
        cfg = self.config
        ensure_output_dirs(cfg)
        return cfg

    def _on_crawl(self):
        cfg = self._build_effective_config()
        if cfg is None:
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, tr("busy"), tr("busy_task"))
            return
        self._log("INFO", tr("crawl_started"))
        self.status.showMessage(tr("status_working"))
        self.btn_crawl.setEnabled(False)

        # Run crawl in QThread to avoid blocking UI
        from .gui import CrawlWorker
        self._crawl_worker = CrawlWorker(cfg, mode="crawl", resume=True)
        self._crawl_worker.progress.connect(self._on_progress)
        self._crawl_worker.log_message.connect(self._log)
        self._crawl_worker.finished_stats.connect(self._on_crawl_finished)
        self._crawl_worker.error.connect(self._on_crawl_error)
        self._crawl_worker.start()

    def _on_crawl_finished(self, stats):
        self._log("INFO", f"{tr('crawl_finished')}: {stats}")
        self.status.showMessage(tr("status_ready"))
        self.btn_crawl.setEnabled(True)

    def _on_crawl_error(self, err):
        self._log("ERROR", err)
        self.status.showMessage(tr("status_ready"))
        self.btn_crawl.setEnabled(True)

    def _on_postprocess(self):
        cfg = self._build_effective_config()
        if cfg is None:
            return
        if hasattr(self, '_crawl_worker') and self._crawl_worker and self._crawl_worker.isRunning():
            QMessageBox.warning(self, tr("busy"), tr("busy_task"))
            return
        self._log("INFO", tr("postprocess_started"))
        self.status.showMessage(tr("status_working"))
        self.btn_postprocess.setEnabled(False)

        from .gui import CrawlWorker
        self._pp_worker = CrawlWorker(cfg, mode="postprocess")
        self._pp_worker.progress.connect(self._on_progress)
        self._pp_worker.log_message.connect(self._log)
        self._pp_worker.finished_stats.connect(self._on_pp_finished)
        self._pp_worker.error.connect(self._on_pp_error)
        self._pp_worker.start()

    def _on_pp_finished(self, stats):
        self._log("INFO", f"Post-process done: {stats}")
        self.status.showMessage(tr("status_ready"))
        self.btn_postprocess.setEnabled(True)

    def _on_pp_error(self, err):
        self._log("ERROR", err)
        self.status.showMessage(tr("status_ready"))
        self.btn_postprocess.setEnabled(True)

    def _on_generate(self):
        cfg = self._build_effective_config()
        if cfg is None:
            return
        corpus_file = str(Path(cfg.output.corpus_file).parent / "corpus_final.jsonl")
        if not Path(corpus_file).exists():
            corpus_file = cfg.output.corpus_file
        if not Path(corpus_file).exists():
            QMessageBox.warning(self, tr("no_corpus"), tr("no_corpus_desc"))
            return

        self.pairs_table.setRowCount(0)
        self.progress_bar.setValue(0)
        self.status.showMessage(tr("status_working"))

        self.worker = FinetuneWorker(
            corpus_file=corpus_file,
            max_per_type=self.spin_max_per_type.value(),
            min_prompt=self.spin_min_prompt.value(),
            max_prompt=self.spin_max_prompt.value(),
            min_completion=self.spin_min_completion.value(),
            max_completion=self.spin_max_completion.value(),
            balance=self.chk_balance.isChecked(),
            remove_pii=self.chk_pii.isChecked(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.pair_found.connect(self._on_pair_found)
        self.worker.finished_result.connect(self._on_generate_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current, total, msg):
        if total > 0:
            self.progress_bar.setValue(int(current * 100 / total))
        self.progress_label.setText(msg)

    def _on_pair_found(self, pair: dict):
        row = self.pairs_table.rowCount()
        self.pairs_table.insertRow(row)
        self.pairs_table.setItem(row, 0, QTableWidgetItem(pair.get("task_type", "")))
        self.pairs_table.setItem(row, 1, QTableWidgetItem(pair.get("prompt", "")[:100]))
        self.pairs_table.setItem(row, 2, QTableWidgetItem(pair.get("completion", "")[:100]))
        self.pairs_table.setItem(row, 3, QTableWidgetItem(pair.get("source", "")[:50]))

    def _on_generate_finished(self, pairs: list, stats: dict):
        self.progress_bar.setValue(100)
        self.status.showMessage(tr("status_ready"))
        self._pairs = pairs
        self._stats = stats

        # Build a readable summary with per-stage counts + warnings
        stage_counts = stats.get("stage_counts", {})
        warnings_list = stats.get("warnings", [])
        q_stats = stats.get("quality", {})
        d_stats = stats.get("dedup", {})

        lines = []
        lines.append(f"=== Final dataset: {len(pairs)} pairs ===")
        lines.append("")
        lines.append("=== By task_type ===")
        for t, n in sorted(stats.get("by_type", {}).items()):
            lines.append(f"  {t:30s} {n:6d}")
        lines.append("")
        lines.append("=== Pipeline stages (pair count) ===")
        # Header row
        all_types = set()
        for sc in stage_counts.values():
            all_types.update(sc.keys())
        all_types = sorted(all_types)
        header = "  Stage" + " " * 18 + "Total" + "".join(f"{t[:12]:>13s}" for t in all_types)
        lines.append(header)
        for stage, sc in stage_counts.items():
            row_total = str(sum(sc.values()))
            row = f"  {stage:22s} {row_total:>5s}" + "".join(f"{sc.get(t, 0):>13d}" for t in all_types)
            lines.append(row)
        lines.append("")
        if q_stats:
            lines.append(f"=== Filter rejected: {q_stats.get('total', 0) - q_stats.get('kept', 0)} / {q_stats.get('total', 0)} ===")
            for reason, n in sorted(q_stats.get("rejected", {}).items(), key=lambda x: -x[1]):
                lines.append(f"  {reason:30s} {n:6d}")
            lines.append("")
        if d_stats:
            lines.append(f"=== Dedup removed: {d_stats.get('removed', 0)} duplicates (mode={d_stats.get('mode', '?')}) ===")
            lines.append("")
        if warnings_list:
            lines.append(f"=== ⚠ Warnings ({len(warnings_list)}) ===")
            for w in warnings_list:
                lines.append(f"  ⚠ {w}")
            lines.append("")
        lines.append("=== Raw stats (JSON) ===")
        lines.append(json.dumps(stats, ensure_ascii=False, indent=2))

        summary = "\n".join(lines)
        self.stats_text.setPlainText(summary)
        self._log("INFO", f"Generated {len(pairs)} pairs; {len(warnings_list)} warnings")

        msg = f"{len(pairs)} instruction pairs generated."
        if warnings_list:
            msg += f"\n\n⚠ {len(warnings_list)} warning(s) — see Stats tab."
        msg += "\nClick Export to save in fine-tuning format."
        QMessageBox.information(self, tr("ft_generate"), msg)

    def _on_error(self, err: str):
        self.status.showMessage(tr("status_ready"))
        self._log("ERROR", err)
        QMessageBox.critical(self, tr("error"), err)

    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] [{level}] {msg}")

    def _show_about(self):
        QMessageBox.about(self, tr("menu_about"),
            "CorpusBuilder — Fine-Tuning Mode\n"
            "Version: 0.2.0\n"
            "Instruction pair generator for LLM fine-tuning\n"
            "Formats: JSONL, ChatML, Alpaca, ShareGPT")

    def _on_progress_signal(self, current, total, msg):
        self._on_progress(current, total, msg)
