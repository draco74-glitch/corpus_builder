<div align="center">

# 🛠️ CorpusBuilder

**Raw corpus builder for LLM pretraining** with deduplication, text normalization, and quality filtering

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Tests: 194+](https://img.shields.io/badge/Tests-194+-green.svg)](tests/)
[![PySide6 GUI](https://img.shields.io/badge/GUI-PySide6-007acc.svg)](https://doc.qt.io/qtforpython-6/)
[![One-dir](https://img.shields.io/badge/Build-One--dir-orange.svg)](CorpusBuilder.spec)
[![Auto-Update](https://img.shields.io/badge/Auto--Update-GitHub_Commits-success.svg)](corpus_builder/auto_updater.py)

[Features](#-features) ·
[Installation](#-installation) ·
[Usage](#-usage) ·
[Architecture](#-architecture) ·
[License](#-license)

</div>

---

## 📋 Table of Contents

- [Features](#-features)
- [Source Types](#-source-types)
- [Installation](#-installation)
- [Usage](#-usage)
- [Build Architecture: One-Dir](#-build-architecture-one-dir)
- [Auto-Update](#-auto-update)
- [Performance Optimizations](#-performance-optimizations)
- [GUI: 15 Interface Improvements](#-gui-15-interface-improvements)
- [Settings](#-settings)
- [Config Generator Wizard](#-config-generator-wizard)
- [Development](#-development)
- [License](#-license)

---

## ✨ Features

### Corpus Collection

- **8 source types**: HTML, PDF, GitHub, StackExchange, DOAJ, arXiv, Crossref, Wikipedia
- **Async crawling** (aiohttp) — 4-6x faster than synchronous
- **Resume after failure** — state.json tracks processed URLs
- **robots.txt + per-domain rate limiter** — polite crawling
- **Video/streaming protection** — blocks 30+ extensions and 20+ domains (YouTube, Vimeo, Twitch)
- **Connection pooling** — 20 connections, auto-retries on 429/500/503
- **HTTP cache** (requests-cache + SQLite WAL) — repeat runs 10x faster
- **Per-URL timeout** — automatically skips URLs that hang for more than N minutes

### Corpus Quality

- **Deduplication** (4 levels):
  - Exact (`sha1` of normalized text)
  - Fuzzy (`MinHash LSH`, configurable Jaccard threshold)
  - URL canonicalization (removes `utm_*`, sorts query string)
  - Image hash (for PDF schematics)
  - Streaming and Incremental modes for large corpora
- **Text normalization**: NFKC + ftfy + zero-width + optional ё→е
- **Quality filtering**:
  - `fasttext-langdetect` — language detection (3-5x more accurate than heuristics)
  - `kenlm` perplexity (optional) — rejects garbage text
  - Spam/toxicity filter (RU+EN keywords)
  - Code/text ratio — extracts code blocks for instruction-tuning
  - Length, alpha ratio, duplicate line ratio, language (RU/EN/bilingual/multi)

### Post-Processing & Export

- **Instruction-tuning pair extraction** (6 types):
  - README ↔ KiCad
  - Question → accepted answer (StackExchange)
  - Datasheet → component specifications
  - Article → TL;DR summary
  - Code → explanation
  - FAQ Q&A
- **Export**: HuggingFace dataset (with dataset_infos.json), Parquet (zstd), JSONL (.gz)

### GUI

- **Dark theme** (VS Code Dark+ style), 5 themes
- **Progress bar with ETA**: "150/1000 | ETA: 5m 30s | 2.3 URL/s"
- **Colored log** (INFO/WARN/ERROR) with search (Ctrl+F)
- **Recent records table** with context menu
- **Statistics**: 4 matplotlib charts + text summary
- **System tray** with completion notification
- **Auto-update** via GitHub commits (Ctrl+U)

---

## 📊 Source Types

| Type | Source | Library | Features |
|------|--------|---------|----------|
| `html` | Articles, blogs | trafilatura | Main text extraction, auto-encoding detection |
| `pdf` | Datasheets, manuals | PyMuPDF + pytesseract | Two-column layout, OCR, tables (pdfplumber), schematic filter |
| `github_repo` | GitHub repos | REST API + ZIP | Issues/PR, Wiki, docs/, Zip Slip protection, LFS detection |
| `stackexchange` | Q&A forums | SE API | Top questions by tags, accepted_answer_id, CC BY-SA license |
| `doaj` | Open journals | DOAJ API | Metadata, abstracts, CC BY license |
| `arxiv` | Preprints | arXiv API | eess.SP, eess.SY, cs.AR, full-text search |
| `crossref` | DOI metadata | Crossref API | Authors, journal, DOI, PDF links |
| `wikipedia` | Encyclopedia | REST API | Direct JSON without HTML parsing, image thumbnails |

---

## 🚀 Installation

### Requirements

- **Python 3.13** (NOT 3.14+ — PySide6 has no wheels for 3.14)
- **Tesseract OCR** (optional, for PDF scans): [install](https://github.com/UB-Mannheim/tesseract/wiki)

### From Source

```bash
git clone https://github.com/draco74-glitch/corpus_builder.git
cd corpus_builder

# Create venv with Python 3.13
python3.13 -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt
pip install -e .[gui,build]

# Launch GUI
python -m corpus_builder.gui
```

### Pre-built .exe (Windows)

1. Download `CorpusBuilder.zip` from [Releases](https://github.com/draco74-glitch/corpus_builder/releases)
2. Extract to any folder
3. Run `CorpusBuilder.exe`

---

## 📖 Usage

### GUI Mode

1. Launch `CorpusBuilder.exe`
2. Click **"✨ Create config.yaml"** — load an Excel file with URLs
3. Click **"▶ Start Crawling"**
4. Wait for completion → click **"⚙ Post-Process"**
5. Result: `corpus_output/corpus_final.jsonl`

### CLI Mode

```bash
# Synchronous crawl
corpus-builder -c config.yaml crawl

# Async crawl (4-8x faster)
corpus-builder -c config.yaml async-crawl --max-concurrent 8

# Post-processing (dedup + filter + normalize + pairs)
corpus-builder -c config.yaml postprocess

# Statistics
corpus-builder -c config.yaml stats

# Compare two corpora
corpus-builder diff corpus_old.jsonl corpus_new.jsonl --html report.html
```

### Config Generator

```bash
# From Excel/CSV
python -m corpus_builder.config_generator from-csv sources.csv -o config.yaml

# Search GitHub repositories
python -m corpus_builder.config_generator from-github --topics kicad --max-repos 100 -o config.yaml

# Top StackExchange questions
python -m corpus_builder.config_generator from-stackexchange --tags kicad --max-questions 100 -o config.yaml

# Wikipedia articles by category
python -m corpus_builder.config_generator from-wikipedia --categories Electronics --lang en -o config.yaml

# Merge multiple configs (with smart deduplication)
python -m corpus_builder.config_generator merge c1.yaml c2.yaml c3.yaml -o config.full.yaml
```

---

## 🏗 Build Architecture: One-Dir

```
dist/
└── CorpusBuilder/
    ├── CorpusBuilder.exe          ← 24 MB (bootloader)
    └── _internal/
        ├── python313.dll
        ├── PySide6/
        ├── corpus_builder/        ← .py files (auto-updatable)
        └── ...
```

| Metric | One-file (old) | One-dir (current) |
|--------|----------------|-------------------|
| .exe size | 450 MB | 24 MB |
| Cold start | 8 sec | 0.8 sec (**10x**) |
| Code update | 450 MB | 150 KB (**2527x smaller**) |
| Antivirus false-positive | frequent | rare |

### Build

```bash
# Windows
build.bat --zip     # build + ZIP + patch.zip

# Linux/macOS
bash build.sh --zip
```

---

## 🔄 Auto-Update

The program **automatically checks for new commits** on GitHub at startup.

| Method | Description |
|--------|-------------|
| Automatic | On startup — toast notification if new commit available |
| `Ctrl+U` | Manual check: Help → Check for Updates |
| Programmatic | `CommitUpdater.check_for_commit_updates()` |

When updating:
1. Only `.py` files are downloaded (~150 KB)
2. A backup of the current folder is created
3. Files are replaced in `_internal/corpus_builder/`
4. Restart — done

---

## ⚡ Performance Optimizations

| # | Optimization | Effect |
|---|-------------|--------|
| 1 | Native aiohttp for HTML | 4-6x on large URL lists |
| 2 | Buffered JSONL writer | 5-15% syscall savings |
| 3 | Parallel OCR for PDF | 10-20x on OCR-heavy PDFs |
| 4 | Connection pooling | 1.3x on repeated connections |
| 5 | SQLite WAL for HTTP cache | 1.4x on repeat runs |
| 6 | Multiprocessing post-processing | 3-5x on 8 cores |
| 7 | Streaming MinHash | RAM savings for large corpora |
| 8 | Lazy crawler initialization | 400 ms startup savings |
| 9 | Pre-filter by robots.txt | 1 check per domain instead of N |
| 10 | HTTP/2 via httpx | 1.2x on HTTP/2 sites |
| 11 | Prefetch robots.txt | 50x for 50+ domains |
| 12 | Gzip JSONL compression | 4-6x disk savings |
| 13 | Memory-mapped reading | 2-3x on files >1 GB |
| 14 | Incremental dedup (LSH in file) | 2-3x on repeat post-processing |

**Expected speedup**: 1000 sources — 48 min → 5-7 min (~7x)

---

## 🎨 GUI: 15 Interface Improvements

| # | Improvement | Description |
|---|-------------|-------------|
| A | Drag-and-Drop config.yaml | Drag files directly into the window |
| B | Context menu | Right-click record → Open URL, Copy, Delete |
| C | Log search (Ctrl+F) | Highlight, navigation ↑↓, match counter |
| D | Splitter state saver | QSplitter positions saved to JSON |
| E | Toast notifications | Popups with fade-in/out animation |
| F | Theme switching | Dark ↔ Light, hot-swap, no restart |
| G | KiCad preview | Parse .kicad_sch, component table |
| H | Recent configs | Last 10 files, auto-filter non-existent |
| I | Progress with ETA | "N/total | ETA: 5m 30s | 2.3 URL/s" |
| J | Corpus diff | Diff dialog with HTML report |
| K | YAML editor | Syntax highlighting (VS Code-style) |
| L | Dashboard | 3 charts + text summary |
| M | First-run wizard | 5 steps: sources → quality → tokens |
| N | Localization RU/EN | 40+ translatable strings |
| O | Material Design themes | Blue, Green, Purple |

---

## ⚙️ Settings

10 tabs with all program options:

1. **📋 General** — theme, language, paths, window size
2. **🌐 Crawling** — User-Agent, timeout, delay, proxy, cache
3. **📄 HTML** — extraction mode, images, file extensions
4. **📕 PDF** — OCR, two-column layout, tables, schematics
5. **🐙 GitHub** — token, branch, Issues, Wiki, docs/
6. **💬 StackExchange** — API key, site, score
7. **✅ Quality** — length, alpha, code, spam, perplexity
8. **🔄 Deduplication** — exact, MinHash, streaming, incremental
9. **⚡ Performance** — async, workers, gzip, parallel
10. **🎨 Interface** — progress bar, theme, logging

Open: `Ctrl+,` or menu Settings → All Settings...

---

## 🧩 Config Generator Wizard

Load an Excel/CSV with columns `url`, `depth`, `categories` — the wizard automatically:
1. Reads the Excel (supports .xlsx, .xls, .csv)
2. For each row with `depth > 0`, performs BFS crawl
3. Deduplicates found URLs
4. Saves a ready-to-use `config.yaml`

**Options**:
- **⚡ Skip crawl** — instant, only URLs from Excel
- **Concurrent seeds** — how many URLs to process in parallel (5 = optimal)
- **Same-domain / subdomains** — link filtering

### Auto-Discover

Automatically search for sources across GitHub, StackExchange, and Wikipedia:

1. Click **"🔄 Auto-Discover Sources"**
2. Select a preset (Electronics, Analog, Microcontrollers, Power, RF, Multilingual)
3. Click **"🔍 Start Search"**
4. Save the generated `config.yaml`

### Merge Configs

Combine multiple config.yaml files into one with smart deduplication:
- Exact URL matching
- Canonicalized URL (removes utm_*, sorts query, normalizes trailing slash)
- Category merging from duplicates

---

## 🔧 Development

### Project Structure

```
corpus_builder/
├── corpus_builder/
│   ├── crawlers/              # 8 crawlers
│   ├── postproc/              # dedup, quality, normalize, export
│   ├── gui.py                 # Main window (QMainWindow)
│   ├── gui_improvements.py    # 15 UI improvements (A-O)
│   ├── settings_dialog.py     # Settings dialog (10 tabs)
│   ├── config_generator.py    # Config generator
│   ├── async_config_generator.py  # Async generator (10-30x)
│   ├── auto_updater.py        # Auto-update via commits
│   ├── auto_discover.py       # Auto source discovery
│   ├── pipeline.py            # Orchestrator
│   └── ...
├── tests/                     # 194+ unit tests
├── CorpusBuilder.spec         # PyInstaller one-dir
├── build.bat / build.sh       # Build scripts
└── README.md
```

### Tests

```bash
pytest tests/ -v                          # all tests
pytest tests/test_quality_filters.py -v   # specific module
pytest tests/ -q --ignore=tests/test_vcr_cassettes.py  # without network
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📄 License

**CorpusBuilder License — Non-Commercial Use**

- ✅ **Free to use** for personal, educational, academic, and research purposes
- ✅ Modification and redistribution (with same license)
- ❌ **Commercial use prohibited** without permission
- 💼 For commercial use — [contact the author](https://github.com/draco74-glitch/corpus_builder)

Full license text: [LICENSE](LICENSE)

---

<div align="center">

**[⬆ Back to top](#-table-of-contents)**

Made with ❤️ for the open-source community

</div>
