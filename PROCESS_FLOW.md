# Stockiya — Process Flow

> How the system runs, what it consumes, what algorithms fire at each step,
> what it produces, and what the user finally sees.
>
> Companion to PRINCIPLES.md (the *why*). This is the *how*.

---

## 1. Daily cadence — when does what run

```
┌────────────────────────────────────────────────────────────────────────┐
│  Asia/Kolkata (IST) — once-daily, post-EOD                             │
├────────────────────────────────────────────────────────────────────────┤
│  16:00      NSE close. Wait 30 min for late prints / corrections.      │
│  16:30      backend/nightly.py kicks off the run.                      │
│  16:30–35   [RG] Regime gate. NIFTY 100 50d-MA check.                  │
│             FAIL → write empty picks file. End run.                    │
│  16:35–17:10  Per-ticker pipeline, parallel by thread, over Nifty 100. │
│  17:10–12   [RK] Rank survivors, [PS] size, [H] hypothesis, [R] render.│
│  17:12      data/picks_<date>.json written. /api/picks now serves it.  │
│  21:00      [O] Outcome tracker. Checks every open pick at T+90/T+180. │
│  Friday 17:30  weekly snapshot of all open picks' Friday close.        │
├────────────────────────────────────────────────────────────────────────┤
│  ON APP OPEN — backfill walk                                            │
│  catchup.py scans data/ for the most recent picks file, then runs the  │
│  pipeline for every missing trading day up to today (with as_of_date   │
│  set so OHLCV is sliced to that date — no lookahead). Cap: 30 days.    │
└────────────────────────────────────────────────────────────────────────┘
```

`POST /api/picks/refresh` allows ad-hoc re-runs on the same EOD bar.
Intra-day data is **intentionally not consumed** — there is no signal we trust faster than the daily bar.

---

## 2. Inputs — files and feeds consumed

| Source | Cached at | Why |
|---|---|---|
| Yahoo Finance — 1y daily OHLCV per ticker | in-memory per run | Price + volume tape |
| Yahoo Finance — `^CNX100` 1y | in-memory per run | Regime index (matches Nifty 100 universe) |
| NSE block-deal CSV (`archives.nseindia.com/.../block.csv`) | `data/deals/block_<date>.csv` | Bonus rank signal |
| NSE bulk-deal CSV | `data/deals/bulk_<date>.csv` | Bonus rank signal |
| `backend/universe.py:UNIVERSE` | code-tracked | The 100 tickers |
| `data/portfolio.csv` | persistent CSV | Open picks tracked for outcome |
| `data/picks_<prior_date>.json` | persistent JSON | Self-heal on middleware boot |

Set `DEMO_MODE=1` to swap Yahoo for `backend/demo_data.py` synthetic fixtures — fully offline-safe for dev.

---

## 3. Algorithms applied — stage by stage

Each stage is one file in `backend/stages/` with signature `run(ctx) -> StageResult`. Swap a file to swap the logic; nothing else changes.

| Stage | File | Algorithm | Math summary |
|---|---|---|---|
| **[RG] Regime** | `stages/regime.py` | Index trend filter | `close(^CNX100) > sma(^CNX100, 50)` |
| **[U] Universe** | `stages/universe.py` | Membership check | `symbol ∈ NIFTY100` |
| **[I] Ingest** | `stages/ingest.py` | Fetch OHLCV + slice to as-of date | Pulls 1y daily; if `ctx.today_iso` is a past date, slices bars to that date (no lookahead) and overrides snapshot.current with the as-of close |
| **[LT] Long-term flow** | `stages/lt_flow.py` | 3+ months of institutional accumulation | `obv_slope_90d >= +3%` AND `up_down_vol(90) >= 1.1` AND `sma_slope(150, lookback=50) >= 0` |
| **[CS] Consolidation** | `stages/consolidation.py` | ATR tightness + duration + trend filter | `atr_pct(14) ≤ 4 %` **AND** `days_within_band(close, ±10 %) ≥ 25` (no upper cap; longer bases score higher in ranker) **AND** `close > sma(150)` |
| **[VD] Volume / Divergence** | `stages/volume.py` | Dry-up + bullish OBV–price divergence | `adv(5) / adv(50) < 0.50` **AND** divergence detector (split-window swing-low: price LL while OBV HL, or price flat ±2 % while OBV +≥2 %) |
| **[BR] Breakout** | `stages/breakout.py` | Resistance break + volume confirm + candle close | `close > rolling_high(20, exclude_today=True)` **AND** `today_volume ≥ 1.5 × adv(50)` **AND** `(close − low) / (high − low) ≥ 0.67` |
| **[RK] Rank** | `stages/rank.py` | Confirmation-strength score | `margin_z_score_past_gates + 0.5 × bonus_signal_count`, sorted desc, top 3 |
| **[PS] Position Sizer** | `position_sizer.py` | Risk-of-account share count | `entry = close`, `stop = entry × 0.92`, `shares = floor(account × 0.01 / (entry − stop))`, `T1 = entry × 1.08`, `T2 = entry × 1.16` |
| **[H] Hypothesis** | `stages/hypothesis.py` | Template-built rationale + adaptive exits | Entry/stop/T1/T2 + 3-scenario exit (target-hit, distribution-flip, time-stop) + day-45/90/180 milestones |
| **[R] Render** | `stages/render.py` | JSON write | Atomic write to `data/picks_<date>.json` |
| **[O] Outcome** | `stages/outcome.py` | T+90 / T+180 return logger | Reads open picks from `portfolio.csv`, fetches close at horizon, writes return to `outcomes.jsonl` |

All raw indicator math lives in `backend/indicators.py` as pure functions (no I/O, lookahead-safe). Stages import it; nothing recomputes.

---

