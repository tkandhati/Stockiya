# Agent Handoff

Last updated: 2026-06-20

## Current Architecture Truth

Stockiya is currently a deterministic, volume-first Nifty 100 screener. The
active engine is the gates-based spine, not the older weighted-composite model
still described in parts of the older docs.

Live flow:

`Regime -> Universe -> Ingest -> Hard Rejects -> Long-Term Flow -> Consolidation -> Volume/Divergence -> Breakout -> Rank -> Hypothesis/Position Sizing -> Render`

Primary source files:
- `backend/orchestrator.py`
- `backend/pipeline.py`
- `backend/stages/__init__.py`
- `backend/stages/*.py`
- `frontend/src/App.tsx`
- `middleware/main.py`

## Latest Change

Added an early contextual volume-spike layer. It is intentionally separate from
official buy alerts:

- Bullish rows mean watch for follow-through.
- Bearish rows mean distribution, climax, exit-risk, or avoid warning.
- A volume spike alone is not enough; classification uses volume ratio, prior
  quietness, close location, support/resistance, extension from 50d MA, 30d
  return, and OBV slope.

New reusable backend function:

- `backend/indicators.py:volume_spike_event`

New response fields:

- `PicksResponse.early_signals`
- `Pick.volume_event`
- `Accumulation.volume_event`

New UI component:

- `frontend/src/components/EarlySignalPanel.tsx`

## Notes For Next Agent

- Treat `PRINCIPLES.md` and the code as fresher than older README architecture
  sections.
- `data/` files in the repo are stale demo artifacts; do not infer current
  market state from them.
- `DATA_SOURCE=bhavcopy` is still a stub. Live data is Yahoo unless
  `DEMO_MODE=1`.
- Build is currently green after this change.

## Validation Already Run

- Python compile: `backend\.venv\Scripts\python.exe -m compileall backend middleware`
- Frontend build: `npm run build` in `frontend`
- Smoke checks:
  - `volume_spike_event(demo_ohlcv(...)).as_dict()`
  - `VolumeEventDTO(**event.as_dict())`

## Recommended Next Work

- Add a historical backtest report bucket for early volume events:
  compare `early_accumulation` and `bullish_ignition` outcomes separately.
- Consider a dedicated `/api/early-signals` endpoint if the watchlist grows.
- Migrate production data toward NSE bhavcopy before trusting results.
