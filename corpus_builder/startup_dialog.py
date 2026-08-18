"""Стартовое окно — выбор режима: Pre-training или Fine-tuning.

При запуске CorpusBuilder показывается это окно.
Пользователь выбирает режим — открывается соответствующее окно.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QApplication,
)

from .logging_setup import get_logger

log = get_logger(__name__)

DARK_BG = "#1e1e1e"
DARKER_BG = "#252526"
ACCENT = "#007acc"
ACCENT_HOVER = "#1f8ad2"
TEXT_PRIMARY = "#d4d4d4"
TEXT_SECONDARY = "#858585"
BORDER = "#3c3c3c"


class StartupDialog(QDialog):
    """Диалог выбора режима при запуске."""

    # Возвращает "pretraining" или "finetuning"
    MODE_PRETRAINING = "pretraining"
    MODE_FINETUNING = "finetuning"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_mode: str | None = None
        self.setWindowTitle("CorpusBuilder — Select Mode")
        self.setFixedSize(600, 400)
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # Title
        title = QLabel("🛠️  CorpusBuilder")
        title.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {ACCENT};")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Select data collection mode")
        subtitle.setStyleSheet(f"font-size: 14px; color: {TEXT_SECONDARY};")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(10)

        # Two large buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(20)

        # Pre-training button
        btn_pre = self._create_mode_button(
            "📚  Pre-Training",
            "Collect raw text corpus for LLM pre-training.\n"
            "Sources: HTML, PDF, GitHub, StackExchange,\n"
            "DOAJ, arXiv, Crossref, Wikipedia.\n"
            "Output: corpus_final.jsonl",
            self.MODE_PRETRAINING,
            "#007acc"
        )
        btn_layout.addWidget(btn_pre)

        # Fine-tuning button
        btn_ft = self._create_mode_button(
            "🎯  Fine-Tuning",
            "Collect instruction-tuning pairs for LLM fine-tuning.\n"
            "Q&A pairs, code explanations, datasheets,\n"
            "summaries, translations, BOM generation.\n"
            "Output: ChatML, Alpaca, ShareGPT formats.",
            self.MODE_FINETUNING,
            "#4ec9b0"
        )
        btn_layout.addWidget(btn_ft)

        layout.addLayout(btn_layout)
        layout.addStretch()

        # Remember choice checkbox
        self.chk_remember = None  # could add "Remember choice" checkbox

        # Footer
        footer = QLabel("v0.2.0  ·  Non-Commercial License  ·  github.com/draco74-glitch/corpus_builder")
        footer.setStyleSheet(f"font-size: 11px; color: {TEXT_SECONDARY};")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)

    def _create_mode_button(self, title: str, description: str,
                             mode: str, color: str) -> QPushButton:
        """Create a large mode selection button."""
        btn = QPushButton(f"{title}\n\n{description}")
        btn.setFixedSize(260, 220)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {DARKER_BG};
                color: {TEXT_PRIMARY};
                border: 2px solid {BORDER};
                border-radius: 12px;
                font-size: 14px;
                text-align: center;
                padding: 20px;
            }}
            QPushButton:hover {{
                border: 2px solid {color};
                background-color: #2a2a2e;
            }}
            QPushButton:pressed {{
                border: 2px solid {color};
                background-color: {color};
            }}
        """)
        btn.clicked.connect(lambda: self._select(mode))
        return btn

    def _select(self, mode: str) -> None:
        self.selected_mode = mode
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QDialog {{
            background-color: {DARK_BG};
        }}
        QLabel {{
            color: {TEXT_PRIMARY};
            font-family: 'Segoe UI', 'SF Pro', 'DejaVu Sans';
        }}
        """)

    @staticmethod
    def ask_mode() -> str:
        """Show dialog and return selected mode.

        Returns "pretraining" or "finetuning".
        If user closes dialog — returns "pretraining" (default).
        """
        dialog = StartupDialog()
        if dialog.exec() == QDialog.Accepted and dialog.selected_mode:
            return dialog.selected_mode
        return StartupDialog.MODE_PRETRAINING
