"""Точка входа для PyInstaller-сборки.

PyInstaller не умеет работать с относительными импортами в main-скрипте,
поэтому отдельный launcher делает абсолютный импорт и вызывает main().
"""
import sys

from corpus_builder.gui import main

if __name__ == "__main__":
    sys.exit(main())
