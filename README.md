<div align="center">

# 🛠️ CorpusBuilder

**Сборщик сырого корпуса для pretraining LLM** с дедупликацией, нормализацией и фильтрацией качества

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests: 166+](https://img.shields.io/badge/Tests-166+-green.svg)](tests/)
[![PySide6 GUI](https://img.shields.io/badge/GUI-PySide6-007acc.svg)](https://doc.qt.io/qtforpython-6/)
[![One-dir](https://img.shields.io/badge/Build-One--dir-orange.svg)](CorpusBuilder.spec)
[![Auto-Update](https://img.shields.io/badge/Auto--Update-GitHub_Commits-success.svg)](corpus_builder/auto_updater.py)

[Возможности](#-возможности) ·
[Установка](#-установка) ·
[Использование](#-использование) ·
[Архитектура](#-архитектура) ·
[Скриншоты](#-скриншоты) ·
[Лицензия](#-лицензия)

</div>

---

## 📋 Оглавление

- [Возможности](#-возможности)
- [Типы источников](#-типы-источников)
- [Установка](#-установка)
- [Использование](#-использование)
- [Архитектура сборки: one-dir](#-архитектура-сборки-one-dir)
- [Авто-обновление](#-авто-обновление)
- [Оптимизации производительности](#-оптимизации-производительности)
- [GUI: 15 улучшений интерфейса](#-gui-15-улучшений-интерфейса)
- [Единое окно настроек](#-единое-окно-настроек)
- [Мастер создания config.yaml](#-мастер-создания-configyaml)
- [Скриншоты](#-скриншоты)
- [Разработка](#-разработка)
- [Лицензия](#-лицензия)

---

## ✨ Возможности

### Сбор корпуса

- **8 типов источников**: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia
- **Асинхронный краулинг** (aiohttp) — 4-6x быстрее синхронного
- **Resume после сбоя** — state.json с отслеживанием обработанных URL
- **robots.txt + per-domain rate limiter** — вежливый обход
- **Защита от видеопотоков** — блоклист 30+ расширений и 20+ доменов (YouTube, Vimeo, Twitch)
- **Connection pooling** — 20 соединений, авто-ретраи на 429/500/503
- **HTTP-кэш** (requests-cache + SQLite WAL) — повторные прогоны в 10x быстрее

### Качество корпуса

- **Дедупликация** 4 уровня:
  - Точная (`sha1` нормализованного текста)
  - Нечёткая (`MinHash LSH`, настраиваемый порог Jaccard)
  - По канонизированному URL (удаление `utm_*`, сортировка query)
  - По хэшу изображений (для PDF-схем)
  - Streaming и Incremental режимы для больших корпусов
- **Нормализация текста**: NFKC + ftfy + zero-width + опционально ё→е
- **Фильтрация качества**:
  - `fasttext-langdetect` — определение языка (точнее эвристики в 3-5x)
  - `kenlm` perplexity (опционально) — отбраковка мусорного текста
  - Спам/токсичность фильтр (RU+EN ключевые слова)
  - Code/text ratio — извлечение блоков кода для instruction-tuning
  - Длина, alpha ratio, доля дублирующихся строк, язык (RU/EN/bilingual/multi)

### Пост-обработка и экспорт

- **Извлечение пар для instruction-tuning** (6 типов):
  - README ↔ KiCad
  - Вопрос → принятый ответ (StackExchange)
  - Datasheet → спецификации компонента
  - Статья → TL;DR
  - Код → описание
  - FAQ Q&A
- **Экспорт**: HuggingFace dataset (с dataset_infos.json), Parquet (zstd), JSONL (.gz)

### GUI

- **Тёмная тема** (VS Code Dark+ стиль), 5 тем оформления
- **Прогресс-бар с ETA**: «150/1000 | ETA: 5m 30s | 2.3 URL/s»
- **Лог с подсветкой** (INFO/WARN/ERROR) и поиском (Ctrl+F)
- **Таблица последних записей** с контекстным меню
- **Статистика**: 4 графика matplotlib + текстовая сводка
- **Трей** с уведомлением о завершении
- **Авто-обновление** через GitHub коммиты (Ctrl+U)

---

## 📊 Типы источников

| Тип | Источник | Библиотека | Особенности |
|-----|----------|------------|-------------|
| `html` | Статьи, блоги | trafilatura | Извлечение главного текста, авто-определение кодировки |
| `pdf` | Datasheet'ы, руководства | PyMuPDF + pytesseract | Двухколоночная вёрстка, OCR, таблицы (pdfplumber), фильтр схем |
| `github_repo` | GitHub репозитории | REST API + ZIP | Issues/PR, Wiki, docs/, защита от Zip Slip, LFS-detection |
| `stackexchange` | Q&A форумы | SE API | Топ-вопросы по тегам, accepted_answer_id, CC BY-SA лицензия |
| `doaj` | Открытые журналы | DOAJ API | Метаданные, рефераты, CC BY лицензия |
| `arxiv` | Научные препринты | arXiv API | eess.SP, eess.SY, cs.AR, полнотекстовый поиск |
| `crossref` | DOI метаданные | Crossref API | Авторы, журнал, DOI, ссылки на PDF |
| `wikipedia` | Энциклопедия | REST API | Прямой JSON без HTML-парсинга, превью изображений |

---

## 🚀 Установка

### Требования

- **Python 3.13** (НЕ 3.14+ — PySide6 не имеет wheels для 3.14)
- **Tesseract OCR** (опционально, для PDF-сканов): [установить](https://github.com/UB-Mannheim/tesseract/wiki)

### Из исходников

```bash
git clone https://github.com/draco74-glitch/corpus_builder.git
cd corpus_builder

# Создать venv на Python 3.13
python3.13 -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Установить зависимости
pip install -r requirements.txt
pip install -e .[gui,build]

# Запустить GUI
python -m corpus_builder.gui
```

### Готовый .exe (Windows)

1. Скачайте `CorpusBuilder.zip` из [Releases](https://github.com/draco74-glitch/corpus_builder/releases)
2. Распакуйте в любую папку
3. Запустите `CorpusBuilder.exe`

---

## 📖 Использование

### GUI режим

1. Запустите `CorpusBuilder.exe`
2. Нажмите **«✨ Создать config.yaml»** — загрузите Excel с URL
3. Нажмите **«▶ Запустить краулинг»**
4. Дождитесь завершения → нажмите **«⚙ Пост-обработка»**
5. Результат: `corpus_output/corpus_final.jsonl`

### CLI режим

```bash
# Синхронный краулинг
corpus-builder -c config.yaml crawl

# Асинхронный (4-8x быстрее)
corpus-builder -c config.yaml async-crawl --max-concurrent 8

# Пост-обработка (дедупликация + фильтр + нормализация + пары)
corpus-builder -c config.yaml postprocess

# Статистика
corpus-builder -c config.yaml stats

# Сравнение двух корпусов
corpus-builder diff corpus_old.jsonl corpus_new.jsonl --html report.html
```

### Мастер создания config.yaml

```bash
# Из Excel/CSV
python -m corpus_builder.config_generator from-csv sources.csv -o config.yaml

# Поиск GitHub репозиториев
python -m corpus_builder.config_generator from-github --topics kicad --max-repos 100 -o config.yaml

# Топ вопросов StackExchange
python -m corpus_builder.config_generator from-stackexchange --tags kicad --max-questions 100 -o config.yaml
```

---

## 🏗 Архитектура сборки: one-dir

```
dist/
└── CorpusBuilder/
    ├── CorpusBuilder.exe          ← 24 МБ (загрузчик)
    └── _internal/
        ├── python313.dll
        ├── PySide6/
        ├── corpus_builder/        ← .py файлы (обновляются автоматически)
        └── ...
```

| Метрика | One-file (старая) | One-dir (текущая) |
|---------|-------------------|-------------------|
| Размер .exe | 450 МБ | 24 МБ |
| Холодный старт | 8 сек | 0.8 сек (**10x**) |
| Обновление кода | 450 МБ | 150 КБ (**2527x меньше**) |
| Антивирус false-positive | частый | редкий |

### Сборка

```bash
# Windows
build.bat --zip     # сборка + ZIP + patch.zip

# Linux/macOS
bash build.sh --zip
```

---

## 🔄 Авто-обновление

Программа **автоматически проверяет новые коммиты** на GitHub при старте.

| Способ | Описание |
|--------|----------|
| Автоматически | При запуске — toast-уведомление если есть новый коммит |
| `Ctrl+U` | Ручная проверка: меню «Справка → Проверить обновления» |
| Программно | `CommitUpdater.check_for_commit_updates()` |

При обновлении:
1. Скачиваются только `.py` файлы (~150 КБ)
2. Создаётся backup текущей папки
3. Файлы заменяются в `_internal/corpus_builder/`
4. Перезапуск — готово

---

## ⚡ Оптимизации производительности

| # | Оптимизация | Эффект |
|---|-------------|--------|
| 1 | Нативный aiohttp для HTML | 4-6x на больших списках URL |
| 2 | Буферизованная запись JSONL | 5-15% экономии на syscalls |
| 3 | Параллельный OCR для PDF | 10-20x на OCR-тяжёлых PDF |
| 4 | Connection pooling | 1.3x на повторных соединениях |
| 5 | SQLite WAL для HTTP-кэша | 1.4x на повторных прогонах |
| 6 | Multiprocessing пост-обработка | 3-5x на 8 ядрах |
| 7 | Streaming MinHash | экономия RAM для больших корпусов |
| 8 | Ленивая инициализация краулеров | 400 мс экономии на старте |
| 9 | Pre-filter по robots.txt | 1 проверка на домен вместо N |
| 10 | HTTP/2 через httpx | 1.2x на HTTP/2 сайтах |
| 11 | Prefetch robots.txt | 50x для 50+ доменов |
| 12 | Сжатие JSONL (.jsonl.gz) | 4-6x экономия места |
| 13 | Memory-mapped чтение | 2-3x на файлах >1 ГБ |
| 14 | Incremental dedup (LSH в файле) | 2-3x на повторных прогонах |

**Ожидаемое ускорение**: 1000 источников — 48 мин → 5-7 мин (~7x)

---

## 🎨 GUI: 15 улучшений интерфейса

| # | Улучшение | Описание |
|---|-----------|----------|
| A | Drag-and-Drop config.yaml | Перетащите файл прямо в окно |
| B | Контекстное меню | ПКМ на записи → Открыть URL, Копировать, Удалить |
| C | Поиск по логу (Ctrl+F) | Подсветка, навигация ↑↓, счётчик N/M |
| D | Сохранение разделителей | Позиции QSplitter в JSON |
| E | Toast-уведомления | Всплывающие окна с fade-in/out анимацией |
| F | Переключение тем | Dark ↔ Light, горячее переключение |
| G | Превью KiCad | Парсинг .kicad_sch, таблица компонентов |
| H | История config.yaml | Последние 10 файлов, авто-фильтр |
| I | Прогресс с ETA | «N/total | ETA: 5m 30s | 2.3 URL/s» |
| J | Сравнение корпусов | Diff dialog с HTML-отчётом |
| K | YAML-редактор | Подсветка синтаксиса (VS Code-style) |
| L | Dashboard | 3 графика + текстовая сводка |
| M | Мастер первого запуска | 5 шагов: источники → качество → токены |
| N | Локализация RU/EN | 40+ переводимых строк |
| O | Material Design темы | Blue, Green, Purple |

---

## ⚙️ Единое окно настроек

10 вкладок со всеми опциями программы:

1. **📋 Общие** — тема, язык, пути, размер окна
2. **🌐 Краулинг** — User-Agent, timeout, delay, proxy, cache
3. **📄 HTML** — режим извлечения, изображения, файлы
4. **📕 PDF** — OCR, двухколоночная вёрстка, таблицы, схемы
5. **🐙 GitHub** — токен, ветка, Issues, Wiki, docs/
6. **💬 StackExchange** — API key, сайт, score
7. **✅ Качество** — длина, alpha, код, спам, perplexity
8. **🔄 Дедупликация** — exact, MinHash, streaming, incremental
9. **⚡ Производительность** — async, workers, gzip, parallel
10. **🎨 Интерфейс** — прогресс-бар, тема, логирование

Открыть: `Ctrl+,` или меню «Настройки → Все настройки...»

---

## 🧩 Мастер создания config.yaml

Загрузите Excel/CSV с колонками `url`, `depth`, `categories` — мастер автоматически:
1. Прочитает Excel (поддержка .xlsx, .xls, .csv)
2. Для каждой строки с `depth > 0` выполнит BFS-обход
3. Дедуплицирует найденные URL
4. Сохранит готовый `config.yaml`

**Опции**:
- **⚡ Skip crawl** — мгновенно, только URL из Excel
- **Параллельных seeds** — сколько URL обрабатывать параллельно (5 = оптимально)
- **Same-domain / поддомены** — фильтрация ссылок

---

## 📸 Скриншоты

> Скриншоты будут добавлены позже

---

## 🔧 Разработка

### Структура проекта

```
corpus_builder/
├── corpus_builder/
│   ├── crawlers/              # 8 краулеров
│   ├── postproc/              # dedup, quality, normalize, export
│   ├── gui.py                 # Главное окно (QMainWindow)
│   ├── gui_improvements.py    # 15 улучшений интерфейса (A-O)
│   ├── settings_dialog.py     # Единое окно настроек (10 вкладок)
│   ├── config_generator.py    # Генератор config.yaml
│   ├── async_config_generator.py  # Асинхронный генератор (10-30x)
│   ├── auto_updater.py        # Авто-обновление по коммитам
│   ├── pipeline.py            # Оркестратор
│   └── ...
├── tests/                     # 166+ unit-тестов
├── CorpusBuilder.spec         # PyInstaller one-dir
├── build.bat / build.sh       # Скрипты сборки
└── README.md
```

### Тесты

```bash
pytest tests/ -v                          # все тесты
pytest tests/test_quality_filters.py -v   # конкретный модуль
pytest tests/ -q --ignore=tests/test_vcr_cassettes.py  # без сетевых
```

### Вклад в проект

См. [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📄 Лицензия

**CorpusBuilder License — Non-Commercial Use**

- ✅ **Свободное использование** для личных, образовательных, академических и research целей
- ✅ Модификация и распространение (с сохранением лицензии)
- ❌ **Коммерческое использование запрещено** без отдельного разрешения
- 💼 Для коммерческого использования — [свяжитесь с автором](https://github.com/draco74-glitch/corpus_builder)

Полный текст лицензии: [LICENSE](LICENSE)

---

<div align="center">

**[⬆ Наверх](#-оглавление)**

Сделано с ❤️ для open-source сообщества

</div>
