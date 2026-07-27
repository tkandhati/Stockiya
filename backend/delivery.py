"""NSE delivery-% loader — the accumulation-vs-churn discriminator.

Delivery % = deliverable qty / traded qty. A volume spike at high delivery is
real accumulation (shares taken to delivery and held); the same spike at low
delivery is intraday churn. Yahoo carries traded volume only — this is the
NSE-published number that volume alone can't give us.

SOURCE: NSE "Security-wise Delivery Position" / MTO files in `data/delivery/`.
Two ways they get there, both offline-safe:
  1. `fetch_and_cache_delivery()` — best-effort per-day MTO download, mirroring
     block_deals.fetch_and_cache_nse_deals. Behind the corporate firewall it
     quietly no-ops; run it where NSE is reachable and copy `data/delivery/`
     across (same pattern as OHLCV/deals from the user's laptop).
  2. Manual drop of MTO/`delivery_*` files into `data/delivery/`.
The ADVISORY READER below (`delivery_advisory` et al.) is strictly file-only —
it never touches the network and degrades to "unavailable" when no files exist.

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

import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .datekeys import date_from_filename, iso_from_filename

log = logging.getLogger("delivery")

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


# --------------------------------------------------------------------------- #
# Live fetch — NSE MTO (delivery) files into the local drop-zone.
#
# SAME pattern as block_deals.fetch_and_cache_nse_deals: best-effort urllib,
# guarded so a firewall/404/holiday never breaks anything. Behind a firewall
# that blocks NSE this simply logs and no-ops; run it where NSE is reachable
# (the user's personal laptop) and hand-copy data/delivery/ across — the exact
# workflow already used for OHLCV and deals.
#
# UNLIKE block/bulk (2 fixed rolling URLs), delivery is a PER-DAY file with the
# date IN the URL, so we backfill a window of recent weekdays. Each file lands
# as delivery_<ISO>.csv, which the file-only reader above already picks up with
# no parser change.
# --------------------------------------------------------------------------- #

# NSE MTO archive URL (subject to change, like the deals URLs). DDMMYYYY.
NSE_MTO_URL = "https://archives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Below this many bytes the response is treated as a holiday/empty placeholder,
# not a real MTO file (a genuine EQ-universe MTO is tens of KB).
_MIN_MTO_BYTES = 2000


def fetch_and_cache_delivery(days_back: int = 40) -> list[str]:
    """Download recent NSE MTO delivery files into `data/delivery/`.

    Best-effort and idempotent: skips DEMO_MODE, skips dates already on disk,
    and treats a 404 / tiny body (market holiday, no file published) as a quiet
    skip — NOT an error. Returns the list of ISO dates newly fetched.

    Run daily (wired into nightly.py) alongside the block/bulk refresh. The
    file-only `delivery_advisory` reader consumes whatever lands here.
    """
    if os.environ.get("DEMO_MODE", "0") == "1":
        log.info("DEMO_MODE=1 — skipping NSE delivery (MTO) download")
        return []

    import urllib.request

    _DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
    have = set(available_dates())
    fetched: list[str] = []
    today = date.today()

    for i in range(1, max(1, days_back) + 1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:        # skip Sat/Sun — NSE publishes no MTO
            continue
        iso = d.isoformat()
        if iso in have:
            continue
        dest = _DELIVERY_DIR / f"delivery_{iso}.csv"
        if dest.exists():
            continue
        url = NSE_MTO_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
        except Exception as e:  # noqa: BLE001 — 404 on holidays is normal
            log.debug("delivery: no file for %s (%s)", iso, e)
            continue
        if not body or len(body) < _MIN_MTO_BYTES:
            log.debug("delivery: %s returned %d bytes — likely holiday, skipping",
                      iso, len(body or b""))
            continue
        dest.write_bytes(body)
        fetched.append(iso)
        log.info("delivery: downloaded %s (%d bytes)", dest.name, len(body))

    if fetched:
        log.info("delivery: fetched %d new file(s): %s",
                 len(fetched), ", ".join(fetched))
    return fetched


def delivery_corpus_status() -> dict:
    """Load-status of the delivery drop-zone — for logging + the health probe.

    Returns {available, days, latest_date, oldest_date, symbols_latest}. Cheap:
    counts dated files and parses only the most-recent one for a symbol count.
    """
    files = _all_files()
    if not files:
        return {
            "available": False, "days": 0, "latest_date": None,
            "oldest_date": None, "symbols_latest": 0,
        }
    oldest_date = files[0][0]
    latest_date, latest_path = files[-1]
    symbols_latest = 0
    try:
        rows = _parse_rows(
            latest_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        )
        symbols_latest = len(rows)
    except OSError:
        symbols_latest = 0
    return {
        "available": True,
        "days": len(files),
        "latest_date": latest_date,
        "oldest_date": oldest_date,
        "symbols_latest": symbols_latest,
    }


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


def _advisory_from_series(series: list[tuple[str, float]]) -> dict:
    """Build the advisory dict from a (date, pct) series (ascending). Pure.

    Shared by delivery_advisory (single symbol) and all_advisories (batch), so
    both produce byte-identical blocks from the same rolling-average math.
    """
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


def delivery_advisory(symbol: str) -> dict:
    """Advisory delivery block for a pick / position card. Never raises.

    Returns {available, latest_pct, latest_date, avg_5d, avg_20d, trend, level,
    note, days}. `available` is False (all fields None) when no files are on
    disk or the symbol never appears — the UI then simply hides the line.
    """
    return _advisory_from_series(delivery_series(symbol, n=LONG_WIN))


def all_advisories() -> dict[str, dict]:
    """Batch: {'SYM.NS': advisory} for EVERY symbol present, in ONE corpus load.

    Parses each delivery file once (not once-per-symbol) — the presentation
    layer needs advisories across many symbols and per-symbol reads would parse
    every file N times. Keys use the universe's `.NS` suffix so callers match
    the rest of the system. Empty dict when no files are on disk.
    """
    series_by_sym: dict[str, list[tuple[str, float]]] = {}
    for d, p in _all_files():          # ascending by date
        try:
            rows = _parse_rows(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
        for sym, row in rows.items():
            series_by_sym.setdefault(sym, []).append((d, row["deliv_pct"]))
    return {
        f"{sym}.NS": _advisory_from_series(series[-LONG_WIN:])
        for sym, series in series_by_sym.items()
    }


def latest_market_pcts() -> list[float]:
    """Cross-section of delivery % on the most-recent date — the market baseline
    for percentile ('against the normal') comparisons. One file parse; [] if none."""
    files = _all_files()
    if not files:
        return []
    _, latest = files[-1]
    try:
        rows = _parse_rows(latest.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return []
    return [r["deliv_pct"] for r in rows.values()]


if __name__ == "__main__":
    # Manual fetch + status:  python -m backend.delivery
    # (No-op behind a firewall — run where NSE is reachable, then copy
    #  data/delivery/ across, same as OHLCV/deals.)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    fetch_and_cache_delivery()
    print(delivery_corpus_status())
