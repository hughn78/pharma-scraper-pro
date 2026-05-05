@echo off
setlocal EnableDelayedExpansion

title Pharma Scraper Pro v2.0
color 0A

echo =========================================
echo   Pharma Scraper Pro v2.0
echo   Launch Script for Windows
echo =========================================
echo.

REM --- Find Python ---
set "PYTHON="
set "PYTHON_NAMES=python python3 py"
for %%P in (%PYTHON_NAMES%) do (
    where %%P >nul 2>&1
    if !errorlevel! == 0 (
        set "PYTHON=%%P"
        goto :found_python
    )
)
echo [ERROR] Python not found. Please install Python 3.10+ and add it to PATH.
echo Download: https://www.python.org/downloads/
pause
exit /b 1

:found_python
echo [OK] Python found: %PYTHON%
for /f "tokens=*" %%V in ('%PYTHON% --version 2^>^&1') do echo        %%V
echo.

REM --- Detect script directory ---
set "SCRIPT_DIR=%~dp0"
set "ENGINE_DIR=%SCRIPT_DIR%engine"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"

REM --- Create virtual environment ---
if not exist "%VENV_DIR%" (
    echo [*] Creating virtual environment...
    %PYTHON% -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment exists.
)

REM --- Activate venv ---
call "%VENV_DIR%\Scripts\activate.bat"
if !errorlevel! neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

REM --- Install / update dependencies ---
echo.
echo [*] Checking dependencies...
python -m pip install --quiet pandas openpyxl requests beautifulsoup4 lxml thefuzz rapidfuzz 2>nul
if !errorlevel! neq 0 (
    echo [*] Upgrading pip and retrying...
    python -m pip install --upgrade pip --quiet
    python -m pip install --quiet pandas openpyxl requests beautifulsoup4 lxml thefuzz rapidfuzz
)
echo [OK] Dependencies ready.

REM --- Create default config.json if missing ---
if not exist "%CONFIG_PATH%" (
    echo [*] Creating default config.json...
    (
        echo {
        echo   "db_path": "data\\canonical_products.db",
        echo   "export_dir": "exports",
        echo   "aggressive_barcode": true,
        echo   "max_html_products": 100,
        echo   "fos_path": "",
        echo   "proxies": ""
        echo }
    ) > "%CONFIG_PATH%"
    echo [OK] config.json created.
)

REM --- Create data directories ---
if not exist "%SCRIPT_DIR%data" mkdir "%SCRIPT_DIR%data"
if not exist "%SCRIPT_DIR%exports" mkdir "%SCRIPT_DIR%exports"
if not exist "%SCRIPT_DIR%reports" mkdir "%SCRIPT_DIR%reports"
if not exist "%SCRIPT_DIR%logs" mkdir "%SCRIPT_DIR%logs"

REM --- Check engine files ---
if not exist "%ENGINE_DIR%\pharma_scraper_pro.py" (
    echo [ERROR] GUI file not found: %ENGINE_DIR%\pharma_scraper_pro.py
    echo        Make sure you cloned/extracted the repo correctly.
    pause
    exit /b 1
)

REM --- Launch ---
echo.
echo =========================================
echo   Starting Pharma Scraper Pro...
echo =========================================
echo.
cd /d "%ENGINE_DIR%"
python pharma_scraper_pro.py
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Application exited with code !errorlevel!.
    echo        Check logs/ for details.
    pause
)

REM --- Deactivate venv ---
deactivate
endlocal
echo.
echo Goodbye.
