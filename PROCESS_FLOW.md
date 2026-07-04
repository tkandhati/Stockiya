# Stockiya — Process Flow

> How the system runs, what it consumes, what algorithms fire at each step,
> what it produces, and what the user finally sees.
>
> Companion to PRINCIPLES.md (the *why*). This is the *how*.

> **Spine: Wyckoff-VPA (2026-07).** The stage list, thresholds, and ranking
> below have been updated to match the current design. See PRINCIPLES.md for
> the spec.

---

## 1. Daily cadence — when does what run

```
┌────────────────────────────────────────────────────────────────────────┐
│  Asia/Kolkata (IST) — once-daily, post-EOD                             │
├────────────────────────────────────────────────────────────────────────┤
│  16:00      NSE close. Wait 30 min for late prints / corrections.      │
│  16:30      backend/nightly.py kicks off the run.                      │
│  16:30–35   [RG] Regime gate. NIFTY 100 50d-MA + ATR20% vol clock.     │
│             FAIL → write empty picks file. End run.                    │
│  16:35–17:10  Per-ticker pipeline (U → I → HR → WY → VSA → AVWAP),     │
│               parallel by thread, over Nifty 100.                      │
│  17:10–12   [RK] Rank survivors, [PS] size, [H] hypothesis, [R] render.│
│  17:12      data/picks_<date>.json written. /api/picks now serves it.  │
│  17:15      [EX] Exit-watch scans every open pick in portfolio.csv;    │
│             fires early-exit alerts into positions_view.               │
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
| **[RG] Regime** | `stages/regime.py` | Index trend + vol clock | `close(^CNX100) > sma(^CNX100, 50)`; ATR20% sets per-day volatility multiplier for downstream thresholds |
| **[U] Universe** | `stages/universe.py` | Membership check | `symbol ∈ NIFTY100` |
| **[I] Ingest** | `stages/ingest.py` | Fetch 180 daily bars + as-of slice | Pulls 180d daily; if `ctx.today_iso` is a past date, slices bars to that date (no lookahead) and overrides snapshot.current with the as-of close |
| **[HR] Hard rejects** | `stages/hard_rejects.py` | Safety gate | `ret_30d ≤ +25 %` **AND** `close ≤ 1.15 × sma(50)` **AND** no auditor-exit / SEBI flag / promoter-pledge > 50 % |
| **[WY] Wyckoff phase** | `stages/wyckoff.py` *(new)* | Phase A→D classifier, scored | Detects Phase C (spring: narrow-range low-vol undercut of Phase-A low) or Phase D (SOS: wide-range up-close ≥ 1.5×ADV50, above 150d MA). Score = phase confidence × phase-preference weight (C=1.0, D=0.9). Replaces the retired [LT]+[CS]+[VD] AND-chain. |
| **[VSA] Bar confirmation** | `stages/vsa.py` *(new)* | Trigger — any of three | **SOS bar**: `close ≥ rolling_high(20)` AND `vol ≥ vol_mult × ADV50` AND `(close-low)/(high-low) ≥ 0.67`; **pocket pivot**: today up-day AND `vol > max(down-day vol in prior 10)`; **no-supply test**: down-day inside Phase-C low AND `vol < 0.60 × ADV10` AND close in upper half. `vol_mult` is 1.5 in normal regime, 2.0 in high-vol. |
| **[AVWAP] VWAP hold** | `stages/avwap.py` *(new)* | Anchored-VWAP structural score | Anchor = lowest close in last 90 sessions. Score = fraction of last 20 bars with `close ≥ AVWAP`, times `sign(slope(AVWAP, 20))`. |
| **[RK] Rank** | `stages/rank.py` | Confirmation-strength score | `wy_score + avwap_score + vsa_margin + 0.5 × bonus_signal_count`, sorted desc, top N (default 3) |
| **[PS] Position Sizer** | `position_sizer.py` | Risk-of-account share count, ATR-adaptive stop | `entry = close`, `stop = entry − max(0.08 × entry, 2 × ATR20)`, `R = entry − stop`, `shares = floor(account × 0.01 / R)`, `T1 = entry + R`, `T2 = entry + 2R` |
| **[H] Hypothesis** | `stages/hypothesis.py` | Template-built rationale + adaptive exits | Entry/stop/T1/T2 + 3-scenario exit (target-hit, distribution-flip, time-stop) + day-45/90/180 milestones |
| **[R] Render** | `stages/render.py` | JSON write | Atomic write to `data/picks_<date>.json` |
| **[EX] Exit-watch** | `stages/exit_watch.py` *(new)* | Volume-based early-exit scan on open picks | Fires if any: OBV-20d neg divergence at new 20d high; churning bar (vol top-20% of 50d, spread bottom-20%, close near open); ≥ 3 distribution days in 15 sessions; two consecutive closes < AVWAP; climax-vol + reversal |
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
│ Rank #1   HDFCBANK.NS   confirmation 3.4  (Phase-D SOS + pocket)│
├──────────────────────────────────────────────────────────────────┤
│ Entry            ₹ 1,813.50                                      │
│ Stop  (2×ATR)    ₹ 1,668.42   R = ₹145.08   Shares to buy:  68  │
│ T1    (+1R)      ₹ 1,958.58   → sell 50 %, stop → BE            │
│ T2    (+2R)      ₹ 2,103.66   → sell remaining 50 %             │
│                                                                  │
│ Volume evidence:                                                 │
│   ✓ Regime ON   (NIFTY 100 +2.1 % vs 50d MA, vol clock: normal) │
│   ✓ [WY]  Phase D — SOS: wide-range up-close, vol 1.7× ADV50    │
│   ✓ [VSA] Pocket-pivot fired: today up-day, vol > any prior-10  │
│   ✓ [AVWAP] Close ₹1,813 > anchored VWAP ₹1,742 (holding 18/20) │
│                                                                  │
│ Bonus confirmations:                                             │
│   ✓ MA stack 50 > 150 > 200                                      │
│   ✓ OBV-90d slope +8.4 %                                         │
│   ✓ CMF-60d +0.19                                                │
│   ✓ NSE block-deal net-buying last 30d                           │
│   ✓ Sector-relative volume 1.8× auto-sector median               │
│   · RS rank 41 (top-30 not cleared)                              │
│                                                                  │
│ Exit-watch (checked daily):                                      │
│   OBV divergence • churning bar • ≥3 dist-days • AVWAP break     │
│                                                                  │
│ Time stops:                                                       │
│   Day 45 → tighten stop to entry − 0.5R if T1 not hit           │
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
| Once daily, post-EOD (17:15 IST) | Exit-watch scan on every open pick | `backend/stages/exit_watch.py` |
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
| Wyckoff phase thresholds (vol multiples, range %, phase-preference weights) | `backend/stages/wyckoff.py` — top-of-file `# tunable` constants |
| VSA trigger thresholds (SOS vol mult, pocket-pivot lookback, no-supply vol %) | `backend/stages/vsa.py` — top-of-file `# tunable` constants |
| AVWAP anchor window, hold-fraction threshold | `backend/stages/avwap.py` — top-of-file `# tunable` constants |
| Exit-watch rules (OBV window, dist-day count, churning bounds) | `backend/stages/exit_watch.py` |
| Regime vol-clock multipliers (normal/high VIX bands) | `backend/stages/regime.py` |
| Account-risk percent or ATR-stop multiple | `backend/position_sizer.py` |
| T1 / T2 R-multiples | `backend/stages/hypothesis.py` |
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
