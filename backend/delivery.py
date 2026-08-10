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
    STREAK_MIN_PCT           — band for the quiet-accumulation consecutive-day streak
    DRIFT_MIN_GAP            — per-rung gap for the slow-accumulation avg-stack drift
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

# Rolling-average windows, in available files (≈ trading days). The advisory
# reports delivery accumulation over: today (latest), week (SHORT_WIN), 15, 30.
SHORT_WIN: int = 5      # "week"
WIN_15: int = 15        # 15-day rolling mean
LONG_WIN: int = 20      # kept for the trend ratio + flow level (internal)
WIN_30: int = 30        # 30-day rolling mean — the longest window shown
MAX_WIN: int = 30       # files fetched/sliced so the 30-day mean is complete
TREND_RISING: float = 1.10
TREND_FALLING: float = 0.90

# Consecutive-day "quiet accumulation" streak — an additive INDICATOR, kept
# SCORING-NEUTRAL like everything else here (never feeds composite / rank /
# selection). Counts the most-recent run of days whose delivery% held at/above
# STREAK_MIN_PCT — i.e. above-normal delivery sustained day after day, which is
# the "someone is quietly taking shares to delivery" build the volume flow can't
# see from traded volume alone. Set just below the STRONG band so a genuine
# multi-day build registers without demanding a 60%+ print every single day.
STREAK_MIN_PCT: float = 55.0
STREAK_NOTE_MIN: int = 3      # only surface the streak in `note` at >= this many days

# Rolling-average-vs-rolling-average "slow accumulation" drift — additive INDICATOR,
# scoring-neutral. `trend` (avg_5d vs avg_20d) can trip on ONE recent spike; this
# instead needs the delivery-% STACK to step up across horizons — avg_5d >= avg_15d
# >= avg_30d with EACH rung >= DRIFT_MIN_GAP points. Requiring every rung to separate
# is what rejects a single spike (which lifts only the short average, leaving
# 15d ≈ 30d): it fires only on a broad, multi-week buildup — slow accumulation even on
# low, unspiky volume. Display-only: never feeds composite / rank / selection.
DRIFT_MIN_GAP: float = 2.0

# Normalized [0,1] "accumulation signal" — a single blendable summary of the
# delivery picture for the presentation-layer delivery-weighted section on the
# picks page. SCORING-NEUTRAL: the canonical composite never sees it; it exists
# only so the UI can tilt an already-selected pick's DISPLAY score. Blends the
# delivery LEVEL (dominant), the consecutive-day STREAK, and the multi-horizon
# DRIFT. None when delivery is unavailable so the UI applies no tilt.
SIGNAL_W_LEVEL: float = 0.40
SIGNAL_W_STREAK: float = 0.35
SIGNAL_W_DRIFT: float = 0.25
STREAK_FULL_DAYS: int = 5      # streak length that maps to a full 1.0 on the streak leg

# Rolling retention: keep enough MTO files that the 30-day (30-trading-day ≈ 42
# calendar-day) rolling mean is always complete, plus a small buffer.
RETENTION_DAYS: int = 45

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


def prune_old_files(keep_days: int = RETENTION_DAYS) -> int:
    """Delete MTO files with a trade date older than `keep_days` (rolling month).

    Files are independent per day, so this just unlinks the stale ones. Returns
    the count removed. Runs automatically at the end of every live fetch so the
    drop-zone stays bounded to ~a month.
    """
    files = _all_files()                      # [(iso, path)] ascending
    if not files:
        return 0
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    removed = 0
    for iso, p in files:
        if iso < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        log.info("delivery: pruned %d file(s) older than %d days", removed, keep_days)
    return removed


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


def fetch_and_cache_delivery(days_back: int = 55) -> list[str]:
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
    try:
        prune_old_files()          # keep the drop-zone to a rolling one-month window
    except Exception:
        log.warning("delivery prune failed (continuing)", exc_info=True)
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


def _streak_from_end(pcts: list[float], min_pct: float) -> int:
    """Length of the most-recent consecutive run with pct >= min_pct (0 if none).

    `pcts` is ascending by date, so the streak is counted from the tail — the
    latest days. A single day below the band breaks (resets) the streak.
    """
    n = 0
    for p in reversed(pcts):
        if p >= min_pct:
            n += 1
        else:
            break
    return n


