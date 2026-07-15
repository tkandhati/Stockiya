# Agent Handoff

Last updated: 2026-07-15

For proposals that have been analyzed but not shipped, see `WISHLIST.md`.
For ideas parked pending trace evidence, see `ideas.md`.

## Latest Change (2026-07-15) — Picks-vs-portfolio reconciliation + volume-based dynamic horizon

Fixed the "same symbol appears as EXIT and BUY on the same day" trust
bug. Also replaced the fixed 6-month `target_date` with a volume-based
bucketed horizon (30/60/90/120/180 days) that continuously monitors
against the user's actual fill for taken positions.

- **New modules** — `backend/horizon.py` (bucketed estimator +
  revalidation), `backend/picks_reconcile.py` (ownership-aware
  annotation of picks vs. open portfolio positions),
  `backend/picks_diff.py` (consecutive-pick delta for audit trail).
- **Reconciliation rules** — taken (paper/live) row with `exit_*` action
  gets `suppressed_from_ui` on today's pick; taken row with
  hold/tighten/extend gets `already_held` annotation; suggested-only
  rows pass through and are superseded at record time.
- **Portfolio replace/duplicate** — `record_picks` now supersedes an
  open `suggested` row when the same symbol re-fires; keeps a taken
  row and adds a fresh `suggested` alongside (two rows with different
  `entry_date` are legitimate — real capital vs. fresh signal).
- **Dynamic horizon** — `end_date = entry_d + horizon_days`, computed at
  entry from confirmation score + Weinstein stage + entry_timing. At
  `end_date`, healthy trajectory → recommend extend to next bucket;
  flipped or at max → recommend exit. `DAY_180` remains the hard cap.
- **Continuous monitoring** — `positions_view` recomputes the effective
  `end_date` from `user_entry_date + horizon_days`, so the horizon
  clock starts from the user's real fill day for taken positions.
- **Schema** — `PICKS_SCHEMA_VERSION` 5→6; `portfolio.csv` gains five
  columns (`end_date`, `horizon_days`, `horizon_basis`, `horizon_source`,
  `superseded_by`). Tolerant reader; older rows load unchanged.
- **Not yet done** — auto-persist horizon extensions (belongs in
  `backend/weekly.py`); backfill `end_date` on pre-existing rows (they
  degrade to classic 45/90/180 rules).
- **Invariants preserved** — no scanner scoring changes; τ, gate
  weights, and `classify_trigger` untouched.
- **Validation** — parse-check clean on all 7 modified files;
  end-to-end scratch-portfolio test verified reconcile splits correctly,
  DAY_180 hard cap fires before horizon logic, duplicate row lands
  when taken position has active exit signal.

See `CHANGELOG.md 2026-07-15` for full write-up. See `PROCESS_FLOW.md §5b`
for the updated lifecycle rules.

---

## Prior Change (2026-07-13) — My Positions V1: ownership + user-actual fill

