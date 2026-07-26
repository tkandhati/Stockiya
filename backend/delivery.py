"""NSE delivery-% loader — the accumulation-vs-churn discriminator.

Delivery % = deliverable qty / traded qty. A volume spike at high delivery is
real accumulation (shares taken to delivery and held); the same spike at low
delivery is intraday churn. Yahoo carries traded volume only — this is the
NSE-published number that volume alone can't give us.

SOURCE (manual drop, offline — same pattern as OHLCV from the user's laptop):
NSE "Security-wise Delivery Position" / MTO files, dropped into `data/delivery/`.
The firewall blocks fetching them, so this module NEVER fetches — it only reads
files that are already on disk, and degrades to "unavailable" when they aren't.

FILE FORMAT (record-type-20 rows; headers optional — we skip anything that
isn't a 7-field type-20 row):

    20, <srno>, <SYMBOL>, <SERIES>, <traded qty>, <deliverable qty>, <% delivery>
    20, 229,    ABB,      EQ,       258917,       108928,            42.07

We keep SERIES == EQ only and match `ABB` to our universe's `ABB.NS`.

FILE NAMING: name each file with its trade date so we can order them without
reading contents, e.g. `delivery_2026-07-24.csv` (recommended). We also accept
`MTO_DDMMYYYY.DAT`, and bare DDMMYYYY / DDMMYY / YYYY-MM-DD anywhere in the name;
failing that we read the date from a `10,MTO,DDMMYYYY,...` or `Trade Date <...>`
header line if present.

Fix points:
    _DELIVERY_DIR            — drop-zone
    STRONG/WEAK_DELIV_PCT    — absolute-level bands
    SHORT_WIN / LONG_WIN     — trend windows
    TREND_RISING/FALLING     — trend ratio thresholds
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from .datekeys import date_from_filename, iso_from_filename

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DELIVERY_DIR = _PROJECT_ROOT / "data" / "delivery"

# Absolute delivery-% bands (tunable). NSE convention: >60% strong hands,
# <40% mostly intraday churn.
STRONG_DELIV_PCT: float = 60.0
WEAK_DELIV_PCT: float = 40.0

# Trend windows (in available files) and ratio thresholds. Short vs long mean.
SHORT_WIN: int = 5
LONG_WIN: int = 20
TREND_RISING: float = 1.10
TREND_FALLING: float = 0.90

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _norm(symbol: str) -> str:
    """Universe symbol (`ABB.NS`) -> NSE name (`ABB`)."""
    return symbol.split(".")[0].strip().upper()


def _date_from_header(lines: list[str]) -> Optional[str]:
    """Fallback: read the trade date from the file's own header lines."""
    for ln in lines[:5]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) >= 3 and parts[0] == "10" and re.fullmatch(r"\d{8}", parts[2] or ""):
            d = date_from_filename(parts[2])
            if d:
                return d.isoformat()
        m = re.search(r"<(\d{2})-([A-Za-z]{3})-(\d{4})>", ln)
        if m:
            mon = _MONTHS.get(m.group(2).upper())
            if mon:
                try:
                    return date(int(m.group(3)), mon, int(m.group(1))).isoformat()
                except ValueError:
                    pass
    return None


def _parse_rows(lines: list[str]) -> dict[str, dict]:
    """{SYMBOL: {traded, deliverable, deliv_pct}} for EQ-series type-20 rows."""
    out: dict[str, dict] = {}
    for ln in lines:
        parts = [p.strip() for p in ln.split(",")]
        # data rows: record type 20, exactly 7 fields, EQ series.
        if len(parts) != 7 or parts[0] != "20":
            continue
        symbol, series = parts[2].upper(), parts[3].upper()
        if series != "EQ":
            continue
        try:
            traded = int(parts[4])
            deliverable = int(parts[5])
            pct = float(parts[6])
        except (TypeError, ValueError):
            continue
        out[symbol] = {"traded": traded, "deliverable": deliverable, "deliv_pct": pct}
    return out


def _file_date(path: Path, lines: list[str]) -> Optional[str]:
    return _date_from_name(path.name) or _date_from_header(lines)


def _all_files() -> list[tuple[str, Path]]:
    """[(iso_date, path)] for every delivery file on disk, ascending by date."""
    if not _DELIVERY_DIR.exists():
        return []
    dated: list[tuple[str, Path]] = []
    for p in _DELIVERY_DIR.iterdir():
        if not p.is_file():
            continue
        d = iso_from_filename(p.name)
        if d is None:
            try:
                d = _date_from_header(p.read_text(encoding="utf-8", errors="ignore").splitlines()[:5])
            except OSError:
                d = None
        if d:
            dated.append((d, p))
    dated.sort(key=lambda t: t[0])
    return dated


def available_dates() -> list[str]:
    """Delivery trade dates on disk, newest first."""
    return sorted({d for d, _ in _all_files()}, reverse=True)


def delivery_series(symbol: str, *, n: int = LONG_WIN) -> list[tuple[str, float]]:
    """Most-recent `n` (date, delivery_pct) for `symbol`, ascending by date."""
    sym = _norm(symbol)
    files = _all_files()[-n:]
    series: list[tuple[str, float]] = []
    for d, p in files:
        try:
            rows = _parse_rows(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
        row = rows.get(sym)
        if row is not None:
            series.append((d, row["deliv_pct"]))
    return series


def _mean(vals: list[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if vals else None


def delivery_advisory(symbol: str) -> dict:
    """Advisory delivery block for a pick / position card. Never raises.

    Returns {available, latest_pct, latest_date, avg_5d, avg_20d, trend, level,
    note, days}. `available` is False (all fields None) when no files are on
    disk or the symbol never appears — the UI then simply hides the line.
    """
    series = delivery_series(symbol, n=LONG_WIN)
    if not series:
        return {
            "available": False, "latest_pct": None, "latest_date": None,
            "avg_5d": None, "avg_20d": None, "trend": None, "level": None,
            "note": "", "days": 0,
        }

    pcts = [p for _, p in series]
    latest_date, latest_pct = series[-1]
    avg_5d = _mean(pcts[-SHORT_WIN:])
    avg_20d = _mean(pcts)

    trend: Optional[str] = None
    if avg_5d is not None and avg_20d and avg_20d > 0:
        ratio = avg_5d / avg_20d
        trend = "rising" if ratio >= TREND_RISING else "falling" if ratio <= TREND_FALLING else "flat"

    level = ("strong" if latest_pct >= STRONG_DELIV_PCT
             else "weak" if latest_pct < WEAK_DELIV_PCT else "moderate")

    level_txt = {
        "strong": "strong hands taking delivery",
        "moderate": "mixed delivery",
        "weak": "mostly intraday churn",
    }[level]
    trend_txt = {"rising": ", rising", "falling": ", fading", "flat": "", None: ""}[trend]
    note = f"Delivery {latest_pct:.0f}% ({level_txt}){trend_txt}"

    return {
        "available": True,
        "latest_pct": latest_pct,
        "latest_date": latest_date,
        "avg_5d": avg_5d,
        "avg_20d": avg_20d,
        "trend": trend,
        "level": level,
        "note": note,
        "days": len(series),
    }
