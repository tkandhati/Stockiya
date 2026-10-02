"""Turn-key, ephemeral NSE bhavcopy fetch for the OFFLINE data source.

Why this module exists
----------------------
Yahoo rate-limits by IP, so a ~300-ticker daily full-universe run 429s no matter
how hard ``yf_session`` throttles. The only durable fix is to stop calling Yahoo
per-ticker and read India-native NSE end-of-day data instead. NSE publishes ONE
"securities bhavdata full" CSV per trading day covering the WHOLE market (OHLCV
*plus* delivery quantity/percent), so the per-day cost is a single download, not
one-call-per-ticker — and NSE's archive host does not throttle a handful of
daily-file downloads the way Yahoo throttles quotes.

What it does (the owner's ask, 2026-10-02: "download and erase once it analyzes,
only the top names by market cap")
-------------------------------------------------------------------------------
1. DOWNLOAD only the daily bhavcopy files the pipeline's lookback needs, into a
   TEMP dir (``data/bhavcopy_raw``). These raw dumps are the bulky part (~1 GB
   over a full window) and are ALWAYS erased after the pivot — never hoarded.
2. PIVOT each daily file's EQ-series rows into per-symbol canonical OHLCV CSVs
   (``data/ohlcv/<SYMBOL>.csv``) + a delivery side-car (``data/delivery``), for
   ONLY the scan universe (``VOLUME_UNIVERSE`` ≈ top names by market cap), not
   the whole market.
3. ROLLING WINDOW: the tiny per-symbol CSVs (~10 MB total) are kept between runs
   and pruned to the lookback window, so each subsequent run only downloads the
   NEW trading days since the last run (typically one file) instead of re-pulling
   nine months of market data on every refresh. Set ``STOCKYA_BHAVCOPY_WIPE=1``
   to also erase the per-symbol CSVs after each run (true zero-footprint; every
   run then re-downloads the full window — slower, but nothing is kept).

Nothing here is destructive outside the three managed dirs under ``data/``, and
nothing raises into the pipeline: a failed/unreachable NSE download leaves the
cache as-is and the per-ticker ``[I] Ingest`` stage surfaces a clean "no OHLCV"
gate failure, exactly as a missing cache does today.

Env knobs (safe defaults; unset == default)
--------------------------------------------
  STOCKYA_OHLCV_DIR            per-symbol OHLCV cache dir   (default data/ohlcv)
  STOCKYA_BHAVCOPY_LOOKBACK_DAYS  calendar days of history  (default 400)
                              to maintain (floored at 320 so the 200d MA /
                              OBV-90d / ADV50 keep full lookback)
  STOCKYA_BHAVCOPY_WIPE       "1" => erase per-symbol CSVs  (default 0 = keep
                              after the run too (zero-footprint) rolling window)
  STOCKYA_BHAVCOPY_SLEEP      polite seconds between daily  (default 0.35)
                              file downloads
  STOCKYA_BHAVCOPY_MAX_DOWNLOADS  hard cap on files fetched (default 500)
                              in one ensure() call (runaway guard)

Self-test / manual run:
  backend\\.venv\\Scripts\\python.exe -m backend.bhavcopy_fetch selftest
  backend\\.venv\\Scripts\\python.exe -m backend.bhavcopy_fetch ensure 2026-10-02
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import date as _date, datetime, timedelta as _td
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

log = logging.getLogger("bhavcopy")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# NSE's "securities bhavdata full" daily file — OHLCV + delivery for the whole
# market. Stable URL format from mid-2020 onward; earlier dates 404 here.
_ARCHIVE_URL = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HDRS = {
    "User-Agent": _UA,
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# --------------------------------------------------------------------------- #
# Env helpers
# --------------------------------------------------------------------------- #
def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def lookback_days() -> int:
    """Calendar-day span of per-symbol history to maintain.

    Floored at 320 (~220 trading bars) so the pipeline's 200d MA / OBV-90d /
    ADV50 keep full lookback (see backend/stages/ingest.py MIN_BARS=200 /
    FULL_LOOKBACK_BARS=260). Default 400 gives ~275 bars with margin.
    """
    return max(320, _envi("STOCKYA_BHAVCOPY_LOOKBACK_DAYS", 400))


# --------------------------------------------------------------------------- #
# Managed directories (all under the git-ignored data/ tree)
# --------------------------------------------------------------------------- #
def ohlcv_dir() -> Path:
    """Per-symbol OHLCV cache dir — the SAME dir backend/fetch.py reads.

    Imported lazily from fetch so writer and reader can never disagree on the
    location. Falls back to <repo>/data/ohlcv if fetch can't be imported.
    """
    try:
        from .fetch import _bhavcopy_ohlcv_dir  # type: ignore
        return _bhavcopy_ohlcv_dir()
    except Exception:  # noqa: BLE001
        raw = os.environ.get("STOCKYA_OHLCV_DIR", str(_PROJECT_ROOT / "data" / "ohlcv"))
        return Path(raw).resolve()


def delivery_dir() -> Path:
    return (_PROJECT_ROOT / "data" / "delivery").resolve()


def _raw_dir() -> Path:
    """Temp dir for raw NSE daily dumps — always erased after a pivot."""
    return (_PROJECT_ROOT / "data" / "bhavcopy_raw").resolve()


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #
def _weekdays(start: _date, end: _date) -> Iterable[_date]:
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri; NSE holidays 404 and are skipped at fetch
            yield d
        d += _td(days=1)


def _raw_path(d: _date) -> Path:
    return _raw_dir() / f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _http_timeout() -> int:
    return max(3, _envi("STOCKYA_BHAVCOPY_TIMEOUT", 15))


def _http_get(url: str, timeout: Optional[int] = None) -> Optional[bytes]:
    """Return body bytes on 200, None on 404 (holiday), raise otherwise."""
    req = urllib.request.Request(url, headers=_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _http_timeout()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_days(days: list[_date]) -> dict:
    """Download the given trading days into the temp raw dir. 404 == holiday.

    Idempotent within a run — a day already on disk is skipped. Polite sleep
    between calls. Never raises on a single-day failure; counts it instead.

    Circuit breaker: a 404 is a holiday (expected, keeps going), but a run of
    consecutive CONNECTION failures means NSE is unreachable/blocked from this
    host — there is no point grinding through ~200 more requests at the socket
    timeout each, so we abort the rest fast. Tunable via
    STOCKYA_BHAVCOPY_MAX_CONSEC_FAIL (default 4).
    """
    sleep_s = max(0.0, _envf("STOCKYA_BHAVCOPY_SLEEP", 0.35))
    cap = max(1, _envi("STOCKYA_BHAVCOPY_MAX_DOWNLOADS", 500))
    max_consec = max(1, _envi("STOCKYA_BHAVCOPY_MAX_CONSEC_FAIL", 4))
    _raw_dir().mkdir(parents=True, exist_ok=True)
    got = holiday = fail = have = 0
    consec_fail = 0
    aborted = False
    for i, d in enumerate(days):
        if i >= cap:
            log.warning("bhavcopy: hit STOCKYA_BHAVCOPY_MAX_DOWNLOADS=%d — stopping early", cap)
            break
        out = _raw_path(d)
        if out.exists():
            have += 1
            continue
        url = _ARCHIVE_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
        try:
            body = _http_get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("bhavcopy: %s download FAILED: %s", d, e)
            fail += 1
            consec_fail += 1
            if consec_fail >= max_consec:
                log.error(
                    "bhavcopy: %d consecutive download failures — NSE appears "
                    "unreachable/blocked from this host; aborting the rest. "
                    "Check connectivity to nsearchives.nseindia.com, or set "
                    "DATA_SOURCE=yahoo / DEMO_MODE=1.", consec_fail,
                )
                aborted = True
                break
            time.sleep(sleep_s)
            continue
        consec_fail = 0
        if body is None:
            holiday += 1
        else:
            out.write_bytes(body)
            got += 1
        time.sleep(sleep_s)
    summary = {"downloaded": got, "already": have, "holidays": holiday,
               "failed": fail, "aborted": aborted}
    log.info("bhavcopy: download %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Pivot + merge (rolling window)
# --------------------------------------------------------------------------- #
def _normalize_symbol(sym: str) -> tuple[str, str]:
    """(bhavcopy_key, output_basename). Strip trailing '.NS' for the NSE match,
    keep the original form for the on-disk filename so RELIANCE.NS stays
    RELIANCE.NS.csv (aligns with backend/fetch.py:_resolve_bhavcopy_csv)."""
    sym = sym.strip()
    if sym.upper().endswith(".NS"):
        return sym[:-3].upper(), sym
    return sym.upper(), sym


def _read_raw(p: Path) -> pd.DataFrame:
    """Read one cached NSE daily CSV, tolerating NSE's leading-space headers."""
    df = pd.read_csv(p)
    df.columns = df.columns.str.strip()
    return df


