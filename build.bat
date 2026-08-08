@echo off
REM ============================================================
REM  Сборка CorpusBuilder (one-dir mode).
REM
REM  Результат: dist\CorpusBuilder\CorpusBuilder.exe
REM  Запуск: 10x быстрее, чем one-file.
REM
REM  Использование:
REM    build.bat            # обычная пересборка
REM    build.bat --clean    # полная очистка + пересборка
REM    build.bat --zip      # собрать + упаковать в ZIP
REM  ============================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo  CorpusBuilder build (one-dir mode)
echo ============================================================
echo.

REM === Шаг 1. Найти Python 3.13 ===
echo [1/6] Поиск Python 3.13...

set PYTHON_EXE=
for /f "tokens=*" %%v in ('py -3.13 --version 2^>nul') do set PY313_VERSION=%%v

if defined PY313_VERSION (
    set PYTHON_EXE=py -3.13
    echo     Найдено: !PY313_VERSION! (через py launcher)
) else (
    for /f "tokens=*" %%v in ('python3.13 --version 2^>nul') do set PY313_VERSION=%%v
    if defined PY313_VERSION (
        set PYTHON_EXE=python3.13
        echo     Найдено: !PY313_VERSION!
    )
)

if not defined PY313_VERSION (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%ProgramFiles%\Python313\python.exe"
        "%ProgramFiles(x86)%\Python313\python.exe"
    ) do (
        if exist "%%~P" (
            set PYTHON_EXE="%%~P"
            for /f "tokens=*" %%v in ('"%%~P" --version 2^>nul') do set PY313_VERSION=%%v
            echo     Найдено: !PY313_VERSION! (в %%~P)
        )
    )
)

if not defined PY313_VERSION (
    echo.
    echo ============================================================
    echo  ERROR: Python 3.13 не найден
    echo ============================================================
    echo  Установите Python 3.13 с https://www.python.org/downloads/
    exit /b 1
)

REM === Шаг 2. Проверить / пересоздать venv ===
echo.
echo [2/6] Проверка виртуального окружения...

set NEED_RECREATE=0
if "%1"=="--recreate-venv" set NEED_RECREATE=1

if exist .venv\Scripts\python.exe (
    for /f "tokens=*" %%v in ('.venv\Scripts\python.exe --version 2^>nul') do set VENV_VERSION=%%v
    echo     Текущий venv: !VENV_VERSION!
    echo !VENV_VERSION! | findstr /C:"3.13" >nul
    if errorlevel 1 (
        echo     Пересоздаю venv на Python 3.13...
        set NEED_RECREATE=1
    )
) else (
    echo     venv не существует — создаю.
    set NEED_RECREATE=1
)

if !NEED_RECREATE!==1 (
    if exist .venv rmdir /s /q .venv
    !PYTHON_EXE! -m venv .venv
)

call .venv\Scripts\activate.bat
python --version | findstr /C:"3.13" >nul
if errorlevel 1 (
    echo ERROR: venv не на Python 3.13
    exit /b 1
)

REM === Шаг 3. Установить зависимости ===
echo.
echo [3/6] Проверка зависимостей...

python -c "import PySide6; print('    PySide6:', PySide6.__version__)" 2>nul
if errorlevel 1 (
    echo     Устанавливаю зависимости...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo     ERROR: не удалось установить зависимости
        exit /b 1
    )
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo     Устанавливаю PyInstaller...
    pip install pyinstaller
)

REM === Шаг 4. Очистка ===
if "%1"=="--clean" (
    echo.
    echo [4/6] Очистка предыдущей сборки...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
) else if "%1"=="--zip" (
    echo.
    echo [4/6] Очистка предыдущей сборки...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
) else (
    echo.
    echo [4/6] Пропуск очистки (используйте --clean для полной очистки)
)

REM === Шаг 5. Сборка one-dir ===
echo.
echo [5/6] Сборка CorpusBuilder (one-dir mode)...
echo.

pyinstaller CorpusBuilder.spec --noconfirm

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  Build FAILED
    echo ============================================================
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete (one-dir mode)
echo ============================================================

if exist dist\CorpusBuilder\CorpusBuilder.exe (
    echo  Output: dist\CorpusBuilder\CorpusBuilder.exe
    echo  Folder: dist\CorpusBuilder\
    echo.
    echo  To run: dist\CorpusBuilder\CorpusBuilder.exe
    echo  To distribute: zip the dist\CorpusBuilder\ folder

    REM Подсчёт размера папки
    set FOLDER_SIZE=0
    for /f "tokens=*" %%s in ('dir /s /a /-c "dist\CorpusBuilder" ^| findstr "байт"') do (
        for /f "tokens=3" %%a in ("%%s") do set FOLDER_SIZE=%%a
    )
    set /a FOLDER_MB=!FOLDER_SIZE! / 1048576
    echo  Size: ~!FOLDER_MB! MB

    REM === Шаг 6. ZIP-дистрибутив (опционально) ===
    if "%1"=="--zip" (
        echo.
        echo [6/6] Создание ZIP-архива...
        powershell -Command "Compress-Archive -Path 'dist\CorpusBuilder\*' -DestinationPath 'dist\CorpusBuilder.zip' -Force"
        if exist dist\CorpusBuilder.zip (
            for %%I in (dist\CorpusBuilder.zip) do echo  ZIP: dist\CorpusBuilder.zip (%%~zI bytes)
        )
    ) else (
        echo.
        echo [6/6] Пропуск ZIP (используйте --zip для создания архива)
    )
) else (
    echo  ERROR: dist\CorpusBuilder\CorpusBuilder.exe not found
    exit /b 1
)
