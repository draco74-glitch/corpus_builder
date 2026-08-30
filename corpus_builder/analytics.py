"""Расширенная аналитика для GUI:
  1. Граф времени — скорость обработки в records/minute
  2. Распределение по доменам — какие сайты дают больше всего данных
  3. Heatmap ошибок — какие домены чаще падают

Используется как самостоятельный виджет, который встраивается в главное окно
или в отдельную вкладку «Аналитика».
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# Палитра для согласованности с главным окном
DARK_BG = "#1e1e1e"
DARKER_BG = "#252526"
BORDER = "#3c3c3c"
TEXT_PRIMARY = "#d4d4d4"
TEXT_SECONDARY = "#858585"
ACCENT = "#007acc"
ERROR_COLOR = "#f44747"
SUCCESS_COLOR = "#4ec9b0"


class AnalyticsWidget:
    """Виджет аналитики с тремя графиками.

    Использование:
        analytics = AnalyticsWidget()
        canvas = analytics.get_canvas()
        layout.addWidget(canvas)
        analytics.refresh(corpus_file, errors_file)
    """

    def __init__(self):
        self.fig = Figure(figsize=(10, 8), facecolor=DARKER_BG)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setStyleSheet("background: transparent;")

        # Применяем тёмную тему matplotlib
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

        # 3 графика: 2 вверху + 1 широкий внизу
        self.ax_speed = self.fig.add_subplot(2, 2, 1)
        self.ax_domains = self.fig.add_subplot(2, 2, 2)
        self.ax_errors = self.fig.add_subplot(2, 1, 2)
        self.fig.tight_layout(pad=1.5)

    def get_canvas(self) -> FigureCanvas:
        return self.canvas

    def refresh(self, corpus_file: str | Path, errors_file: str | Path | None = None) -> None:
        """Перерисовать все графики на основе данных из corpus и errors."""
        corpus_file = Path(corpus_file)
        if not corpus_file.exists():
            self._draw_empty("Нет данных о корпусе")
            return

        # Загружаем записи корпуса
        records = []
        with open(corpus_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        # Загружаем ошибки, если файл указан и существует
        error_records = []
        if errors_file and Path(errors_file).exists():
            with open(errors_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        error_records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        self._draw_speed_chart(records)
        self._draw_domains_chart(records)
        self._draw_errors_heatmap(records, error_records)
        self.fig.tight_layout(pad=1.5)
        self.canvas.draw_idle()

    def _draw_empty(self, message: str) -> None:
        """Показать сообщение «нет данных» на всех графиках."""
        for ax in (self.ax_speed, self.ax_domains, self.ax_errors):
            ax.clear()
            ax.text(0.5, 0.5, message, ha="center", va="center",
                    color=TEXT_SECONDARY, transform=ax.transAxes)
            ax.set_axis_off()
        self.canvas.draw_idle()

    def _draw_speed_chart(self, records: list[dict]) -> None:
        """Граф скорости: сколько записей в минуту было собрано."""
        self.ax_speed.clear()

        if not records:
            self.ax_speed.text(0.5, 0.5, "Нет данных", ha="center", va="center",
                               color=TEXT_SECONDARY, transform=self.ax_speed.transAxes)
            self.ax_speed.set_axis_off()
            return

        # Группируем по минутам
        by_minute: Counter[str] = Counter()
        for r in records:
            ts = r.get("date_accessed") or ""
            if ts:
                # Округляем до минуты: 2026-08-07T11:22:33 → 2026-08-07 11:22
                minute = ts[:16].replace("T", " ")
                if minute.startswith("20"):
                    by_minute[minute] += 1

        if not by_minute:
            self.ax_speed.text(0.5, 0.5, "Нет меток времени", ha="center", va="center",
                               color=TEXT_SECONDARY, transform=self.ax_speed.transAxes)
            self.ax_speed.set_axis_off()
            return

        # Сортируем по времени
        sorted_minutes = sorted(by_minute.items())
        times = [m[0][-5:] for m in sorted_minutes]  # только HH:MM
        counts = [m[1] for m in sorted_minutes]

        self.ax_speed.plot(times, counts, color=ACCENT, marker="o", linewidth=2, markersize=4)
        self.ax_speed.fill_between(times, counts, color=ACCENT, alpha=0.2)
        self.ax_speed.set_title("Скорость обработки (records/minute)", fontsize=10)
        self.ax_speed.set_xlabel("Минута", fontsize=9)
        self.ax_speed.set_ylabel("Записей", fontsize=9)
        self.ax_speed.tick_params(labelsize=8)
        self.ax_speed.grid(True, alpha=0.3)
        # Поворачиваем метки оси X для читаемости
        for label in self.ax_speed.get_xticklabels():
            label.set_rotation(45)
            label.set_horizontalalignment("right")

    def _draw_domains_chart(self, records: list[dict]) -> None:
        """Распределение по доменам — топ-10 источников."""
        self.ax_domains.clear()

        if not records:
            self.ax_domains.text(0.5, 0.5, "Нет данных", ha="center", va="center",
                                 color=TEXT_SECONDARY, transform=self.ax_domains.transAxes)
            self.ax_domains.set_axis_off()
            return

        by_domain: Counter[str] = Counter()
        for r in records:
            url = r.get("source_url") or ""
            if not url:
                continue
            try:
                domain = urlparse(url).netloc
                if domain:
                    by_domain[domain] += 1
            except Exception:
                continue

        if not by_domain:
            self.ax_domains.text(0.5, 0.5, "Нет URL", ha="center", va="center",
                                 color=TEXT_SECONDARY, transform=self.ax_domains.transAxes)
            self.ax_domains.set_axis_off()
            return

        # Топ-10 доменов
        top = by_domain.most_common(10)
        domains = [d[0][:25] for d in top]  # обрезаем до 25 символов
        counts = [d[1] for d in top]

        # Горизонтальная bar-диаграмма для читаемости длинных доменов
        self.ax_domains.barh(domains[::-1], counts[::-1], color=ACCENT)
        self.ax_domains.set_title("Топ-10 доменов по числу записей", fontsize=10)
        self.ax_domains.set_xlabel("Записей", fontsize=9)
        self.ax_domains.tick_params(labelsize=8)
        self.ax_domains.grid(True, alpha=0.3, axis="x")

    def _draw_errors_heatmap(self, records: list[dict], error_records: list[dict]) -> None:
        """Heatmap ошибок: какие домены чаще падают vs успешны."""
        self.ax_errors.clear()

        # Считаем успешные и ошибочные записи по доменам
        by_domain: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "error": 0})
        for r in records:
            url = r.get("source_url") or ""
            try:
                domain = urlparse(url).netloc
            except Exception:
                continue
            if not domain:
                continue
            if r.get("status") == "ok":
                by_domain[domain]["ok"] += 1
            else:
                by_domain[domain]["error"] += 1

        # Также считаем из errors.jsonl
        for e in error_records:
            url = e.get("source_url") or ""
            try:
                domain = urlparse(url).netloc
            except Exception:
                continue
            if domain:
                by_domain[domain]["error"] += 1

        if not by_domain:
            self.ax_errors.text(0.5, 0.5, "Нет данных", ha="center", va="center",
                                color=TEXT_SECONDARY, transform=self.ax_errors.transAxes)
            self.ax_errors.set_axis_off()
            return

        # Топ-15 доменов по общему числу обработок
        totals = [(d, v["ok"], v["error"]) for d, v in by_domain.items()]
        totals.sort(key=lambda x: x[1] + x[2], reverse=True)
        top = totals[:15]

        domains = [t[0][:25] for t in top]
        ok_counts = [t[1] for t in top]
        err_counts = [t[2] for t in top]

        # Stacked horizontal bar
        import numpy as np
        y = np.arange(len(domains))
        self.ax_errors.barh(y, ok_counts, color=SUCCESS_COLOR, label="OK", alpha=0.8)
        self.ax_errors.barh(y, err_counts, left=ok_counts, color=ERROR_COLOR,
                            label="Errors", alpha=0.8)
        self.ax_errors.set_yticks(y)
        self.ax_errors.set_yticklabels(domains)
        self.ax_errors.set_title("Успешные vs ошибочные записи по доменам (топ-15)", fontsize=10)
        self.ax_errors.set_xlabel("Записей", fontsize=9)
        self.ax_errors.tick_params(labelsize=8)
        self.ax_errors.legend(loc="lower right", fontsize=9)
        self.ax_errors.grid(True, alpha=0.3, axis="x")
        self.ax_errors.invert_yaxis()  # топ-домен сверху
