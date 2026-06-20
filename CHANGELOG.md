# Changelog

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
