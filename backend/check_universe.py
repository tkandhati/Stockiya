"""Universe sanity check — find dead / stale tickers in the Nifty 100 list.

Run:
    python -m backend.check_universe

For each ticker in `backend/universe.py:UNIVERSE`, fetches 1y of OHLCV and
classifies into:

    ok         : >= 200 bars, last bar within 7 days
    low_bars   : <  200 bars (likely recent listing / split mid-year)
    stale      : last bar older than 7 days (Yahoo lost the symbol)
    empty/404  : yfinance returned nothing or errored

Use the report to edit `universe.py` when NSE corporate actions render a
ticker unmappable on Yahoo (e.g. TATAMOTORS demerger, LTIM rename).
"""

from __future__ import annotations

import sys
import time
from datetime import date
from typing import Optional

from .universe import UNIVERSE
from .yahoo import history_ohlcv


# Sleep between sequential fetches to stay below Yahoo's per-host rate limit.
# 0.3 s × 100 tickers = 30 s of throttle on top of the actual fetch time.
_THROTTLE_SECS = 0.3


def _last_bar_date(df) -> Optional[date]:
    try:
        last_idx = df.index[-1]
        if hasattr(last_idx, "date"):
            return last_idx.date()
        return last_idx  # already a date
    except Exception:
        return None


def check_universe(min_bars: int = 200, stale_days: int = 7) -> dict:
    """Walk the universe and bucket each ticker by data health."""
    today = date.today()
    out: dict[str, list] = {"ok": [], "low_bars": [], "stale": [], "empty": []}
    total = len(UNIVERSE)

    for i, sym in enumerate(UNIVERSE, start=1):
        sys.stderr.write(f"\r[{i}/{total}] checking {sym:25}")
        sys.stderr.flush()
        if i > 1:
            time.sleep(_THROTTLE_SECS)
        try:
            df = history_ohlcv(sym)
        except Exception as e:
            out["empty"].append((sym, f"fetch error: {e}"))
            continue

        if df is None or df.empty:
            out["empty"].append((sym, "empty (likely 404 / delisted)"))
            continue

        bars = len(df)
        last_date = _last_bar_date(df)

        if bars < min_bars:
            out["low_bars"].append(
                (sym, f"{bars} bars (<{min_bars}) — too short for 200d MA")
            )
            continue

        if last_date is not None and (today - last_date).days > stale_days:
            out["stale"].append(
                (sym, f"last bar {last_date.isoformat()} ({(today - last_date).days}d old)")
            )
            continue

        out["ok"].append(sym)

    sys.stderr.write("\n")
    return out


def _print_report(report: dict) -> None:
    total = sum(len(v) for v in report.values())
    print()
    print("=" * 64)
    print(f"  Universe health report  ({total} tickers)")
    print("=" * 64)
    print(f"  ok        : {len(report['ok']):>3}")
    print(f"  low_bars  : {len(report['low_bars']):>3}")
    print(f"  stale     : {len(report['stale']):>3}")
    print(f"  empty/404 : {len(report['empty']):>3}")

    for category in ("empty", "stale", "low_bars"):
        if not report[category]:
            continue
        print()
        print(f"--- {category} ({len(report[category])}) ---")
        for sym, reason in report[category]:
            print(f"  {sym:25} {reason}")

    bad_total = len(report["empty"]) + len(report["stale"]) + len(report["low_bars"])
    if bad_total:
        print()
        print("Action: edit backend/universe.py to remove or replace the symbols above.")
        print("        For NSE-listed companies, try alternate Yahoo suffixes (.BO for BSE)")
        print("        or look up the new symbol after a corporate action.")
    else:
        print("\nAll tickers healthy.")


if __name__ == "__main__":
    report = check_universe()
    _print_report(report)
