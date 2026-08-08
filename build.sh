#!/usr/bin/env bash
# ============================================================
#  Сборка CorpusBuilder (one-dir mode).
#
#  Результат: dist/CorpusBuilder/CorpusBuilder
#  Запуск: 10x быстрее, чем one-file.
#
#  Использование:
#    bash build.sh                  # обычная пересборка
#    bash build.sh --clean           # полная очистка + пересборка
#    bash build.sh --zip             # собрать + упаковать в ZIP
#    bash build.sh --recreate-venv   # пересоздать venv
#  ============================================================

set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo " CorpusBuilder build (one-dir mode)"
echo "============================================================"
echo ""

# === Шаг 1. Найти Python 3.13 ===
echo "[1/6] Поиск Python 3.13..."

PYTHON_BIN=""
PY_VERSION=""

CANDIDATES=(
    "python3.13"
    "python3"
    "/usr/local/bin/python3.13"
    "/opt/homebrew/bin/python3.13"
    "/usr/bin/python3.13"
)

for cmd in "${CANDIDATES[@]}"; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" --version 2>&1 || echo "")
        if [[ "$VER" == *"3.13"* ]]; then
            PYTHON_BIN="$cmd"
            PY_VERSION="$VER"
            echo "    Найдено: $VER ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo ""
    echo "============================================================"
    echo " ERROR: Python 3.13 не найден"
    echo "============================================================"
    echo " Установите Python 3.13:"
    echo "   sudo apt install python3.13 python3.13-venv"
    echo "   brew install python@3.13"
    exit 1
fi

# === Шаг 2. Проверить / пересоздать venv ===
echo ""
echo "[2/6] Проверка виртуального окружения..."

NEED_RECREATE=0
if [ "$1" == "--recreate-venv" ]; then
    NEED_RECREATE=1
fi

if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
    VENV_VER=$(.venv/bin/python --version 2>&1 || echo "")
    echo "    Текущий venv: $VENV_VER"
    if [[ "$VENV_VER" != *"3.13"* ]]; then
        echo "    Пересоздаю venv на Python 3.13..."
        NEED_RECREATE=1
    fi
else
    echo "    venv не существует — создаю."
    NEED_RECREATE=1
fi

if [ "$NEED_RECREATE" -eq 1 ]; then
    if [ -d ".venv" ]; then
        rm -rf .venv
    fi
    "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

VENV_VER=$(python --version 2>&1)
if [[ "$VENV_VER" != *"3.13"* ]]; then
    echo ""
    echo "============================================================"
    echo " ERROR: venv не на Python 3.13"
    echo "============================================================"
    exit 1
fi

# === Шаг 3. Установить зависимости ===
echo ""
echo "[3/6] Проверка зависимостей..."

if ! python -c "import PySide6; print('    PySide6:', PySide6.__version__)" 2>/dev/null; then
    echo "    Устанавливаю зависимости..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt
fi

if ! python -c "import PyInstaller" 2>/dev/null; then
    echo "    Устанавливаю PyInstaller..."
    pip install pyinstaller
fi

# === Шаг 4. Очистка ===
if [ "$1" == "--clean" ] || [ "$1" == "--zip" ] || [ "$1" == "--recreate-venv" ]; then
    echo ""
    echo "[4/6] Очистка предыдущей сборки..."
    rm -rf build dist
else
    echo ""
    echo "[4/6] Пропуск очистки (используйте --clean для полной очистки)"
fi

# === Шаг 5. Сборка one-dir ===
echo ""
echo "[5/6] Сборка CorpusBuilder (one-dir mode)..."
echo ""

pyinstaller CorpusBuilder.spec --noconfirm

echo ""
echo "============================================================"
echo " Build complete (one-dir mode)"
echo "============================================================"

if [ -f "dist/CorpusBuilder/CorpusBuilder.exe" ] || [ -f "dist/CorpusBuilder/CorpusBuilder" ]; then
    # Подсчёт размера
    SIZE=$(du -sh "dist/CorpusBuilder/" 2>/dev/null | cut -f1)
    echo " Output: dist/CorpusBuilder/"
    echo " Size:   $SIZE"
    echo ""
    echo " To run: ./dist/CorpusBuilder/CorpusBuilder"
    echo " To distribute: zip -r CorpusBuilder.zip dist/CorpusBuilder/"

    # === Шаг 6. ZIP-дистрибутив (опционально) ===
    if [ "$1" == "--zip" ]; then
        echo ""
        echo "[6/6] Создание ZIP-архива..."
        cd dist
        zip -r CorpusBuilder.zip CorpusBuilder/ -q
        ZIP_SIZE=$(ls -lh CorpusBuilder.zip | awk '{print $5}')
        echo " ZIP: dist/CorpusBuilder.zip ($ZIP_SIZE)"
        cd ..
    else
        echo ""
        echo "[6/6] Пропуск ZIP (используйте --zip для создания архива)"
    fi
else
    echo "ERROR: dist/CorpusBuilder/ not found"
    exit 1
fi
