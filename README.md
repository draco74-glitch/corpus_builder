# corpus-builder

Сборщик сырого корпуса для pretraining LLM с дедупликацией, нормализацией и фильтрацией качества.
Проект реализован на основе технического анализа исходной версии `corpus_builder` и закрывает все критические проблемы оригинала.

## Два режима работы

- **CLI** — `corpus-builder crawl` / `async-crawl` / `postprocess` / `stats` / `diff` (для серверов, CI, пакетных задач)
- **GUI** — отдельное окно с кнопками «Загрузить config», «Папка корпуса», прогресс-баром, логом, таблицей последних записей, графиками статистики и аналитикой. Упаковывается в один `.exe` через PyInstaller.

## Оптимизации производительности (14 улучшений)

| # | Улучшение | Эффект |
|---|-----------|--------|
| 1 | **Нативный aiohttp для HTML** (`crawlers/async_html_crawler.py`) | 4-6x ускорение на больших списках URL |
| 2 | **Буферизованная запись в JSONL** (`writer.py:CorpusWriter`) | 5-15% экономии на syscalls |
| 3 | **Параллельный OCR для PDF** (`pdf_crawler._parallel_ocr`) | 10-20x на OCR-тяжёлых PDF |
| 4 | **Connection pooling** (`robots.make_session`) | 1.3x на повторных соединениях |
| 5 | **SQLite WAL-mode** для requests-cache (`http_cache._optimize_sqlite_cache`) | 1.4x на повторных прогонах |
| 6 | **Multiprocessing пост-обработка** (`parallel_postproc.py`) | 3-5x на 8 ядрах |
| 7 | **Streaming MinHash** (`dedup.dedup_minhash_streaming`) | экономия RAM для больших корпусов |
| 8 | **Ленивая инициализация краулеров** (`crawlers/__init__.py`) | 400 мс экономии на старте |
| 9 | **Pre-filter по robots.txt** (`robots.pre_filter_by_robots`) | 1 проверка на домен вместо N |
| 10 | **HTTP/2 через httpx** (`httpx_client.py`) | 1.2x на HTTP/2 сайтах |
| 11 | **Prefetch robots.txt** (`robots.prefetch_robots`) | параллельная загрузка для 50+ доменов |
| 12 | **Сжатие JSONL на лету** (`writer.GzipCorpusWriter`) | 4-6x экономия места на диске |
| 13 | **Memory-mapped чтение** (`mmap_reader.MmapJsonlReader`) | 2-3x на пост-обработке файлов > 1 ГБ |
| 14 | **Incremental dedup** (`incremental_dedup.IncrementalDedup`) | 2-3x на повторных прогонах пост-обработки |

**Ожидаемое ускорение**:
- 1000 источников: 48 мин → 5-7 мин (~7x)
- 10k источников: 5 часов → 45 минут (~7x)

## Возможности

### Типы источников (8 типов)

