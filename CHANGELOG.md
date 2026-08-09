# Changelog

Все заметные изменения проекта CorpusBuilder документируются здесь.
Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [Unreleased]

## [0.2.0] — 2026-08-08

### Added
- **One-dir архитектура** — запуск в 10x быстрее, авто-обновление без пересборки
- **Авто-обновление по коммитам** — подтягивание .py файлов из GitHub main
- **8 типов источников**: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia
- **14 оптимизаций производительности** (7x ускорение):
  - Асинхронный aiohttp-краулинг (4-6x)
  - Параллельный OCR для PDF (10-20x)
  - Multiprocessing пост-обработка (3-5x)
  - SQLite WAL для HTTP-кэша (1.4x)
  - Streaming MinHash для больших корпусов
  - Incremental dedup с сохранением LSH-индекса
  - Memory-mapped чтение для файлов >1 ГБ
  - Сжатие JSONL на лету (.jsonl.gz)
- **Расширенные фильтры качества**:
  - fasttext-langdetect (язык)
  - kenlm perplexity (опционально)
  - Спам/токсичность фильтр
  - Code/text ratio с извлечением блоков кода
- **GitHub-краулер**: Issues/PR, Wiki, docs/ directory
- **PDF-краулер**: двухколоночная вёрстка, pdfplumber таблицы, фильтр схем
- **4 типа SFT-пар**: datasheet→specs, article→summary, code→explanation, FAQ Q&A
- **GUI: 15 улучшений интерфейса** (A-O):
  - Drag-and-drop config.yaml
  - Контекстное меню на записях
  - Поиск по логу (Ctrl+F)
  - Toast-уведомления
  - 5 тем оформления (dark, light, Material Blue/Green/Purple)
  - Превью KiCad-файлов
  - История недавних config.yaml
  - Прогресс с ETA
  - Сравнение корпусов (diff dialog)
  - Встроенный YAML-редактор
  - Dashboard с метриками
  - Мастер первого запуска
  - Локализация RU/EN
- **Единое окно настроек** — 10 вкладок со всеми опциями программы
- **Мастер создания config.yaml** — Excel → BFS-обход → готовый конфиг
- **Защита от видеопотоков** — блоклист 30+ расширений и 20+ доменов
- **Экспорт**: HuggingFace dataset, Parquet (zstd), HTML diff-отчёты
- **166 unit-тестов**, все проходят

### Fixed
- Loguru crash в PyInstaller windowed mode (sys.stderr = None)
- kenlm не собирается на Python 3.13+ — сделан опциональным
- PySide6 не устанавливается на Python 3.14 — pin requires-python <3.14
- Краулинг зависает на страницах с видео
- Кнопка «Создать config.yaml» не работала из-за asyncio event loop на Windows

## [0.1.0] — 2026-08-06

### Added
- Базовый пайплайн: краулинг → дедупликация → нормализация → качество
- 4 типа источников: HTML (trafilatura), PDF (PyMuPDF), GitHub, StackExchange
- Resume после сбоя через state.json
- robots.txt + per-domain rate limiter
- Дедупликация: точная (sha1), MinHash (LSH), по URL, по изображениям
- Нормализация: NFKC + ftfy + zero-width + ё→е
- CLI: crawl, postprocess, stats
- 56 unit-тестов