def _accum_drift(short: Optional[float], mid: Optional[float],
                 longv: Optional[float], gap: float) -> Optional[str]:
    """Slow-accumulation drift from the delivery-% rolling-average STACK (5d/15d/30d).

    Needs all three windows. 'rising' only when EVERY adjacent rung steps up by
    >= gap (avg_5d − avg_15d AND avg_15d − avg_30d) — a broad, multi-horizon
    buildup. The gap-per-rung requirement is what rejects a single recent spike,
    which lifts only the short average and leaves mid ≈ long. 'falling' mirrors
    it; 'flat' otherwise; None when < 30 days (can't confirm a slow drift yet).
    """
    if short is None or mid is None or longv is None:
        return None
    if short - mid >= gap and mid - longv >= gap:
        return "rising"
    if mid - short >= gap and longv - mid >= gap:
        return "falling"
    return "flat"


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _accum_signal(latest_pct: Optional[float], streak_days: int,
                  drift: Optional[str]) -> Optional[float]:
    """Normalized [0,1] blendable delivery signal — level (dominant) + streak + drift.

    level : 0 at/below the WEAK band, 1 at/above the STRONG band, linear between.
    streak: streak_days / STREAK_FULL_DAYS, capped at 1.
    drift : rising=1.0, flat/None=0.5, falling=0.0.
    None when there is no delivery (latest_pct is None) so the UI applies no tilt.
    Presentation-only; never enters the composite / rank / selection.
    """
    if latest_pct is None:
        return None
    span = STRONG_DELIV_PCT - WEAK_DELIV_PCT
    level_c = _clamp01((latest_pct - WEAK_DELIV_PCT) / span) if span > 0 else 0.0
    streak_c = _clamp01(streak_days / STREAK_FULL_DAYS) if STREAK_FULL_DAYS > 0 else 0.0
    drift_c = {"rising": 1.0, "flat": 0.5, "falling": 0.0}.get(drift, 0.5)
    return round(_clamp01(
        SIGNAL_W_LEVEL * level_c + SIGNAL_W_STREAK * streak_c + SIGNAL_W_DRIFT * drift_c
    ), 3)


def _advisory_from_series(series: list[tuple[str, float]]) -> dict:
    """Build the advisory dict from a (date, pct) series (ascending). Pure.

    Shared by delivery_advisory (single symbol) and all_advisories (batch), so
    both produce byte-identical blocks from the same rolling-average math.
    """
    if not series:
        return {
            "available": False, "latest_pct": None, "latest_date": None,
            "avg_5d": None, "avg_15d": None, "avg_20d": None, "avg_30d": None,
            "trend": None, "level": None, "note": "", "days": 0,
            "accum_streak_days": 0, "accum_streak_min_pct": STREAK_MIN_PCT,
            "accum_drift": None, "accum_signal": None,
        }

    pcts = [p for _, p in series]
    latest_date, latest_pct = series[-1]
    avg_5d = _mean(pcts[-SHORT_WIN:])       # week
    avg_15d = _mean(pcts[-WIN_15:])
    avg_20d = _mean(pcts[-LONG_WIN:])       # internal (trend + flow level)
    avg_30d = _mean(pcts[-WIN_30:])

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
    roll = []
    if avg_5d is not None:
        roll.append(f"wk {avg_5d:.0f}%")
    if avg_15d is not None:
        roll.append(f"15d {avg_15d:.0f}%")
    if avg_30d is not None:
        roll.append(f"30d {avg_30d:.0f}%")
    roll_txt = f"; {', '.join(roll)}" if roll else ""
    accum_streak = _streak_from_end(pcts, STREAK_MIN_PCT)
    accum_drift = _accum_drift(avg_5d, avg_15d, avg_30d, DRIFT_MIN_GAP)
    accum_signal = _accum_signal(latest_pct, accum_streak, accum_drift)
    streak_txt = (
        f"; ≥{STREAK_MIN_PCT:.0f}% for {accum_streak}d (quiet accumulation)"
        if accum_streak >= STREAK_NOTE_MIN else ""
    )
    note = f"Delivery today {latest_pct:.0f}% ({level_txt}){roll_txt}{trend_txt}{streak_txt}"

    return {
        "available": True,
        "latest_pct": latest_pct,
        "latest_date": latest_date,
        "avg_5d": avg_5d,       # week
        "avg_15d": avg_15d,
        "avg_20d": avg_20d,     # internal (trend + flow level)
        "avg_30d": avg_30d,
        "trend": trend,
        "level": level,
        "note": note,
        "days": len(series),
        # Additive quiet-accumulation signals (display-only).
        "accum_streak_days": accum_streak,   # level persistence — sustained ≥ band
        "accum_streak_min_pct": STREAK_MIN_PCT,
        "accum_drift": accum_drift,          # slow multi-horizon buildup (spike-proof)
        "accum_signal": accum_signal,        # [0,1] blendable summary (presentation-only)
    }


def delivery_advisory(symbol: str) -> dict:
    """Advisory delivery block for a pick / position card. Never raises.

    Returns {available, latest_pct, latest_date, avg_5d, avg_20d, trend, level,
    note, days}. `available` is False (all fields None) when no files are on
    disk or the symbol never appears — the UI then simply hides the line.
    """
    return _advisory_from_series(delivery_series(symbol, n=MAX_WIN))


def delivery_streak(symbol: str, *, min_pct: float = STREAK_MIN_PCT) -> int:
    """Consecutive most-recent days `symbol`'s delivery% held >= `min_pct`.

    The 'quiet build' persistence signal — sustained above-normal delivery,
    i.e. someone taking shares to delivery day after day. Additive INDICATOR
    only, scoring-neutral like the rest of this module. 0 when no data / no
    streak. Convenience wrapper; the same value ships inside delivery_advisory.
    """
    return _streak_from_end(
        [p for _, p in delivery_series(symbol, n=MAX_WIN)], min_pct
    )


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
        f"{sym}.NS": _advisory_from_series(series[-MAX_WIN:])
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
