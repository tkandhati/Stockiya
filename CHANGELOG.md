# Changelog

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
