@echo off
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   Stockiya  --  stopping
echo ================================================================
echo.

set killed=0

REM ---- Kill middleware (port 8000) --------------------------------
echo Looking for middleware on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Stopping middleware ^(PID %%a^)
    taskkill /F /PID %%a >nul 2>&1
    set killed=1
)

REM ---- Kill frontend (Vite — typically 5173, may pick 5174/5175) --
for %%p in (5173 5174 5175 5176) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p " ^| findstr "LISTENING"') do (
        echo   Stopping frontend on port %%p ^(PID %%a^)
        taskkill /F /PID %%a >nul 2>&1
        set killed=1
    )
)

echo.
if %killed%==0 (
    echo Nothing was running. Stockiya was already stopped.
) else (
    echo Stockiya stopped.
)
echo.
echo You can close any leftover terminal windows manually.
echo.
timeout /t 3 /nobreak >nul
