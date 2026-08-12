#!/usr/bin/env bash
# ── MaxBot v0.1 Control Server Launcher ──────────────────────────────────
#
# Starts the MaxBot dashboard / control API on the local network.
# Trading does NOT start automatically — press START from the iPhone PWA.
#
# Usage:
#   ./start_maxbot.sh
#
# Environment variables (all optional):
#   MAXBOT_API_TOKEN     — protect START/STOP endpoints
#   MAXBOT_BIND_HOST     — server bind address   (default: 0.0.0.0)
#   MAXBOT_API_PORT      — server port           (default: 8765)
#   MAXBOT_IB_HOST       — TWS/Gateway host      (default: 127.0.0.1)
#   MAXBOT_IB_PORT       — TWS/Gateway port      (default: 7497)
#   MAXBOT_IB_CLIENT_ID  — IBKR client ID        (default: 1)
#
# Requirements:
#   1. TWS Paper must already be running and logged in
#   2. pip install -r requirements.txt && pip install -e backend/
# ─────────────────────────────────────────────────────────────────────────

set -e

# Locate repository root (script may be run from anywhere)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present
if [ -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
    source "${SCRIPT_DIR}/venv/bin/activate"
fi

# Ensure Python can find the trading_lab package
export PYTHONPATH="${SCRIPT_DIR}/backend/src:${PYTHONPATH:-}"

# ── Python version guard ────────────────────────────────────────────────
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "?")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")

if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 14 ]; then
    echo ""
    echo "  ❌ MAXBOT requires Python 3.11–3.13 for ib_insync."
    echo "     Current Python: ${PY_VERSION}"
    echo ""
    echo "  Fix: recreate venv with Python 3.12:"
    echo "     brew install python@3.12"
    echo "     mv venv venv_py${PY_VERSION}_backup"
    echo "     /opt/homebrew/bin/python3.12 -m venv venv"
    echo "     source venv/bin/activate"
    echo "     pip install -r requirements.txt"
    echo "     pip install -e backend/"
    echo ""
    exit 1
fi

echo ""
echo "  ╔═══════════════════════════════════╗"
echo "  ║         MAXBOT v0.1               ║"
echo "  ║     Paper Trading Dashboard       ║"
echo "  ╚═══════════════════════════════════╝"
echo ""

# Launch the control API server
exec python3 -m trading_lab.live.control_api
