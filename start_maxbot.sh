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

# Ensure Python can find the trading_lab package
export PYTHONPATH="${SCRIPT_DIR}/backend/src:${PYTHONPATH:-}"

echo ""
echo "  ╔═══════════════════════════════════╗"
echo "  ║         MAXBOT v0.1               ║"
echo "  ║     Paper Trading Dashboard       ║"
echo "  ╚═══════════════════════════════════╝"
echo ""

# Launch the control API server
exec python -m trading_lab.live.control_api
