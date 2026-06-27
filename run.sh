#!/usr/bin/env bash
# GhostDebugger one-shot setup + launch script.
#
# Usage: ./run.sh

set -e

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
    echo "No .env found — copying .env.example. Add your FIREWORKS_API_KEY to .env when ready."
    cp .env.example .env
fi

echo "Launching GhostDebugger..."
streamlit run ui/app.py