def _emit_mto(d: _date, sub: pd.DataFrame) -> None:
    """Generate a daily ``MTO_DDMMYYYY.DAT`` from the bhavcopy EQ rows so the
    app-wide delivery reader (backend/delivery.py) works on the offline path —
    no separate NSE MTO download needed (bhavcopy already carries delivery).

    Record-20 format delivery.py parses: ``20,serial,SYMBOL,EQ,TRADED,DELIVERABLE,PCT``.
    Written for the scan universe only. Reversible: STOCKYA_BHAVCOPY_EMIT_MTO=0.
    """
    if not _envb("STOCKYA_BHAVCOPY_EMIT_MTO", True) or sub is None or sub.empty:
        return
    try:
        root = delivery_dir()
        root.mkdir(parents=True, exist_ok=True)
        out = root / f"MTO_{d.strftime('%d%m%Y')}.DAT"
        lines: list[str] = []
        serial = 1
        for _, r in sub.iterrows():
            traded = pd.to_numeric(r.get("TTL_TRD_QNTY"), errors="coerce")
            dq = pd.to_numeric(r.get("DELIV_QTY"), errors="coerce")
            dp = pd.to_numeric(r.get("DELIV_PER"), errors="coerce")
            if pd.isna(traded) or pd.isna(dq) or pd.isna(dp):
                continue
            lines.append(f"20,{serial},{r['SYMBOL']},EQ,{int(traded)},{int(dq)},{dp}")
            serial += 1
        if lines:
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 — delivery is advisory; never break the pivot
        log.debug("bhavcopy: MTO emit failed for %s", d, exc_info=True)


