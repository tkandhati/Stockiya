# Changelog

## 2026-07-21 — Entry-stage label ladder (pre/at/post-breakout)

Adds an advisory ladder that answers a question the UI could not answer
before: **"is this pick pre-breakout, at the pivot, freshly confirmed, or
already extended?"** Motivated by a user comparison of ABB (recommended
2026-07-10, +9.96% by 2026-07-20) vs COLPAL (fresh candidate 2026-07-20).
Both had near-identical 5-week base tightness (9.79% vs 9.13%) but sat on
opposite sides of the breakout cycle. Chart shape was the same; the
timing was opposite. There was no field on the pick payload that made
that distinction machine-readable.

Follows the "additive labels over pick redesigns" preference: selection,
ranking, sizing, and exit rules are byte-identical. Only the payload
grows.

**1. New module — `backend/entry_stage_label.py`**

Pure function `entry_stage_label(**features) -> str`. Zero I/O.
Deterministic. Mirrors the shape of `backend/action_labels.py` but
classifies the *entry moment* rather than the *holding lifecycle*.

Ladder (11 states):

```
DEEP_BASE                  ≥8% under 20d high
BUILDING_BASE              -8..-2% under, tightness 10-12%
COILED_PRE_BREAKOUT        -8..-2% under, tightness <10%
AT_PIVOT                   |break_pct| ≤ 1.5%, vol10/vol50 ≥ 1.0x
AT_PIVOT_NO_DEMAND         |break_pct| ≤ 1.5%, vol10/vol50 < 1.0x
BREAKOUT_CONFIRMED_TODAY   BR gate passed on this bar
POST_BREAKOUT_HEALTHY      0..+5% above SMA20 (or +0..+5% since entry)
POST_BREAKOUT_EXTENDED     +5..+10% above SMA20
LATE_CHASE                 >+10% above SMA20 within 10 bars of trigger
FAILED_BREAKOUT_RETEST     -3% or worse within 10 bars of trigger
DATA_UNAVAILABLE           essential features missing
```

All threshold constants live at the top of the file — the ONLY place
band edges live. Any tuner ratchet works on the constants; callers
never hard-code an edge.

**2. Wired in `backend/stages/hypothesis.py::build_pick_payload`**

Fresh picks now carry two additional keys:

- `entry_stage` — one of the 11 ladder labels.
- `entry_stage_features` — audit dict:
  `{break_pct, close_vs_sma20_pct, vol_ratio_10d_50d,
    tightness_25bar_pct, br_passed_today}`.

Inputs are computed from the pick's own OHLCV (SMA20, 25-bar high/low,
10-bar & 50-bar volume mean) plus BR features (`break_pct` and
`br.passed`). No new fetch; all inputs already in memory.

**3. Wired in `backend/positions_view.py::list_active_positions`**

Held positions now carry `entry_stage` alongside `action_label`.
Inputs are `days_since_scanner_entry` + `pnl_pct`; the classifier
degrades gracefully when SMA20 is unavailable and falls back to the
gain-since-entry proxy for POST_BREAKOUT_* / LATE_CHASE /
FAILED_BREAKOUT_RETEST classification.

**4. Schema bumped — v7 → v8 in `backend/stages/render.py`**

Catchup will now regenerate any picks_<date>.json written before the
label was added. Field is additive; older UI code that doesn't know
about `entry_stage` simply ignores it (tolerant reader convention).

**5. Sanity check on the motivating cases**

| Snapshot | Label produced |
|---|---|
| ABB @ 2026-07-10 (pre-pick, break_pct=-6.41%, vol10/vol50=0.78×) | `COILED_PRE_BREAKOUT` |
| ABB @ 2026-07-20 (held, +9.96% in 8 sessions, close/SMA20=+6.35%) | `POST_BREAKOUT_EXTENDED` |
| COLPAL @ 2026-07-20 (candidate, break_pct=-1.10%, vol10/vol50=0.65×) | `AT_PIVOT_NO_DEMAND` |

Matches the eyeball comparison exactly. The ladder codifies what a
human previously had to read off a chart.

**6. Explicitly out of scope**

- **No changes to selection / sizing / exits.** Composite/BR still owns
  picking; `action_label` still owns holding lifecycle.
- **No frontend surfacing yet.** The field is present on the API
  payload; a UI pill next to the pick headline is a follow-up.
- **No back-population of historical outcomes.** Old traces do not get
  labeled retroactively; only forward picks and today's held positions
  carry the field.

## 2026-07-19 — Weekend / holiday no-fire guard

Ships the behavior parked yesterday in `ideas.md → Weekend / holiday
no-fire behavior`. On non-trading days the pipeline no longer writes
`data/picks_<date>.json` or touches the portfolio ledger; the middleware
serves the previous active trading day's picks instead. Motivated by
the user ask: *"on saturday and sunday it should not create a file and
picks also holidays (when no data found). should show previous active
day."*

**1. New module — `backend/trading_day.py`**

Pure helpers, no I/O beyond filesystem enumeration and the append-only
no-fire log. Safe to import from stages / middleware / scripts.

- `classify_pre_pipeline(date_iso) → TradingDayVerdict` — weekend
  detection (Sat / Sun via `weekday() >= 5`).
- `classify_post_ingest(date_iso, ingest_total, ingest_failed) →
  TradingDayVerdict` — holiday detection from 100% ingest failure
  (no fresh OHLCV for anyone → likely a market holiday / bhavcopy
  not yet published).
- `latest_picks_file_on_or_before(target_date) → Optional[Path]` —
  walks `data/` for the newest `picks_YYYY-MM-DD.json` at or before
  target. Filesystem-only; does not read file contents.
- `load_previous_picks(target_date) → Optional[dict]` — reads that
  file and augments the `message` field with "Showing picks from
  <source_date> — <today> is a non-trading day". On-disk file is
  never modified.
- `log_no_fire(verdict, extra)` — appends one row to
  `data/traces/no_fire_days.jsonl` with `reason ∈ {weekend,
  holiday_no_data, data_missing_error}` so `weekly-learn` can tell
  intentional skips from bugs, and pick-precision stats don't count
  skipped days as misses.

**2. Wired in `backend/orchestrator.py::run_universe`**

Two guards, both leaving the trading-day happy path byte-identical:

- **Entry (weekend):** before Phase 0 regime check. If Sat / Sun →
  log `no_fire_days.jsonl` row with `reason=weekend`, return
  previous picks file unchanged (or an empty response if no prior
  file exists). No pipeline run, no `picks_<today>.json` write, no
  portfolio update, no daily-diagnostic snapshot.
- **Post-ingest (holiday):** immediately after ingest-failure counts
  are computed. If `ingest_failed == ingest_total` (100% failure) →
  log row with `reason=holiday_no_data`, return previous file
  unchanged. The pre-existing **90-99% "data misconfigured"** branch
  is untouched and still writes a diagnostic empty-picks file with
  fix instructions — that path is for env-config errors, not
  holidays, and the operator needs the diagnostic to see the fix.

**3. Wired in `middleware/picks.py`**

- `generate_picks` only calls `write_picks(today, response)` when
  `response["date"] == today`. When orchestrator returns a previous
  day's picks (weekend / holiday), we no longer overwrite today's
  filename slot with stale-date content — the archive stays honest.
- `get_or_generate_picks` short-circuits weekends without invoking
  the pipeline at all: reads the previous file directly. Avoids
  spinning up the fetch layer on Sundays.

**4. Wired in `middleware/main.py::_todays_pick_for`**

Falls through to the previous active trading day when today's picks
file doesn't exist, so the stock-detail page's "Pick Today" pill
stays consistent with what `/api/picks` served.

**5. Behavior matrix**

| Day type | picks_<today>.json | UI shows |
|---|---|---|
| Trading day + fresh data | Written | Today's picks (unchanged behavior) |
| Sat / Sun | **Not written** | Previous file + "Showing picks from <date> — <today> is a non-trading day" |
| Holiday (100% ingest fail) | **Not written** | Same as above |
| 90-99% ingest fail (misconfig) | Written (diagnostic) | Existing misconfig fix-instruction message (unchanged) |

**Not touched (out of scope, deferred)**

- Full NSE holiday calendar (parked as pillar G in `ideas.md`
  → Balanced-holding block). The "100% ingest fail = holiday"
  heuristic honestly covers the user's ask without needing a
  calendar file — the fact that bhavcopy didn't publish IS the
  holiday signal, at zero calendar-maintenance cost.
- `backend/weekly.py` (Friday close snapshot) — Fridays are
  trading days by construction; NSE-Friday holidays are rare.
  Bundled with pillar G when the calendar lands.
- Outcome scoring, sliding-window learn — run on T+90 / T+180
  lag, unaffected by the daily-run gate.

**Verified**

AST-parse on all four modified files (`trading_day.py`, `orchestrator.py`,
`picks.py`, `main.py`). Unit-level checks on the guard helpers:
weekend → `is_trading_day=False, reason=weekend`; weekday with 100%
ingest fail → `holiday_no_data`; weekday with 90% ingest fail →
`is_trading_day=True` (misconfig, not holiday). `latest_picks_file_on_or_before`
against real `data/` correctly returns the newest matching file and
`None` when nothing predates the target.

**Fix-points**

- Weekend definition: `weekday() >= 5` in
  `backend/trading_day.py::classify_pre_pipeline` (Sat=5, Sun=6).
- Holiday threshold: `ingest_failed == ingest_total` in
  `classify_post_ingest`. Deliberately distinct from the pre-existing
  90% misconfigured threshold in `orchestrator.py`.
- No-fire trace: `data/traces/no_fire_days.jsonl` (append-only JSONL).
- Guard order in `run_universe`: weekend check → Phase 0 (regime)
  → Phase 1 (ingest) → holiday check. Rest of the chain is
  unchanged.

---

## 2026-07-18 — Honesty refit: anti-whipsaw + swing-framing language

Three code fixes + a documentation refit. Motivated by the DIVISLAB.NS
day-1 flip case: pick admitted 2026-07-16, flipped to `exit_distribution`
on 2026-07-17 on a 1.19x ADV pullback and a close 1.1% below the 20d
high. That is a whipsaw, not a real distribution signal — and the whole
class of that whipsaw was possible because the exit-side thresholds were
easier to trip than the admission-side thresholds. This refit closes
that gap and renames the "3-6 month hold" language across the codebase
to match what the numbers actually describe (swing, not multi-month
position).

