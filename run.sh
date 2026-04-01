#!/bin/bash
# Launch StockViewer on macOS

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Change to the project directory and run
cd "$SCRIPT_DIR"
python3 run.py
