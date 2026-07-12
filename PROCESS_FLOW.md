# Stockiya — Process Flow

> How the system runs, what it consumes, what algorithms fire at each step,
> what it produces, and what the user finally sees.
>
> Companion to PRINCIPLES.md (the *why*). This is the *how*.

> **Live spine (2026-07-04 evening): v3 soft-gate composite.** The table in §3
> lists both the Wyckoff-VPA target stages `[WY] [VSA] [AVWAP] [EX]` and the
> currently-running intermediate stages `[ACS] [AC] [LT] [CS] [VD] [BR]`.
> The intermediate stages are wired in `stages/__init__.py:PER_TICKER_CHAIN`
> **today**; the Wyckoff-VPA stages are the next target (see AGENT_HANDOFF.md).
> Selection is `hard_gates_passed(r) AND composite_score ≥ COMPOSITE_TAU`,
> where `COMPOSITE_TAU` and per-stage weights load from
> `config/stage_weights.json`.

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

**Truth-in-labelling (2026-07-12):** the stock-detail page no longer hardcodes *"Why all four gates passed"*. It reads `pick_today.gate_confirmation_status.status` (emitted by `build_pick_payload`) and shows:

- `hard_confirmed` → *"Why all N gates passed"*
- `composite_qualified` → *"Composite-qualified — p/t legs confirmed (soft-fail: BR, …)"*

This is the honest surface for the v3 soft-gate composite: a pick can clear the composite `S ≥ τ` while an individual leg's own boolean check failed. The old wording was accurate under the retired 5-AND-gates spine and misleading under v3.

---

## 5b. My Positions — post-pick lifecycle

Every row `[R] Render` produces is also written to `data/portfolio.csv` by
`build_pick_payload → portfolio.record_picks` for downstream monitoring.
That ledger is the single source of truth behind `/api/positions`, served by
`positions_view.list_active_positions` and rendered at
`frontend/src/pages/PositionsPage.tsx`.

### Ledger key & dedupe rule (data/portfolio.csv)

`portfolio.record_picks` is append-only with an **idempotency guard**:

```python
existing = {(r["symbol"], r["entry_date"]) for r in rows}
for p in payload["picks"]:
    if (p["symbol"], today) in existing:
        continue        # already recorded — skip
    rows.append(new_row); added += 1
```

Consequences:
- Same ticker, same day → **cannot** duplicate (guarded).
- Same ticker, **different day** → new tranche row by design (allows
  add-on-strength entries).
- If duplicates ever appear in the CSV, root cause is either manual edits
  or legacy rows from before the guard existed — not the pipeline.

Key columns: `pick_id, trace_id, entry_date, symbol, entry_price,
stop_price, t1_price, t2_price, shares_total, shares_at_t1, shares_at_t2,
confirmation_score, target_date, target_min_date, target_max_date,
status, hit_t1, hit_t1_date, exit_date, exit_price, exit_reason, pnl_pct,
last_updated`. Default holding window centered at **6 months** with ±2
months tolerance (`portfolio.py:202-207`), aligning with `[O]` outcome
horizons at T+90 and T+180.

### Lifecycle state machine

```
   entry_date
      │
      ├─ close ≥ t1_price ────► status = partial_t1
      │      │                  hit_t1 = true, hit_t1_date set
      │      │
      │      └─ close ≥ t2_price ─► status = target_hit
      │                             exit_date / exit_price / pnl_pct set
      │
      ├─ close ≤ stop_price ──► status = stopped         (closed)
      │
      ├─ Day 45  (no T1) ─── tighten stop to entry × (1 − 4%)   (advisory)
      ├─ Day 90  (no T1) ──► status = timed_out          (forced exit)
      ├─ Day 180 ──────────► status = timed_out          (final exit)
      │
      └─ signal flip (OBV divergence /
         ≥3 distribution days /
         AVWAP break)         ── UI action = "exit_distribution" (advisory only;
                                  does NOT change status — human confirms)
```

Constants (`backend/positions_view.py` top-of-file, tunable):
`DAY_45 = 45`, `DAY_90 = 90`, `DAY_180 = 180`,
`DAY_45_TIGHTEN_PCT = 0.04`, `EXPECTED_T1_TRADING_DAYS = 21`.

