# Stockiya — Installation Guide

End-to-end setup on a fresh personal laptop. **First-time install: ~10 minutes. Daily startup after that: 1 click.**

> No cron, no scheduler, no Windows Task Scheduler. The app self-heals on every open — when you start it, it auto-detects missing data and fetches what's needed in the background.

---

## 1. Prerequisites

Install these once, in any order. All free.

| Tool | Version | Where to get | Notes |
|---|---|---|---|
| **Python** | 3.11 or 3.12 (3.13 also works) | [python.org/downloads](https://www.python.org/downloads/) | **During install, tick "Add Python to PATH"** |
| **Node.js** | 18+ (LTS) | [nodejs.org](https://nodejs.org/) | Install the LTS version |
| **Git** | any recent | [git-scm.com](https://git-scm.com/) | For Windows, pick "Git Bash" + "Use Git from Command Prompt" during setup |

### Network check (do this first — it decides whether real data works)

Open PowerShell or Git Bash and run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d"
```

Expected output:

| Result | Meaning |
|---|---|
| `200` | Yahoo reachable — proceed with `DEMO_MODE=0` (real data) |
| `429` | Rate-limited but reachable — works, just slower; proceed with `DEMO_MODE=0` |
| Timeout / DNS error / `403` | Network blocks Yahoo Finance — either switch networks (mobile hotspot, home Wi-Fi) or run with `DEMO_MODE=1` (synthetic fixtures, NOT real prices) |

> **Corporate networks often block Yahoo.** If you're on one, install on a personal laptop or use a VPN/hotspot during nightly fetches.

### (Optional) Anthropic API key

Not required. Without it, the deterministic rule-based picker runs and produces identical-quality picks. The LLM only writes prose for the detail page.

If you want it anyway, get a key at [console.anthropic.com](https://console.anthropic.com/) → choose `claude-haiku-4-5` model (~₹0.50/day at typical use).

---

## 2. Clone the repository

Pick a parent folder (e.g. `C:\Projects` on Windows, `~/projects` on macOS/Linux), then:

```bash
cd C:\Projects                                 # Windows
# or:  cd ~/projects                           # macOS/Linux

git clone https://github.com/tkandhati/Stockiya.git
cd Stockiya
```

---

## 3. Backend — Python virtual environment + dependencies

From the project root:

### Windows (PowerShell)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
cd ..
```

### macOS / Linux

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
cd ..
```

### Corporate-network SSL fix (Windows only)

If `pip install` fails with SSL / certificate errors:

```powershell
pip install pip-system-certs
```

This makes `requests` use the Windows certificate store (handles corporate MITM proxies). Then re-run `pip install -r requirements.txt`.

---

## 4. Configure `backend\.env`

Open `backend\.env` in any text editor and adjust:

```env
# Optional. Without this, the rule-based fallback runs (still produces good picks).
ANTHROPIC_API_KEY=

# Default model. Haiku is plenty for this task.
MODEL=claude-haiku-4-5

# 0 = real Yahoo Finance data (recommended)
# 1 = bundled demo fixtures (UI development only — NOT real prices)
DEMO_MODE=0

# yahoo (default) | bhavcopy (TODO)
DATA_SOURCE=yahoo

# 1 = sure-shot only — often 0 picks/day; high conviction when something fires
# 0 = relaxed — more picks per day; original behavior
STRICT_MODE=1
```

Recommended starting values: leave the API key blank, keep `DEMO_MODE=0` and `STRICT_MODE=1`.

---

## 5. Frontend — npm dependencies

From the project root:

```bash
cd frontend
npm install
cd ..
```

Time: ~1–2 minutes. Installs ~250 packages into `frontend/node_modules/`.

---

## 6. First run — the auto-catchup proves itself

Open **two terminals**, both at the project root.

### Terminal 1: middleware (HTTP API + auto-catchup)

**Windows:**
```powershell
backend\.venv\Scripts\python.exe -m uvicorn middleware.main:app --port 8000
```

**macOS / Linux:**
```bash
backend/.venv/bin/python -m uvicorn middleware.main:app --port 8000
```

Watch the log. You should see something like:

```
INFO:     Started server process [...]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
[INFO] startup: Catchup launched in background
[INFO] catchup: Today's prepared/ missing — triggering nightly
[INFO] nightly: Universe: 100 tickers (DATA_SOURCE=yahoo, DEMO_MODE=0)
[INFO] nightly: Done. 100/100 succeeded
[INFO] catchup: Catchup done
```

The first nightly takes **30–60 seconds** (fetches 1 year of OHLCV for all 100 Nifty tickers). The API serves immediately — catchup runs in the background.

### Terminal 2: frontend (UI)

```bash
cd frontend
npm run dev
```

Open the URL Vite prints — usually `http://localhost:5173`.

You should see:
- The motto "Don't invent. Follow the institutions. Pick one."
- 0–3 pick cards (or *"Nothing actionable today"* if the strict gate finds nothing)
- Click any card → full reasoning checklist + accumulation panel + exit scenarios

---

## 7. Verify everything works

Run these from a third terminal (or the existing one after frontend is up):

```bash
curl http://localhost:8000/api/health
# Expected: {"status":"ok","date_ist":"YYYY-MM-DD","demo_mode":false}

curl http://localhost:8000/api/picks
# Expected: JSON with 0–3 picks, each with stage/timing/target_window/reasoning

curl http://localhost:8000/api/stock/HDFCBANK.NS
# Expected: full accumulation panel + peers + history
```

If all three respond with valid JSON, **you're done with setup**.

---

## 8. (Optional) One-click launcher

### Windows — save as `start.bat` in the project root

```bat
@echo off
start "Stockiya middleware" /D "%~dp0" cmd /k "backend\.venv\Scripts\python.exe -m uvicorn middleware.main:app --port 8000"
start "Stockiya frontend"   /D "%~dp0frontend" cmd /k "npm run dev"
timeout /t 8 >nul
start http://localhost:5173
```

Double-click `start.bat` → two terminals open → browser auto-opens after 8 seconds. Close the terminals when done.

### macOS / Linux — save as `start.sh` in the project root

```bash
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
osascript -e "tell app \"Terminal\" to do script \"cd $DIR && backend/.venv/bin/python -m uvicorn middleware.main:app --port 8000\""
osascript -e "tell app \"Terminal\" to do script \"cd $DIR/frontend && npm run dev\""
sleep 8
open http://localhost:5173
```

`chmod +x start.sh` then double-click or run `./start.sh`.

---

## How daily use looks (no scheduler, no manual steps)

```
Open the app at 9 AM Monday after a weekend
   ↓
Middleware boots
   ↓
Catchup hook fires in background
   ↓
Detects: data/prepared/<today>/ doesn't exist
   ↓
Runs nightly automatically — fetches all 100 tickers
   ↓
Detects: open picks have no Friday close in portfolio_weekly.csv
   ↓
Runs weekly automatically — fetches Friday's close, marks any target_hit / stopped / timed_out
   ↓
Picks ready in browser (~30-60 seconds after middleware boots)
```

You never touch a cron tab or Task Scheduler. **Just open the app.**

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `pip install` SSL / certificate error | Corporate network MITM proxy | `pip install pip-system-certs` then re-run |
| `ModuleNotFoundError: No module named 'backend'` | Started uvicorn from wrong directory | Must run from **project root**, not from inside `backend/` or `middleware/` |
| Frontend at `:5173` shows blank or "Failed to fetch" | Middleware not running | Start uvicorn first; verify with `curl http://localhost:8000/api/health` |
| Catchup logs `429 Too Many Requests` | Yahoo rate-limit | Normal during first batch fetch — wait 30 min and re-run middleware. If persistent, switch network. |
| `/api/picks` returns 0 picks | Either nothing meets STRICT_MODE today (correct behavior — investing tool) OR fetch failed | Check `data/prepared/<TODAY>/_manifest.json` — count "ok: true" rows. If most failed → network issue. If most succeeded → strict gate just rejected everything today, check back tomorrow. |
| Frontend shows red "DEMO MODE" banner | `.env` has `DEMO_MODE=1` | Change to `0` and restart middleware |
| `port 8000 already in use` | Stale uvicorn process | `taskkill /F /IM python.exe` (Windows) / `pkill -f uvicorn` (macOS/Linux), then restart |
| Picks look identical day after day | Caching working as designed — picks regenerate once per IST day | This is correct. Use `POST /api/picks/refresh` if you really want a fresh re-pick mid-day. |
| `/api/picks/refresh` returns same picks | Same — STRICT_MODE gate is deterministic on yesterday's close. New picks need new data (next day). | This is correct. |

---

## Where to look next

- **`README.md`** — full project overview, volume strategies explained, API endpoints, project structure
- **`PRINCIPLES.md`** — investing rule-set the engine and any LLM both follow
- **`http://localhost:8000/docs`** — interactive Swagger API docs (after middleware is running)
- **`data/portfolio.csv`** — every pick the engine ever surfaced; opens cleanly in Excel
- **`data/portfolio_weekly.csv`** — Friday closes for open picks (auto-updated by catchup)

---

## Disclaimer

Stockiya is for **educational use only**. Picks are algorithmic and **not financial advice**. The engine has not been backtested on historical Indian data — paper-trade the first 10–15 picks before deploying real capital. Markets are risky; past patterns don't guarantee future returns.
