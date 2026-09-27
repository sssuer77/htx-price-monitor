@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  HTX Contract Price Monitor - launcher
rem  NOTE: keep this file ASCII-only and CRLF. cmd.exe mis-parses
rem        non-ASCII bytes, and LF-only .bat breaks if-blocks.
rem ---------------------------------------------------------------

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)

if not defined PY (
  echo [ERROR] Python not found.
  echo         Install Python 3.9+ from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PY% -m htxmon %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [HINT] htxmon exited with code %RC%.
  echo        CA / HTTPS error   : %PY% -m htxmon --check
  echo        WebSocket blocked  : harmless, falls back to REST polling
  echo        Anything else      : see README.md section "????"
  echo.
  pause
)

exit /b %RC%