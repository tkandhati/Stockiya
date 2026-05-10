@echo off
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   Stockiya  --  one-time setup
echo ================================================================
echo.
echo This will:
echo   1. Create the Python virtual environment in backend\.venv
echo   2. Install Python dependencies (~2-3 minutes)
echo   3. Install Node.js dependencies (~1-2 minutes)
echo   4. Create backend\.env from the template
echo.
echo Run this ONCE per machine. After that, just use start.bat.
echo.
pause

REM ---- Check Python is on PATH ------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not on PATH.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo During install, TICK "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM ---- Check Node is on PATH --------------------------------------
where npm >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Node.js is not on PATH.
    echo Install the LTS version from https://nodejs.org/
    echo.
    pause
    exit /b 1
)

REM ---- 1. Backend venv --------------------------------------------
echo.
echo [1/4] Creating Python virtual environment...
cd backend
if exist .venv (
    echo      .venv already exists -- skipping creation.
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: failed to create venv.
        cd ..
        pause
        exit /b 1
    )
)

REM ---- 2. pip install ---------------------------------------------
echo.
echo [2/4] Installing Python packages (this can take 2-3 minutes)...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed.
    echo If you are on a corporate network with SSL issues, run:
    echo     backend\.venv\Scripts\pip.exe install pip-system-certs
    echo then retry setup.bat
    echo.
    cd ..
    pause
    exit /b 1
)

REM ---- 3. backend/.env --------------------------------------------
echo.
echo [3/4] Setting up backend\.env ...
if exist .env (
    echo      backend\.env already exists -- leaving it alone.
) else (
    copy .env.example .env >nul
    echo      Created backend\.env from template.
    echo      Edit it later if you want to add an Anthropic API key (optional).
)

cd ..

REM ---- 4. Frontend npm install ------------------------------------
echo.
echo [4/4] Installing Node packages (this can take 1-2 minutes)...
cd frontend
call npm install
if errorlevel 1 (
    echo.
    echo ERROR: npm install failed.
    echo Check your Node.js version: node --version
    echo.
    cd ..
    pause
    exit /b 1
)
cd ..

echo.
echo ================================================================
echo   Setup complete.
echo ================================================================
echo.
echo Next steps:
echo   - Double-click  start.bat   to launch the app
echo   - Double-click  stop.bat    to shut everything down cleanly
echo.
pause