Every pick the scanner emits now starts as `ownership="suggested"` in
`data/portfolio.csv`. On the `My Positions` page the user can accept
(paper / live, with optional custom entry date / price / shares / notes)
or decline. `positions_view` re-anchors P&L, `days_held`, and day-45/90/180
time-stops on whichever entry is populated (user's fill takes precedence
when provided, otherwise falls back to the scanner's numbers). Stop /
T1 / T2 stay at the scanner's absolute price levels — they're targets on
the tape, not offsets from the fill.

- **Schema** — five additive columns on `portfolio.csv`: `ownership`,
  `user_entry_date`, `user_entry_price`, `user_shares`, `user_notes`.
  Tolerant reader; older rows load unchanged.
- **Downstream filters** — `positions_view.list_active_positions`,
  `portfolio.update_open_picks` (weekly Friday), and `stages/outcome.py`
  (T+90 / T+180) all skip `ownership="declined"`.
- **API** — `POST /api/positions/{pick_id}/take` (paper|live with
  optional user_* fields) and `POST /api/positions/{pick_id}/decline`.
- **UI** — `PositionsPage.tsx` renders `Suggested` and `Held` sections;
  `PositionCard.tsx` shows an ownership badge plus a "Your fill" strip
  when user's inputs diverge from scanner's.
- **Invariants preserved** — no scanner change; `pipeline.py`,
  `config/stage_weights.json`, and τ untouched; the pick set is
  byte-identical to yesterday.
- **Validation** — `python -m compileall backend middleware` clean;
  `npm run build` clean (734 kB main bundle, +8 kB for the inline
  take-fill form). Corporate-firewall constraint stands — no live
  pipeline run performed.

See `CHANGELOG.md` for the full write-up + file list. See
`PROCESS_FLOW.md §5b` for the routing table.

---

## Latest Change (2026-07-12) — pre-breakout feedback: 3 bug fixes + advisory volume metrics

Triggered by "Stockiya — Feedback for Claude Code" reviewing an ABB.NS pick.
See CHANGELOG for the full write-up; short version:

- **Bug 1 (UI wording lie).** `hypothesis.py` now emits
  `gate_confirmation_status`; `StockDetailPage.tsx` reads it. No more
  "all four gates passed" when a leg's own row shows failing evidence.
- **Bug 2 (thesis sign-flip).** `_build_headline` branches on
  `sign(break_pct)`. Negative days now say "closed X% below 20d high — no
  confirmed breakout yet."
- **Bug 3 (OBV divergence + instability).** `volume_signals.py` unified onto
  `indicators.obv()` (one source). New `indicators.obv_norm_slope_pct` —
  slope-of-regression normalized by `mean(|OBV|)` — is bounded across zero
  crossings and is the preferred user-facing form. `AccumulationSignals`
  now emits `obv_norm_slope_90d_pct` / `obv_norm_slope_180d_pct` alongside
  the legacy % forms.
- **Additive advisory metrics** in `indicators.py` and surfaced in
  `stages/breakout.py`: `volume_robust_zscore`, `dry_up_streak_days`,
  `anomaly_cluster_count`. Zero threshold changes — existing gates untouched.
- **Test:** `Stockya-tuner/scripts/test_prebreakout_feedback.py` runs against
  `data/ohlcv/ABB.csv` and reproduces the feedback's numbers exactly.

Follow-up items from this feedback (`PB / BR split`, deferred overlays)
have been analyzed and moved to `WISHLIST.md`.

---


## Current Architecture Truth

Stockiya is a deterministic, volume-only Nifty 100 screener for a **3-6 month
swing hold**. The **design spec** is the Wyckoff-VPA spine described in
PRINCIPLES.md and ARCHITECTURE.md §0-§0.3. The **code as of this commit** runs
an **intermediate v3 soft-gate composite spine** — Wyckoff-VPA target still
ahead, but the "5-AND-gates blocks every pick" issue is solved.

### Design (source of truth: PRINCIPLES.md)

```
[U] → [I] → [HR] → [WY] scored → [VSA] trigger → [AVWAP] scored → [RK] → [PS] → [H] → [R]
                                                                                      │
                                                                     [EX] Exit-watch runs
                                                                     daily on held picks
```

- Two hard gates: `[HR]` (safety) and `[VSA]` (entry trigger).
- Two scored stages: `[WY]` (Wyckoff phase confidence) and `[AVWAP]` (anchored-VWAP hold).
- One trigger stage `[VSA]` fires on **any** of SOS bar / pocket pivot / no-supply test.
- One new daily stage `[EX]` scans open picks for volume-based early exit.
- All thresholds are ATR20-normalized so the same rule works in calm and volatile regimes.

### Live code (v3 soft-gate composite, as of 2026-07-04 evening)

```
Regime -> Universe -> Ingest -> Hard Rejects -> [ACS] Accum-Screen -> [AC] Accumulation ->
    Long-Term Flow -> Consolidation -> Volume/Divergence -> Breakout -> Rank -> Hypothesis/Position Sizing -> Render
```

Selection is now `hard_gates_passed AND composite_score >= COMPOSITE_TAU`
(config-driven from `config/stage_weights.json`). Only `[U] [I] [HR]` short-
circuit on failure; every other stage always runs and contributes 0 margin
if it fails. Ranking uses the same weighted composite plus bonus signals.

Primary source files:
- `backend/orchestrator.py`
- `backend/pipeline.py`
- `backend/stages/__init__.py`
- `backend/stages/*.py`
- `frontend/src/App.tsx`
- `middleware/main.py`

---

## Latest Change (2026-07-04 evening — code + docs)

**Soft-gate composite spine + tuner ratchet + robustness fixes shipped
in code.** See `CHANGELOG.md` 2026-07-04 evening entry for the full list.
Documentation rewrite from the morning covered:

- `PRINCIPLES.md` — full rewrite (new spine, exit-watch, ATR-adaptive stops)
- `ARCHITECTURE.md` — new §0-§0.3 prepended; §0.4 onward marked archival
- `PROCESS_FLOW.md` — stages table, cadence, pick payload, fix-points updated
- `AGENT_HANDOFF.md` — this file
- `CHANGELOG.md` — new dated block
- `README.md` — top-level intent + pipeline diagram updated

The user's decision reason: the current serial 5-AND-gate chain was rejecting
almost every otherwise-strong setup — a single missed sub-threshold killed the
whole ticker. Wyckoff-VPA folds the three structural checks into one *scored*
[WY] stage so a weak leg is tolerated; only the trigger bar and hard rejects
remain binary.

### Prior in-flight work (still valid, not obsolete)

- `backend/indicators.py:volume_spike_event` — bullish_ignition, early_accumulation, etc.
- `backend/orchestrator.py` — `_collect_near_misses`, `_collect_ready_to_break`, `_collect_early_volume_signals`
- `PicksResponse.early_signals`, `Pick.volume_event`, `Accumulation.volume_event`
- `frontend/src/components/EarlySignalPanel.tsx`

These remain the presentation layer for the near-miss / closer-to-passing /
early-warning surfaces on the picks page. Under the Wyckoff-VPA spine they'll
be repopulated from [WY] score gradations and [EX] exit-watch, rather than
from failed-gate reasons.

### Dead code to reconcile

- `backend/stages/accum_screen.py` — [ACS] tier-1 accumulation screen, fully implemented but **not imported** in `stages/__init__.py` and not in `PER_TICKER_CHAIN`.
- `backend/stages/accumulation.py` — [AC] tier-2 with ADI divergence, same status.
- `scripts/accum_window_sweep.py` — offline W-sweep utility.

**Reuse plan:** the accumulation modules already compute range-tightness,
volume-dryness, and ADI positive divergence — three of the primitives [WY]
needs. Rather than delete them, the next agent should extract the shared
helpers into `backend/indicators.py` and delete the two dead stage files, or
rename `accumulation.py → wyckoff.py` and expand its check-set to cover
Phase C / Phase D.

---

## Notes For Next Agent

- **`PRINCIPLES.md` is the source of truth.** When it disagrees with any other
  doc or with the code, PRINCIPLES.md wins and the other must be updated.
- `ARCHITECTURE.md` §0.4 onward is *legacy content*. Don't cite line numbers
  from it as if they describe live behavior — read the code.
- `data/` files in the repo are stale demo artifacts; do not infer current
  market state from them.
- `DATA_SOURCE=bhavcopy` is still a stub. Live data is Yahoo unless
  `DEMO_MODE=1`.
- Corporate firewall blocks live Yahoo/NSE/PyPI — never propose runs that need
  the internet. Use cached OHLCV or `DEMO_MODE=1`.

## Recommended Next Work (in order)

**Already landed — do NOT redo:**
- ✅ Soft-gate composite pipeline (`pipeline.py`) — 2026-07-04
- ✅ ACS + AC wired into `PER_TICKER_CHAIN` — 2026-07-04
- ✅ Composite filter in orchestrator — 2026-07-04
- ✅ `rank.py` uses live weights from config — 2026-07-04
- ✅ `config/stage_weights.json` + `scripts/tune_weights.py` (champion-challenger) — 2026-07-04
- ✅ Crash-handler / ingest / DEMO_MODE robustness fixes — 2026-07-04
- ✅ Trace `SCHEMA_VERSION` bumped to 3 — 2026-07-04
- ✅ NIFTY 500 universe + `STOCKYA_UNIVERSE=custom` file loader — 2026-07-05
- ✅ Composite threshold τ tuned 0.35 → 0.28 — 2026-07-05
- ✅ Empty-state UI unified into `ClosestToFiringPanel` (killed NearMiss + ReadyToBreak + EarlySignal panels) — 2026-07-05
- ✅ Picks-response `PICKS_SCHEMA_VERSION` bumped to 5 — 2026-07-05
- ✅ `.env.example` default flipped to `DATA_SOURCE=yahoo` (live pull) — 2026-07-05
- ✅ `start.bat` auto-heals missing `backend\.env` — 2026-07-05
- ✅ Adaptive per-ticker scan windows in `[ACS]` and `[AC]` via `indicators.adaptive_windows()` — 2026-07-05 (supersedes the earlier fixed `(10, 20, 40)` sweep)

**Still open (roughly in priority order):**

1. **Write `backend/stages/wyckoff.py`** — Phase A→D classifier from
   PRINCIPLES §2.1. Return `StageResult` with `score` = phase confidence ×
   phase-preference weight. Wiring: add to `PER_TICKER_CHAIN` and to
   `config/stage_weights.json` (currently `"WY": 0.00`).
2. **Write `backend/stages/vsa.py`** — trigger with three sub-rules
   (SOS / pocket-pivot / no-supply). Lift `_check_pocket_pivot_today` from
   `rank.py` into `indicators.py` first.
3. **Write `backend/stages/avwap.py`** — one new indicator
   `anchored_vwap(df, anchor_idx)` + a scored stage.
4. **Write `backend/stages/exit_watch.py`** and add a call site in
   `backend/nightly.py` (17:15 IST slot, after the picks-render step).
5. **Frontend:** repoint `EarlySignalPanel.tsx` to consume `[EX]` output
   in addition to `volume_spike_event`.
6. **Backtest:** run the tuner over 12 months to build a real champion
   metric in `config/stage_weights.json` (currently `champion_metric.value:
   null` — first tuner run will bootstrap).
7. **Local OHLCV cache** — on corporate, `DEMO_MODE=1` works but doesn't
   give real prices. Need a `STOCKYA_OHLCV_DIR` with browser-downloaded
   bhavcopy or a personal-laptop-built cache.

## Validation Already Run (2026-07-04 evening)

- `python -m compileall backend middleware scripts` — clean.
- Import smoke on `pipeline`, `stages`, `orchestrator`, `rank`,
  `tune_weights` — all resolve.
- `DEMO_MODE=1 fetch_ohlcv('HDFCBANK.NS')` → 252 rows synthetic, no network.
- `scripts.tune_weights` dry-run → "refuse: 0 outcomes < 20" (correct).
- **End-to-end pipeline run not performed** — corporate firewall constraint
  in `memory/feedback_no_live_runs.md`.