**1. Failed-breakout micro-stop retune** (`backend/signal_trajectory.py`)

Three thresholds tightened, together stopping the day-1 flip pattern:

- `FAILED_BR_MIN_TRADING_DAYS = 1` *(new)* — entry session (day 0) is
  now disarmed. A pick admitted today cannot flip on today's tape.
- `FAILED_BR_VOLUME_MULT = 1.5` *(was 1.0)* — admission required 1.3x
  ADV50; exit on a 1.0x pullback was easier to trip than admission,
  asymmetric in the wrong direction. 1.5x now means genuine supply, not
  average-day noise.
- `FAILED_BR_CLOSE_BUFFER = 0.99` *(new)* — close must be < resistance
  × 0.99 (a full 1% back inside the base), not any tick below. Prevents
  intrabar jitter from flipping a still-holding breakout.

Classifier + human-readable exit message both updated so the reason
string reports the new trip levels honestly.

**2. Sunday-d45 display fix** (`backend/positions_view.py`)

`time_stops.day_45/90/180` displayed dates are now rolled forward to the
next weekday when the raw calendar date lands on Sat/Sun via a new
`_roll_forward_to_weekday` helper. **Display-only** — enforcement in
`_action_for` still uses raw calendar days via `days_held`, so behavior
is identical. The Sunday-d45 case that shipped before this fix (entry
2026-07-16 → d45 shown as 2026-08-30, a Sunday) is now impossible.

NSE holiday-calendar arithmetic (idea G) is still deferred.

**3. DEMO_MODE banner on positions page** (`middleware/schemas.py`,
`middleware/main.py`, `frontend/src/types.ts`,
`frontend/src/pages/PositionsPage.tsx`)

`PositionsResponse` gains `demo_mode: bool = False`, populated from the
env in `get_positions()`. `PositionsPage` now imports and renders
`<DemoBanner />` at the top when `data?.demo_mode` is truthy —
consistent with the banner already shown on `PicksPage` and
`StockDetailPage`. Closes the gap where a synthetic-data position card
looked identical to a real one.

**4. Language refit — "3-6 month hold" → swing framing**

Nine files updated to replace "3-6 month" language with the honest
phrasing: **"Swing hold — 3 weeks to 3 months typical, up to 6 months
for runners; day-180 is the outer hard cap, not a target."**

- `README.md` — top-of-file description + honest expectation callout
- `PRINCIPLES.md` §1 holding period paragraph + §4 time-stop table row
- `ARCHITECTURE.md` — "Group A — Long-term lens" heading refined
  ("admission-evidence window, not hold duration"); price-levels comment
- `PROCESS_FLOW.md` — DAY_180 description + constants block
- `AGENT_HANDOFF.md` — Current Architecture Truth opening paragraph
- `backend/backtest.py:62` — `DEFAULT_HOLD_DAYS` comment
- `backend/stages/__init__.py` — `lt_flow` module doc line
- `frontend/src/pages/PicksPage.tsx` — subtitle
- `frontend/src/pages/BacktestPage.tsx` — two prose blocks
- `frontend/src/components/PositionCard.tsx` — `exit_final` label
  "Day-180 final exit" → "Day-180 hard cap"; d45/d90/d180 spans gain
  `title` tooltips explaining each milestone; short italic subtitle
  under the card: *"Typical swing hold: 3 weeks to 3 months. Day-180
  is the outer hard cap, not a target."*

No constants changed values. No behavior changes tied to language.
Historical outcomes labels remain valid — no schema-version bump needed.

**What is still parked** (from previous conversations): two-session flip
confirmation (idea D), latched EXIT-Confirmed state (idea E),
EOD-freeze the action (idea F), strength-% UI for state indicators,
trading-day unification of DAY_45/90/180 arithmetic (Level 2),
NSE-holiday calendar (idea G), runner-lane build (Option 2).

## 2026-07-17 (later-3) — Balanced-holding foundation: action priority + label separation

Four small changes that make the label pipeline correct + honest without
tightening any existing exit rule. Everything larger (persisted
hysteresis, latched confirmed-exits, protect-the-runner enforcement, ATR
sizing, survival model) is parked in `ideas.md` with revisit signals.

**1. URGENT — Action-priority correction** (`backend/positions_view.py`)

The old order in `_action_for` was:
```
trajectory_flip → day_180 → end_date → day_90 → close-safety →
stop → t2 → t1 → day_45 → hold
```
A stop hit on day ≥ 180 got labeled `exit_final`, poisoning every
downstream tuner label. A T2 hit on a distribution day got labeled
`exit_distribution`. Both wrong.

New order (matches the user's ladder specification exactly):
```
close-safety → stop → t2 → t1 → distribution → day_180 →
day_90 → end_date → day_45 → hold
```

Hardest reason wins. Verified with a 7-case regression test: `stop @
day 200 → exit_stop`, `t2 @ day 200 → exit_t2`, `t1 @ day 200 →
exit_t1`, `stop + distribution together → exit_stop`, and the pure
`day 200 → exit_final` path still works when no price event fires.

**2. Outcome label v2 — separate mark-to-market from realized**
(`backend/stages/outcome.py`)

`LABEL_SCHEMA_VERSION = 2`. Additive fields on every outcome row:

- `mtm_return_pct` — mark-to-market return at snapshot day. Always defined.
- `return_pct` — kept as alias to `mtm_return_pct` for v1 readers.
- `is_open` — position still open at snapshot? False iff portfolio
  row's `status ∈ _CLOSED_STATUSES` (13 exit statuses enumerated).
- `realized_return_pct` — populated only when `is_open=False`, from
  the portfolio row's authoritative `exit_price` (or snapshot close as
  fallback). Lifecycle-accurate ladder P&L is v3 territory, parked.
- `exit_reason_final` — the terminal exit reason if closed; else null.

Tuner still trains on `return_pct` (= `mtm_return_pct` under v2), so
today's `scripts/tune_weights.py` behavior is byte-identical. Once
enough `realized_return_pct` rows accumulate, the tuner can filter to
closed positions only for a cleaner (smaller-sample) training set.

**3. Split-date advisory labels** (`backend/stages/hypothesis.py`)

Every pick payload gains a `date_labels` dict:
- `next_review` — operational checkpoint (5 sessions).
- `expected_breakout_window` — setup clock (10–20 sessions).
- `hard_time_stop` — capital cap from actual fill (DAY_180).

Advisory only — the raw exit_schedule still drives enforcement. This
gives the UI honest labels for the three orthogonal clocks the user
watches, without changing runtime behavior.

**4. 9-state action ladder** (`backend/action_labels.py` — new)

New module maps raw `_action_for` outputs to a human-readable ladder:
```
MAINTAIN_HEALTHY | MAINTAIN_DRY_UP | MONITOR_EARLY_WEAKNESS
REVIEW_WEAKNESS_CONFIRMED | EXTEND_5D | TAKE_PROFIT_T1 | TAKE_PROFIT_T2
EXIT_STOP | EXIT_DISTRIBUTION | DATA_UNAVAILABLE
```
Populated on each position dict as `action_label`. The soft states
(MONITOR / REVIEW / MAINTAIN_DRY_UP) require `soft_signal_count` /
`is_dry_up` context that isn't persisted yet — today they map to
`MAINTAIN_HEALTHY` unless the caller explicitly provides context.
Persistence layer (idea D in ideas.md) is the next natural step.

**Anti-over-tightening properties:**

- Priority reorder can only re-classify events that WERE firing — no
  ticker is newly exited because of this change.
- Label v2 fields are all optional-typed / tolerant-read; every v1 reader
  continues to work.
- Split-date labels are advisory metadata, not enforcement.
- 9-state ladder is advisory metadata, not enforcement.
- Config unchanged. Weights unchanged. `distribution_veto_mode: "shadow"`
  unchanged. `MIN_OUTCOMES_TO_TUNE = 20` unchanged.

**Verified invariants (isolated tests, no live state touched):**

Priority regression (7 cases):
```
stop-hit @ day 200:  exit_stop         (was exit_final)   PASS
t2-hit  @ day 200:  exit_t2           (was exit_final)   PASS
t1-hit  @ day 200:  exit_t1           (was exit_final)   PASS
dist-flip @ day 50: exit_distribution (unchanged)        PASS
stop+dist @ day 50: exit_stop         (stop wins)        PASS
day 200 no events:  exit_final        (hard cap)         PASS
close=None:         hold              (data safety)      PASS
```

Action label mapping (11 cases) — all PASS.

**Parked in `ideas.md` under "Balanced-holding + honest-labels — deferred
pillars":**

D–P — persisted warning count, latched EXIT-Confirmed, unified data
source, NSE calendar, contextual extension, 270-day bucket + protect-
the-runner enforcement, trailing-stop at T2, continuous hit-detection,
multi-horizon snapshots, lifecycle-accurate realized P&L, frontend enum
sync, ATR-adaptive sizing, survival model.

---

## 2026-07-17 (later-2) — Sliding-window ⇒ champion-challenger auto-invocation

Wired the sliding-window trigger to the existing champion-challenger tuner.
Every fire (every 5 newly-matured T+90 outcomes) now:

  1. Computes the per-stage IC diagnostic (as before).
  2. **Invokes `scripts.tune_weights.run_programmatic(apply=True)`.**
  3. Writes the tuner's full decision block into the same event file.

The user's ethos: "learn from every closed pick, don't wait for a monthly
retune." This closes the loop end-to-end while keeping the two safety
floors the tuner already has:

  a. `MIN_OUTCOMES_TO_TUNE = 20` — below this the tuner refuses to fit.
     No matter how many events fire, weights stay untouched until enough
     labels accumulate. Verified: at n=5, `config/stage_weights.json`
     is byte-identical before and after the fire.
  b. **Strict-beat ratchet** with `EPSILON = 0.001` — even above the floor,
     the tuner overwrites weights ONLY if a fresh fit's mean-of-top-3
     replay metric exceeds the incumbent by EPSILON. No beat, no write.
     Verified: at n=25 with random outcomes, the ridge candidate beat the
     bootstrap champion by +0.0248 and the ratchet accepted; had it not
     beaten, `config_written` would be False.

