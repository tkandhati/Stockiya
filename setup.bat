@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ================================================================
echo   Stockiya  --  one-time setup
echo ================================================================
echo.
echo This will:
echo   1. Verify Python and Node.js are installed
echo   2. Create the Python virtual environment in backend\.venv
echo   3. Install Python dependencies (~2-3 minutes)
echo   4. Install Node.js dependencies (~1-2 minutes)
echo   5. Create backend\.env from the template
echo.
echo Run this ONCE per machine. After that, just use start.bat.
echo.
pause

REM ---- Check Python is on PATH ------------------------------------
echo.
echo [Step 1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python is not on PATH.
    echo Install Python 3.11 or 3.12 from https://www.python.org/downloads/
    echo During install, TICK "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM ---- Show Python version + warn if 3.13 -------------------------
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo      Python version: !PYVER!

echo !PYVER! | findstr /b "3.13 3.14" >nul
if not errorlevel 1 (
    echo.
    echo NOTE: Python !PYVER! is recent. Some packages may need to build from
    echo       source ^(via their own pyproject.toml^), which requires a C
    echo       compiler. If install fails with a "pyproject.toml" / "metadata"
    echo       error, install Python 3.12 instead -- it has pre-built wheels:
    echo       https://www.python.org/downloads/release/python-3128/
    echo.
    timeout /t 4 /nobreak >nul
)

echo !PYVER! | findstr /b "3.10 3.9 3.8 3.7 2." >nul
if not errorlevel 1 (
    echo.
    echo ERROR: Python !PYVER! is too old. This project needs 3.11 or newer.
    echo.
    pause
    exit /b 1
)

REM ---- Check Node is on PATH --------------------------------------
echo.
echo [Step 2/5] Checking Node.js...
where npm >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Node.js is not on PATH.
    echo Install the LTS version from https://nodejs.org/
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
echo      Node version: !NODEVER!

REM ---- 3. Backend venv --------------------------------------------
echo.
echo [Step 3/5] Setting up Python virtual environment...
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
    echo      Created backend\.venv
)

REM ---- 4. pip install ---------------------------------------------
echo.
echo [Step 4/5] Installing Python packages (this can take 2-3 minutes)...
echo.
call .venv\Scripts\activate.bat

REM Upgrade pip / setuptools / wheel FIRST so metadata parsing is current
echo      Upgrading pip, setuptools, wheel...
python -m pip install --upgrade pip setuptools wheel --quiet
if errorlevel 1 (
    echo      WARNING: pip upgrade had issues but continuing...
)

REM Skip if everything is already installed (idempotent re-run)
pip show fastapi yfinance pandas pydantic --quiet 1>nul 2>&1
if not errorlevel 1 (
    echo      All required packages already installed -- skipping pip.
    goto :pip_done
)

REM Try wheel-only install first (fast, no compilation needed)
echo      Trying wheel-only install (preferred)...
pip install -r requirements.txt --only-binary :all: --quiet
if not errorlevel 1 (
    echo      OK -- all packages installed from pre-built wheels.
    goto :pip_done
)

REM Wheel-only failed. Diagnose: Python 3.13+ often hits this when pandas /
REM curl-cffi have no wheel and pip tries a pyproject.toml source build that
REM needs a C compiler. Don't even attempt source build silently -- fail loud.
echo.
echo ================================================================
echo   ERROR: pip install failed -- no pre-built wheel for !PYVER!.
echo ================================================================
echo.
echo You probably saw an error mentioning "pyproject.toml" or
echo "Building wheel for [package] (pyproject.toml)". That's pip
echo trying to BUILD the package from source, which needs a C compiler.
echo.
echo Easiest fix ^(2 minutes^):
echo   1. Install Python 3.12 from:
echo      https://www.python.org/downloads/release/python-3128/
echo      Tick "Add Python to PATH" during install.
echo   2. Delete this folder: backend\.venv
echo   3. Re-run setup.bat. It will use Python 3.12 and get wheels for everything.
echo.
echo Alternative ^(20+ min, advanced^):
echo   Install "Microsoft C++ Build Tools" and retry --
echo   https://visualstudio.microsoft.com/visual-cpp-build-tools/
echo.
cd ..
pause
exit /b 1

:pip_done
echo      All Python packages installed successfully.

REM ---- backend\.env -----------------------------------------------
if exist .env (
    echo      backend\.env already exists -- leaving it alone.
) else (
    copy .env.example .env >nul
    echo      Created backend\.env from template.
    echo      Edit it later if you want to add an Anthropic API key (optional).
)

cd ..

REM ---- 5. Frontend npm install ------------------------------------
echo.
echo [Step 5/5] Installing Node packages (this can take 1-2 minutes)...
cd frontend
call npm install --silent
if errorlevel 1 (
    echo.
    echo ERROR: npm install failed.
    echo Check your Node.js version: node --version (need 18+)
    echo.
    cd ..
    pause
    exit /b 1
)
echo      All Node packages installed successfully.
cd ..

echo.
echo ================================================================
echo   Setup complete!
echo ================================================================
echo.
echo Next steps:
echo   - Double-click  start.bat   to launch the app
echo   - Double-click  stop.bat    to shut everything down cleanly
echo.
pause
