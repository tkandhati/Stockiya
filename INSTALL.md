# Stockiya — Install

**3 commands. ~5 minutes. Windows steps shown; macOS/Linux equivalent in parens.**

## Prerequisites
- **Python 3.12** ([download](https://www.python.org/downloads/release/python-3128/)) — tick *"Add Python to PATH"*.
  - 3.13 also works but may hit `pyproject.toml` build errors; 3.12 has pre-built wheels for every dep.
- **Node.js 18+ LTS** ([download](https://nodejs.org/))

## Install

```powershell
git clone https://github.com/tkandhati/Stockiya.git
cd Stockiya
.\setup.bat        # one click; installs venv + npm + creates backend\.env
```

(macOS/Linux: run the equivalent commands in `setup.bat` line-by-line — venv, `pip install -r backend/requirements.txt`, `npm --prefix frontend install`, `cp backend/.env.example backend/.env`.)

## Run

```powershell
.\start.bat        # opens backend:8000, frontend:5173, browser
.\stop.bat         # closes everything
```

Or manually (two terminals):
```powershell
backend\.venv\Scripts\python -m uvicorn middleware.main:app --port 8000
cd frontend ; npm run dev
```

Browser: <http://localhost:5173>

## Configure — first-run data path (REQUIRED)

**This is the #1 gotcha.** With no `backend\.env`, or with the wrong
`DATA_SOURCE`, every ticker fails at `[I] Ingest` and you get zero picks
with a wall of `FileNotFoundError` in the log. Pick exactly ONE of:

| Situation | Set in `backend\.env` |
|---|---|
| First-run smoke test (synthetic prices, UI verification only) | `DEMO_MODE=1` |
| Home / non-firewalled network | `DATA_SOURCE=yahoo` |
| Corporate firewall, have local bhavcopy CSVs | `DATA_SOURCE=bhavcopy`<br>`STOCKYA_OHLCV_DIR=<abs path to <SYMBOL>.NS.csv files>` |

Full knob list:

| Var | Default | What it does |
|---|---|---|
| `DEMO_MODE` | `0` | `1` = synthetic data. Takes precedence over `DATA_SOURCE`. **UI only, do not trade.** |
| `DATA_SOURCE` | `bhavcopy` | `yahoo` = live yfinance; `bhavcopy` = local CSV cache in `STOCKYA_OHLCV_DIR` |
| `STOCKYA_OHLCV_DIR` | *(sibling tuner cache)* | Absolute path to per-symbol OHLCV CSVs. Point at your OWN cache; the tuner sibling is not guaranteed to exist. |
| `STOCKYA_UNIVERSE` | `nifty100` | `nifty50` (fast) / `nifty100` / `nifty200` / `nifty500` (~456 tickers, curated) / `custom` (reads `config/universe_custom.txt`, one ticker per line). |
| `STOCKYA_ACCOUNT_VALUE` | `100000` | Capital used by `[PS] Position Sizer` for share-count math |

Composite threshold `τ` and per-stage weights `wᵢ` live in
`config\stage_weights.json`, not `.env`. `scripts/tune_weights.py` updates them.

No LLM key needed. The pipeline is deterministic, volume-only.

## Common failures

| Error | Fix |
|---|---|
| Every ticker logs `FileNotFoundError: Bhavcopy CSV missing for … in Stockya-tuner\data\ohlcv` | Missing/misconfigured `backend\.env`. Set `DEMO_MODE=1` (fastest) or a valid `STOCKYA_OHLCV_DIR`. See "Configure" above. |
| Orchestrator log: `DATA SOURCE MISCONFIGURED — N of N tickers failed [I] Ingest` | Same fix — `.env` needs one of the three configs above. |
| `Building wheel for X (pyproject.toml)` then fails | You're on Python 3.13+. Install Python 3.12, delete `backend\.venv`, re-run `setup.bat`. |
| `port 8000 already in use` | `stop.bat` first (or kill the leftover `python.exe` listening on 8000) |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Corporate proxy — `pip install pip-system-certs` then retry |
| Frontend shows 500 on `/api/picks` | Backend hasn't finished first fetch — wait ~30s; or check `backend` terminal for stack trace |
| `0 picks` shown, log looks healthy | Legit outcome — nothing cleared composite threshold today. Check tomorrow. |

## Where things live

```
backend/pipeline.py        ← shared StageResult contract
backend/orchestrator.py    ← run_universe() — the entry point
backend/stages/            ← one file per pipeline stage (swap any independently)
backend/signals/           ← pure indicator math (OBV, CMF, MAs, …)
backend/block_deals.py     ← NSE block + bulk deal downloader

data/picks_<date>.json     ← today's picks (consumed by middleware)
data/traces/               ← per-ticker JSONL stage trace (the RL dataset)
data/portfolio.csv         ← every pick ever surfaced
```

## See also
- `README.md` — full project tour, pipeline diagram
- `PRINCIPLES.md` — the volume-investing rules the pipeline enforces
- `WEEKLY_TRACKING.md` — what to monitor on a weekly / bi-weekly cadence

## Disclaimer
Educational use only. Picks are algorithmic, **not financial advice**. Paper-trade the first 10–15 picks before deploying real capital.
