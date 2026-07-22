# Ideas parking lot

Deferred ideas that have been discussed but deliberately not shipped. Each entry states the idea, why it was parked, and the concrete signal that would justify revisiting it.

## Guiding principle

Before entertaining any new scoring-logic change (tilts, new stages, threshold shifts), first check whether `data/traces/` + `learnings.md` already show the current regime is under- or over-scoring in that direction. Change-now without trace evidence is a bet; wait-and-measure is the same signal, cheaper. If the traces show no signal after a reasonable window, the honest move is usually to delete or redesign the dormant machinery, not to bolt more logic on top.

---

## Pre-breakout volume-based fine-tune

**Date parked:** 2026-07-15
**Status:** Deferred pending trace evidence

### The idea

Two phases proposed to strengthen the pre-breakout regime using volume-native signals.

**Phase 1 — Quiet Accumulation Tilt.** Combine dormant Group E metrics into a bounded advisory tilt on the composite score:

- `dry_up_streak_days_p25 >= 3` (already emitted by `[BR]`)
- `anomaly_cluster_count_15d >= 1` (already emitted by `[BR]`)
- Optional: `sma(delivery_pct, 30) / sma(delivery_pct, 90) >= 1.15` (blocked — no `data/delivery/`, firewall)
- Gate: `AC.score >= TRIGGER_AC_MIN_SCORE` (0.6)
- Tilt: up to +5% on composite when both/all fire

**Phase 2 — Wyckoff Spring / No-Supply trigger.** Populate the currently-stubbed `[VSA]` slot with real structural detection:

- Spring: undercut of base support + reclaim + no volume expansion + strong close
- No-Supply Test: narrow spread + dry volume + close in upper half + holding support
- Wire into `classify_trigger` so `pre_breakout` requires structural evidence, not just AC strength

### Why parked

1. **Pre-breakout path is already recently tuned.** The `AC.score >= 0.6` floor in `classify_trigger` was added after the Bajaj-Auto incident. VD-weight is already redistributed to LT/AC on `pre_breakout`. Layering another tilt on top of a recent fix, before that fix has proven itself, risks re-introducing the same over-admission failure from a new angle.

2. **No evidence of under-firing.** Adding a +5% tilt is only worth it if pre-breakout picks are currently ranked systematically too low. That claim needs data, not intuition.

3. **Group E is already traced.** The metrics flow into `data/traces/` daily. `weekly-learn` can measure their predictive value against T+90/T+180 outcomes with zero code changes. Do that first.

4. **Phase 2's wiring assumes a `[VSA]` stage that doesn't exist.** `"VSA": 0.00` in `COMPOSITE_WEIGHTS` is a stub placeholder; no code populates `stage_results["VSA"]`. Would require building a new stage from scratch OR folding into `[BR]` features.

### Signal to revisit

Revisit **only** when all of the following are true:

- [ ] 4–6 weeks of `weekly-learn` output has regressed `dry_up_streak_days_p25` and `anomaly_cluster_count_15d` against T+90/T+180 outcomes.
- [ ] The digest shows a measurable, positive correlation between these metrics and forward returns on names that are otherwise borderline (composite near tau).
- [ ] The tilt strength (currently guessed at 5%) can be calibrated to the *measured* effect size, not chosen arbitrarily.

If Group E shows **no** predictive value after that window, the right move is to delete or redesign it — not to add a tilt.

### Fix-points if it ever ships

- Tilt home: `backend/pipeline.py` (new small helper) or `backend/composite_tilts.py`. Not `breakout.py` — that's [BR] gate math.
- Constants: `QUIET_TILT_MAX = 0.05`, `DRY_STREAK_MIN = 3`, `DELIV_RATIO_MIN = 1.15`, `CLUSTER_MIN = 1`.
- Trace key: `quiet_accum_tilt_applied` in the [S] trace payload so weekly-learn can measure the tilt against outcomes.
- Phase 2 first-cut: option (B) — emit `vsa_event` as advisory feature on `[BR]`, do not touch `classify_trigger` until measured.
- Delivery data: separate ingest problem (firewall + NSE); do not couple to this fine-tune.

---

## Wyckoff/VSA fine-tune trio (A price-tightness · B AVWAP anchor · C effort-vs-result)

**Date parked:** 2026-07-21
**Status:** Idea **C** SHIPPED 2026-07-21 (VSA effort-vs-result gate on the pocket pivot — see below). Ideas **A** and **B** deferred, reasoning captured here.

### Context — what shipped (C) and why

Three Wyckoff-motivated refinements were proposed. Only **C** touched running code with a low blast radius, so it shipped; A and B are parked.

