@echo off
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   Stockiya  --  starting up
echo ================================================================
echo.

REM ---- Sanity check: setup must have been run ---------------------
if not exist "backend\.venv\Scripts\python.exe" (
    echo ERROR: Backend virtual environment not found.
    echo        Run setup.bat first.
    echo.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo ERROR: Frontend node_modules not found.
    echo        Run setup.bat first.
    echo.
    pause
    exit /b 1
)

REM ---- Check ports are free ---------------------------------------
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo WARNING: Port 8000 is already in use.
    echo          Run stop.bat first, or close whatever is using port 8000.
    echo.
    pause
    exit /b 1
)

echo Starting middleware (HTTP API) on http://localhost:8000 ...
start "Stockiya middleware" /D "%~dp0" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn middleware.main:app --port 8000"

echo Starting frontend (UI) on http://localhost:5173 ...
start "Stockiya frontend" /D "%~dp0frontend" cmd /k "npm run dev"

echo.
echo Waiting for services to come up ^(this is also when fresh data is fetched on first run of the day^)...
timeout /t 10 /nobreak >nul

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