## 4. Outputs — files produced

| Path | Written by | Contents |
|---|---|---|
| `data/picks_<YYYY-MM-DD>.json` | `[R] Render` | Today's 0–3 picks with full payload (entry, stop, T1, T2, shares, evidence, time stops) |
| `data/traces/run_<date>_<ticker>.jsonl` | every stage | Per-stage features, score, evidence — the RL feature dataset |
| `data/traces/outcomes.jsonl` | `[O] Outcome` | Realised return at T+90 / T+180 per pick — the RL reward labels |
| `data/portfolio.csv` | `[H] Hypothesis` | Append-only ledger: `trace_id, entry_date, entry, stop, T1, T2, shares` |
| `data/portfolio_weekly.csv` | `weekly.py` | Friday close prices for each open pick |
| `data/deals/*.csv` | `block_deals.py` | Cached NSE block + bulk deal CSVs |

Trace `schema_version: 2` (new gates spine). Old `schema_version: 1` rows are still readable; the `outcome` reward column is unchanged so prior outcomes still feed the RL dataset.

---

## 5. Final recommendation — what the user sees

```
┌──────────────────────────────────────────────────────────────────┐
│ Rank #1   HDFCBANK.NS    confirmation 4.8 (4 of 5 bonuses fired) │
├──────────────────────────────────────────────────────────────────┤
│ Entry            ₹ 1,813.50                                      │
│ Stop  (−8 %)     ₹ 1,668.42         Shares to buy:  68           │
│ T1    (+8 %)     ₹ 1,958.58         → sell 50 %, stop → BE       │
│ T2    (+16 %)    ₹ 2,103.66         → sell remaining 50 %        │
│                                                                  │
│ Why this passed all 4 gates:                                     │
│   ✓ Regime ON   (NIFTY 100 +2.1 % vs 50d MA)                    │
│   ✓ Consolidation   ATR/price 3.2 %, 31 days in band, > 150d MA │
│   ✓ Volume/Divergence   5d vol = 38 % of 50d, OBV +6.8 % HL    │
│   ✓ Breakout   close +1.9 % over 20d high, vol 1.7× avg, 78 % UT│
│                                                                  │
│ Bonus confirmations:                                             │
│   ✓ MA stack 50 > 150 > 200                                      │
│   ✓ OBV-90d slope +8.4 %                                         │
│   ✓ NSE block-deal net-buying last 30d                           │
│   ✓ Pocket-pivot today                                           │
│   · RS rank 41 (top-30 not cleared)                              │
│                                                                  │
│ Time stops:                                                       │
│   Day 45 → tighten stop to ₹ 1,740 if T1 not hit                │
│   Day 90 → exit at market if T1 not hit                          │
│   Day 180 → unconditional exit on remaining shares               │
└──────────────────────────────────────────────────────────────────┘
```

At the top of every page is a regime banner:
- **Regime ON** (green) — today's picks are shown
- **Regime HALTED** (red) — *"No buy alerts will issue until NIFTY 100 closes above its 50-day MA."*

If zero tickers cleared all four gates on a regime-on day, the page shows *"Nothing actionable today — quality over quantity."*

---

## 6. Intervals at a glance

| Interval | Job | Module |
|---|---|---|
| Once daily, post-EOD (16:30 IST) | Full pipeline | `backend/nightly.py` |
| On middleware boot | Backfill: walk every missing trading day since the last picks file (capped at 30) | `backend/catchup.py` |
| Once daily, late evening | Outcome check on all open picks (T+90 / T+180) | `backend/stages/outcome.py` |
| Once weekly (Friday close) | Snapshot all open picks' Friday close; update T1/T2/stop status | `backend/weekly.py` |
| On demand | Force re-run on the same EOD bar | `POST /api/picks/refresh` |
| On `/api/positions` request | Read portfolio.csv, enrich with current prices + today's action | `backend/positions_view.py` |

API serving and UI fetches are **stateless reads** of the JSON files produced above. No live computation in the request path.

---

## 7. Fix-points — where to intervene

| Want to change… | Edit |
|---|---|
| The four gate thresholds (4 %, 1.5×, 0.67, etc.) | Top-of-file `# tunable` constants in each `backend/stages/*.py` |
| Account-risk percent or stop percent | `backend/position_sizer.py` |
| T1 / T2 multiples or ladder ratio | `backend/stages/hypothesis.py` |
| Day-45 / 90 / 180 milestones | `backend/stages/hypothesis.py` |
| Bonus-signal list for ranking | `backend/stages/rank.py:BONUS_CHECKS` |
| Universe (add / remove tickers) | `backend/universe.py:UNIVERSE` |
| Regime indices (e.g. add Nifty IT) | `backend/stages/regime.py:REGIME_TICKERS` |

Every threshold change is captured in the trace JSONL `FINAL` row, so the RL replay buffer always knows which threshold set produced which pick.

---

## 8. Coding-trap mitigations (baseline)

| Trap | This round | Next iteration |
|---|---|---|
| Lookahead bias | Live path uses EOD-closed bars only; backtest harness must slice `df.iloc[:-1]` before scoring | Explicit replay clock with frozen-data fixture |
| Static support / resistance | All resistance = `rolling_high(20, exclude_today=True)`. No hardcoded levels anywhere | Volume-profile node detection as a secondary level |
| Unadjusted data | `assert auto_adjust=True` in `yahoo.py`; log split events for review | Migrate to NSE bhavcopy (already adapter-ready) |
| Curve-fitting | Thresholds are `# tunable` constants, not magic numbers | LinUCB contextual bandit over threshold space once ≥ 90 days of outcomes accumulate |

---

## Disclaimer

Educational. Algorithmic recommendations are **not financial advice**. Paper-trade the first 10–15 picks before deploying real capital.
