@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo [错误] 没找到 Python，请安装 Python 3.9+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

%PY% -m htxmon %*
if errorlevel 1 (
  echo.
  echo [提示] 如果提示没有 CA 证书或 HTTPS 失败，可先运行:  %PY% -m htxmon --check
  pause
)