- HTML (статьи, блоги) — на базе `trafilatura`
- PDF (datasheet'ы, руководства) — `PyMuPDF` с поддержкой **двухколоночной вёрстки**, опциональным OCR (tesseract), **извлечением таблиц через pdfplumber**, **фильтром схем через OCR-ключевые слова** и структурированием по TOC
- GitHub-репозитории — через REST API + ZIP-архив (с защитой от Zip Slip), опционально: **Issues/PR**, **Wiki**, **директория docs/**
- StackExchange (вопросы и ответы) — через официальный API
- **DOAJ** — поиск открытых научных статей через DOAJ API
- **arXiv** — статьи из разделов eess.SP, eess.SY, cs.AR через arXiv API
- **Crossref** — метаданные DOI и рефераты
- **Wikipedia REST API** — статьи напрямую в JSON, без HTML-парсинга

- **Устойчивость к сбоям**:
  - `state.json` с отслеживанием обработанных и ошибочных URL
  - `--resume` для продолжения после сбоя
  - Чекпойнты каждые 50 источников (настраивается)
  - Все ошибки пишутся в `errors.jsonl` с указанием причины

- **Вежливость к сайтам**:
  - Проверка `robots.txt` per-domain с кэшированием
  - Per-domain rate limiter (задержка между запросами на один домен)
  - Кастомный User-Agent

- **Безопасность**:
  - Уникальные имена файлов на основе хэша URL (нет гонки за именами)
  - Защита от Zip Slip при распаковке GitHub-архивов
  - Проверка реального размера при стриминге (не только Content-Length)
  - Токены GitHub/StackExchange через переменные окружения

- **Качество корпуса для pretraining**:
  - **Дедупликация** трёх уровней:
    - Точная (`sha1` нормализованного текста)
    - Нечёткая (`MinHash LSH`, настраиваемый порог Jaccard)
    - По канонизированному URL (удаление `utm_*`, сортировка query)
    - По хэшу изображений (для PDF-схем)
  - **Нормализация текста**:
    - `unicodedata.NFKC` (приведение полноширинных и совместимых символов)
    - `ftfy.fix_text` (исправление «сломанных» кодировок)
    - Удаление zero-width и управляющих символов
    - Опциональная нормализация `ё → е` для русского
  - **Фильтрация качества**:
    - Длина (min/max chars)
    - Соотношение не-буквенных символов
    - Доля дублирующихся строк
    - Язык (RU/EN/bilingual/multi)

- **Дополнительно**:
  - Извлечение пар для instruction-tuning (README ↔ KiCad, вопрос ↔ принятый ответ)
  - Логирование через `loguru` с ротацией файлов
  - Прогресс-бар через `tqdm`
  - Pydantic-валидация конфигурации и записей

## Установка

### Системные зависимости

- Python 3.10+
- Tesseract OCR (для OCR-фолбэка PDF-сканов):
  ```bash
  # Ubuntu/Debian
  sudo apt install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng

  # macOS
  brew install tesseract tesseract-lang
  ```

### Python-зависимости

```bash
cd corpus_builder
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# или для development-установки:
pip install -e .[dev]
```

## Использование

### 1. Подготовка конфигурации

```bash
cp config.example.yaml config.yaml
# отредактируйте config.yaml, добавив свои источники
```

### 2. Запуск краулинга

```bash
# Полный прогон
corpus-builder crawl

# С возобновлением после сбоя (по умолчанию)
corpus-builder crawl

# Только первые 5 источников для отладки
corpus-builder crawl --limit 5

# Только HTML-страницы
corpus-builder crawl --source-type html

# Тестовый прогон без записи на диск
corpus-builder crawl --dry-run
```

### 3. Пост-обработка

```bash
# Дедупликация + фильтр + нормализация + извлечение пар
corpus-builder postprocess
```

### 4. Статистика

```bash
corpus-builder stats
```

## Структура выходных файлов

```
corpus_output/
├── raw_corpus.jsonl         # сырой корпус (с дубликатами)
├── deduped.jsonl            # после дедупликации
├── filtered.jsonl           # после фильтра качества
├── corpus_final.jsonl       # финальный нормализованный (для pretraining)
├── instruction_pairs.jsonl  # пары для instruction-tuning
├── errors.jsonl             # журнал ошибок краулинга
├── state.json               # состояние для resume
└── crawl.log                # логи

downloaded_files/             # бинарные файлы (PDF, KiCad, изображения)
```

## Формат записи корпуса

Каждая строка `corpus_final.jsonl` — JSON-объект:

```json
{
  "source_url": "https://...",
  "source_type": "html",
  "content": "Текст статьи...",
  "content_sha1": "abc123...",
  "downloaded_files": [
    {
      "type": "image",
      "original_url": "...",
      "local_path": "downloaded_files/schematic_abc.png",
      "sha1": "def456...",
      "size_bytes": 12345
    }
  ],
  "metadata": {"title": "...", "date": "..."},
  "categories": ["electronics", "kicad"],
  "date_accessed": "2026-08-06T11:22:33",
  "language": "ru",
  "license": null,
  "quality_score": 0.85,
  "is_duplicate": false,
  "duplicate_of": null,
  "status": "ok"
}
```

## Формат пар для instruction-tuning

`instruction_pairs.jsonl`:

```json
{
  "prompt": "На основе KiCad-описания...",
  "completion": "README проекта...",
  "source": "https://github.com/...",
  "task_type": "kicad_to_description"
}
```

## Переменные окружения

```bash
# .env (в .gitignore!)
GITHUB_TOKEN=ghp_xxx...           # для повышенного лимита GitHub API
STACKEXCHANGE_KEY=xxx...          # опционально, для повышенного лимита SE API
```

## Тесты

```bash
pytest tests/ -v
```

## Архитектура

```
corpus_builder/
├── corpus_builder/
│   ├── __init__.py
│   ├── __main__.py            # python -m corpus_builder
│   ├── cli.py                 # Click CLI
│   ├── config.py              # загрузка/валидация YAML
│   ├── models.py              # pydantic-модели
│   ├── state.py               # resume-состояние
│   ├── logging_setup.py       # loguru
│   ├── robots.py              # robots.txt + rate-limit
│   ├── http.py                # общие HTTP-функции, safe download
│   ├── text_utils.py          # нормализация, хэши, shingles
│   ├── pipeline.py            # оркестратор
│   ├── crawlers/
│   │   ├── __init__.py        # реестр
│   │   ├── base.py            # BaseCrawler (ABC)
│   │   ├── html_crawler.py    # trafilatura + bs4 fallback
│   │   ├── pdf_crawler.py     # PyMuPDF + pytesseract
│   │   ├── github_crawler.py  # GitHub API + ZIP (Zip Slip-safe)
│   │   └── forum_crawler.py   # StackExchange API
│   └── postproc/
│       ├── __init__.py
│       ├── dedup.py           # exact + MinHash + URL + images
│       ├── normalize.py       # NFKC + ftfy + ё→е
│       ├── quality.py         # длина, alpha, dup-lines, язык
│       └── extract_pairs.py   # README↔KiCad, Q↔A
├── tests/
│   ├── test_text_utils.py
│   ├── test_state.py
│   ├── test_quality.py
│   └── test_dedup.py
├── config.example.yaml
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Что закрыто из анализа оригинального проекта

| # | Проблема | Решение |
|---|----------|---------|
| 1 | Zip Slip | `github_crawler._safe_save` с `realpath`-проверкой |
| 2 | Гонка за именами файлов | `http.safe_filename` = `slugify + sha1(url)[:12]` |
| 3 | Импорт utils относительно cwd | Пакетная структура, `from corpus_builder.X import Y` |
| 4 | Нет resume | `State` + `--resume`, чекпойнты каждые 50 |
| 5 | Кодировка ответа | `charset-normalizer` fallback при `iso-8859-1` |
| 6 | Нет robots.txt | `RobotsCache` per-domain |
| 7 | Нет rate-limit per-domain | `RateLimiter` с min-интервалом на домен |
| 8 | Ветка захардкожена | `_get_default_branch` через GitHub API |
| 9 | Нет поддержки токена/LFS | `GITHUB_TOKEN` env + LFS-detection |
| 10 | Нет SE API | `StackExchangeCrawler` через `/questions/{id}` + `/answers` |
| 11 | Дубликаты контента | 3 уровня: exact + MinHash + URL |
| 12 | Нет нормализации | `normalize_text` (NFKC+ftfy+zero-width), `normalize_yo` |
| 13 | print вместо логов | `loguru` с rotating file handler |
| 14 | Нет OCR | `pytesseract` при `page_text < threshold` |
| 15 | Нет CLI | `click` с `crawl` / `postprocess` / `stats` |
| 16 | Нет валидации схемы | pydantic `CorpusRecord`, `AppConfig` |
| 17 | Размер только по Content-Length | Реальный подсчёт при стриминге |
| 18 | Конфиг не валидируется | `AppConfig(**yaml.safe_load(f))` |

## Лицензия

MIT — для собранного корпуса смотрите лицензии источников.
StackExchange: CC BY-SA 4.0 (записывается в поле `license`).
GitHub: по лицензии репозитория (запрашивается через API).

---

## GUI режим

Программа поставляется в двух вариантах:

1. **CLI** (для серверов и CI): `corpus-builder crawl --config config.yaml`
2. **GUI** (для домашнего использования): отдельное окно с кнопками

### Запуск GUI из исходников

```bash
pip install -e ".[gui]"
python -m corpus_builder.gui
# или
corpus-builder-gui
```

### Сборка .exe (PyInstaller)

```bash
pip install -e ".[gui,build]"

# Linux/macOS
bash build.sh
bash build.sh --clean  # пересобрать с нуля

# Windows
build.bat
build.bat --clean
```

Результат — `dist/CorpusBuilder.exe` (Windows) или `dist/CorpusBuilder` (Linux).
Размер ~225–250 МБ (включает Python + PySide6 + matplotlib + PyMuPDF + pyarrow).
Запуск двойным кликом, никаких дополнительных установок не требуется.

> ⚠️ Сборку нужно выполнять на той ОС, для которой предназначен .exe —
> PyInstaller не кросс-компилирует. Для Windows .exe собирайте на Windows.

### Возможности GUI

Главное окно содержит:

1. **Секция «Конфигурация»**:
   - Поле для выбора `config.yaml`
   - Поле для выбора папки, куда сохранять корпус (перекрывает конфиг)
   - Кнопка «Открыть папку» — открывает папку корпуса в проводнике
   - Опции: «Продолжить (resume)», «Повторить упавшие»

2. **Секция «Действия»** — кнопки:
   - ▶ Запустить краулинг
   - ⚙ Пост-обработка (дедупликация + фильтр + нормализация + пары)
   - ⏹ Остановить (мягкая остановка после текущего URL)
   - ⬇ Экспорт в HuggingFace dataset
   - ⬇ Экспорт в Parquet

3. **Секция «Прогресс»**:
   - Прогресс-бар (текущий URL / всего)
   - Текстовая метка с текущим URL

4. **Вкладки**:
   - **Лог** — цветной лог событий (INFO/WARN/ERROR) с авто-скроллом
   - **Последние записи** — таблица из 20 последних собранных записей (URL, тип, длина, язык, quality_score)
   - **Статистика** — четыре графика matplotlib (типы источников, языки, длины, качество) + текстовая сводка

5. **Трей**:
   - Сворачивание окна в системный трей
   - Уведомление при завершении задачи
   - Двойной клик по иконке — показать окно

6. **Resume**:
   - При закрытии во время работы краулинг мягко останавливается
   - Путь к config и папке запоминается в `~/.corpus_builder_gui.json`
   - Повторный запуск — просто отметьте «Продолжить» и нажмите «Запустить краулинг»

### Сценарий работы типового пользователя

1. Скачайте `CorpusBuilder.exe`
2. Отредактируйте `config.example.yaml` под свои источники, переименуйте в `config.yaml`
3. Запустите `CorpusBuilder.exe`
4. Нажмите «Обзор...» в строке `config.yaml` → выберите свой `config.yaml`
5. При необходимости укажите папку вывода (или используйте из конфига)
6. Убедитесь, что галка «Продолжить (resume)» стоит
7. Нажмите «▶ Запустить краулинг»
8. Следите за прогрессом в логе и таблице «Последние записи»
9. По завершении нажмите «⚙ Пост-обработка» — получите финальный `corpus_final.jsonl`
10. При необходимости экспортируйте в HuggingFace или Parquet

### Если что-то сломалось

- **Краулинг упал** — откройте `corpus_output/errors.jsonl`, посмотрите причину. Исправьте конфиг, отметьте «Повторить упавшие», нажмите «Запустить краулинг».
- **Программа зависла** — нажмите «⏹ Остановить» (мягкая остановка). Если не помогает — закройте через Диспетчер задач, при следующем запуске поставьте «Продолжить».
- **.exe не запускается** — проверьте, что Windows Defender не заблокировал файл (часто ложно срабатывает на PyInstaller-сборки). Скачайте файл заново и нажмите «Свойства → Разблокировать» в контекстном меню.
- **Не находит tesseract** — OCR PDF-сканов работать не будет, остальной функционал — будет. Для OCR установите [tesseract](https://github.com/UB-Mannheim/tesseract/wiki) и добавьте в PATH.

### Улучшения по сравнению с CLI-версией

- Наглядность: видно, что программа делает прямо сейчас, а не только последний `print()`
- Безопасность: при закрытии окна краулинг не теряется — состояние сохраняется
- Контроль: можно остановить в любой момент и продолжить позже
- Аналитика: графики статистики позволяют оценить качество корпуса до обучения
- Доступность: не нужно знать Python / командную строку — `.exe` запускается двойным кликом