**New in `scripts/tune_weights.py`**

- `run_programmatic(apply=True, updated_by_tag="tune_weights.py")` — pure
  function that returns a decision dict, no stdout side-effects. Callers
  can log the outcome to their own audit trail without needing to parse
  text. The existing `run()`, `main()`, and `python -m scripts.tune_weights
  --apply` CLI behavior are unchanged.
- Decision values: `refused_min_outcomes | reject_ratchet | bootstrap |
  accept | would_accept_dry_run | error`.
- Returns: `{invoked_at, n_outcomes, decision, reason,
  champion_metric_recomputed, best_candidate, best_metric,
  config_written, ...}`.

**New in `backend/sliding_window_learn.py`**

- `CHAMPION_CHALLENGER_MODE` top-of-file constant. Values:
    - `"apply"` (default) — tuner called with `apply=True`; ratchet writes on
      strict beat.
    - `"dry_run"` — tuner called with `apply=False`; decision logged but
      config untouched even on beat. Useful for A/B observing what the
      ratchet would have done.
    - `"disabled"` — CC step skipped entirely; back to diagnostic-only events.
- `_invoke_champion_challenger()` — defensive wrapper. Any tuner import
  failure or crash returns an error-shaped dict; the event file is always
  written even if the CC step fails.
- Event schema bumped to `schema_version: 2` — adds
  `champion_challenger: {invoked, mode, decision, reason,
  champion_metric_recomputed, best_candidate, best_metric,
  config_written, ...}` and sets `action_taken` to
  `champion_challenger_applied` or `champion_challenger_no_op`.

**Ratchet's `updated_by` tag** — when the CC accepts via the sliding-window
path, the config's `updated_by` field becomes
`tune_weights.py:sliding_window:<ridge|mean-return>` so weight changes
driven by this trigger are distinguishable from a manual `python -m
scripts.tune_weights --apply`. Same signal in `history[].updated_by` for
each ratchet event.

**What does NOT change:**

- Tuner's math (`fit_ridge`, `fit_mean_return_weighted`, `replay_metric`,
  `normalize`) — untouched.
- `RIDGE_LAMBDA = 0.1`, `EPSILON = 0.001`, `TOP_N_FOR_METRIC = 3`,
  `MIN_OUTCOMES_TO_TUNE = 20`, `EVAL_HORIZON_DAYS = 90` — untouched.
- `SCORED_STAGE_IDS` — untouched. Weights sum to 1.0 preserved.
- `distribution_veto_mode` — untouched (still `"shadow"`).
- CLI behavior — `python -m scripts.tune_weights [--apply | --force-apply]`
  behaves identically.

**Verification (isolated in-repo tests, real config restored after):**

At **n=5** (below floor):
```
cc.invoked            : True
cc.mode               : apply
cc.decision           : refused_min_outcomes
cc.config_written     : False
config_before == config_after : PASS  (byte-identical)
```

At **n=25** (above floor) with seeded fake trace files:
```
cc.invoked            : True
cc.decision           : accept
cc.reason             : beats champion by +0.0248 (>= EPSILON=0.001)
cc.best_candidate     : ridge
cc.best_metric        : 0.051333
cc.champion_metric_recomputed : 0.026567
cc.config_written     : True
updated_by            : tune_weights.py:sliding_window:ridge
```

**Operator's runbook (updated):**

1. Nothing to do daily — the trigger fires automatically every 5 T+90
   outcomes.
2. Weekly (see WEEKLY_TRACKING.md row 8): inspect
   `data/learning_events/sliding_*.json`. Each file contains both the IC
   block and the champion_challenger block — the audit trail is complete.
3. If `champion_challenger.config_written: true` appears, a weight change
   has landed. Cross-check `config/stage_weights.json:updated_by` and
   `history[].chosen_fit` to see which fitter won and why.
4. To pause auto-CC (e.g. during a research sprint), set
   `CHAMPION_CHALLENGER_MODE = "dry_run"` or `"disabled"` in
   `backend/sliding_window_learn.py`. Reversible in one edit.

---

## 2026-07-17 (later) — Sliding-window learning trigger (diagnostic-only)

Event-driven per-stage IC (information coefficient) diagnostic that fires
every 5 newly-matured T+90 outcomes. Purpose: give visibility into
whether each stage's score is actually predicting T+90 return, **without
touching live weights** until the cumulative matured count clears the
existing `MIN_OUTCOMES_TO_TUNE = 20` ratchet floor.

Motivation: the user's ethos is "learn from every closed pick, don't wait
for a monthly retune." The honest constraint is that logistic-fit weights
at n=5 thrash on noise — one lucky-winner window would push AC up 20 %,
the next unlucky window would reverse it. Solution: separate *seeing*
the signal (cheap, safe at n=5) from *acting* on it (still requires the
20-outcome ratchet floor).

**New module** — `backend/sliding_window_learn.py`

- `maybe_fire_event(new_row)` — called from `stages/outcome.py` after
  every outcome append. Guarded caller-side so a bug here can never break
  outcome logging.
- Trigger: counts total T+90 outcomes; fires when count advances past
  `last_processed_count + TRIGGER_EVERY_N` (default 5). Idempotent —
  re-invoking at the same count does not re-fire.
- Filters to `horizon_days == 90` outcomes. T+180 appends never trigger
  a diagnostic event (would double the event rate).
