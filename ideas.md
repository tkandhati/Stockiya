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