def download_deals() -> dict:
    """Pull the NSE bulk/block deal CSVs (institutional footprint used by the
    volume analysis) via the existing block_deals downloader. Best-effort — a
    failure/firewall just logs. Reversible: STOCKYA_BHAVCOPY_FETCH_DEALS=0."""
    if not _envb("STOCKYA_BHAVCOPY_FETCH_DEALS", True):
        return {"deals": "disabled"}
    try:
        from . import block_deals
        block_deals.fetch_and_cache_nse_deals()
        return {"deals": "ok"}
    except Exception as e:  # noqa: BLE001
        log.warning("bhavcopy: bulk/block deal download skipped: %s", e)
        return {"deals": "failed"}


def _read_existing_ohlcv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    try:
        df = pd.read_csv(path)
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception:  # noqa: BLE001 — a corrupt cache file must not break a run
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def latest_cached_date(symbols: list[str]) -> Optional[_date]:
    """Most recent Date present across the per-symbol OHLCV cache, or None.

    Used to decide the incremental download window — only days AFTER this need
    fetching. Reads a bounded sample for speed (the universe shares trading
    days, so any populated symbol's max date is representative).
    """
    root = ohlcv_dir()
    latest: Optional[_date] = None
    checked = 0
    for sym in symbols:
        _, out_name = _normalize_symbol(sym)
        p = root / f"{out_name}.csv"
        if not p.exists():
            continue
        try:
            tail = pd.read_csv(p, usecols=["Date"]).tail(1)
            if tail.empty:
                continue
            d = pd.Timestamp(tail["Date"].iloc[0]).date()
        except Exception:  # noqa: BLE001
            continue
        if latest is None or d > latest:
            latest = d
        checked += 1
        if checked >= 25:  # enough to establish the shared latest trading day
            break
    return latest


