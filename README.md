# Stockiya

> **Don't invent. Follow the institutions. Pick one.**

Local web app that surfaces up to **3 Indian (Nifty 100) stock picks per day** for a 3–6 month hold. Pure volume strategy — picks are stocks where **institutions are visibly accumulating over months**. Deterministic, no LLM, RL-ready.

> Educational use only. **Not financial advice.**

---

## What it does

1. **Screens Nifty 100** every day for multi-month institutional-accumulation footprints — the spot *before* price moves.
2. **Picks 0–3 setups** that clear the volume gate. Quality over quantity — if nothing qualifies, you see *"nothing actionable today"*.
3. **Every pick ships with** Weinstein stage, entry timing, target window, all 4 exit scenarios, and an auditable point-by-point reasoning checklist.
4. **Every decision is traced** to `data/traces/run_<date>_<ticker>.jsonl` — the RL training dataset for tomorrow.

Read [PRINCIPLES.md](./PRINCIPLES.md) for the investing rule-set.  
Read [ARCHITECTURE.md](./ARCHITECTURE.md) for a full walkthrough: data loading, signal math, every strategy, final selection, and the RL instrumentation.

---

## Architecture — pipeline of swappable stages

```
                       ┌─ rejected ─► trace log ─► RL replay buffer
                       │
[U] Universe ─► [I] Ingest ─► [HR] Hard Rejects ─► [LT] LongTerm Volume (50%) ─►
[TT] Trend Template (15%) ─► [MT] MidTerm Volume (20%) ─► [DD] Direct Deals (10%) ─►
[BR] Breakouts (5%) ─► [S] Score & Rank ─► [H] Hypothesis+Exit ─► [R] Render ─► UI
                                                                                  │
                                                                                  └─► [O] Outcome
                                                                                        T+90/T+180
                                                                                        feeds RL
```

Every stage = one file in `backend/stages/` with the same `run(ctx) -> StageResult` signature. **Replace any file to swap that stage's logic; nothing else changes.**

Weights live in `backend/pipeline.py:STAGE_WEIGHTS`. Defensible against PRINCIPLES.md line-by-line:

| Stage | Weight | Why |
|---|---|---|
| LT Long-Term Volume | **50%** | PRINCIPLES §0 — the primary signal |
| MT Mid-Term Volume  | **20%** | "Confirmation, not the decision" |
| TT Trend Template   | **15%** | Minervini structure check |
| DD Direct Deals     | **10%** | NSE block + bulk deals — named institutional trades |
| BR Breakouts        | **5%**  | Pocket pivot / VDU / CAN SLIM timing |
| | **100%** | |

---

## The volume indicators

All math lives in `backend/volume_signals.py` (kept as one engine) and is exposed via `backend/signals/`. Stages read the same `AccumulationSignals` object — no recomputation, no duplication.

### Long-term lens
- Stan Weinstein Stage (1 base / 1→2 / 2 advance / 3 top / 4 decline)
- 30-week MA + slope
- OBV-90d / OBV-180d slopes (Granville)
- Chaikin Money Flow (60d)
- Up/Down volume ratio (90d)
- Minervini Trend Template (50 > 150 > 200, rising)
- Base length (days within ±10% band)
- QoQ volume growth

### Mid-term confirmation
- Wyckoff phase (accumulation / markup / distribution / markdown)
- OBV-30d, CMF-21d, MFI-14
- Up/Down (30d), 60d VWAP posture
- Vol-trend (10d vs 30d avg)

