# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec для сборки CorpusBuilder.exe.

Собирает one-file executable с GUI (windowed mode).
Использование:
    pyinstaller CorpusBuilder.spec
Результат: dist/CorpusBuilder.exe (на Windows) или dist/CorpusBuilder (на Linux)
"""

import sys
from pathlib import Path

block_cipher = None

# Корень проекта (где лежит этот .spec-файл)
project_root = Path(SPECPATH).resolve()

# Точка входа — небольшой launcher, который делает абсолютные импорты
launcher_path = str(project_root / "launcher.py")

a = Analysis(
    [launcher_path],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # Подключаем конфиг-пример и README для встраивания
        (str(project_root / "config.example.yaml"), "."),
        (str(project_root / "README.md"), "."),
    ],
    hiddenimports=[
        # Все модули, которые PyInstaller может не заметить
        "corpus_builder",
        "corpus_builder.gui",
        "corpus_builder.cli",
        "corpus_builder.pipeline",
        "corpus_builder.config",
        "corpus_builder.models",
        "corpus_builder.state",
        "corpus_builder.http",
        "corpus_builder.text_utils",
        "corpus_builder.robots",
        "corpus_builder.logging_setup",
        "corpus_builder.crawlers",
        "corpus_builder.crawlers.base",
        "corpus_builder.crawlers.html_crawler",
        "corpus_builder.crawlers.pdf_crawler",
        "corpus_builder.crawlers.github_crawler",
        "corpus_builder.crawlers.forum_crawler",
        "corpus_builder.postproc",
        "corpus_builder.postproc.dedup",
        "corpus_builder.postproc.normalize",
        "corpus_builder.postproc.quality",
        "corpus_builder.postproc.extract_pairs",
        "corpus_builder.postproc.export",
        # Сторонние
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
        "fitz",                  # PyMuPDF
        "pytesseract",
        "PIL",
        "PIL.Image",
        "loguru",
        "slugify",
        "tqdm",
        "click",
        "yaml",
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
    a.binaries,
    a.datas,
    [],
    name="CorpusBuilder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX не сжимаем — иначе очень долгая сборка
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,             # windowed приложение (на Windows = .exe без консоли)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.ico",  # если появится иконка
)

