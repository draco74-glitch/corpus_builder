<div align="center">

# 🛠️ CorpusBuilder

**Raw corpus builder for LLM pretraining** with deduplication, text normalization, and quality filtering

[![License: Non-Commercial](https://img.shields.io/badge/License-Non--Commercial-blue.svg)](LICENSE)
[![Version: 0.2.1](https://img.shields.io/badge/Version-0.2.1-orange.svg)](CHANGELOG.md)
[![Python 3.10–3.13](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests: 460](https://img.shields.io/badge/Tests-450-green.svg)](tests/)
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

- **9 source types** (all configurable): HTML, PDF, GitHub, StackExchange (question **or** `/questions/tagged/<tag>` list), forum, DOAJ, arXiv, Crossref, Wikipedia
- **Async crawling** — per-task executors over the *same* polite session (configured User-Agent, pooling, 429/5xx retries, HTTP cache), per-domain semaphores, rate limiter and per-URL timeout
- **Resume after failure** — state.json tracks processed URLs
- **robots.txt + per-domain rate limiter** — polite crawling; fail-closed when the
  file cannot be fetched, with a per-source `ignore_robots: true` escape for
  API-backed crawlers (see `config.example.yaml`)
- **Video/streaming protection** — 27 media extensions + 20 streaming/social domains, plus Content-Type, size and wall-clock limits per download
- **Connection pooling** — 20 connections / 50 max, auto-retries on 429/500/502/503/504
- **HTTP cache** (requests-cache + SQLite WAL, TTL config) — repeat runs reuse responses; already-downloaded files are revalidated by size (HEAD) so refreshed datasheets are re-fetched
- **Per-URL timeout** — automatically skips URLs that hang for more than N minutes

### Corpus Quality

- **Deduplication** (4 levels; record identity = content, not `source_url`):
  - Exact (`sha1` of normalized text)
  - Fuzzy (`MinHash LSH`, configurable Jaccard threshold)
  - URL canonicalization (removes `utm_*`, sorts query string)
  - Image hash (for PDF schematics)
  - Streaming and Incremental modes for large corpora (`dedup.streaming` / `dedup.incremental`)
  - Record identity is content/position based: repeated or missing `source_url` no longer crashes post-processing or silently deletes distinct content
- **Text normalization**: NFKC + ftfy + zero-width + optional ё→е
- **Quality filtering**:
  - `fasttext-langdetect` — language detection (more accurate than heuristics)
  - Rejection counters report concrete reasons (`too_short`, `spam:commercial`,
    `duplicate`, …) instead of `unknown`
  - `kenlm` perplexity (optional) — rejects garbage text
  - Spam/toxicity filter (RU+EN keywords)
  - Code/text ratio — extracts code blocks for instruction-tuning
  - Length, alpha ratio, duplicate line ratio, language (RU/EN/bilingual/multi)

### Post-Processing & Export

- **Instruction-tuning pair extraction** — one shared `task_type` vocabulary for
  both entry points (GUI *Post-Process* and the Fine-Tuning window):
  `kicad_to_description` / `description_to_kicad`, `qa_stackexchange`,
  `multi_turn_dialogue`, `datasheet_specs` / `datasheet_structure`,
  `article_summary` / `article_expansion`, `code_explanation`,
  `concept_explanation`, `bom_generation`, `faq_qa`
  (a `translation` generator exists but is **disabled** — it needs parallel corpora)
- **Prompt templates come from `prompt_variations`** and can be extended with a
  `prompts.yaml` next to the project — no Russian-only strings baked into the data
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
| `github_repo` | GitHub repos | REST API + ZIP | Issues/PR with comments and paging, wiki from `{repo}.wiki`, archive streamed to disk with size cap, Zip Slip protection, LFS pointers skipped |
| `stackexchange` | Q&A (set `ignore_robots: true`, the site 403s its own robots.txt) | SE API | Single question **or** top-questions-by-tag list; accepted answer kept in metadata; CC BY-SA |
| `doaj` | Open journals | DOAJ API | Metadata, abstracts, CC BY license |
| `arxiv` | Preprints | arXiv API (HTTPS) | `cat:`/`ti:` queries, `/list/<cat>` and `/abs/<id>` URLs, ids |
| `crossref` | DOI metadata | Crossref API | Single DOI resolved via `/works/{doi}`; licenses from the record; polite-pool contact in UA |
| `wikipedia` | Encyclopedia | REST API | Direct JSON without HTML parsing, ru/en/de hosts, thumbnails |
| `forum` | alias of `stackexchange` | SE API | Same crawler; kept for already-written configs |

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

### CLI extras

```bash
corpus-builder crawl --async          # async path (config: pipeline.use_async)
corpus-builder export --format both   # corpus_final.jsonl → HF dir + Parquet
corpus-builder package --zip          # ZIP/patch.zip for auto-update (dev tool)
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

CorpusBuilder can update itself from GitHub commits (`Ctrl+U`), which replaces the
`.py` files inside the one-dir build — no `.exe` rebuild needed.

Safety rules implemented in `corpus_builder/auto_updater.py`:

| Rule | Why |
|------|-----|
| A release asset is installed **only** if a `<asset>.sha256` sidecar matches (opt-out: `AutoUpdater(require_digest=False)`) | the updater writes code that runs on next start |
| Truncated downloads are rejected (`Content-Length` vs bytes written, incremental SHA-256) | half-written module = broken install |
| Every archive member is validated (`verify_member_path`): no absolute paths, no `..`, no non-`.py` | Zip Slip |
| The commit marker moves **only** when the update applied completely; otherwise the backup is restored and the next run retries | previously a partial update froze the app in a mixed version state |
| `CommitUpdater` reads the branch it was constructed with | `branch=` was silently ignored, `main` always used |
| In dev mode the *imported package directory* is updated, not `cwd/corpus_builder` | running from another folder used to rewrite an unrelated checkout |

Manual rollback: `CommitUpdater().restore_backup()` (also used automatically after a
failed update).

---

## 🔌 Migration notes (0.2.0 → 0.2.1)

Behaviour deliberately changed — check your configs/scripts:

| Change | You may need to |
|--------|-----------------|
| `task_type` names unified: `article_to_summary` → `article_summary`, `summary_to_article` → `article_expansion`, `code_to_explanation` → `code_explanation`, `datasheet_to_specs` → `datasheet_specs`, `datasheet_to_structure` → `datasheet_structure` | update filters/statistics that matched old names |
| Fresh (`--no-resume`) crawl now rewrites `raw_corpus.jsonl` + `errors.jsonl` instead of appending | keep a copy of the previous corpus if you relied on appending |
| `state.json` / `errors.jsonl` / `crawl.log` default to the folder of `corpus_file` | pin them explicitly in `output:` if you want them elsewhere |
| Fine-Tuning export writes `finetune_pairs.jsonl` (previously `instruction_pairs.jsonl`) so it cannot silently overwrite the pre-train pipeline output | rename in downstream scripts |
| PII: numbers are redacted only next to phone/IP/SSN context; obfuscated e-mails are fully removed | set `finetune.pii_aggressive: true` for the old blanket behaviour |
| Spam filter: only explicit advertising signals are rejected | expect a larger filtered corpus; check `rejected_by_reason` |
| `dedup.streaming` / `dedup.incremental` / `export.write_gzip` / `pipeline.use_async` are now honoured | enable explicitly in `config.yaml` |
| Updater: a release asset needs `<asset>.sha256`, and the commit marker only advances on a fully applied update | publish the sidecar with each release |
| `cdns` `cloudflare.com` / `cdn.jsdelivr.net` removed from the streaming blocklist; query-string media detection now matches value suffixes only | previously skipped domains are crawled again |

## 🛡 Data-safety notes (0.2.1)

Things the pipeline now guarantees — mostly regressions found in review, see
[CHANGELOG](CHANGELOG.md):

- **`resume=False` rewrites the corpus instead of appending to it.** Previously
  `--no-resume` cleared `state.json` but `raw_corpus.jsonl` was only ever opened
  in append mode, so every fresh run duplicated the whole dataset (and then broke
  dedup). `--dry-run` never touches existing files.
- **Duplicate or missing `source_url` cannot crash or silently delete records.**
  Dedup keys records by position/content; `duplicate_of` still shows the
  original URL.
- **The spam filter only fires on advertising.** It used to contain a rule
  “long text without electronics keywords = spam”, which discarded every
  non-component text (and even I2C/resistor pages) while reporting the reason as
  `unknown`.
- **PII redaction is context-aware.** Version quads (`1.22.331.4`), resistor
  lists (`4.7.10.2`), part numbers (`123-45-6789`) and spaced timing values stay
  intact; unambiguous phone formats, real emails/tokens/keys (including
  obfuscated “user [at] host [dot] tld”, now fully removed) are redacted. Set
  `finetune.pii_aggressive: true` for the old blanket behaviour.
- **Parquet/HF export is lossless w.r.t. crawler output**: `metadata` and
  `downloaded_files` are kept (KiCad paths and accepted-answer ids are needed to
  rebuild pairs), and unknown values stay NULL instead of being coerced to
  `""` / `0.0`.
- **`max_per_type` truncation is visible**: `stats["dropped_by_type"]` reports
  how many pairs balancing removed.

---

## ⚡ Performance Optimizations

Wired and disabled_by_default where noted. Numbers are *expected* effects from the
design, not benchmarks — measured numbers are welcome as PRs.

| # | Optimization | Status | Notes |
|---|-------------|--------|-------|
| 1 | Async crawl (executors over one session) | ✅ `pipeline.use_async`, `corpus-builder async-crawl`, GUI checkbox | keeps UA/pool/retries/cache/rate-limit semantics of the sync path |
| 2 | Buffered JSONL writer | ✅ `postproc/normalize.py` | `CorpusWriter` |
| 3 | Parallel OCR for PDF | ✅ `crawlers.pdf.ocr_parallel_workers` | thread pool around the external tesseract process |
| 4 | Connection pooling + retries | ✅ always | 20/50, 429/5xx |
| 5 | HTTP cache (requests-cache + SQLite WAL) | ✅ `output.use_http_cache`, `cache_ttl_hours` | off it falls back to a plain pooled session |
| 6 | Multiprocessing post-processing | ✅ `pipeline.parallel_postproc` (off by default) | quality stage |
| 7 | Streaming MinHash | ✅ `dedup.streaming` (off) | corpus is not loaded whole |
| 8 | Lazy crawler import | ✅ | importing `corpus_builder.crawlers` no longer pulls PyMuPDF/trafilatura |
| 9 | Pre-filter by robots.txt | ✅ both crawl paths | one fetch per domain, before crawling |
| 10 | HTTP/2 via httpx | ⚠️ experimental helper, not connected | see `httpx_client` module docstring |
| 11 | Prefetch robots.txt | ✅ used by the pre-filter | |
| 12 | Gzip JSONL export | ✅ `export.write_gzip` | writes `corpus_final.jsonl.gz` |
| 13 | Memory-mapped reading | ✅ via incremental dedup | |
| 14 | Incremental dedup (LSH index on disk) | ✅ `dedup.incremental` (off) | index file in config |

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

### Tests and linting

```bash
pytest tests/ -v                          # all tests
pytest tests/test_quality_filters.py -v   # specific module
pytest tests/ -q --ignore=tests/test_vcr_cassettes.py  # without network
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## ⚠️ Operational notes

- **robots.txt 403**: some crawlers-facing sites answer 403 for `/robots.txt` itself.
  Following RFC 9309 the pipeline then treats the host as disallowed and skips those
  sources (visible as a `WARNING` in the log, counted in `skipped`). For sources whose
  crawler talks to an *API on another host* (StackExchange → `api.stackexchange.com`),
  set `ignore_robots: true` on that source. `output.robots_fail_open: true` restores
  the previous "unreachable = allowed" behaviour globally.
- **The updater executes code downloaded from the repository.** It now requires a
  matching `<asset>.sha256` for release assets, but the commit-based flow still trusts
  whatever is on the configured branch. If you need stronger guarantees, disable
  auto-update (`gui.check_updates_on_start: false` + don't press Ctrl+U) and update
  from reviewed release archives.
- **Performance figures in the table above are design expectations, not benchmarks.**
- **`tesseract` is optional.** Without it, scanned-PDF OCR is skipped and the
  "schematic" image filter keeps nothing (fail-closed) instead of saving every image.

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
