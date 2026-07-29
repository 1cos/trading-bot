#!/usr/bin/env bash
# Start the BDRR Backtest Lab
# Usage: ./start_lab.sh
# Then open http://localhost:5001

set -e
cd "$(dirname "$0")"

echo "=== BDRR Backtest Lab ==="
echo "Starting server..."
echo "Open http://localhost:5001"
echo ""

python -m trading_lab.backtest_server
