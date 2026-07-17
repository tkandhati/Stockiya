# test_data/

**Manual drop-zone for real NSE historical OHLCV CSVs.**

The corporate firewall blocks Yahoo/NSE live fetches, so any pipeline testing
must run against CSVs pasted here **by the user**.

> **No synthetic data.** Claude will not generate, fabricate, or fill in bars.
> If a stage needs data for a ticker and this folder is empty, Claude must
> ask you to paste the CSV first.

## Filename convention

One CSV per ticker. Filename = NSE symbol, exactly as it appears in
`config/nifty100.txt` (no `.NS` suffix, no exchange prefix):

```
test_data/
├── RELIANCE.csv
├── HDFCBANK.csv
└── INFY.csv
```

## Expected columns

The raw NSE "Historical Data" download for an equity has this header
(tab- or comma-separated, headers case-sensitive):

```
DATE, SERIES, OPEN, HIGH, LOW, PREV. CLOSE, LTP, CLOSE, VWAP, 52W H, 52W L, VOLUME, VALUE, NO. OF TRADES
```

| Column          | Used by pipeline | Notes |
|---|---|---|
| `DATE`          | yes | `DD-MMM-YYYY` on NSE (e.g. `02-Jan-2026`) — loader normalizes to `YYYY-MM-DD` |
| `SERIES`        | filter | Keep only `EQ` rows |
| `OPEN`          | yes | |
| `HIGH`          | yes | |
| `LOW`           | yes | |
| `PREV. CLOSE`   | — | Ignored |
| `LTP`           | — | Ignored |
| `CLOSE`         | yes | Unadjusted; adjust for splits/bonuses separately if needed |
| `VWAP`          | — | Ignored (pipeline computes anchored VWAP itself) |
| `52W H`         | — | Ignored |
| `52W L`         | — | Ignored |
| `VOLUME`        | yes | May contain commas from NSE — loader strips them |
| `VALUE`         | — | Ignored |
| `NO. OF TRADES` | — | Ignored |

Paste the file exactly as downloaded from NSE — the loader handles the noise.

## How much history

The pipeline reads a **180-bar** slice (see `backend/stages/ingest.py`), so
paste **at least ~260 trading days** so 200d MA, OBV-90d, and ADV50 have full
lookback with margin.

## Not tracked in git

CSVs pasted here may be large and are yours — add file-level entries to
`.gitignore` if you don't want them versioned. This folder is a fixture drop,
not a shared dataset.