`/api/positions` returns **only** rows with `status ∈ {open, partial_t1}`.
Closed rows (`target_hit`, `stopped`, `timed_out`, `hypothesis_broken`)
stay in `portfolio.csv` for history and are the input feed to
`outcomes.jsonl`.

### Monitoring cadence

| When (IST) | Job | Effect on ledger |
|---|---|---|
| Daily 17:15 | `[EX]` exit-watch volume scan (`stages/exit_watch.py`) | Advisory only — surfaces `exit_distribution` in `/api/positions`, does NOT set status |
| Friday 17:30 | `weekly.py` → `portfolio.update_open_picks()` | May set `hit_t1` / `partial_t1` / `target_hit` / `stopped` / `timed_out`; appends one row per open pick to `portfolio_weekly.csv` |
| Daily 21:00 | `[O]` outcome tracker (`stages/outcome.py`) | At T+90 and T+180 per pick, appends realized return + hit flags to `data/traces/outcomes.jsonl` |

### Learning loop

```
   portfolio.csv                outcomes.jsonl               stage_weights.json
   (per-pick decisions)  ───►   (per-pick reward at T+90 /   ───►  (weights wᵢ + τ)
                                 T+180, joined via trace_id
                                 to confirmation_score
                                 and per-stage features)
```

`scripts/tune_weights.py` reads `outcomes.jsonl`, fits candidate weight
vectors (ridge + mean-return), and **only overwrites `config/stage_weights.json`
if the candidate's replay metric strictly beats the current champion's**.
Champion-challenger ratchet — accuracy is monotone-non-decreasing by
construction. This is the "learning" that closes back into future picks.

### Known limitations — "suggested vs held" and "user-actual entry" not captured

Two related gaps in the current ledger, both about the user's real
position vs the scanner's assumption:

**1. No accept / decline gesture.** Every pick the scanner emits enters
`portfolio.csv` with `status = open` and is monitored equally. There is
no column recording whether the user actually **took** the position
(paper / live) or **declined** it. All picks show up in `/api/positions`
until they hit an exit condition — including ones the user never took.

**2. No user-actual entry.** `portfolio.record_picks`
(`backend/portfolio.py:172`) auto-fills `entry_date = today` and
`entry_price = close_from_[PS]` at pick time. Every downstream number
— stop, T1, T2, day-45/90/180 checkpoints, P&L, and the "hold / sell at
T1 / exit" action column in `positions_view.py` — is computed against
the scanner's *assumed* entry, not the user's *real* fill. If the user
actually bought a day later at a different price (slippage, limit
order, discretionary timing), the guidance surface is speaking to a
position they don't own.

**Base V1 to close both gaps — additive, no schema break**

Extend `portfolio.csv` with five optional columns; tolerant reader
treats blanks as "use scanner's numbers":

```
ownership         : suggested | paper | live | declined
user_entry_date   : ISO date or ""      (blank = use scanner's)
user_entry_price  : float or 0
user_shares       : int or 0
user_notes        : free-form string
```

`positions_view.py` routing when computing action + P&L:

```
entry_effective   = user_entry_date  or scanner entry_date
price_effective   = user_entry_price or scanner entry_price
shares_effective  = user_shares      or scanner shares_total
stop / T1 / T2    = recomputed off price_effective + original R math
day-45/90/180     = counted from entry_effective
pnl_pct           = (close_today - price_effective) / price_effective
```

UI: on any `ownership = suggested` row, offer
`[Take (paper)] [Take (live)] [Decline]`. "Take" opens a small form
(entry_date / entry_price / shares / notes); blank fields = accept
scanner's numbers. Save re-anchors monitoring immediately.

Downstream: `weekly.py` and `outcome.py` skip `ownership = declined`
rows so cycles aren't burned monitoring rejected picks. `outcomes.jsonl`
can record both scanner-entry and user-entry price so realized returns
are tracked against both surfaces (the algorithm's assumption vs. the
user's actual fill).

Neither gap is implemented today. Both are flagged here as roadmap. See
CHANGELOG 2026-07-12 (design) for the decision trail and sequencing
options.

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
