"""Автоматический поиск источников для config.yaml.

Объединяет несколько стратегий:
  1. GitHub topics — поиск репозиториев
  2. StackExchange tags — топ вопросов
  3. Wikipedia categories — статьи по категориям
  4. Seed-crawl — обход стартовых URL

Дедуплицирует URL и сохраняет готовый config.yaml.

Использование:
    from corpus_builder.auto_discover import AutoDiscover
    discover = AutoDiscover()
    sources = discover.discover(
        topics=["kicad", "pcb"],
        se_tags=["kicad", "stm32"],
        wiki_categories=["Electronics", "Printed circuit boards"],
    )
    discover.save_config(sources, "config.auto.yaml")
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

from .config_generator import (
    build_config, from_github_topics, from_stackexchange_tags,
    from_wikipedia, make_source,
)
from .logging_setup import get_logger

log = get_logger(__name__)

ProgressCallback = Callable[[int, int, str], None]


class AutoDiscover:
    """Автоматический поиск источников из нескольких платформ.

    Использование:
        discover = AutoDiscover()
        sources = discover.discover(
            topics=["kicad", "pcb"],
            se_tags=["kicad", "stm32"],
            wiki_categories=["Electronics"],
            wiki_lang="en",
            max_per_source=50,
        )
        discover.save_config(sources, "config.auto.yaml")
    """

    def __init__(self):
        self._all_sources: list[dict] = []
        self._seen_urls: set[str] = set()
        self._stats: dict[str, int] = {}

    def discover(
        self,
        topics: list[str] | None = None,
        se_tags: list[str] | None = None,
        se_site: str = "electronics",
        wiki_categories: list[str] | None = None,
        wiki_lang: str = "en",
        seed_urls: list[str] | None = None,
        max_per_source: int = 50,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict]:
        """Запустить авто-поиск источников.

        Параметры:
            topics: GitHub topics (например, ["kicad", "pcb", "embedded"])
            se_tags: StackExchange теги (например, ["kicad", "stm32"])
            se_site: StackExchange сайт (по умолчанию "electronics")
            wiki_categories: Wikipedia категории (например, ["Electronics", "Operational amplifiers"])
            wiki_lang: язык Wikipedia (en, ru, de, fr, ...)
            seed_urls: стартовые URL для seed-crawl
            max_per_source: максимум источников с одной платформы

        Возвращает list[dict] уникальных источников.
        """
        self._all_sources = []
        self._seen_urls = set()
        self._stats = {}

        total_steps = 0
        if topics:
            total_steps += 1
        if se_tags:
            total_steps += 1
        if wiki_categories:
            total_steps += 1
        if seed_urls:
            total_steps += 1

        current_step = 0

        # 1. GitHub topics
        if topics:
            current_step += 1
            if on_progress:
                on_progress(current_step, total_steps, f"GitHub: поиск по topics={topics}")
            self._search_github(topics, max_per_source)

        # 2. StackExchange tags
        if se_tags:
            current_step += 1
            if on_progress:
                on_progress(current_step, total_steps, f"StackExchange: поиск по tags={se_tags}")
            self._search_stackexchange(se_tags, se_site, max_per_source)

        # 3. Wikipedia categories
        if wiki_categories:
            current_step += 1
            if on_progress:
                on_progress(current_step, total_steps, f"Wikipedia: поиск по categories={wiki_categories}")
            self._search_wikipedia(wiki_categories, wiki_lang, max_per_source)

        # 4. Seed URLs
        if seed_urls:
            current_step += 1
            if on_progress:
                on_progress(current_step, total_steps, f"Seed URLs: добавление {len(seed_urls)} URL")
            for url in seed_urls:
                self._add_source(url, categories=["seed"])

        if on_progress:
            on_progress(total_steps, total_steps, f"Готово: {len(self._all_sources)} источников")

        log.info(f"Auto-discover complete: {self._stats}")
        return self._all_sources

    def _add_source(self, url: str, categories: list[str] | None = None) -> None:
        """Добавить источник с дедупликацией."""
        if url in self._seen_urls:
            return
        self._seen_urls.add(url)
        self._all_sources.append(make_source(url, categories=categories))

    def _search_github(self, topics: list[str], max_repos: int) -> None:
        """Поиск репозиториев на GitHub."""
        try:
            sources = from_github_topics(
                topics=topics,
                max_repos=max_repos,
            )
            for s in sources:
                self._add_source(s["url"], s.get("categories"))
            self._stats["github"] = len(sources)
            log.info(f"GitHub: found {len(sources)} repos")
        except Exception as e:
            log.warning(f"GitHub search failed: {e}")
            self._stats["github"] = 0

    def _search_stackexchange(self, tags: list[str], site: str, max_questions: int) -> None:
        """Поиск вопросов на StackExchange."""
        try:
            sources = from_stackexchange_tags(
                site=site,
                tags=tags,
                max_questions=max_questions,
                min_score=3,
            )
            for s in sources:
                self._add_source(s["url"], s.get("categories"))
            self._stats["stackexchange"] = len(sources)
            log.info(f"StackExchange: found {len(sources)} questions")
        except Exception as e:
            log.warning(f"StackExchange search failed: {e}")
            self._stats["stackexchange"] = 0

    def _search_wikipedia(self, categories: list[str], lang: str, max_articles: int) -> None:
        """Поиск статей на Wikipedia."""
        try:
            sources = from_wikipedia(
                categories=categories,
                lang=lang,
                max_articles=max_articles,
                depth=1,
            )
            for s in sources:
                self._add_source(s["url"], s.get("categories"))
            self._stats["wikipedia"] = len(sources)
            log.info(f"Wikipedia: found {len(sources)} articles")
        except Exception as e:
            log.warning(f"Wikipedia search failed: {e}")
            self._stats["wikipedia"] = 0

    def save_config(self, sources: list[dict], output_path: str | Path) -> str:
        """Сохранить источники как config.yaml.

        Возвращает путь к созданному файлу.
        """
        build_config(sources, output_path)
        log.info(f"Config saved: {output_path} ({len(sources)} sources)")
        return str(output_path)

    def get_stats(self) -> dict:
        """Вернуть статистику по платформам."""
        return {
            "total": len(self._all_sources),
            "by_platform": self._stats,
            "unique_urls": len(self._seen_urls),
        }

    @staticmethod
    def get_preset_topics() -> dict[str, list[str]]:
        """Предустановленные наборы тем для быстрого старта."""
        return {
            "electronics_general": {
                "github_topics": ["kicad", "pcb", "embedded", "electronics"],
                "se_tags": ["kicad", "pcb", "schematic", "embedded"],
                "se_site": "electronics",
                "wiki_categories": ["Electronics", "Printed circuit boards", "Electronic circuits"],
                "wiki_lang": "en",
            },
            "analog_design": {
                "github_topics": ["analog", "op-amp", "ltspice"],
                "se_tags": ["analog", "operational-amplifier", "op-amp"],
                "se_site": "electronics",
                "wiki_categories": ["Operational amplifiers", "Analog circuits", "Analog electronics"],
                "wiki_lang": "en",
            },
            "microcontrollers": {
                "github_topics": ["stm32", "avr", "esp32", "arduino"],
                "se_tags": ["stm32", "avr", "esp32", "arduino"],
                "se_site": "electronics",
                "wiki_categories": ["Microcontrollers", "STM32", "Arduino"],
                "wiki_lang": "en",
            },
            "power_electronics": {
                "github_topics": ["power-electronics", "smps", "dc-dc-converter"],
                "se_tags": ["power-electronics", "smps", "dc-dc"],
                "se_site": "electronics",
                "wiki_categories": ["Power electronics", "Switched-mode power supplies", "Voltage regulators"],
                "wiki_lang": "en",
            },
            "rf_microwave": {
                "github_topics": ["rf", "antenna", "microwave", "sdr"],
                "se_tags": ["rf", "antenna", "microwave", "sdr"],
                "se_site": "electronics",
                "wiki_categories": ["Radio frequency", "Antennas (radio)", "Microwave technology"],
                "wiki_lang": "en",
            },
            "russian_electronics": {
                "github_topics": ["electronics", "kicad"],
                "se_tags": [],
                "se_site": "electronics",
                "wiki_categories": ["Электроника", "Печатные платы", "Радиоэлектроника"],
                "wiki_lang": "ru",
            },
        }
