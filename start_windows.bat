@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Creating Python environment - first run only...
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Edit .env and config.yaml, then run this file again.
  notepad .env
  notepad config.yaml
  exit /b
)
python run.py
pause