def pivot_and_merge(symbols: list[str], window_start: _date, today: _date) -> dict:
    """Pivot every cached raw daily file into per-symbol OHLCV + delivery CSVs,
    MERGING with any existing rows and PRUNING to [window_start, today].

    Every universe symbol's CSV is rewritten so stale rows (older than
    window_start) age out even when no new rows arrived — that is what keeps the
    footprint bounded to the rolling window.
    """
    pairs = [_normalize_symbol(s) for s in symbols]
    keys = {k for k, _ in pairs}
    key_to_out = {k: out for k, out in pairs}

    new_rows: dict[str, list[dict]] = {k: [] for k in keys}
    files_read = 0
    for d in _weekdays(window_start, today):
        p = _raw_path(d)
        if not p.exists():
            continue
        files_read += 1
        try:
            df = _read_raw(p)
        except Exception:  # noqa: BLE001
            continue
        if "SYMBOL" not in df.columns or "SERIES" not in df.columns:
            continue
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        sub = df[(df["SERIES"] == "EQ") & (df["SYMBOL"].isin(keys))]
        _emit_mto(d, sub)  # generated daily delivery file for the app-wide reader
        for _, r in sub.iterrows():
            new_rows[r["SYMBOL"]].append({
                "Date": d.isoformat(),
                "Open": pd.to_numeric(r.get("OPEN_PRICE"), errors="coerce"),
                "High": pd.to_numeric(r.get("HIGH_PRICE"), errors="coerce"),
                "Low": pd.to_numeric(r.get("LOW_PRICE"), errors="coerce"),
                "Close": pd.to_numeric(r.get("CLOSE_PRICE"), errors="coerce"),
                "Volume": pd.to_numeric(r.get("TTL_TRD_QNTY"), errors="coerce"),
                "DeliveryQty": pd.to_numeric(r.get("DELIV_QTY"), errors="coerce"),
                "DeliveryPct": pd.to_numeric(r.get("DELIV_PER"), errors="coerce"),
            })

    ohlcv_root = ohlcv_dir()
    deliv_root = delivery_dir()
    ohlcv_root.mkdir(parents=True, exist_ok=True)
    deliv_root.mkdir(parents=True, exist_ok=True)
    ws = pd.Timestamp(window_start)

    written = pruned = empty = 0
    for key, recs in new_rows.items():
        out_name = key_to_out[key]
        ohlcv_path = ohlcv_root / f"{out_name}.csv"
        existing = _read_existing_ohlcv(ohlcv_path)
        fresh = pd.DataFrame(recs) if recs else pd.DataFrame(columns=existing.columns)

        combined = pd.concat([existing, fresh], ignore_index=True, sort=False)
        if combined.empty or "Date" not in combined.columns:
            empty += 1
            continue
        combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
        combined = combined.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
        combined = combined.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
        before = len(combined)
        combined = combined[combined["Date"] >= ws]  # rolling-window prune
        pruned += before - len(combined)
        if combined.empty:
            empty += 1
            continue
        combined["Date"] = combined["Date"].dt.strftime("%Y-%m-%d")

        out = combined[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        out["Volume"] = out["Volume"].round().astype("int64")
        out.to_csv(ohlcv_path, index=False)
        written += 1

        if {"DeliveryQty", "DeliveryPct"}.issubset(combined.columns):
            deliv = combined[["Date", "DeliveryQty", "DeliveryPct"]].dropna(
                subset=["DeliveryQty", "DeliveryPct"], how="all"
            )
            if not deliv.empty:
                deliv.to_csv(deliv_root / f"{out_name}.csv", index=False)

    summary = {"files_read": files_read, "symbols_written": written,
               "rows_pruned": pruned, "symbols_empty": empty}
    log.info("bhavcopy: pivot %s", summary)
    return summary


# --------------------------------------------------------------------------- #
# Orchestration: ensure window present, then clean up
# --------------------------------------------------------------------------- #
def _erase_raw() -> None:
    """Delete the temp raw-dump dir (the bulky part). Always safe/idempotent."""
    try:
        shutil.rmtree(_raw_dir(), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _wipe_ohlcv(symbols: list[str]) -> None:
    """Zero-footprint mode: remove the per-symbol OHLCV + delivery CSVs too."""
    oroot, droot = ohlcv_dir(), delivery_dir()
    for sym in symbols:
        _, out_name = _normalize_symbol(sym)
        for p in (oroot / f"{out_name}.csv", droot / f"{out_name}.csv"):
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def ensure_universe_data(today_iso: Optional[str] = None,
                         symbols: Optional[list[str]] = None) -> dict:
    """Make the per-symbol OHLCV cache current for the lookback window.

    Downloads only the trading days missing since the last run (full backfill on
    an empty cache), pivots+merges them into the per-symbol CSVs, prunes to the
    rolling window, then ALWAYS erases the raw dumps. Returns a summary dict;
    never raises (a download failure leaves the cache untouched).
    """
    if symbols is None:
        from .universe import VOLUME_UNIVERSE
        symbols = list(VOLUME_UNIVERSE)

    today = _date.fromisoformat(today_iso) if today_iso else _date.today()
    window_start = today - _td(days=lookback_days())

    latest = latest_cached_date(symbols)
    # Download from the day AFTER the latest cached bar (incremental), or the
    # full window on a cold/stale cache.
    if latest is not None and latest >= window_start:
        dl_start = latest + _td(days=1)
    else:
        dl_start = window_start
    days = list(_weekdays(dl_start, today))

    log.info("bhavcopy: ensure window %s..%s (latest cached=%s, %d day(s) to fetch)",
             window_start.isoformat(), today.isoformat(),
             latest.isoformat() if latest else "none", len(days))

    dl = {"downloaded": 0, "already": 0, "holidays": 0, "failed": 0}
    piv: dict = {}
    deals: dict = {}
    try:
        if days:
            dl = download_days(days)
        piv = pivot_and_merge(symbols, window_start, today)
        deals = download_deals()  # bulk/block deals — same offline cycle
    finally:
        _erase_raw()  # the bulky raw dumps never persist between runs
    return {"window_start": window_start.isoformat(), "today": today.isoformat(),
            "download": dl, "pivot": piv, "deals": deals}


def cleanup_after_run(symbols: Optional[list[str]] = None,
                      today_iso: Optional[str] = None) -> None:
    """Post-analysis cleanup: enforce the central archive/retention policy
    (backend/data_archive.py) across every downloaded + generated file, then —
    in WIPE mode — erase the per-symbol CSVs too (zero-footprint)."""
    _erase_raw()
    try:
        from . import data_archive
        data_archive.apply(today_iso)
    except Exception:  # noqa: BLE001
        log.exception("bhavcopy: data_archive.apply failed")
    if _envb("STOCKYA_BHAVCOPY_WIPE", False):
        if symbols is None:
            from .universe import VOLUME_UNIVERSE
            symbols = list(VOLUME_UNIVERSE)
        _wipe_ohlcv(symbols)
        log.info("bhavcopy: STOCKYA_BHAVCOPY_WIPE=1 — per-symbol OHLCV cache erased.")


class offline_session:
    """Context manager: ensure the offline data window before a run, clean up
    after. Used by middleware/picks.py so a Refresh is a true download→analyze→
    erase cycle. Safe to enter even if NSE is unreachable (ensure never raises).
    """

    def __init__(self, today_iso: Optional[str] = None):
        self.today_iso = today_iso
        self.symbols: Optional[list[str]] = None

    def __enter__(self) -> "offline_session":
        try:
            from .universe import VOLUME_UNIVERSE
            self.symbols = list(VOLUME_UNIVERSE)
        except Exception:  # noqa: BLE001
            self.symbols = None
        try:
            ensure_universe_data(self.today_iso, self.symbols)
        except Exception:  # noqa: BLE001
            log.exception("bhavcopy: ensure_universe_data failed — pipeline will "
                          "surface a clean 'no OHLCV' gate failure where data is missing")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            cleanup_after_run(self.symbols, self.today_iso)
        except Exception:  # noqa: BLE001
            log.exception("bhavcopy: cleanup_after_run failed")


# --------------------------------------------------------------------------- #
# CLI / self-test
# --------------------------------------------------------------------------- #
def selftest() -> dict:
    """Fetch ONE recent trading day and report what NSE returned. Never raises.

    Run this on the machine that will serve picks — its output tells you whether
    the NSE archive is reachable and whether the daily file parses.
    """
    report: dict = {"ohlcv_dir": str(ohlcv_dir()), "lookback_days": lookback_days()}
    # Walk back from yesterday to the most recent weekday with a published file.
    probe = _date.today() - _td(days=1)
    for _ in range(7):
        if probe.weekday() < 5:
            url = _ARCHIVE_URL.format(ddmmyyyy=probe.strftime("%d%m%Y"))
            try:
                body = _http_get(url)
            except Exception as e:  # noqa: BLE001
                report["error"] = f"{type(e).__name__}: {e}"
                report["probe_date"] = probe.isoformat()
                return report
            if body is not None:
                report["probe_date"] = probe.isoformat()
                report["bytes"] = len(body)
                try:
                    from io import BytesIO
                    df = pd.read_csv(BytesIO(body))
                    df.columns = df.columns.str.strip()
                    report["columns"] = list(df.columns)[:10]
                    report["rows"] = int(len(df))
                    report["has_delivery"] = "DELIV_PER" in df.columns
                except Exception as e:  # noqa: BLE001
                    report["parse_error"] = f"{type(e).__name__}: {e}"
                return report
        probe -= _td(days=1)
    report["error"] = "no published bhavcopy found in the last 7 days"
    return report


def _main() -> None:
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:]
    cmd = args[0] if args else "selftest"
    if cmd == "selftest":
        print(json.dumps(selftest(), indent=2, default=str))
    elif cmd == "ensure":
        today = args[1] if len(args) > 1 else None
        print(json.dumps(ensure_universe_data(today), indent=2, default=str))
    elif cmd == "wipe":
        from .universe import VOLUME_UNIVERSE
        _erase_raw()
        _wipe_ohlcv(list(VOLUME_UNIVERSE))
        print("erased raw dumps + per-symbol OHLCV/delivery cache")
    else:
        print("usage: python -m backend.bhavcopy_fetch [selftest|ensure [YYYY-MM-DD]|wipe]")


if __name__ == "__main__":
    _main()