### Breakout triggers
- Pocket Pivot (Morales/Kacher)
- Volume Dry-Up (Minervini)
- CAN SLIM breakout (O'Neil)

### Direct institutional flow
- NSE block + bulk deal aggregates (30d net buy ratio)

---

## Layout

```
backend/
├── pipeline.py             ← StageResult contract + run_pipeline()
├── orchestrator.py         ← run_universe() — entry point
├── stages/                 ← one file per stage (the swap points)
│   ├── universe.py    [U]      gate
│   ├── ingest.py      [I]      gate (fetch OHLCV + compute signals)
│   ├── hard_rejects.py [HR]    gate (Wyckoff distribution, parabolic, etc.)
│   ├── lt_volume.py   [LT]     50% — long-term institutional
│   ├── trend_template.py [TT]  15% — Minervini structure
│   ├── mt_volume.py   [MT]     20% — this-month confirmation
│   ├── direct_deals.py [DD]    10% — NSE block + bulk deals
│   ├── breakouts.py   [BR]     5%  — pocket pivot / VDU / CAN SLIM
│   ├── score.py       [S]      rank + select top 3
│   ├── hypothesis.py  [H]      template-built rationale + 4-exit plan
│   ├── render.py      [R]      writes data/picks_<date>.json
│   └── outcome.py     [O]      T+90 / T+180 realized return — RL reward
├── signals/                ← facade over volume_signals.py
├── volume_signals.py       ← all indicator math (1100 lines, one engine)
├── block_deals.py          ← NSE block/bulk CSV downloader + 30d aggregator
├── universe.py             ← Nifty 100 list
├── yahoo.py, fetch.py      ← data source adapters
├── demo_data.py            ← DEMO_MODE=1 synthetic OHLCV
├── nightly.py, weekly.py   ← cron entry points
├── catchup.py              ← self-heal on middleware boot
└── portfolio.py            ← pick ledger + weekly close ledger

middleware/
├── main.py                 ← FastAPI app (/api/health, /api/picks, /api/stock/{symbol})
├── picks.py                ← thin: read picks_<date>.json or call run_universe()
├── schemas.py              ← Pydantic DTOs
└── picks_cache.py          ← file-based daily cache

frontend/
└── src/                    ← Vite + React + TypeScript + Tailwind

data/
├── picks_<YYYY-MM-DD>.json ← today's picks (served to UI)
├── traces/                 ← per-ticker JSONL stage trace — the RL dataset
│   ├── run_<date>_<ticker>.jsonl
│   └── outcomes.jsonl      ← T+90 / T+180 realized returns
├── deals/                  ← cached NSE block + bulk deal CSVs
├── portfolio.csv           ← every pick the engine ever surfaced
└── portfolio_weekly.csv    ← Friday closes for open picks
```

---

## HTTP API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{status, date_ist, demo_mode}` |
| `GET /api/picks` | Today's picks (served from disk; runs pipeline if file missing) |
| `POST /api/picks/refresh` | Force re-run the pipeline |
| `GET /api/stock/{symbol}` | Detail panel (volume signals, sparkline, today's pick if any) |

Interactive docs at <http://localhost:8000/docs> when middleware is running.

---

## Setup & run

See [INSTALL.md](./INSTALL.md) — `setup.bat` then `start.bat`. ~5 minutes.

## Operating cadence

See [WEEKLY_TRACKING.md](./WEEKLY_TRACKING.md) — what to monitor weekly, bi-weekly, and at T+90/T+180 to keep the system honest.

---

## Roadmap

| Status | Item |
|---|---|
| ✅ done | Pipeline of swappable stages |
| ✅ done | Per-ticker JSONL traces (RL dataset, no schema change needed) |
| ✅ done | NSE block + bulk deals downloader |
| ✅ done | Outcome tracker (T+90 / T+180 writes to `outcomes.jsonl`) |
| ⏳ next | `scripts/weekly_report.py` — auto-generate the Friday report |
| ⏳ next | Contextual-bandit weight tuner (once ~3 months of outcomes accumulated) |
| ⏳ next | NSE bhavcopy adapter (replaces yfinance for India-native data) |
| ⏳ later | Offline RL (CQL/IQL) for per-stage threshold tuning |

---

## Disclaimer

Educational. Picks are algorithmic and **not financial advice**. The pipeline has **not been backtested** on multi-year Indian data — paper-trade the first 10–15 picks before deploying real capital.
