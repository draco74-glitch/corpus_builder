#!/usr/bin/env bash
# Сборка CorpusBuilder.exe через PyInstaller.
#
# Использование:
#   bash build.sh
#   bash build.sh --clean        # очистить build/ dist/ перед сборкой
#
# Результат: dist/CorpusBuilder.exe (или dist/CorpusBuilder на Linux/macOS)

set -euo pipefail
cd "$(dirname "$0")"

# Активируем venv если есть
if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Устанавливаем dev-зависимости, если pyinstaller отсутствует
if ! command -v pyinstaller &>/dev/null; then
    pip install pyinstaller
fi

# Опциональная очистка
if [ "${1:-}" = "--clean" ]; then
    echo "Cleaning previous build artifacts..."
    rm -rf build dist *.spec.bak
fi

echo "Building CorpusBuilder.exe..."
pyinstaller CorpusBuilder.spec --noconfirm

echo ""
echo "=== Build complete ==="
if [ -f "dist/CorpusBuilder.exe" ]; then
    echo "Output: dist/CorpusBuilder.exe"
    ls -lh dist/CorpusBuilder.exe
elif [ -f "dist/CorpusBuilder" ]; then
    echo "Output: dist/CorpusBuilder"
    ls -lh dist/CorpusBuilder
else
    echo "ERROR: no output binary found in dist/"
    exit 1
fi
