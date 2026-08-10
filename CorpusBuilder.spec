# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec для сборки CorpusBuilder (one-dir mode).

One-dir архитектура:
  - .exe файл маленький (~15 МБ, только загрузчик)
  - Все зависимости в _internal/ рядом с .exe
  - Запуск в 10-15x быстрее (не нужно распаковывать во временную папку)
  - Антивирусы меньше ругаются (нормальная структура приложения)
  - Возможность авто-обновления (замена отдельных .py файлов)

Использование:
    pyinstaller CorpusBuilder.spec --noconfirm
Результат:
    dist/CorpusBuilder/CorpusBuilder.exe   (Windows)
    dist/CorpusBuilder/CorpusBuilder       (Linux)
"""

import sys
from pathlib import Path

block_cipher = None

project_root = Path(SPECPATH).resolve()
launcher_path = str(project_root / "launcher.py")

a = Analysis(
    [launcher_path],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "config.example.yaml"), "."),
        (str(project_root / "README.md"), "."),
        # corpus_builder как отдельные .py файлы (для авто-обновления через patch.zip)
        # PyInstaller копирует их в _internal/corpus_builder/
        # Это позволяет заменять отдельные файлы без пересборки .exe
        (str(project_root / "corpus_builder"), "corpus_builder"),
    ],
    hiddenimports=[
        "corpus_builder",
        "corpus_builder.gui",
        "corpus_builder.gui_improvements",
        "corpus_builder.cli",
        "corpus_builder.pipeline",
        "corpus_builder.config",
        "corpus_builder.models",
        "corpus_builder.state",
        "corpus_builder.http",
        "corpus_builder.text_utils",
        "corpus_builder.robots",
        "corpus_builder.logging_setup",
        "corpus_builder.app_settings",
        "corpus_builder.settings_dialog",
        "corpus_builder.async_config_generator",
        "corpus_builder.config_generator",
        "corpus_builder.config_generator_dialog",
        "corpus_builder.config_editor",
        "corpus_builder.crawlers",
        "corpus_builder.crawlers.base",
        "corpus_builder.crawlers.html_crawler",
        "corpus_builder.crawlers.pdf_crawler",
        "corpus_builder.crawlers.github_crawler",
        "corpus_builder.crawlers.forum_crawler",
        "corpus_builder.crawlers.academic_crawlers",
        "corpus_builder.crawlers.async_html_crawler",
        "corpus_builder.postproc",
        "corpus_builder.postproc.dedup",
        "corpus_builder.postproc.normalize",
        "corpus_builder.postproc.quality",
        "corpus_builder.postproc.extract_pairs",
        "corpus_builder.postproc.export",
        "corpus_builder.async_pipeline",
        "corpus_builder.http_cache",
        "corpus_builder.httpx_client",
        "corpus_builder.proxy_rotator",
        "corpus_builder.diff",
        "corpus_builder.analytics",
        "corpus_builder.writer",
        "corpus_builder.mmap_reader",
        "corpus_builder.incremental_dedup",
        "corpus_builder.parallel_postproc",
        "corpus_builder.quality_filters",
        "corpus_builder.auto_updater",
        "corpus_builder.auto_discover",
        "corpus_builder.auto_discover_dialog",
        "corpus_builder.zip_distributor",
        "corpus_builder.merge_config_dialog",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "matplotlib.backends.backend_qtagg",
        "pyarrow",
        "pyarrow.parquet",
        "trafilatura",
        "ftfy",
        "charset_normalizer",
        "datasketch",
        "fitz",
        "pytesseract",
        "PIL",
        "PIL.Image",
        "loguru",
        "slugify",
        "tqdm",
        "click",
        "yaml",
        "selectolax",
        "aiohttp",
        "httpx",
        "asyncio",
        "concurrent.futures",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "test",
        "tests",
        "pytest",
        "vcrpy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CorpusBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CorpusBuilder",
)
