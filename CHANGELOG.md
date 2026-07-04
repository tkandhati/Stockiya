# Changelog

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
