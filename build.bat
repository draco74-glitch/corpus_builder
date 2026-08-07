@echo off
REM Сборка CorpusBuilder.exe на Windows.
REM Использование: build.bat [--clean]

cd /d "%~dp0"

if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

where pyinstaller >nul 2>nul
if errorlevel 1 pip install pyinstaller

if "%1"=="--clean" (
    echo Cleaning previous build artifacts...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
)

echo Building CorpusBuilder.exe...
pyinstaller CorpusBuilder.spec --noconfirm

echo.
echo === Build complete ===
if exist dist\CorpusBuilder.exe (
    echo Output: dist\CorpusBuilder.exe
    dir dist\CorpusBuilder.exe
) else (
    echo ERROR: dist\CorpusBuilder.exe not found
    exit /b 1
)