- Reads the last `WINDOW_SIZE = 5` T+90 rows + their per-stage margins
  from `data/traces/run_*.jsonl` (same loader pattern as
  `scripts/tune_weights.py`, so an IC promoted here lines up 1:1 with
  the tuner's feature vector).
- Computes Pearson r per stage (stdlib math, no numpy dependency; matches
  the tuner's zero-deps posture). Zero-variance series and all-zero
  margins return `None` — no fake-positive ICs.
- Emits `learning_hints` when `|IC| ≥ IC_STRONG_THRESHOLD = 0.5`. Positive
  IC → "candidate to weight up"; strongly negative → "investigate;
  possible over-fit or reversed convention". **Never** writes to
  `config/stage_weights.json` — hints are text guidance for the human
  operator running the manual tuner.

**Event file schema** (`data/learning_events/sliding_<date>_n<count>.json`)

Atomic write via `.tmp` rename. Append-only over time; each event is one
file, never rewritten. Contents: `{ts, schema_version:1, trigger_every_n,
window_size, matured_count_total, matured_count_since_last_event,
samples[], mean_return_pct, ic_by_stage{}, learning_hints[],
action_taken:"diagnose_only", recommendation}`.

Plus a state file `data/learning_events/state.json` tracking
`last_processed_count` and `events_written` for idempotency.

**Wiring in `backend/stages/outcome.py`**

Single addition after `_append_outcome(row)` writes the row: import the
trigger and call it inside a broad try/except. The outcome-logging path
must remain reliable regardless of any bug in the diagnostic layer.

**Verification (deterministic tests, no live data):**

Ran an in-repo smoke test with 5 seeded T+90 rows + 5 more, isolating
`outcomes.jsonl` and `data/learning_events/`:

- First fire at n=5 → one event file written. ✓
- Second call at n=5 (idempotent) → NO re-fire. ✓
- T+180 row appended → NO fire (only T+90 counts). ✓
- 5 more T+90 rows appended → second event at n=10 with
  `matured_count_since_last_event = 5`. ✓
- `state.json` shows `events_written = 2`, `last_processed_count = 10`. ✓
- Pearson math correct on perfect ±1, undefined on zero variance and
  n < 3. ✓

**Promotion pathway (unchanged):**

1. Sliding-window emits an event every 5 closed picks.
2. Operator reads `data/learning_events/sliding_*.json` weekly (see
   WEEKLY_TRACKING.md row 8).
3. When a stage shows consistent IC sign across ≥ 3 consecutive windows
   AND cumulative matured count ≥ `MIN_OUTCOMES_TO_TUNE = 20`, run
   `python -m scripts.tune_weights --apply` manually. The
   champion-challenger ratchet decides whether the fresh outcomes have
   enough signal to actually beat the current weights.

**What did NOT change:**

- `scripts/tune_weights.py` unchanged. Its `MIN_OUTCOMES_TO_TUNE = 20`
  floor, ridge regression, mean-return-weighted candidate, and champion-
  challenger ratchet all intact. Auto-invocation from the sliding-window
  trigger was **deliberately not added** — the operator is the ratchet's
  final gate.
- `config/stage_weights.json` weights + `distribution_veto_mode`
  unchanged. No code path in this commit mutates them.

**Fix points:**

- `backend/sliding_window_learn.py` top-of-file constants:
  `TRIGGER_EVERY_N`, `WINDOW_SIZE`, `IC_STRONG_THRESHOLD`,
  `MIN_SAMPLES_FOR_IC`, `EVAL_HORIZON_DAYS`, `SCORED_STAGE_IDS`.
- Event log location: `data/learning_events/sliding_*.json` (git-ignored
  under `data/*`).

---

## 2026-07-17 — Precision-first refit (pillars 1–5, dark-launch)

Extends the spine with **five additive changes** aimed at pre-breakout
precision without disturbing the live picks logic. Everything ships at
weight 0 or in shadow mode: a fresh `stage_weights.json` clone against
this commit produces byte-identical picks to yesterday. Config toggles
promote each piece independently once shadow traces prove it.

Motivation: the audit (see ideas.md → *Precision-first refit — deferred
pillars*) called out three risks the current spine could not answer
cleanly — engineered volume spikes, gap-up bull traps, and repeated
stealth-distribution days. All three are anti-institution-trick patterns
that unsigned volume metrics (OBV/ADV/CMF cumulatives) cannot
disambiguate. The refit ships the primitives + a shadow-mode veto layer
that *can* — without moving any existing weight or threshold.

**1. Signed-pressure primitives** (`backend/indicators.py`)

Three pure functions, no callers yet, weight 0 in composite:

- `close_location_value(o, h, l, c)` — CLV = `(2C − H − L) / (H − L)`
  clamped to [-1, +1]. Zero-range bars → None.
- `signed_volume_pressure(df, adv_window=60, rv_clip=3.0)` — per-bar
  series of `CLV_t × clip(V_t / median(V, N), 0, rv_clip)`. Uses the
  trailing median (fat-tail reason as `volume_robust_zscore`); RV is
  clipped at 3.0 so a single earnings-day print cannot dominate a 20-bar
  EWM aggregate — this is the anti-"engineered spike" property, not a
  heuristic filter.
- `ewm_signed_pressure(series, halflife)` — latest EWM value. Halflife
  choices map to horizons the plan calls out: 3 → ~5-bar tape, 10 →
  ~20-bar mid-swing flow, 30 → ~60-bar base-period flow.

**2. Finalized-bar hygiene** (`backend/stages/ingest.py`)

`[I]` now runs two cleanups **before** the `MIN_BARS` check:

- `_clean_malformed_rows` drops rows with NaN in any OHLC column or
  non-positive Volume. Suspended-day / holiday-phantom bars used to leak
  through the data-source layer; every downstream indicator assumed
  real numbers.
- `_drop_partial_session_bar` is IST-aware. If the last bar's date equals
  today (IST) and IST time is before 15:35, the bar is dropped as a
  partial-session read. Backtests skip this check because they always
  use finalized as-of slices.
- `FULL_LOOKBACK_BARS = 260` (advisory) — trace records
  `has_full_lookback` so downstream calibration knows when a sample is
  truncated. `MIN_BARS = 200` unchanged; **no ticker is newly rejected**
  compared to yesterday.

**3. Distribution veto stage `[DV]`** (`backend/stages/distribution_veto.py` — new)

Appended last in `PER_TICKER_CHAIN` so it sees the full tape after `[BR]`.
Three deterministic checks:

- `weak_close_spike`: today volume-z ≥ 2.0 AND close ≤ bottom third of
  the day's range — "big volume, sellers won".
- `gap_up_weak_close`: today.Open ≥ 2% above yesterday.Close AND close
  in bottom half of range — classic bull-trap gap-up.
- `dist_day_cluster`: ≥ 3 sessions in the last 15 with down-close AND
  volume > ADV20. ADV20 is computed on the prefix *before* the lookback
  window so a distribution day doesn't reduce its own baseline.

Two modes controlled by `config/stage_weights.json →
distribution_veto_mode`:

| Mode | Effect on picks | Trace records |
|---|---|---|
| `"shadow"` **(default)** | always `passed=True` — zero impact | `would_veto`, `veto_reasons`, `dist_day_count_15` |
| `"block"` | veto → `passed=False` → ticker dropped | same |

The config loader auto-promotes `"DV"` into `HARD_GATE_IDS` in `block`
mode (`backend/pipeline.py:_load_weight_config`), so one JSON toggle
controls both the stage's own pass/fail decision and its selection
impact.

**4. Client classifier + augmented `DealAggregate`** (`backend/block_deals.py`)

- `classify_client(name) -> ClientClass` — case-insensitive regex over a
  16-pattern table. Buckets: `custodian | fii | dii | prop | individual
  | unknown`. Deliberately conservative: HUF / PVT LTD / numbered
  accounts return `unknown`, never `institutional`. False-institutional
  labels would poison the downstream envelope claim.
- `DealAggregate` gains `institutional_buy_qty`, `institutional_sell_qty`,
  `institutional_net_qty`, `institutional_client_count`,
  `has_disclosed_large_client`, `client_class_counts`. Additive — every
  existing reader of `net_qty_ratio` etc. is untouched.

**5. Accumulation-assessment envelope** (`backend/stages/render.py` +
`backend/stages/hypothesis.py`)

Every pick payload now carries an advisory `accumulation_assessment`
dict. Separates three claims the plan said must never be conflated:

| Claim | Field |
|---|---|
| Accumulation pressure inferred from price+volume | `level` ∈ `emerging \| building \| strong \| ready \| distribution` |
| Participant evidence supporting that inference | `participant_evidence` ∈ `inferred \| disclosed_large_client` |
| Probability the setup produces a successful breakout | `score_0_100` (unchanged — the composite still owns picking) |

Level thresholds anchor on the current `COMPOSITE_TAU = 0.28`:
`ready ≥ 0.55` (requires BR trigger too, else demoted to `strong`),
`strong ≥ 0.45`, `building ≥ 0.35`, else `emerging`. **Distribution
overrides any bullish level** — if `would_veto` fires in shadow mode,
`level = distribution` even when the composite says otherwise.

The envelope is **never used to gate selection**. It labels what the
composite already decided.

**Schema bumps + tolerance:**

- `PICKS_SCHEMA_VERSION` 6 → 7 (per-pick `accumulation_assessment`).
- `middleware/schemas.py:Pick` gains `accumulation_assessment:
  Optional[dict]` so Pydantic doesn't silently drop it.
- `DealAggregate` extended fields all default to zero / False; old
  callers unchanged.

**What did NOT change (invariants verified via smoke test):**

- `HARD_GATE_IDS = {U, I, HR}` in shadow mode (unchanged).
- `COMPOSITE_TAU = 0.28` (unchanged).
- All existing `scored_stage_weights` (unchanged).
- `TRIGGER_AC_MIN_SCORE = 0.6` Bajaj-Auto safety floor (unchanged).
- `compute_composite`, `_reweight_for_trigger`, `classify_trigger`
  bodies (unchanged).
- Every existing stage (`universe`, `hard_rejects`, `accum_screen`,
  `accumulation`, `lt_flow`, `consolidation`, `volume`, `breakout`,
  `rank`, `hypothesis`, `render`, `outcome`) — byte-identical logic.

**Anti-over-tightening guardrails:**

- `distribution_veto_mode` defaults to `"shadow"` — no ticker is newly
  rejected until you flip the config.
- New signed-pressure primitives have no consumers; adding them to the
  composite requires (a) editing `_DEFAULT_COMPOSITE_WEIGHTS` **and**
  (b) surviving the champion-challenger ratchet in `tune_weights.py`.
- `[I]` hygiene drops only truly malformed rows; the `MIN_BARS` floor
  did not move.

**Fix points for anyone wanting to promote or tune:**

- `config/stage_weights.json → distribution_veto_mode` — flip
  `"shadow"` → `"block"` after ≥ 4 weeks of shadow traces confirm veto
  precision.
- `backend/stages/distribution_veto.py` top-of-file constants —
  `Z_SPIKE_THRESHOLD`, `BOTTOM_THIRD_MAX_CLV`, `GAP_UP_MIN_PCT`,
  `DIST_CLUSTER_MIN_DAYS`, `DIST_CLUSTER_LOOKBACK`.
- `backend/stages/render.py → _LEVEL_BANDS` — level thresholds; will be
  re-anchored to empirical out-of-sample bands once ≥ 200 matured
  setups exist (see ideas.md).
- `backend/block_deals.py → _CLIENT_PATTERNS` — regex table for
  participant classification.

**Deferred (see ideas.md):**

- Delivery-percent loader from NSE `sec_bhavdata_full_*.csv`.
- Excess-move signed pressure (needs sector-index fetch).
- Full logistic β re-fit + Theil–Sen runway (needs ≥ 200 `[HR]`-passer
  labels + `[EX]` live + 60-session block-mode veto history).
- Prereq for the above: widening `stages/outcome.py` to label every
  `[HR]`-passer, not just picks.

---

## 2026-07-15 — Picks-vs-portfolio reconciliation + volume-based dynamic horizon

Fixed a trust-breaking bug: the picks pipeline and `positions_view` were
two independent code paths with no cross-reference, so the same symbol
could appear in today's buy list AND today's exit list on the same day.
Root cause: `record_picks` deduped only on `(symbol, entry_date)` and no
downstream filter compared today's picks against currently-held positions.

Four coordinated changes, all additive to the CSV/JSON schemas (tolerant
readers preserved):

**1. Picks reconciliation** (`backend/picks_reconcile.py` — new)

Ownership-aware annotation of today's picks:

| Existing row | Existing action | New pick behaviour |
|---|---|---|
| suggested (never taken) | any | pass through (record_picks supersedes old) |
| paper / live | `exit_*` | `suppressed_from_ui` flag; hidden from render, still recorded |
| paper / live | hold / tighten / extend_horizon | `already_held` annotation, kept visible |

`split_visible_from_suppressed` separates the reconciled list. Only visible
picks go into `picks_<date>.json`; `record_picks` gets the full list so
the fresh signal on a taken-position exit day still lands as a new
`suggested` row alongside the taken one (duplicate rows with different
entry_dates are legitimate — one is the user's real capital, the other
is the fresh signal).

**2. Portfolio replace/duplicate/supersede** (`backend/portfolio.py`)

`record_picks` rewritten with three rules:
- Open **suggested** row for same symbol → `status="superseded"`,
  `superseded_by=<new_pick_id>`. Add new row.
- Open **taken** row for same symbol → survives untouched. Add new
  `suggested` row alongside.
- No open row → add new row.

New status value: `superseded`. New CSV columns: `end_date`, `horizon_days`,
`horizon_basis`, `horizon_source`, `superseded_by`.

**3. Volume-based dynamic horizon** (`backend/horizon.py` — new)

The fixed 6-month `target_date` is replaced with a bucketed end_date
derived from confirmation strength + Weinstein stage + entry timing.
Buckets: `(30, 60, 90, 120, 180)` days. Deterministic; no live data.

At `end_date`, `positions_view` calls `revalidated_horizon_days`:
- Trajectory healthy + not at max bucket → recommend `extend_horizon`
- Trajectory flipped or at max bucket → recommend `exit_end_date`

`DAY_180` remains an unconditional hard cap; `_action_for` was reordered
so the 180-day final exit precedes any horizon-extension logic.

**4. Consecutive-pick diff** (`backend/picks_diff.py` — new)

Every pick that re-fires within a 30-day lookback carries a
`change_since_prev_pick` block: confirmation score delta, bonuses
added/removed, entry_timing / weinstein_stage changes, price_plan
deltas, rank movement. Empty if the pick is new to the window.

**Continuous monitoring on user fill** — `positions_view` now recomputes
the effective `end_date` as `entry_d + horizon_days` where `entry_d`
respects `user_entry_date` when set. Taken positions' horizon clocks
start from the user's fill, not the scanner's original scoring day.
Stored end_date is preserved on the row for audit (`stored_end_date`
in the API response).

**Schema bumps:**
- `PICKS_SCHEMA_VERSION` 5 → 6 (adds per-pick `holding_horizon`,
  `already_held`, `suppressed_from_ui`, `change_since_prev_pick`).
- `portfolio.csv` gains 5 columns; old rows load fine (all optional).

**Wiring in `backend/orchestrator.py`:** after `pick_payloads` are built,
the pipeline attaches horizon → reconciles vs. portfolio → attaches diffs
→ splits visible from suppressed → renders visible only → records all.

Fix points:
- `HORIZON_BUCKETS` — allowed horizon bucket set (`backend/horizon.py`).
- `RECONCILE_HARD_FILTER_ACTION_PREFIXES` — which portfolio actions trigger
  UI suppression (`backend/picks_reconcile.py`).
- `PICK_DIFF_LOOKBACK_DAYS` — how far back to search for the previous pick
  (`backend/picks_diff.py`).

### 2026-07-15 (follow-up) — Trust-safety filter + frontend rendering

Two problems surfaced after the initial ship: (1) the contradiction still
appeared on-screen in the transient window BEFORE the daily pipeline had
re-run to supersede the stale suggested row, and (2) the backend was
emitting `change_since_prev_pick` / `already_held` / `holding_horizon`
but the frontend had zero rendering code for them.

**Backend — defensive filter in `positions_view.list_active_positions`**

New helper `_symbols_in_todays_picks(today_iso)` reads
`data/picks_<today>.json`. `list_active_positions` now skips any open
row whose ownership is `suggested` AND whose symbol appears in today's
picks AND whose entry_date is not today. This closes the mid-day window
between pipeline runs where a stale suggested row would still surface
its exit signal even though a fresh pick was already in the buy list.

Taken (paper/live) rows are never hidden by this filter — the user's
real capital always shows, and `picks_reconcile` handles the
contradiction on the picks side via `suppressed_from_ui`.

**Frontend — pick-card rendering for the new schema-v6 fields**

- `frontend/src/types.ts`: added `HoldingHorizon`, `AlreadyHeld`,
  `ChangeSincePrevPick`, `PickDelta<T>`, `BonusDiff`, `RankChange`, and
  four new optional fields on `Pick`.
- `frontend/src/components/PickCard.tsx`: three new blocks —
  amber "Already held" banner (ownership + entry date + days held +
  P&L + current portfolio action), sky-blue "Since last pick" diff
  panel (score delta with color, bonuses added/lost, timing/stage
  changes, rank climb/drop), and a "Horizon Nd" pill badge.

Pure additions; no changes to existing render paths. `tsc --noEmit`
clean on `tsconfig.app.json`.

### 2026-07-15 (follow-up 2) — Middleware schema pass-through, multi-day trail, daily diagnostic

Three tightly-related additions surfaced from a debugging session on the
running app:

**1. Middleware Pydantic schema was stripping schema-v6 fields.**

Symptom: picks_<date>.json on disk had `holding_horizon` /
`change_since_prev_pick` populated, but the browser saw none of them —
so the "Since last pick" panel never appeared even for symbols that had
been picked days in a row. Root cause: `middleware/schemas.py:Pick` had
no field declarations for the schema-v6 additions, so Pydantic
silently dropped them during API serialization.

Fix: five new optional fields on the `Pick` DTO, kept as
`Optional[dict]` / `Optional[list]` rather than strict nested models so
the API remains tolerant of backend sub-field additions:

- `holding_horizon`, `already_held`, `change_since_prev_pick`,
  `suppressed_from_ui`, `pick_history`.

**2. Multi-day `pick_history` trail on every pick.**

`change_since_prev_pick` only shows the delta vs the single most-recent
prior appearance. For a symbol picked N days in a row, that's not
enough — the user wants the full trajectory. New backend function
`compute_pick_history` (`backend/picks_diff.py`) walks
`data/picks_<date>.json` files backwards over
`PICK_HISTORY_LOOKBACK_DAYS` (default 30) and returns up to
`PICK_HISTORY_MAX_ENTRIES` (default 7) prior appearances, newest first.

Each entry carries a `direction` tag comparing its score to the OLDER
entry immediately below it:

| direction | Meaning |
|---|---|
| `positive` | this day's score was higher than the day before |
| `negative` | lower |
| `neutral` | flat |
| `first_appearance` | oldest entry in the trail (nothing to compare) |

Wired into `orchestrator.py` alongside `attach_change_diffs`.

Frontend renders a compact monospace table with color-coded rows
(emerald / rose / slate) and glyphs (▲ ▼ · ◇). Legend inline.

**3. `data/daily_diagnostic.md` — one file, everything.**

New module `backend/daily_diagnostic.py` writes a self-contained
markdown snapshot at the end of every pipeline run (Phase 6 in
orchestrator, after `record_picks`). Overwrites in place. Uploading
this single file gives a diagnostician:

- Environment (Python, git HEAD, executable path)
- Code fingerprints (loaded module paths, `PORTFOLIO_FIELDS` contents,
  `PICKS_SCHEMA_VERSION` in the running process)
- Pipeline run summary (universe, survivors, visible / suppressed
  counts, regime status)
- Reconcile events (from trace JSONLs)
- Portfolio state (by-status breakdown, open positions table,
  duplicate-symbol detection)
- Picks JSON per-pick summary (which schema-v6 fields are present)
- Errors captured during the run

Fail-open — a diagnostic write failure never breaks the pipeline.

Fix points:
- `PICK_HISTORY_LOOKBACK_DAYS`, `PICK_HISTORY_MAX_ENTRIES`
  (`backend/picks_diff.py`)
- `DIAGNOSTIC_PATH` (`backend/daily_diagnostic.py`) — change if you
  want to keep history rather than overwrite.

### 2026-07-15 (follow-up 3) — Trajectory metric-mirror flip thresholds

Symptom: BAJAJ-AUTO trajectory oscillated between `flipped` and
`stable` on alternate days for a stable position — visibly wrong.

Root cause: `signal_trajectory._classify_positive` and
`_classify_ratio` had a hair-trigger flip condition (`current <= 0`
for positive metrics, `current < 1.0` for ratio metrics). For a
setup admitted just above the LT admission floor (e.g. OBV-90d at
+6%), day-to-day OBV noise around zero would trigger "flipped" one
day and un-flip the next. The exit logic was **asymmetric** with
respect to admission: pick logic required meaningfully positive
signals to admit, but exit logic fired on merely non-positive
signals.

Fix: metric-specific mirror. Each classifier now takes a
`flip_threshold` keyword; each call site in `_build_report` passes
a metric-specific constant that mirrors the corresponding LT
admission floor from `backend/stages/lt_flow.py`:

```
Metric              Admission floor    Flip threshold    Constant
─────────────────   ───────────────    ──────────────    ────────────
OBV-90d slope       >= +3.0%           <= -3.0%          FLIP_THRESHOLD_OBV_90D_PCT
Up/down vol ratio   >= 1.1             <= 0.9            FLIP_THRESHOLD_UP_DOWN_RATIO
150d MA slope       >= 0.0%            <= -0.5%          FLIP_THRESHOLD_MA150_PCT
                                       (0.0 + buffer)
```

Backward-compat: default `flip_threshold=0.0` (positive) /
`flip_threshold=1.0` (ratio) reproduce the original behaviour, so
any other caller of the classifiers is unaffected.

Verified with a 7-day BAJAJ-AUTO-shaped simulation: entry OBV +6%,
OBV oscillating between -1.5% and +3.1% never fires "flipped" now;
only a genuine drop below -3.0% (the mirror threshold) triggers the
exit signal.

Entry-value provenance (already correct, called out for clarity):
`_load_stage_features(symbol, entry_date_iso, "LT")` reads
`data/traces/run_<entry_date>_<symbol>.jsonl` for the pick's
specific entry date. `positions_view` passes each row's own
`r["entry_date"]`, so every row's trajectory is anchored to its
own scoring day's OBV / MA / ratio values.

Fix points:
- `FLIP_THRESHOLD_OBV_90D_PCT` — mirror of `LT.OBV_90D_SLOPE_MIN`
- `FLIP_THRESHOLD_UP_DOWN_RATIO` — mirror of `LT.UPDOWN_90D_MIN`
- `FLIP_THRESHOLD_MA150_PCT` — mirror of `LT.MA150_SLOPE_MIN` with
  a small negative buffer (0.5 pp) so a barely-flat MA doesn't trip
  a flip.

## 2026-07-14 — Fragile pre-breakout admission fix (Bajaj-Auto incident)

Bajaj-Auto was recommended on 2026-07-13 as a Pocket-Pivot pre-breakout,
then flagged for exit on 2026-07-14 after a routine −2.1% day flipped the
10d/30d OBV inflection from `healing` to `hemorrhaging`. Diagnosis: the
exit rule was not too aggressive — the entry was too lenient. A pick admitted
on a barely-positive 10d slope has no safety margin against normal ATR
noise, and any exit rule that respects the healing thesis will fire on that
same noise. Fix belongs at entry.

Two minimal edits, no new thresholds added, practical yield preserved.

**1. Weight relief gated on AC strength** (`backend/pipeline.py`)

`classify_trigger` previously returned `pre_breakout` on any `AC.passed`.
A marginal AC scorer (e.g. score 0.30) earned the same VD weight cut as a
strong-base coil (e.g. score 0.80). Now the classifier requires
`AC.score >= TRIGGER_AC_MIN_SCORE` (default 0.6):

```
Before                                           After
──────────────────────────────────────────       ────────────────────────────────────────────
pre_breakout = AC.passed AND BR fail             pre_breakout = AC.score ≥ 0.6 AND BR fail

any AC-passer earns VD weight relief             only strong coils earn VD weight relief
→ fragile picks admitted                         → marginal coils fall back to fixed weights
```

The AC score already captures range tightness, volume dryness, and rising
ADI slope — i.e. accumulated volume across the base. Anchoring the weight
relief on it means the relief is earned by long-window evidence, not by a
short-window inflection flag.

**2. Healing margin bump reduced from 0.10 to 0.05** (`backend/stages/volume.py`)

The `obv_flow_inflection` ±margin tilt in `[VD]` was decision-sized (±10%
of the [0,1] margin range). On a single-bar-sensitive slope, that was
enough to push marginal picks over the composite threshold. Reduced to
advisory-sized (±5%) — enough to tiebreak between strong candidates, too
small to admit a marginal one. The feature is still surfaced in traces for
auditability.

**Practical-yield sanity**: neither edit adds a new hurdle. Edit 1 restricts
a *relaxation*; edit 2 shrinks a *bonus*. Picks that were passing on strong
LT + strong AC keep passing; the ones losing admission are exactly the
fragile-coil cases we want to filter.

**What Bajaj-Auto looks like under the new rules**: if yesterday's AC score
was ≥ 0.6, the pick is still recommended, but with a smaller short-window
tailwind — today's −2.1% doesn't sit near the flip boundary. If AC was
< 0.6, the pick isn't recommended in the first place. Either path is more
coherent than the shipped 07-13 behaviour.

**Tests** — 4 new cases in `scripts/test_pre_breakout_accuracy.py` (31
total, all pass):

```
AC.score = 0.7 + BR fail        →  pre_breakout      (weight relief granted)
AC.score = 0.4 + BR fail        →  neutral           (relief withheld)
AC.score = 0.6 (threshold)      →  pre_breakout      (inclusive boundary)
Composite: marginal-AC pick     →  adjusted == fixed (no relief)
VELOCITY_MARGIN_BONUS <= 0.05                        (regression guard)
```

**Explicitly not changed** (asked at diagnosis time, rejected as
non-root-cause):

- B1' exit rule kept as-is. With fewer fragile entries admitted, it fires
  less often on noise; the frame's twitchiness was a symptom, not the
  disease.
- No shielded-grace exit variant. Would mask the entry problem.
- No stacked tightening (magnitude AND persistence AND corroboration on
  the 10d slope). Would cross the practical-yield line for near-zero
  benefit on top of edit 1.

## 2026-07-13 — Exit-rule accuracy: healing-velocity override (B1') + failed-breakout micro-stop (B1.5)

Two additive exit rules that close accuracy gaps opened by the earlier
pre-breakout entry work — no changes to entry gates, no changes to the
composite scorer.

**1. Healing-velocity override for divergent entries** (`backend/signal_trajectory.py`)

The standard B1 exit rule ("OBV rolls over → exit") is a category error
for picks entered with `obv_flow_inflection == "healing"`: their long-window
OBV was already negative when we bought — that's what defined the setup.
Adopting B1 literally would mark them for exit on day 1. New classifier
`_classify_healing_flip(entry_inflection, current_inflection, days)`:

```
entry            current            days                    state
─────────────    ─────────────      ──────────────────      ─────────────
healing          hemorrhaging       any within grace        flipped (exit)
healing          healing            within grace            strong  (hold)
healing          neutral            within grace            stable  (hold)
healing          healing            grace expired           stable
healing          any non-healing    grace expired           weakening
any other        —                  —                       unknown (no override)
```

`HEALING_GRACE_TRADING_DAYS` defaults to 10 sessions. After the grace
expires we fall back to the standard 90d indicators — the divergent-entry
benefit-of-the-doubt is time-bounded.

**2. Failed-breakout micro-stop (B1.5)** (`backend/signal_trajectory.py`)

New rule for SOS-breakout picks: within the first
`FAILED_BR_WINDOW_TRADING_DAYS` (default 5) sessions, if `close < 20d high`
(the entry-day resistance level captured in the BR stage's features) AND
`today's volume >= FAILED_BR_VOLUME_MULT × ADV50` (default 1.0×), exit at
next open. A breakout that closes back below its own resistance on heavy
volume is institutional distribution — the -8% B2 stop gives back too much.

Both rules integrate into the existing `compute_trajectory(...)` pipeline
as new `IndicatorDelta` entries, so any `flipped` state surfaces through
the same `exit_recommendation` bit that already drives
`positions_view._action_for(..., trajectory_flip=True)`. No new plumbing.

**Wiring** (`backend/positions_view.py`)

`compute_trajectory` now accepts `trading_days_since_entry`, computed from
the scanner entry date via the existing `_trading_days_between` helper.
Both windowed rules receive that value on every daily positions refresh.

**Payload changes** (`backend/stages/hypothesis.py`)

`distribution_flip_note` is now trigger-aware — divergent entries get the
healing-velocity wording, standard entries get the OBV-90d wording.
SOS-breakout picks additionally get an `exit_schedule.day_5_failed_breakout`
milestone with the exact resistance level and volume threshold.

**Tests** — 15 new cases in `scripts/test_pre_breakout_accuracy.py` (27
total, all pass). All in-memory synthetic fixtures — no network calls
(corporate firewall blocks live fetches per project constraints):

```
healing->hemorrhaging inside grace   -> flipped
healing->healing inside grace         -> strong
healing->neutral inside grace         -> stable
grace expired + healing intact        -> stable
grace expired + neutral               -> weakening
non-divergent entry                   -> unknown (no override)
close < resistance + heavy vol in 5d  -> flipped (B1.5)
close > resistance                    -> stable
close < resistance + light vol        -> stable (no B1.5)
outside 5d window                     -> unknown (defer to B2)
non-BR pick (no resistance)           -> unknown
full trajectory: divergent + hemorrhaging -> exit
full trajectory: divergent + healing       -> hold
full trajectory: failed breakout day-3     -> exit
full trajectory: failed conditions day-8   -> no B1.5 fire
```

**Considered and rejected:**

- *CMF < -0.05 exit threshold* — CMF is not wired into the live
  `signal_trajectory` pipeline (legacy `volume_signals.py` only, harvested
  and not carried forward). Rule targets a signal the exit path doesn't
  consume; moot.
- *21d EMA trailing stop post-T1* — improves risk management but doesn't
  fix a bug or contradiction; would add a new indicator and post-T1
  runtime mode. Deferred until we have outcome data to justify the
  additional complexity.

## 2026-07-13 — Pre-breakout accuracy: trigger-contextual weighting + OBV flow velocity

Two targeted changes to raise the hit-rate on Pocket-Pivot / No-Supply-Test
pre-breakout setups without loosening any gate.

**1. Trigger-contextual composite reweighting** (`backend/pipeline.py`)

The composite scorer previously applied a fixed weight vector to every
survivor regardless of setup type. That penalised pre-breakouts for the
property that defines them: quiet mid-term flow. New helper
`classify_trigger(stage_results)` derives the regime from which gates
fired, and `_reweight_for_trigger(...)` rebalances at composite time.

```
regime            trigger conditions             weight change (sum-preserving)
─────────────     ──────────────────────────     ──────────────────────────────
pre_breakout      AC pass AND BR fail            VD × 0.5; freed share → LT + AC
sos_breakout      BR pass                        no change
neutral           neither                         no change
```

Fix points at the top of `pipeline.py`:
`TRIGGER_MT_STAGE_ID`, `TRIGGER_MT_SHRINK_FRAC`, `TRIGGER_MT_REDISTRIBUTE`.

**2. OBV flow-velocity inflection** (`backend/indicators.py`, `backend/stages/volume.py`)

New pure indicator `obv_flow_inflection(close, volume, short=10, long=30)`
compares the short-window OBV slope against the long-window slope and
labels the tape as `healing | hemorrhaging | neutral | unavailable`.
Semantics:

```
healing        long slope < 0  AND  short slope > 0   (multi-week weakness, last 2w up)
hemorrhaging   long slope < 0  AND  short slope < 0   (both windows negative, still bleeding)
neutral        anything else                          (no adjustment)
```

Wired into `[VD]` as a bounded ±10% margin tilt. Not a hard gate — only
tilts the ranker inside a plausible band. Fix points at the top of
`backend/stages/volume.py`: `VELOCITY_SHORT_WIN`, `VELOCITY_LONG_WIN`,
`VELOCITY_MARGIN_BONUS`. The stage also surfaces
`obv_flow_inflection`, `obv_slope_short_pct`, `obv_slope_long_pct` as
features on the StageResult (readable in every trace row).

**Tests** — `scripts/test_pre_breakout_accuracy.py`, 12 cases, all pass:

```
weight-sum invariance across all three regimes
pre_breakout composite gain vs fixed-weight: +11% on the canonical fixture
sos_breakout composite unchanged from fixed weights
classifier: pre_breakout / sos_breakout / neutral triage
synthetic ABB-like fixture → healing         (short +80.4%, long -15.0%)
synthetic bull-trap fixture → hemorrhaging   (short -11.5%, long -29.8%)
synthetic healthy-BR fixture → neutral       (short  +4.2%, long +13.8%)
VD stage surfaces inflection feature on real fixture
```

Deterministic; no live fetches. Fixtures are constructed in-memory with
seeded numpy RNGs so re-runs are byte-identical.

**Considered and rejected:**

- *Full-alignment ranker bonus* — required VD > 0.6 to fire, which by
  definition excludes every pre-breakout. Adopting it alongside (1) would
  give with one hand and take with the other.
- *Signal-adjusted position sizing* — changes P&L not hit-rate. Also
  contradicts the thesis that pre-breakouts are the highest-conviction
  setup. Revisit only after (1)+(2) accumulate outcome data.

## 2026-07-13 — My Positions V1: ownership + user-actual fill capture

The "did I actually take this pick, and at what fill?" story is now
end-to-end. Every pick the scanner emits starts as `ownership=suggested`;
the user can accept it (paper / live, with optional custom fill) or
decline it. Position monitoring re-anchors on the user's fill when
provided, and skips declined rows entirely.

**portfolio.csv — 5 additive columns (tolerant reader; old rows load unchanged)**

| Column | Values | Blank means |
|---|---|---|
| `ownership` | `suggested \| paper \| live \| declined` | (new rows default to `suggested`) |
| `user_entry_date` | ISO date | use scanner's `entry_date` |
| `user_entry_price` | float | use scanner's `entry_price` |
| `user_shares` | int | use scanner's `shares_total` |
| `user_notes` | free-form | (none) |

**Routing** in `backend/positions_view.py`:

```
entry_effective   = user_entry_date  or scanner entry_date
price_effective   = user_entry_price or scanner entry_price
shares_effective  = user_shares      or scanner shares_total
days_held         = today − entry_effective
pnl_pct           = (close_today − price_effective) / price_effective
day-45/90/180     = counted from entry_effective
stop / T1 / T2    = scanner's absolute price levels (unchanged)
```

Stop / T1 / T2 stay at the scanner's absolute prices — they're targets
on the tape, not offsets from the fill. Trajectory anchoring also stays
on the scanner's entry date.

**Downstream filters** — `positions_view.list_active_positions`,
`portfolio.update_open_picks`, and `stages/outcome.py` all skip
`ownership="declined"`; no cycles spent monitoring rejected picks and
no realized-return noise in `outcomes.jsonl` from picks nobody held.

**API**

- `POST /api/positions/{pick_id}/take` — body:
  `{ownership: "paper"|"live", user_entry_date?, user_entry_price?,
  user_shares?, user_notes?}`. Returns the refreshed
  `PositionsResponse`.
- `POST /api/positions/{pick_id}/decline` — no body.

**UI** (`frontend/src/pages/PositionsPage.tsx`)

Two sections: **Suggested** (Take paper / Take live / Decline; Take
opens an inline form) and **Held** (ownership badge + "Your fill" strip
showing user's inputs vs. scanner's whenever they diverge).

**Invariants preserved**

- `pipeline.py:HARD_GATE_IDS = {U, I, HR}` — untouched.
- `config/stage_weights.json`, composite weights, and τ — untouched.
- Scanner unchanged; the pick set is byte-identical to what it emitted
  yesterday.
- Data survives code changes: additive schema, tolerant reader treats
  missing `user_*` fields as blank → fall back to scanner's numbers.
  Older rows created before this ship load without modification.
- Deterministic — user-fill fields are manual inputs; no live data
  fetch, no LLM, no external API.

**Files changed**

```
backend/portfolio.py            (schema, record_picks, update_open_picks, set_ownership)
backend/positions_view.py       (user-fill fallback routing)
backend/stages/outcome.py       (skip declined)
middleware/schemas.py           (Position DTO, TakePositionRequest DTO)
middleware/main.py              (POST /api/positions/{id}/take, /decline)
frontend/src/types.ts           (PositionOwnership, Position fields, TakePositionRequest)
frontend/src/api.ts             (takePosition, declinePosition)
frontend/src/components/PositionCard.tsx   (ownership badge, Suggested/Taken footers)
frontend/src/pages/PositionsPage.tsx       (Suggested / Held sections)
PROCESS_FLOW.md                 (§5b rewritten to reflect shipped state)
```

**Validation**

- `python -m compileall backend middleware` — clean.
- `npm run build` in `frontend` — clean (734 kB main bundle, +8 kB
  for the inline take-fill form).
- No live pipeline run performed (corporate firewall constraint).
- Browser flow not executed by CI; type check + build pass.

## 2026-07-12 (docs) — My Positions lifecycle documented

No code change. `PROCESS_FLOW.md` gains a new §5b that captures the
post-pick lifecycle running in code since 2026-07-04 but never surfaced
in one place. Motivated by user question: "how long does it get
monitored, when will it close, and how does completed monitoring
summarize into learning?"

- **Dedupe rule.** `portfolio.record_picks` guards on
  `(symbol, entry_date)` — same-day duplicates are impossible;
  same-ticker-different-day tranches are by design (add-on-strength).
- **State machine.** `open → partial_t1 → target_hit / stopped /
  timed_out` with day-45 stop tighten (4%), day-90 forced exit if no
  T1, day-180 unconditional final exit. `/api/positions` filters to
  `{open, partial_t1}`; closed rows stay in `portfolio.csv` as history.
- **Cadence.** Daily 17:15 `[EX]` advisory-only exit-watch; Friday 17:30
  `weekly.py` may change status; daily 21:00 `[O]` writes realized
  returns to `outcomes.jsonl` at T+90 / T+180.
- **Learning loop.** `outcomes.jsonl` → `scripts/tune_weights.py`
  champion-challenger ratchet against `config/stage_weights.json`
  (accuracy monotone-non-decreasing).
- **Documented gap (closed 2026-07-13).** At publish time there was no
  `suggested vs held` distinction — every pick entered as `status=open`
  and was monitored equally. The V1 ship on 2026-07-13 (top entry)
  closed this gap with an `ownership` column + user-fill fields.

Validation:
- doc-only; no code, config, or data touched.

## 2026-07-12 — pre-breakout feedback: 3 bug fixes + additive volume metrics

Triggered by the "Stockiya — Feedback for Claude Code" review after cross-
checking two UI surfaces for the same ABB.NS pick. All three reported bugs
were reproduced against `data/ohlcv/ABB.csv` (128 EOD rows) before fixing.

**Bugs fixed**

1. *Gate-pass label overstates confirmation.* Under the v3 soft-gate composite
   spine a pick can clear `S >= τ` while a listed leg (e.g. BR) failed its own
   boolean. The old hardcoded header "Why all four gates passed" therefore
   lied whenever the composite carried a soft-failed leg.
   - `backend/stages/hypothesis.py` now emits `gate_confirmation_status`
     `{status, passed[], failed[], counts}` alongside `gates_evidence`.
   - `frontend/src/pages/StockDetailPage.tsx` branches the heading on
     `status`: "Why all N gates passed" (hard_confirmed) vs "Composite-
     qualified — p/t legs confirmed (soft-fail: …)" (composite_qualified).
   - `frontend/src/types.ts` gains the `GateConfirmationStatus` interface.

2. *Sign-flip in generated thesis text.* `_build_headline` templated
   `"broke {break_pct:+.1f}% above 20d high"` unconditionally, producing the
   self-contradictory `"broke -6.4% above 20d high"` on non-triggering days.
   - Branches on `sign(break_pct)`. Negative path now reads
     `"closed X.X% below 20d high on Yx vol — no confirmed breakout yet"`.
   - Positive path preserved: `"broke +X.X% above 20d high on Yx vol"`.

3. *OBV disagreement across UI + unstable OBV-180d.* Two separate cumsum
   implementations plus `% change vs a base bar` that blows up when the base
   is near zero (OBV is a signed cumulative). Same ABB series produced e.g.
   `-4334 %` at n=120 with the % form.
   - Unified: `backend/volume_signals.py` now imports `indicators.obv()` and
     `indicators.obv_slope_pct()`. Single source of truth.
   - Added `indicators.obv_norm_slope_pct(obv_series, n)` — linear-regression
     slope normalized by `mean(|OBV|)`, scaled to % / window. Bounded across
     zero crossings.
   - `AccumulationSignals` now emits `obv_norm_slope_90d_pct` and
     `obv_norm_slope_180d_pct` alongside the legacy % forms; UI should
     prefer the norm variants for display. Existing threshold call sites in
     `lt_flow.py` and `rank.py` still consume the % form (strategy math
     untouched) but the metric can be swapped once outcomes accumulate.

**Additive pre-breakout metrics (advisory; no thresholds consume them yet)**

These are the "genuine and additive" pieces from suggestions B/C/E of the
feedback. Multi-lookback machinery (`adaptive_windows`, `vol_dryness_ratio`)
was left in place — these are companions, not replacements.

- `indicators.volume_robust_zscore(volume, n=50)` — robust z via median +
  MAD × 0.6745. Treats sleepy large-caps and hyperactive small-caps on
  their own tape.
- `indicators.dry_up_streak_days(volume, n=50, percentile=25)` — count of
  consecutive trailing sessions with volume below the p25 of the last n
  bars. Streak, not a snapshot.
- `indicators.anomaly_cluster_count(volume, n=50, lookback=15, z_threshold=2)`
  — count of |z|≥2 spike sessions in the trailing 15. Catches "the pocket
  pivot fired 12 days ago, not today."

Wired into `backend/stages/breakout.py` features dict as
`vol_robust_z_50d`, `dry_up_streak_days_p25`, `anomaly_cluster_count_15d`.
Purely informational — the existing `vol_ratio_today_50d >= 1.3` check is
still the only volume decision-maker.

**Deferred (roadmap; strategy-touching, need explicit user approval)**

- Split BR into `PB` (pre-breakout: pocket-pivot, no-supply) and `BR`
  (SOS-only). Sketched in PRINCIPLES.md `[VSA]` section, not yet coded.
- NSE delivery-% overlay. Bhavcopy ingest already writes
  `data/delivery/<SYMBOL>.csv`; wiring as a filter/multiplier is a scoring
  change.
- Promote block/bulk-deal net-buy from a +1 bonus to a rank multiplier.
- Sector-relative volume z-score against the sector's same-day median.

**Validation**

- New smoke test `Stockya-tuner/scripts/test_prebreakout_feedback.py`.
  Runs against `data/ohlcv/ABB.csv`. Reproduces the feedback's numbers
  exactly (`break_pct = -6.41 %`, `vol_ratio = 0.28×`, upper-third `0.24`),
  proves the OBV pct-form pathology (`n=120 → -4334.2 %` vs norm-form
  `+149.9 %`), and asserts the headline no longer contains "broke -X% above".
- `python -m compileall backend middleware` clean.

## 2026-07-05 (late-3) — adaptive windows (per-ticker, not per-rule)

User pushback on the previous fix: `(10, 20, 40)` was still a hardcoded rule.
Replaced with a **per-ticker adaptive triplet** anchored by realized ATR:

- New `indicators.adaptive_windows(df, base=20)` — returns `(W/2, W, 2W)`
  where `W = clamp(base × normal_atr / current_atr, base/2, 2×base)`, then
  clamped to `[5, 60]`. Pure function of df; deterministic.
- High-vol stocks (ATR20% > 2%) → shorter windows, e.g. `(8, 16, 32)`.
- Low-vol stocks (ATR20% < 1%) → longer windows, up to `(20, 40, 60)`.
- ACS and AC call `adaptive_windows()` by default; backtest override via
  `acs_windows` / `ac_windows` still forces a specific triplet for tuning.
- `features.windows_scanned` records what each ticker actually scanned,
  so the trace shows the *reach* used per pick.

Rationale: fixed windows encode a hidden assumption that every stock's
accumulation base is the same length. False. Fast tape (ADANIENT-style)
compresses and breaks in weeks; slow tape (HDFCBANK-style) takes months.
The scan now positions itself around each ticker's own volatility clock,
without needing ML — pure deterministic scaling from ATR.

## 2026-07-05 (late-2) — multi-window ACS/AC scan (superseded by late-3)

Initial pass: fixed `ACCUM_WINDOWS = (10, 20, 40)` sweep. Kept for one
release cycle; late-3 replaces the fixed tuple with adaptive_windows().

## 2026-07-05 (late) — first-run resilience + empty-state honesty

Three small follow-ups after the personal-PC first-run kept surfacing the
same "0 picks / bhavcopy missing" trap:

- **`start.bat` auto-heals `backend\.env`.** If the file is missing on
  start, `start.bat` now copies `backend\.env.example` → `backend\.env`
  (which defaults to `DEMO_MODE=1`) instead of silently launching with the
  code-default `DATA_SOURCE=bhavcopy`. Prints a clear line so the user knows.
- **Empty-state message tells the truth on data-misconfig days.** When
  ≥90 % of tickers fail `[I] Ingest`, `orchestrator.py` sets
  `response.message` to the actionable fix ("Data source misconfigured —
  N/M tickers failed at [I] Ingest. Set DEMO_MODE=1 in backend/.env...")
  instead of the misleading "Nothing actionable today". UI shows it in the
  empty state block.
- **`PicksPage.tsx` fallback text de-staled.** Removed the "cleared all
  five gates" language (accurate under the retired 5-AND-gates spine, wrong
  under v3 soft-gate composite). Now points the user at the tabbed
  Closest-to-Firing panel below when nothing clears τ.

Validation:
- `python -m compileall backend middleware` — clean
- `npm run build` — clean

## 2026-07-05 — Nifty 500 universe + trader-UI empty state

Follow-up to the 2026-07-04 evening wire-up. Three tight changes based on user
feedback ("only Nifty 100 → Nifty 500", "still no picks", "empty page has too
many panels, creates confusion"):

- **Universe expanded.** `backend/universe.py` now exports `NIFTY_500`
  (~456 dedup'd tickers curated from prior knowledge — not the official NSE
  snapshot; will drift at each rebalance). New `STOCKYA_UNIVERSE=nifty500`
  option plus a **`custom` escape hatch** that reads one ticker per line from
  `config/universe_custom.txt` (`#` comments and `.NS` suffix optional).
- **Composite threshold τ lowered 0.35 → 0.28** in `config/stage_weights.json`.
  Modest relax to admit more marginal picks; the champion-challenger ratchet
  in `scripts/tune_weights.py` will reject any tuner delta that produces a
  worse metric, so the accuracy floor is unchanged.
- **Empty-state UI collapsed to a single tabbed panel.** Killed three
  overlapping panels (`NearMissPanel`, `ReadyToBreakPanel`, `EarlySignalPanel`)
  and their backend collectors, replaced with **`ClosestToFiringPanel`**:
  three tabs (Accumulation / Breakout / Overall), 4 columns per row
  (`Symbol · S · Gap · Held back by`), max 5 rows per tab. Trader-UI rule:
  every column earns its place or gets cut.
- Backend: `orchestrator._collect_closest_to_firing` groups tickers by
  strategy leader (`_weighted_margin` over `{ACS, AC}` vs `{LT, CS, VD, BR}`)
  and surfaces `_pulled_down_by` = `argmax wᵢ · (1 − mᵢ)` — the one stage
  that would flip the ticker if it fully fired.
- Middleware schema: `PulledDownBy` / `ClosestRow` / `ClosestToFiring`
  replace the removed DTOs. Picks response `schema_version` bumped **4 → 5**.

Files changed:
```
backend/universe.py, orchestrator.py, stages/render.py
config/stage_weights.json
middleware/schemas.py
frontend/src/types.ts, pages/PicksPage.tsx
frontend/src/components/ClosestToFiringPanel.tsx  (new)
  (deleted: NearMissPanel.tsx, ReadyToBreakPanel.tsx, EarlySignalPanel.tsx)
```

Validation:
- `python -m compileall backend middleware scripts` — clean
- `npm run build` in `frontend` — clean (726 kB main bundle, unchanged)
- End-to-end run not performed — corporate-firewall constraint stands

## 2026-07-04 (evening) — v3 soft-gate composite spine actually shipped

Follow-up to the morning documentation pivot. The full Wyckoff-VPA rewrite
(new `wyckoff.py` / `vsa.py` / `avwap.py` stage files) is still ahead; today
we shipped the **intermediate step** that unblocks picks immediately:

- **Soft-gate composite** — `backend/pipeline.py` now short-circuits only on
  `HARD_GATE_IDS = {U, I, HR}`. Every other stage always runs; failure just
  contributes 0 to the composite `S = Σ wᵢ · mᵢ`. Ends the "one missed
  sub-threshold kills the ticker" behavior that was rejecting ~all picks.
- **ACS + AC wired in** — `backend/stages/accum_screen.py` (tier-1 45-bar
  range+vol) and `backend/stages/accumulation.py` (tier-2 180-bar +
  ADI positive divergence) are now live in `PER_TICKER_CHAIN`. Previously
  dead code.
- **Live weight config** — `config/stage_weights.json` is the single control
  surface for `wᵢ` and the composite threshold `τ`. `pipeline.py` loads it
  at import; falls back to seed defaults if unreadable.
- **Champion-challenger tuner** — `scripts/tune_weights.py` reads
  `data/traces/outcomes.jsonl`, fits ridge + mean-return candidates, and
  **only overwrites the config if the candidate strictly beats the current
  champion's replay metric**. Monotone by construction: accuracy cannot
  regress.
- **`rank.py`** now computes confirmation from the same weighted composite,
  not just LT/CS/VD/BR.
- **Robustness fixes uncovered during first-run**:
  - `pipeline.py` crash handler now extracts `stage_id` from the module,
    so hard-gate crashes actually stop the chain.
  - `stages/ingest.py` catches `FileNotFoundError` from the bhavcopy
    resolver and returns a clean `[I]` failure with the .env fix in
    `reason` / `fix_point`.
  - `orchestrator.py` prints a loud diagnostic when ≥90% of the universe
    fails `[I]` (points at `.env` misconfig, not strategy).
  - `fetch.py` — `DEMO_MODE=1` now short-circuits the source dispatch, so
    one env var alone gets synthetic OHLCV. Previously required both
    `DEMO_MODE=1` AND `DATA_SOURCE=yahoo`.
- **`backend/.env.example`** — rewritten with `DEMO_MODE=1` first-run
  default (matches the corporate-firewall constraint in memory) and a
  `STOCKYA_OHLCV_DIR` pointer for user-populated caches.

Trace schema bumped to `SCHEMA_VERSION = 3`. Old v1 / v2 rows remain readable.

Validation:
- `python -m compileall backend middleware scripts` — clean.
- Import smoke: `pipeline.py`, `stages/__init__.py`, `orchestrator.py`,
  `stages/rank.py`, `scripts/tune_weights.py` — all resolve.
- `DEMO_MODE=1` fetch test — 252 bars synthetic OHLCV, no network.
- End-to-end run **not** performed (no live data on this machine).

## 2026-07-04 (morning)

**Strategy pivot to Wyckoff-VPA spine (documentation-only, code follows).**

- Retired the 5-serial-AND-gates chain
  (`LT → CS → VD → BR`). Under the old design, missing any single sub-threshold
  rejected the ticker; in volatile regimes this killed most otherwise-strong
  setups.
- New spine, per PRINCIPLES.md §2:
  - `[HR]` hard rejects  — binary safety gate (kept)
  - `[WY]` Wyckoff phase — **scored** (0-1) confidence in Phase C or Phase D
  - `[VSA]` bar confirmation — binary trigger, fires on ANY of SOS bar /
    pocket-pivot / no-supply test
  - `[AVWAP]` anchored VWAP hold — **scored** structural check
  - `[EX]` exit-watch — new daily scan on open picks (OBV divergence /
    churning / ≥3 distribution days / AVWAP break / climax reversal)
- Every volume-ratio and range threshold is now ATR20-normalized, with a
  per-day regime multiplier from realized vol. Fixed 1.5× / 4 % thresholds are
  gone.
- Position sizer switched to `stop = entry − max(0.08 × entry, 2 × ATR20)`;
  targets are 1R / 2R off that stop instead of fixed 8 % / 16 %.
- Holding period now explicitly documented as 3-6 months (matches T+90 / T+180
  outcome horizons).
- Documentation updated: `PRINCIPLES.md` (full rewrite), `ARCHITECTURE.md`
  (new §0-§0.3 top block; §0.4 onward marked as archival legacy),
  `PROCESS_FLOW.md`, `AGENT_HANDOFF.md`, `README.md`.
- **No stage code touched in this commit.** See AGENT_HANDOFF.md
  "Recommended Next Work" for the 10-step wire-up order.

Validation:
- Doc-only change; no build or compileall run.

## 2026-06-20

- Added contextual volume spike detection to classify latest EOD bars as:
  `bullish_ignition`, `early_accumulation`, `support_absorption`,
  `bearish_distribution`, `climax_warning`, or `neutral`.
- Exposed early volume indications in `/api/picks` as a separate watchlist-style
  list, distinct from official buy alerts.
- Added volume event details to selected pick payloads and stock detail volume
  analysis.
- Fed bearish distribution/climax events into active-position trajectory checks
  so they can trigger exit warnings earlier than slow long-term metrics.
- Added frontend display for early volume indications on the main page, pick
  cards, and stock detail cards.
- Bumped picks response schema to v3 and made `/api/picks` regenerate stale
  same-day cache files.
- Fixed two frontend TypeScript build blockers in `PriceSparkline` and
  `BacktestPage`.

Validation:
- `backend\.venv\Scripts\python.exe -m compileall backend middleware`
- `npm run build` from `frontend`
- Backend smoke checks for `volume_spike_event` and `VolumeEventDTO`
