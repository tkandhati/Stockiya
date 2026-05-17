@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ================================================================
echo   Stockiya  --  starting up
echo ================================================================
echo.

REM ==================================================================
REM  PRE-FLIGHT CHECKS  -- catch problems BEFORE we launch services
REM  every check prints what it is doing and what to do if it fails
REM ==================================================================

REM ---- [1/5] Backend venv exists ----------------------------------
echo [1/5] Checking backend Python venv...
if not exist "backend\.venv\Scripts\python.exe" (
    echo       MISSING: backend\.venv\Scripts\python.exe
    echo       FIX:     Run setup.bat first.
    echo.
    pause
    exit /b 1
)
echo       OK.

REM ---- [2/5] Backend critical packages installed ------------------
echo [2/5] Checking backend Python packages...
backend\.venv\Scripts\python.exe -c "import fastapi, uvicorn, yfinance, pandas, pydantic" 2>nul
if errorlevel 1 (
    echo       MISSING: one of fastapi / uvicorn / yfinance / pandas / pydantic
    echo       FIX:     Run setup.bat again, or manually:
    echo                  backend\.venv\Scripts\activate.bat
    echo                  pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)
echo       OK.

REM ---- [3/5] Frontend node_modules exists -------------------------
echo [3/5] Checking frontend node_modules...
if not exist "frontend\node_modules" (
    echo       MISSING: frontend\node_modules
    echo       FIX:     Run setup.bat first, or manually:
    echo                  cd frontend
    echo                  npm install
    echo.
    pause
    exit /b 1
)
echo       OK.

REM ---- [4/5] Port 8000 free ---------------------------------------
echo [4/5] Checking port 8000 (middleware) is free...
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo       BUSY: port 8000 is already in use.
    echo       FIX:  Run stop.bat ^(it kills 8000 and 5173-5176^), then re-run start.bat.
    echo.
    pause
    exit /b 1
)
echo       OK.

REM ---- [5/5] Port 5173 free (or vite will pick 5174+) -------------
echo [5/5] Checking port 5173 (frontend) is free...
netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo       NOTE: port 5173 busy -- Vite will pick 5174/5175/5176 automatically.
) else (
    echo       OK.
)

echo.
echo ----------------------------------------------------------------
echo   Pre-flight checks passed. Launching services...
echo ----------------------------------------------------------------
echo.

REM ==================================================================
REM  LAUNCH
REM ==================================================================

echo Starting middleware (HTTP API) on http://localhost:8000 ...
start "Stockiya middleware" /D "%~dp0" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn middleware.main:app --port 8000"

echo Starting frontend (UI) on http://localhost:5173 ...
start "Stockiya frontend" /D "%~dp0frontend" cmd /k "npm run dev"

REM ---- Health-check the middleware (poll /docs instead of dumb sleep) ----
echo.
echo Waiting for middleware to come up (polling http://localhost:8000/docs)...
set /a tries=0
:health_wait
set /a tries+=1
if !tries! GTR 30 (
    echo       WARNING: middleware did not respond on /docs after 30s.
    echo                Check the "Stockiya middleware" terminal window for errors.
    goto :health_done
)
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://localhost:8000/docs' -UseBasicParsing -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto :health_wait
)
echo       Middleware is UP.
:health_done

echo Opening browser...
start http://localhost:5173

echo.
echo ================================================================
echo   Stockiya is running.
echo ================================================================
echo.
echo   Browser:    http://localhost:5173
echo   API:        http://localhost:8000
echo   API docs:   http://localhost:8000/docs
echo.
echo   To stop:    double-click stop.bat
echo               (or close both spawned terminal windows manually)
echo.
echo You can close THIS launcher window safely -- the app keeps running.
echo.
pause
