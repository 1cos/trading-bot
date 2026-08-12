# MaxBot v0.1 — Daily Operation Guide

## Every Morning

1. **Open TWS** on Mac mini
2. **Select Paper Trading** and log in
3. **Launch MaxBot** — open Terminal and run:
   ```
   cd ~/trading_bot
   ./start_maxbot.sh
   ```
   The dashboard URL will be printed (e.g. `http://192.168.1.42:8765`)

4. **Open MaxBot on iPhone** — tap the MaxBot icon on Home Screen
5. **Verify** the dashboard shows:
   - IBKR connected
   - Paper ✓
6. **Configure** watchlist / direction / mode if needed
7. **Press START MAXBOT** and confirm

MaxBot will begin monitoring and trading (or observing) automatically.

## End of Session

1. **Press STOP MAXBOT** on iPhone
2. **Export session** — tap "Export JSON" or "Export Report"
3. Close the dashboard server with `Ctrl+C` in Terminal
4. Close TWS if done for the day

## First-Time iPhone Setup

1. On iPhone Safari, open the dashboard URL shown by the launcher
2. Tap **Share** → **Add to Home Screen**
3. Name it "MaxBot" and tap Add
4. From now on, tap the MaxBot icon to open the dashboard

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `MAXBOT_API_TOKEN` | (none) | Protect START/STOP from unauthorized access |
| `MAXBOT_BIND_HOST` | `0.0.0.0` | Server bind address |
| `MAXBOT_API_PORT` | `8765` | Dashboard/API port |
| `MAXBOT_IB_HOST` | `127.0.0.1` | TWS/Gateway host |
| `MAXBOT_IB_PORT` | `7497` | TWS/Gateway port (Paper default) |
| `MAXBOT_IB_CLIENT_ID` | `1` | IBKR API client ID |

## Important

- **The launcher starts the control server only** — it does NOT start trading
- **Trading starts when you press START on the iPhone** — this is intentional
- **PAPER ONLY** — MaxBot v0.1 refuses to operate on live accounts
- **Trade limits are OFF** for the current testing phase
- Session logs are saved to `logs/maxbot/` on shutdown
