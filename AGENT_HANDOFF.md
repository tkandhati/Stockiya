# Agent Handoff

Last updated: 2026-07-04

## Current Architecture Truth

Stockiya is a deterministic, volume-only Nifty 100 screener for a **3-6 month
swing hold**. The **design spec** is the Wyckoff-VPA spine described in
PRINCIPLES.md and ARCHITECTURE.md §0-§0.3. The **code as of this commit** is
still running the earlier 5-serial-gates chain — the pivot has been documented
but not yet wired.

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

### Live code (still on the old spine)

```
Regime -> Universe -> Ingest -> Hard Rejects -> Long-Term Flow -> Consolidation -> Volume/Divergence -> Breakout -> Rank -> Hypothesis/Position Sizing -> Render
```

Primary source files:
- `backend/orchestrator.py`
- `backend/pipeline.py`
- `backend/stages/__init__.py`
- `backend/stages/*.py`
- `frontend/src/App.tsx`
- `middleware/main.py`

---

## Latest Change (2026-07-04)

**Documentation-only pivot to the Wyckoff-VPA spine.** No stage code was
touched in this pass. The rewrite is limited to:

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

1. **Extract shared primitives** from `stages/accum_screen.py` and
   `stages/accumulation.py` into `backend/indicators.py`:
   `range_pct_window`, `vol_dryness_ratio`, `adi`, `norm_slope` are already
   there; verify no divergence between the two callers.
2. **Write `backend/stages/wyckoff.py`** implementing the Phase A→D classifier
   from PRINCIPLES §2.1. Return a `StageResult` with `score` = phase
   confidence × phase-preference weight.
3. **Write `backend/stages/vsa.py`** as the new trigger with three
   sub-rules (SOS / pocket-pivot / no-supply). `pocket_pivot` primitive
   already lives in `rank.py:_check_pocket_pivot_today` — lift it to
   `indicators.py`.
4. **Write `backend/stages/avwap.py`** — one new indicator
   `anchored_vwap(df, anchor_idx)` + a scored stage.
5. **Wire `PER_TICKER_CHAIN`** in `stages/__init__.py`:
   `[universe, ingest, hard_rejects, wyckoff, vsa, avwap]`. Remove
   `lt_flow`, `consolidation`, `volume`, `breakout` from the chain (leave
   files in place for one release cycle).
6. **Update `rank.py`** so the confirmation formula reads
   `wy_score + avwap_score + vsa_margin + 0.5 × bonus_count`.
7. **Write `backend/stages/exit_watch.py`** and add a call site in
   `backend/nightly.py` (17:15 IST slot, after the picks-render step).
8. **Bump `SCHEMA_VERSION` to 3** in `pipeline.py` and add a
   `spine: "wyckoff-vpa"` field to the FINAL trace row so the RL buffer can
   distinguish v2 (gates) vs v3 (Wyckoff-VPA) decisions.
9. **Frontend:** repoint `EarlySignalPanel.tsx` to consume the new
   `[EX]` output instead of / in addition to `volume_spike_event`.
10. **Backtest:** run the tuner over the last 12 months with the new spine
    and compare pick-count / hit-rate against the archived gates-spine run.

## Validation Already Run (this commit)

- Only doc edits. No code compiled, no build re-run.
- Doc consistency: cross-checked stage IDs between PRINCIPLES.md,
  ARCHITECTURE.md §0.1, PROCESS_FLOW.md §3, and this file.