**C — Effort-vs-result on the pocket pivot (SHIPPED).** VSA principle: volume is the *effort*, the bar's spread (High-Low) + close location are the *result*. A big-volume / tiny-spread (or wide-but-weak-close) up bar is churn/absorption, not demand — the HFT micro-trap. New pure helper `indicators.effort_vs_result_ok(df)` requires the trigger bar's spread to exceed the **trailing-average spread** (parameter-free — the threshold *is* the average, no tuned constant; the user's original `sma(high-low,20)` with an exclude-today fix) AND the close to finish in the upper half of the range. Wired as an AND-condition into both live pocket-pivot sites — `rank.py::_check_pocket_pivot_today` (rank bonus) and `volume_signals.py::_pocket_pivot_count` (narrative/entry-timing) — through the one shared helper so the two can't drift (the OBV-divergence lesson). Low risk: pocket pivot is a bonus/annotation, not a hard gate, so tightening it can't cause zero-picks.

### A — Price-tightness `std(close,10) < 0.5·ATR(20)` in [AC] — DEFERRED

**The idea.** Add a volatility-contraction (VCP) check to `[AC]`: standard deviation of the last-10 closes below half of ATR(20) confirms coiling. Stated goal: filter "dead stocks" (low volume + high random variance) from coiling pre-breakouts (low volume + microscopic variance).

**Why parked:**
1. **Largely redundant.** `[AC]` (`backend/stages/accumulation.py`) already requires, on the same bar, *all three* of: tight range (`range_pct_window ≤ TIGHT_RANGE_PCT_MAX`, adaptive to 2.5×ATR20%), volume dryness (`vol_dryness_ratio < 0.95`), and ADI positive divergence. A dead stock with high price variance already fails the range check; low volume alone doesn't pass because divergence must also fire. The incremental filtering power over the existing gate is small.
2. **Only genuinely-new nuance is close-dispersion vs extreme-range.** `std(close,10)` is wick-insensitive where the existing `range_pct` uses high/low extremes — a subtly different, real signal. Worth *measuring*, not worth hard-gating on faith.
3. **Hand-tuned constant + gate-tightening.** The `0.5` coefficient is a hand-picked threshold; adding it as a 4th AND-condition tightens a gate that currently works. Collides with the guiding principle at the top of this file (no new scoring logic without trace evidence the regime is mis-scoring) and PRINCIPLES §9 (thresholds evolved by the tuner, never hand-tuned) and the "additive labels over pick redesigns" preference.

**Signal to revisit:** ship as a **weight-0 traced feature first** — emit `close_std10_over_atr20` from `_score_at_window` (same dark-launch pattern as the signed-pressure primitives / Group E). Only promote to a gate/tilt once `weekly-learn` shows it separates winners from dead stocks against T+90 outcomes (first cohort ~2026-10-02), and let the tuner set the cutoff instead of freezing `0.5`.

**Fix-points if it ships:** feature computed in `backend/stages/accumulation.py::_score_at_window`; new trace key `close_std10_over_atr20` in the `[AC]` feature dict; no change to `passed` logic in v1.

### B — Anchor AVWAP to the selling climax instead of the lowest close — DEFERRED

**The idea.** The current `[AVWAP]` spec anchors at the "lowest close of the last 90 sessions" (PRINCIPLES §2.3). Proposal: anchor instead to the highest-volume bar in 90 sessions — the Wyckoff Phase-A selling climax where smart money first absorbed supply.

**Why parked:**
1. **`[AVWAP]` is not built yet.** It is a documented target stage (PRINCIPLES §2.3, AGENT_HANDOFF.md:465 "write `backend/stages/avwap.py`"), a `0.00`-weight stub in `COMPOSITE_WEIGHTS`, with no code computing an anchored VWAP anywhere. This is feedback on an unbuilt spec, not a change to running code — nothing to implement today.
2. **"Highest volume bar" ≠ selling climax.** The max-volume bar over 90 days is just as often a breakout day, an earnings gap-up, an index-rebalance print, or a distribution bar. Anchoring the VWAP to a *top* inverts the signal — price sits below it and a healthy base falsely reads as weak. Volume alone can't distinguish absorption from distribution (the reason `signed_volume_pressure` exists).
3. **The right rule is a *guarded* climax, which the repo already defines.** PRINCIPLES §2.1 defines Phase A as *highest 60d volume AND widest range AND close in lower third*. The principled anchor is that qualified climax bar, with **fallback to lowest-close** when no qualified climax exists (slow drift-down bases have no single climax; lowest-close is always well-defined and monotonic).

**Signal to revisit:** when `backend/stages/avwap.py` is actually written. Fold the guarded-climax anchor into that stage's design from the start rather than shipping the naive max-volume rule.

**Fix-points if it ships:** new indicator `anchored_vwap(df, anchor_idx)` + anchor selector `phase_a_climax_idx(df, lookback=90)` (high-vol ∧ wide-range ∧ lower-third close; else `argmin(close)`) in `backend/indicators.py`; consumed by the new `backend/stages/avwap.py`; spec update in PRINCIPLES §2.3.

---

## Precision-first refit — deferred pillars

**Date parked:** 2026-07-17
**Status:** Related pillars 1–5 shipped dark-launch on 2026-07-17; these three items are the follow-ups that need more data / more infra before they earn their weight.

### The idea (three follow-ups)

**Follow-up A — Delivery-percent loader (`backend/delivery.py`).** Load NSE `sec_bhavdata_full_<date>.csv` from `test_data/deliveries/` (same manual-drop pattern as `test_data/`). Feed `%DlyQt to TrdQt` into the composite via a new metric in `stages/volume.py`: signed delivery anomaly with 2–5 session follow-through. Turns the "participant evidence" ladder from `inferred → disclosed_large_client` into `inferred → delivery_confirmed → disclosed_large_client`.

**Follow-up B — Excess-move signed pressure.** The signed-pressure family shipped today only computes `Pclose = CLV × RV`. The plan's second half — `Pmove = ExcessMove × RV` with `ExcessMove = tanh((stock_ret − sector_ret) / ATR20%)` — needs sector-index bars, which the fetch layer doesn't carry. Add a lightweight sector-benchmark loader (Nifty sector indices, one bar per day per sector) into `backend/fetch.py`, then extend `indicators.signed_volume_pressure` with an `excess_move` variant.

**Follow-up C — Calibrated logistic β re-fit + Theil–Sen runway.** Replace the fixed `scored_stage_weights` dict with a regularized monotonic logistic fit against a labeled-outcome set: label = *"breakout above 60-session resistance within 20 sessions AND +2 ATR before −1 ATR or 90-session expiry"*. Fitter lives inside `scripts/tune_weights.py`; the champion-challenger ratchet keeps regression-safety. The Theil–Sen runway extrapolation lands inside the future `stages/exit_watch.py` — daily score slope over last 10 finalized sessions with bootstrap CI, capped at 20-session horizon.

### Why parked (each has its own gate)

**A · delivery loader:**
1. Requires a new manual drop-file family (`test_data/deliveries/sec_bhavdata_*.csv`) — bootstrapping cost that isn't justified until pillars 1–5 show measurable shadow-trace uplift.
2. The `has_disclosed_large_client` path (pillar 4) already labels institutional participation via block/bulk classifier — first check whether that label alone shifts pick precision before adding a second evidence source.

**B · excess-move / sector benchmark:**
1. `backend/fetch.py` currently only pulls per-ticker OHLCV. Adding index bars is a fetch-layer change, not a stages change; belongs with the LT-flow overhaul, not the pressure work.
2. Firewall constraint: sector-index bars will need the same `test_data/`-style drop-zone as tickers, plus a mapping table (ticker → NSE sector). Wire once, cost pays for itself only after `Pclose` alone has been outcome-tested.

**C · logistic re-fit:**
1. Sample-size ceiling. The plan asks for 500 matured non-overlapping setups; today the `outcomes.jsonl` label set is a fraction of that (system running ~a few months, 3–6mo horizon). A logistic fit at n<200 overfits.
2. **Selection-bias prerequisite.** Current `[O] Outcome` only labels tickers the pipeline selected. Fitting β from that set trains the model to reproduce the current filter, not to find winners. **Blocker: expand `stages/outcome.py` to write T+90 / T+180 labels for every `[HR]`-passer, not just the picks.** That change is small and independent — do it now so labels accumulate.
3. Theil–Sen runway needs `stages/exit_watch.py` to exist and be running daily — that stage is still in-progress per README roadmap.

### Signal to revisit

Revisit **A** when:
- [ ] Pillars 1–5 have ≥4 weeks of shadow traces AND
- [ ] `weekly-learn` shows `signed_volume_pressure_ewm10` or `dv_would_veto` has non-trivial correlation with T+90 outcomes (either direction — a negative correlation on veto candidates is exactly what the plan wants).

Revisit **B** when: A is landed OR when the LT-flow stage is next revised, whichever is sooner. Standalone value is smaller than A's.

Revisit **C** when: (i) `outcomes.jsonl` has ≥200 matured labels for `[HR]`-passers (not just picks — see prereq), (ii) `[EX]` exit-watch is live for ≥8 weeks, (iii) shadow-mode veto has been flipped to block for ≥60 trading sessions with net-positive outcome tape.

### Fix-points if they ever ship

- **A.** Loader in `backend/delivery.py`; new metric `delivery_signed_anomaly` in `indicators.py`; wire in `stages/volume.py`; drop-zone at `test_data/deliveries/`.
- **B.** Sector-index fetch in `backend/fetch.py::fetch_sector_index(symbol)`; mapping `config/sector_index_map.json`; new indicator `excess_move_pressure` in `indicators.py`.
- **C.** Logistic-fit branch in `scripts/tune_weights.py` gated behind `--fit-mode=logistic` flag; monotonicity constraint on `distribution_risk` (must be non-positive coefficient); Theil–Sen slope helper in `indicators.py::theilsen_slope`; runway consumer in `stages/exit_watch.py`.
- **Prereq for C — trace scope widening.** Modify `stages/outcome.py` so `outcomes.jsonl` labels every `[HR]`-passer, not just selected picks. New column `was_selected: bool` distinguishes trained-on picks from the counterfactual set. This is cheap and independent — the honest thing to do now regardless of whether C ever ships.

### Advanced participant-flow (paid data path) — parked separately

The plan mentions NSE's paid EOD order/trade dataset with Custodian / Proprietary / Client-Retail flags. That is materially stronger than any classifier we can build from block/bulk names. Not shipping because: (a) paid subscription, (b) firewall constraint on live fetch, (c) the classified block/bulk path in pillar 4 covers the same *intent* at a fraction of the cost. Revisit only if the classified path proves out AND the org is willing to buy the feed.

---

## Balanced-holding + honest-labels — deferred pillars

**Date parked:** 2026-07-17
**Status:** Correctness fix + advisory labels + schema separation shipped on 2026-07-17. Everything below needs medium/larger surgery.

### What already shipped (context)

- **URGENT — action-priority correction** in `positions_view._action_for`. Stop / T2 / T1 now precede distribution / DAY_180 / end_date. This was a real correctness bug: stop hits on day ≥ 180 were previously labeled `exit_final`, poisoning outcome labels.
- **Outcome label v2** — additive columns `mtm_return_pct`, `is_open`, `realized_return_pct`, `exit_reason_final`, `label_schema_version = 2`. Legacy `return_pct` preserved as MTM alias.
- **Split-date labels** on every pick — advisory metadata `date_labels.next_review / expected_breakout_window / hard_time_stop`.
- **9-state action ladder** — `backend/action_labels.py` maps raw actions to `MAINTAIN_HEALTHY | MAINTAIN_DRY_UP | MONITOR_EARLY_WEAKNESS | REVIEW_WEAKNESS_CONFIRMED | EXTEND_5D | TAKE_PROFIT_T1/T2 | EXIT_STOP | EXIT_DISTRIBUTION | DATA_UNAVAILABLE`. Populated on each position dict as `action_label`. Advisory-only; the raw `action` still drives every enforcement decision.

### The ideas (medium and larger, ranked by ROI)

**D — Two-session hysteresis with persisted warning count.** Add a `warning_count` and `last_warning_ts` column to `data/portfolio.csv`. When any soft indicator (OBV weak, MA slope down, up/down vol < 1.0) fires: increment. On a clean session: decrement. Distribution / MONITOR / REVIEW states only fire when count crosses a threshold. Prevents the one-bar bearish → immediate exit → next-day back-to-hold whiplash the user flagged. Anti-flip pattern: `warning_count >= 2` before REVIEW, `>= 3` before an EXIT-Confirmed.

**E — Latched EXIT-Confirmed state until user acknowledges.** Currently a confirmed distribution can flip back to Hold the next day if the signal is transient. Add a `confirmed_exit_at` column; once set, the action stays at `EXIT_DISTRIBUTION` until the user explicitly closes or acknowledges the position. Requires a `/api/positions/{id}/acknowledge` endpoint + a portfolio.py setter.

**F — Unified finalized-data source.** `positions_view` currently fetches Yahoo separately via `fetch_close(symbol)`. That path may see a partial-session bar different from the pipeline's finalized `[I] Ingest` data. Route both through the same in-memory OHLCV cache and reuse the ingest hygiene guards. Same fix as the `[I]` finalized-bar hygiene from 2026-07-17, applied to the monitor path.

**G — NSE trading-calendar arithmetic.** `_add_trading_days` / `_trading_days_between` currently approximate with weekdays. Wire the official NSE 2026 trading-holiday circular so `days_held` and `end_date` math are session-accurate. Small module: `backend/nse_calendar.py` loading a `config/nse_holidays_2026.json`. Refresh yearly.

**H — Contextual extension formula.** Today `revalidated_horizon_days` extends any healthy position by one bucket. Refined rule per plan: extend 5–10 sessions only when *(support intact AND no confirmed distribution AND (signed pressure stable/improving OR relative strength improving OR price progressing to resistance OR breakout holding))*. Pressure-signal integration requires the signed-volume-pressure primitives from the earlier refit — they exist in `indicators.py` but are not yet plumbed into the trajectory checker.

**I — 270-day bucket + protect-the-runner enforcement.** Add `270` to `HORIZON_BUCKETS`, replace `DAY_180` unconditional cap with `is_runner_healthy()` gate. A proven winner at day 180 (realized ≥ 1R, trajectory strong, no DV veto) extends to 270; anyone else exits at 180. Depends on D (persisted state) for reliable "trajectory strong" call.

**J — Trailing-stop at T2.** Instead of hard sell at fixed +16%, activate a trailing stop = `max(current_stop, close − 2×ATR20)`. Position exits only when the trail is hit — a stock going to +40% keeps running. Needs ATR-adaptive sizing (parked as fix #1 in an earlier ideas block) to be honest.

**K — Continuous lifecycle hit-detection.** Move `hit_t1` / `hit_t2` / `hit_stop` marking from "T+90 snapshot check" to "daily crossing detection" inside `[EX]` exit-watch. First-time-crossed wins; never un-marks. Fixes the false-negative on runners that hit T1 mid-window and pulled back.

**L — Multi-horizon outcome snapshots.** Extend `HORIZONS_DAYS = [90, 180]` to include each position's own end_date bucket (30/60/90/120/180/270). New outcome row per horizon per pick. Tuner gains `--horizon` flag; default stays 90 so today's behavior is preserved.

**M — Lifecycle-accurate `realized_return_pct` (v3 label schema).** Today v2's `realized_return_pct` uses the portfolio row's `exit_price` (snapshot honest). A v3 upgrade computes the actual ladder P&L: `0.5 × (T1_price/entry − 1) + 0.5 × (final_exit_price/entry − 1)` when T1 was hit lifecycle-wise. Requires K to fire honestly.

**N — Frontend action-enum sync.** `frontend/src/types.ts` must know about the 9-state ladder AND every raw `action` value that `_action_for` can return. Backend actions absent from the type union render as "Hold" today — silent contract drift. Small once D-M land, but must be re-done every time a new action is introduced.

**O — ATR-adaptive stop/T1/T2 (from earlier audit — restated here).** `position_sizer.size_position()` uses fixed `-8% / +8% / +16%`. Should be ATR-normalized: `stop = entry − max(2 × ATR20, 0.06 × entry)`, `t1 = entry + 1R`, `t2 = entry + 2R`. Biggest single label-quality lever. Needs a `POSITION_SIZER_MODE = "atr" | "fixed_pct"` config toggle + a `label_schema_version` bump so old and new labels don't get stitched into one training set.

**P — Learned survival / time-to-event model.** Once ≥ 500 matured setups exist, replace linear T+90 label with a proper survival model — outputs a **time range** for expected T1/T2 hit, not a fixed date. Handles the fundamental truth that stock-price-completion time is a random duration. Large scope; comes after everything above stabilizes.

### Signal to revisit

Order roughly matches the user's own change-size ranking (Small → Medium → Large):

- **D** (persisted warning count) — revisit after ≥ 4 weeks of the current action-priority fix has produced clean labels. The persistence layer is only useful if the base labels it feeds off are honest.
- **E** (latched EXIT-Confirmed) — bundle with D; same portfolio.csv touch.
- **F** (unified data source) — revisit when `positions_view` next needs a bug fix in its fetch path.
- **G** (NSE calendar) — revisit before end of 2026 (the current weekday-arithmetic approximation gets worse across long holds and holiday-heavy months).
- **H** (contextual extension) — after signed-pressure primitives (in `indicators.py` already) have shadow-trace evidence per the earlier ideas block.
- **I / J / K / L / M** — sequenced. K unlocks M; I depends on D; J depends on O.
- **N** (frontend enum) — every time D–M ships, N tags along.
- **O** (ATR sizing) — revisit as a standalone project; biggest single improvement but breaks label continuity — schedule a label_schema_version bump.
- **P** (survival model) — after ≥ 500 matured setups exist AND the current MTM-based ratchet has proven itself.

### Fix-points if they ever ship

- **D.** `data/portfolio.csv` new columns `warning_count`, `last_warning_ts`; new helper `positions_view._register_warning(row, kind)`; `_action_for` gains a `warning_count` param.
- **E.** `data/portfolio.csv` new column `confirmed_exit_at`; new endpoint `POST /api/positions/{pick_id}/acknowledge` in `middleware/main.py`; setter in `backend/portfolio.py`.
- **F.** Refactor `fetch_close` sites to route through a shared cache — same helper the pipeline uses for `[I] Ingest`.
- **G.** New module `backend/nse_calendar.py`; new config `config/nse_holidays_2026.json`; replace weekday math in `positions_view._add_trading_days` / `_trading_days_between`.
- **H.** New function `backend/signal_trajectory.py::is_runner_healthy(...)` that reads signed-pressure EWM + relative strength + resistance-progress signals; `_action_for` calls it to decide extension length.
- **I.** `backend/horizon.py` add 270 to `HORIZON_BUCKETS`; `positions_view._action_for` `DAY_180` guard becomes conditional on `is_runner_healthy()`.
- **J.** New advisory field `exit_schedule.trailing_stop_at_t2`; enforcement in `_action_for` uses `max(current_stop, close - 2 × ATR)`.
- **K.** Extend `stages/exit_watch.py` with daily hit-detection loop; writes `hit_t1_date` etc. back to `data/portfolio.csv`.
- **L.** `stages/outcome.py:HORIZONS_DAYS` gains per-position horizons; `scripts/tune_weights.py` gains `--horizon N` flag.
- **M.** `stages/outcome.py:LABEL_SCHEMA_VERSION → 3`; realized calc uses hit_t1_date + hit_t2_date from portfolio row.
- **N.** `frontend/src/types.ts` — one union type across the 9 ladder labels and every raw `action`. TypeScript exhaustive-check via `never` on any unknown branch.
- **O.** `backend/position_sizer.py` new `POSITION_SIZER_MODE` toggle; ATR passed through from `[I] Ingest`.
- **P.** New module `backend/survival.py`; scikit-survival dependency; trained on outcomes.jsonl v3 with time-to-event columns; endpoint that returns `(t1_low, t1_median, t1_high)` per pick.

---

## Multi-window volume accumulation-strength label

**Date parked:** 2026-07-18
**Status:** Deferred. User explicitly likes current picks; wants a *purely additive* strength annotation, not a pick-logic change. v1 design agreed conceptually; implementation deferred until we're ready to attach + trace it without disturbing the recent anti-whipsaw / swing-framing refit.

### The narrow idea (v1, additive-only)

Attach a strength label + continuous score to every current pick and open position. Does **not** touch selection, sizing, or exits.

**Metric — signed volume pressure at three aligned windows (20 / 60 / 120 days):**

```
signed_pressure_W = mean( volume × sign(close − close_prev),  over W days )
                  ─────────────────────────────────────────────────────────
                              mean( volume, over W days )
```

Three readings: `short_pressure` (W=20), `swing_pressure` (W=60), `regime_pressure` (W=120). Sign correction (volume × daily direction) prevents rising-volume-on-falling-price being misread as accumulation — the classic volume-only failure mode.

**Aggregate — equal weights, deliberately not tuned:**

```
accumulation_strength = mean(short_pressure, swing_pressure, regime_pressure)   # -1..+1
```

**Label bands (v1 cutoffs, guessed — MUST be re-fit from outcomes before hardening):**

```
> +0.35        STRONG_ACCUMULATION
+0.20 to +0.35 BUILDING_ACCUMULATION
+0.05 to +0.20 EARLY_ACCUMULATION
−0.05 to +0.05 NEUTRAL_VOLUME
−0.20 to −0.05 SOFT_DISTRIBUTION
< −0.20        DISTRIBUTION_WARNING
```

**Attachment — additive JSON field on pick + portfolio row:**

```json
"accumulation": {
  "strength": 0.31,
  "label": "BUILDING_ACCUMULATION",
  "windows": { "short_20": 0.18, "swing_60": 0.42, "regime_120": 0.33 }
}
```

Current picks untouched. Reader gains a transparency signal — "which regime is the picker leaning on right now."

### Wider ideas deferred with this

Designed conceptually in the same conversation but deliberately parked until v1's continuous scores have accumulated in traces long enough to validate. Ordered by increasing scope:

**(a) Parallel multi-formula ensemble.** Many independent formulae running in parallel: OBV slopes at 30/90d, CMF 21/60d, ADL slope, up/down-volume ratios at 30/90d, volume dry-up ADV5/ADV50, breakout volume today/ADV50, pocket pivot, MFI trend, price-volume divergence, block/bulk net-buy when data available. Each emits standardized JSON with continuous `score`, `direction`, and per-signal `label` (`ACCUMULATION_EVIDENCE`, `BUYING_PRESSURE`, `SUPPLY_DRYING`, `DEMAND_CONFIRMATION`).

**(b) Signal-label vs trade-label separation.** No single formula creates a `BUY_ALERT`. Formulae emit **signal labels** (evidence type). A composite emits **trade labels** (`BUY_ALERT / WATCHLIST_READY / EARLY_SIGNAL / NEAR_MISS / REJECTED / ALREADY_OPEN`). Signal labels never leak into action decisions.

**(c) Composite ensemble structure (weights unspecified in code):**

```
long_term_score    = weighted sum of long-horizon formulae (OBV 90d, up/down vol 90d, CMF 60d, ADL, block-deal)
setup_score        = weighted sum of setup formulae (dry-up, OBV divergence, CMF 21d, tight-base)
trigger_score      = weighted sum of trigger formulae (breakout volume, upper-third close, pocket pivot, resistance break)
accumulation_score = weighted sum of (long_term_score, setup_score, trigger_score)
```

**Weights ship equal / z-score-sum for v1. NEVER hand-edited.** v2 weights come from a fitted regularized logistic against `outcomes.jsonl` and are promoted with a `label_schema_version` bump.

**(d) Horizon-tagged trade labels:**

```
SCALP    1-3 days       V1/V20 spike + ATR expansion         ATR trail / day 3
BURST    1-10 days      V5/V20 > 1.5, close > SMA20          SMA20 break OR day 10
ACCUM    2-6 weeks      V20/V60 > 1.2 sustained, z-60 < 80   SMA60 break AND V20/V60 rolls
REGIME   2-6 months     V50/V100 > 1.15 for 3+ wks           SMA120 break AND V50/V100 rolls
```

**(e) Grade A/B/C by window agreement:**

```
Grade A: 3-of-3 aligned → auto-enter, full size
Grade B: 2-of-3 aligned → auto-enter, half size
Grade C: 1-of-3 aligned → watchlist only, shown but not sized
```

Grading controls sizing/visibility, **never filters signals to zero** — a slow day shows mostly Grade C so the app never goes blank.

**(f) Per-ticker state machine, no overlapping holds:**

```
[flat] ─BURST─► [BURST held] ─promote─► [ACCUM held] ─promote─► [REGIME held]
```

- One active position per ticker. BURST signal on an ACCUM-held ticker is confirmation, not a new fill.
- **Promotion = upgrade in place.** Same entry date, new horizon label, new exit rule. No second position, no overlapping dates.
- **Demotion = relax, don't exit.** REGIME losing 120d but keeping 60d drops to ACCUM. Full exit only when all applicable windows fail.
- **Cooldown (~5-10 sessions)** after full exit to kill re-fire whipsaws.

**(g) Store-everything learning surface.** Every formula computes a continuous score on **every stock every day**, including for `REJECTED` candidates. Batch/candidate table:

```
batch_id, candidate_id, symbol, selected, rank, decision_label, accumulation_label,
accumulation_score, per_formula_outputs, failed_gate
```

Enables `weekly-learn` to answer "this rejected stock ran 20% — which formula saw it, which gate blocked it." Rejects label the app; enriches learning.

**(h) Fitted-weight cycle (v1 → v2).** After 4-8 weeks of accumulated traces, `weekly-learn` fits weights via regularized logistic against realized outcomes. Weights versioned (`accumulation_weights_v2.json`), never hand-edited. v1 (equal weights) stays for A/B comparison for one more cycle before retiring.

### Why parked

1. **User explicitly said current picks are good; only wants a strength annotation.** Anything beyond v1 is a redesign the user did not ask for.
2. **v1 has to earn its keep first.** The wider ensemble only pays off if the boring baseline shows the *shape* of the signal is useful. If signed-pressure-at-three-windows correlates with nothing in the traces, adding 10 more formulae won't fix it.
3. **Fitted weights need trace history.** Zero traces of the new metric exist today. Even v1 needs 4-8 weeks of continuous logging before weights (or bands) can be honestly re-fit. Anything beyond v1 requires *more* trace history, sequentially.
4. **Recent anti-whipsaw / swing-framing work is still settling.** Layering a new labeling system on top risks obscuring which recent fix moved the needle. Let the current refit prove itself first (per the guiding principle at the top of this file).
5. **Threshold-overfitting risk grows with formula count.** Six formulae × per-formula thresholds = dozens of tuning knobs. v1 has three windows, one metric, one aggregate — the smallest defensible surface.

### Signal to revisit — v1

Ship v1 (the narrow signed-pressure label) when *all* of the following are true:

- [ ] There is a concrete moment the user wants transparency on pick strength (portfolio review, weekly digest) and current output doesn't provide it.
- [ ] Trace-writing plumbing can capture `accumulation.strength` + per-window readings on every pick + every portfolio row *without* touching selection logic.
- [ ] Bands ship as **loggable, not hard-gated** — every pick carries the continuous `strength` value in traces so future band re-fits are honest.

### Signal to revisit — extensions (a)-(h)

Revisit *any* extension only after:
- [ ] v1 has ≥ 6-8 weeks of trace history AND
- [ ] `weekly-learn` shows the v1 `strength` value has non-trivial correlation with realized T+30 / T+90 outcomes on picks. If v1 shows no signal, the fuller ensemble is a bet against evidence — kill or redesign, don't expand.

Per-extension gates:
- **(a) parallel formulae** — after v1 shows signal AND user needs finer-grained accumulation *types* the single metric can't distinguish (quiet vs breakout).
- **(f) state machine** — after v1's readings on portfolio rows show measurable churn cost from re-fires / overlaps.
- **(g) rejected-candidate storage** — earlier is better; independent of the rest. Cheap unlock for (h).
- **(h) fitted weights** — never before ≥ 200 matured setups per horizon in `outcomes.jsonl` (same prerequisite as pillar C in the precision-first block above; same selection-bias trap — must include non-selected candidates).

### Fix-points if v1 ever ships

- **Metric.** New indicator `signed_volume_pressure_windowed(prices, volumes, window)` in `backend/indicators.py`. Pure function, no I/O.
- **Aggregator.** New helper `accumulation_strength(short, swing, regime)` in `backend/indicators.py` returning `{ strength, label, windows }`.
- **Attachment on picks.** `backend/pipeline.py` (or the pick-emitter) appends the `accumulation` dict to each pick row *after* selection. Does NOT gate selection.
- **Attachment on portfolio.** `backend/positions_view.py` recomputes strength daily from the same OHLCV cache used by `[I] Ingest`; writes to the portfolio row's advisory column. **Never** used in `_action_for` decisioning in v1.
- **Trace key.** New column `accumulation_strength_v1` in `data/traces/outcomes.jsonl` and per-ticker traces. Continuous value only — do NOT store the label alone (bands change; raw score is the ground truth).
- **Config.** `config/accumulation_windows.json` = `{"short": 20, "swing": 60, "regime": 120}`. `config/accumulation_bands_v1.json` = the guessed cutoffs above. Both loaded once at startup; band file is versioned so v2 (fitted) can supersede without deleting v1.
- **Frontend.** `frontend/src/types.ts` gains an optional `accumulation` field on pick/position types. UI shows label + strength pill under existing pick card. Optional at first — no breakage if absent.
- **Reject-set logging (extension (g), independent of the rest).** `pipeline.py` writes `data/rejected_candidates.jsonl` with each non-selected ticker's `accumulation.strength`. Cheap, unlocks (h) later.

### Fix-points if extensions (a)-(h) ever ship

- **(a) parallel formulae.** New module `backend/accumulation_formulae.py`; one function per formula, standardized JSON schema. Registry pattern so `weekly-learn` can enumerate them.
- **(b) signal-vs-trade separation.** New enum `SignalLabel` in `backend/labels.py` distinct from existing `TradeLabel`. Composite lives in `backend/decide.py::decide_trade_label(signals) -> TradeLabel`. Formulae never import `TradeLabel`.
- **(c) composite structure.** `backend/accumulation_composite.py` — three sub-scores + top-level combine. Weights loaded from `config/accumulation_weights_v{n}.json`. v1 = equal.
- **(d) horizon-tagged labels.** Extend `backend/action_labels.py` (or new `backend/horizon_labels.py`) with the four-tier taxonomy + exit rules.
- **(e) grade A/B/C.** New function `grade_pick(pick) -> "A"|"B"|"C"` in `backend/decide.py`. Sizing hook in `backend/position_sizer.py` scales full/half/none.
- **(f) state machine.** New module `backend/ticker_state.py` with `advance(current_state, new_signals, cooldown_days) -> next_state`. Persisted per ticker in `data/ticker_states.csv`.
- **(g) rejected-candidate storage.** Already listed under v1 fix-points as an independent cheap unlock.
- **(h) fitted weights.** New branch in `scripts/tune_weights.py` (`--fit=accumulation`) — regularized logistic on stored per-formula outputs vs realized outcomes. Emits `config/accumulation_weights_v{n+1}.json`. Champion-challenger ratchet against v_n. Same selection-bias prerequisite as pillar C above: must include `[HR]`-passers, not just picks.

---

## Weekend / holiday no-fire behavior — SHIPPED 2026-07-19

**Date parked:** 2026-07-18
**Date shipped:** 2026-07-19 (see `CHANGELOG.md → 2026-07-19`)
**Status:** SHIPPED. Guard implemented independently of pillar G (NSE calendar); "100% ingest fail = holiday" heuristic covers the ask without needing a static calendar file. NSE calendar (pillar G) remains parked for the *outcome-scoring / calendar-arithmetic* side of things — the daily-run guard no longer depends on it.

**What shipped (summary — full narrative in CHANGELOG):**

- New module `backend/trading_day.py` — pure helpers: `classify_pre_pipeline` (weekend), `classify_post_ingest` (100% ingest fail = holiday), `latest_picks_file_on_or_before`, `load_previous_picks`, `log_no_fire`.
- `backend/orchestrator.py::run_universe` — weekend guard before Phase 0; holiday guard after ingest counts. Both leave the trading-day happy path byte-identical.
- `middleware/picks.py` — `generate_picks` guards the write with `response["date"] == today`; `get_or_generate_picks` short-circuits weekends without invoking the pipeline.
- `middleware/main.py::_todays_pick_for` — falls through to previous active day so the stock-detail "Pick Today" pill stays consistent with `/api/picks`.
- New trace file `data/traces/no_fire_days.jsonl` — one row per skip, `reason ∈ {weekend, holiday_no_data, data_missing_error}`.

**Everything below is the original 2026-07-18 parked design, preserved for audit.**

---

## Weekend / holiday no-fire behavior — original parked design (2026-07-18)

**Date parked:** 2026-07-18
**Status:** Deferred behavior change to daily pipeline. Small scope; wants to piggyback on the NSE-calendar work already parked as pillar G above.

### The idea

On non-trading days (Saturday, Sunday, and NSE holidays), the pipeline should:

1. **Not create a new picks file** (or portfolio snapshot) for that date.
2. **Show the previous active trading day's output** in whatever UI / weekly digest surfaces the picks.
3. **Not run outcome scoring** on non-trading days — no fresh bar means no honest evaluation.

Applies uniformly to the daily / weekly / monthly schedulers. Same guard also covers the "data unavailable" case — if a nominally-trading day returns no OHLCV, fall through to previous active day but flag the anomaly (do NOT silently mark it as a holiday).

### Why parked

1. **Depends on NSE trading-calendar (pillar G above).** Without the calendar, "holiday" detection falls back on weekday-only rules — misses NSE-only holidays like Diwali, Republic Day, Mahavir Jayanti, etc. Any solution shipped before G will need re-wiring when G lands.
2. **Weekend guard is trivial and could ship early** — but honesty says do both together so the calendar wiring is done once, not twice.
3. **"Data missing" case needs care.** Silent fallback would hide real fetch failures. Guard must distinguish `no_data_intentional` (holiday) from `no_data_error` (fetch failed / source outage) in traces.

### Signal to revisit

Revisit when pillar **G (NSE trading-calendar arithmetic)** is ready to land. Bundle this behavior into the same PR — one calendar wiring, two features earned.

If pillar G is delayed further (e.g., past end of 2026), consider shipping the weekend-only guard standalone as a short-term fix, with an explicit TODO to widen to holidays once G lands.

### Fix-points if it ever ships

- **Guard.** New helper `backend/nse_calendar.py::is_trading_day(date) -> bool` returning `False` for Sat/Sun/holidays. Same module as pillar G.
- **Pipeline gate.** `backend/pipeline.py` early-returns on non-trading days after logging `no_fire: non-trading day <date>`. No new pick file written. No new portfolio snapshot.
- **Digest fallback.** Wherever the UI / weekly digest reads latest picks, wrap in `latest_picks_on_or_before(today)` — reads previous trading day's file if today's absent. `backend/portfolio.py` and `middleware/main.py` are the two read sites.
- **Outcome scoring guard.** `stages/outcome.py` also gates on `is_trading_day` — no forward-return update on non-trading days.
- **Trace hygiene.** Non-trading-day skips log to `data/traces/no_fire_days.jsonl` with reason `holiday | weekend | data_missing_error`. Distinguishes intentional skips from bugs so `weekly-learn` doesn't count them against pick precision.
- **Data-missing anomaly.** If a scheduled trading day returns no OHLCV, write `data_missing_error` to `no_fire_days.jsonl` AND surface the previous active day to the UI, but also emit a monitoring alert so a real fetch outage isn't hidden behind the fallback.
- **Frontend badge.** UI shows a small "showing picks from <prev trading day>" pill on non-trading-day views so the user knows the data isn't stale by accident.

---

## Principle-alignment enforcement gaps — 2026-07-22 audit

**Date parked:** 2026-07-22
**Status:** Deferred. Surfaced by an audit against the two founding principles ("buy when institutions are *quietly entering*, not when momentum has run and they're distributing to retail"; "monitor positions daily, hold the price/time targets, and trace any alteration"). The *scoring and labeling* already encode both principles. These three items are the places where **enforcement** is softer than the stated intent. All three alter live behavior, so none was shipped — they wait for the trace-evidence gates below.

### Context — what the audit confirmed is already aligned (no change needed)

- Early-accumulation bias is real in the math: `[ACS]`/`[AC]` require tight range + dry volume + **rising ADI while price is flat** (quiet absorption, not a momentum spike); `[LT]` demands 90d-OBV↑ + 90d up/down ≥ 1.1 + 150d-MA↑ (months of footprint); `volume_signals._classify_entry_timing` rewards `early` (+0.25), penalizes `late` (−0.10), hard-rejects Stage-4/distribution ("missed", −0.20); parabolic penalty −0.30 for >25%/30d; `entry_stage_label.LATE_CHASE` flags a chase on the card.
- Principle 2 is fully satisfied: T1/T2 **price** targets are never mutated (verified — no write path in `positions_view`), stop moves **up-only**, the **time** horizon extends only on a healthy trajectory, and `backend/position_trace.py` writes a dated JSONL row per position per day with an `alterations[] = {target, kind, from, to, reason}` record. The trace records recommendations; committing stays `portfolio.update_open_picks`' job.

### Gap 1 — Distribution veto is in `shadow`, not `block`

**The idea.** Flip `config/stage_weights.json → "distribution_veto_mode"` from `"shadow"` to `"block"`. In `block` mode `[DV]` (`backend/stages/distribution_veto.py`) short-circuits any ticker whose recent tape shows a distribution footprint (weak-close volume spike, gap-up-sold-into bull trap, or ≥3 distribution-day cluster in 15 sessions). This is the single most direct "don't buy while institutions are distributing to retail" guard, and today it only *observes* (`would_veto` written to trace; `passed` always True).

**Why parked:**
1. **Explicitly gated on shadow validation.** The config's own `_distribution_veto_mode_help` says flip "only after ≥4 weeks of shadow traces confirm veto precision against outcomes." Flipping on faith could reject good setups on a footprint that doesn't actually predict failure.
2. **It removes picks.** Even though a veto is philosophically pure (it only cuts traps, never changes what *qualifies*), it still changes the live pick set — so it wants outcome evidence first, per the guiding principle at the top of this file.
3. **Cross-linked prerequisite already exists.** The "Precision-first refit" block above (pillar C, revisit condition iii) already treats "shadow-mode veto flipped to block for ≥60 trading sessions" as a downstream dependency — arming DV is the unlock for that, not an isolated tweak.

**Signal to revisit:** flip to `block` when **all** hold:
- [ ] ≥4 weeks (≥20 trading sessions) of shadow traces exist with `dv.would_veto` populated.
- [ ] `weekly-learn` shows veto candidates (`would_veto = true`) have **materially worse** T+90/T+180 outcomes than non-veto passers — i.e. the footprint has precision, not just recall.
- [ ] The three sub-rules are validated **individually** (a cluster veto may earn its keep while a gap-up veto over-fires, or vice-versa) before the combined gate goes hard.

**Fix-points if it ships:** one-line config change `config/stage_weights.json:9` → `"block"` (the loader in `pipeline._load_weight_config` auto-adds `"DV"` to `HARD_GATE_IDS`). No stage-code change. Consider a per-rule enable flag (`dv_rules_enabled: ["dist_day_cluster"]`) in the config if the sub-rules validate unevenly — small addition in `distribution_veto._load_mode` / `run`.

### Gap 2 — Long-term-flow `[LT]` is a soft gate, not a hard gate

**The idea.** Add `"LT"` to `config/stage_weights.json → "hard_gate_stage_ids"` so a stock **cannot** be selected without months of institutional footprint. Today only `U/I/HR` are hard (`pipeline.py:_DEFAULT_HARD_GATES`); `[LT]` is a soft gate at weight 0.15, so a ticker that **fails** the 90d-OBV / up-down / 150d-MA checks still flows down the chain and can be admitted on `[AC] + [BR]` strength alone (LT just contributes 0 to the composite). That admits the exact case the first principle warns against: a fresh breakout with a thin accumulation base. `[BR]` carries weight 0.20 (joint-largest), so a momentum trigger is heavily weighted.

**Why parked:**
1. **Biggest blast radius of the three.** Making LT mandatory is a genuine pick-logic tightening — it will *reduce* pick count, possibly to zero on quiet days, which collides with the "app never goes blank" preference elsewhere in this file.
2. **Partly mitigated already.** `_reweight_for_trigger` halves `[VD]` and redistributes to `LT/AC` on `pre_breakout` setups, and the `TRIGGER_AC_MIN_SCORE = 0.6` floor (post-Bajaj-Auto) already blocks the weakest coiled entries. The residual risk is narrower than it first looks: a *pure* `sos_breakout` (BR pass) with failing LT.
3. **Needs the counterfactual.** Whether LT-fail picks actually underperform is measurable but unmeasured. Fitting this from selected-only outcomes is the selection-bias trap noted in pillar C — needs `[HR]`-passer labels, not just picks.

**Signal to revisit:** promote LT to hard only when:
- [ ] `weekly-learn` (or a backtest via `scripts/tune_weights.py`) shows picks that **passed BR but failed LT** have distinctly worse T+90/T+180 returns than picks that passed both.
- [ ] The pick-count impact is quantified on cached bars (how many days would go to zero picks?) and judged acceptable, OR paired with a "show best-available with a soft-LT warning label" fallback so the app doesn't blank out.
- [ ] Prefer the **intermediate step first**: keep LT soft but *raise its weight* and/or add an LT-fail advisory label on the card (additive, no pick removed) before making it a hard gate.

**Fix-points if it ships:** hard-gate path — add `"LT"` to `hard_gate_stage_ids` in `config/stage_weights.json:7`. Intermediate/additive path — bump `scored_stage_weights.LT`, and/or emit an `lt_flow_failed` advisory flag consumed by the pick renderer (`stages/render.py`) and `frontend/src/types.ts`. Threshold constants stay in `backend/stages/lt_flow.py` (`OBV_90D_SLOPE_MIN`, `UPDOWN_90D_MIN`, `MA150_SLOPE_MIN`) — tuner-owned, never hand-edited.

### Gap 3 — Time target moves (horizon extend + end-date re-anchor); confirm this is the desired reading of "don't alter the target time"

**The idea.** The founding instruction says "don't alter the target **time and price** — or if you do, show the trace." The app never alters **price** (T1/T2), but it **does** alter **time**: `horizon.revalidated_horizon_days` extends a healthy position to the next bucket (30→60→90→120→180), and `positions_view` re-anchors the effective `end_date` to the user's actual fill date. Both are now traced (`position_trace._alterations_for` → `horizon_extend` / `horizon_reanchor`). This item is a **decision to confirm**, not a bug: either (a) keep the current *extend-only-with-trace* behavior (recommended — it satisfies "you can alter but show the trace" and only ever lengthens runway on healthy volume, never shortens the profit target), or (b) add a config toggle to **freeze** the time horizon entirely.

**Why parked:** the current behavior already honors the "show the trace" clause, and freezing the horizon would remove the "let a healthy runner keep its months of runway" benefit that the swing-hold thesis (3 weeks–3 months typical, day-180 hard cap) depends on. No change unless the user explicitly wants time frozen.

**Signal to revisit:** only if the user, on seeing the daily traces, decides horizon drift is undesirable — then ship the freeze toggle. Otherwise leave as-is; the trace is the deliverable.

**Fix-points if a freeze toggle ships:** `config` flag `horizon_extension_enabled: false`; short-circuit in `backend/horizon.py::revalidated_horizon_days` (return `(None, "extension_disabled")`, which makes `_action_for` fall to `exit_end_date` at the stored end date); the end-date re-anchor in `positions_view.list_active_positions` (the `effective_end_d = entry_d + horizon_days` block) would also need a flag to pin to `stored_end_date`. Keep the trace either way so the constancy is auditable.

### Cross-links

- Gap 1 is the unlock for **Precision-first refit → pillar C** (revisit condition iii) and feeds its `dv_would_veto` outcome regression.
- Gap 2 shares the **selection-bias / `[HR]`-passer labeling** prerequisite with pillar C — do that trace-scope widening first (it's cheap and independent).
- Gap 3 relates to **Balanced-holding pillar H** (contextual extension formula) — if H ships, the extend decision becomes signal-driven rather than always-one-bucket, which is the more principled version of "alter time only with evidence."
