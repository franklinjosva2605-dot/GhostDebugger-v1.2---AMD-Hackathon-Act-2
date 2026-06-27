@echo off
REM GhostDebugger one-shot setup + launch script for Windows.

cd /d "%~dp0"

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -q -r requirements.txt

if not exist .env (
    echo No .env found - copying .env.example. Add your FIREWORKS_API_KEY to .env when ready.
    copy .env.example .env
)

echo Launching GhostDebugger...
streamlit run ui\app.py
