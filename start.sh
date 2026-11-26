#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "Installing dependencies..."
pip install -r requirements.txt
echo "Starting Vaagai Pharm..."
if command -v xdg-open > /dev/null; then
    xdg-open http://127.0.0.1:5000 &
elif command -v open > /dev/null; then
    open http://127.0.0.1:5000 &
fi
python3 app.py
