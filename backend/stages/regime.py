"""[RG] Market Regime gate.

Runs ONCE per day at the start of the orchestrator — NOT per ticker. Halts
all per-ticker work if the configured index benchmark closes below its
50-day moving average. The master switch from PRINCIPLES Section 2.

Default: NIFTY 100 (^CNX100) — aligned with our Nifty 100 trading universe.

Fix points:
    REGIME_TICKERS : indices that gate the day (tuple, all must pass)
    MA_PERIOD      : moving-average window (default 50)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Optional

import pandas as pd

from ..indicators import sma
from ..yahoo import history_ohlcv


# --------------------------------------------------------------------------- #
# Tunable constants
# --------------------------------------------------------------------------- #

REGIME_TICKERS: tuple[str, ...] = ("^CNX100",)  # tunable — match Nifty 100 universe
MA_PERIOD: int = 50                                       # tunable


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #

@dataclass
class RegimeIndexCheck:
    symbol: str
    close: Optional[float]
    ma50: Optional[float]
    gap_pct: Optional[float]   # (close/ma - 1) * 100
    passed: bool
    reason: str


@dataclass
class RegimeStatus:
    passed: bool
    checks: list[RegimeIndexCheck] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {
                    "symbol": c.symbol,
                    "close": c.close,
                    "ma50": c.ma50,
                    "gap_pct": c.gap_pct,
                    "passed": c.passed,
                    "reason": c.reason,
                }
                for c in self.checks
            ],
        }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _slice_to_as_of(df: pd.DataFrame, as_of: _date) -> pd.DataFrame:
    """Drop bars dated after as_of. Safe with tz-aware or tz-naive indices."""
    cutoff = pd.Timestamp(as_of)
    try:
        idx = df.index.normalize()
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        return df[idx <= cutoff]
    except Exception:
        return df[[ts.date() <= as_of for ts in df.index]]


def check_regime(as_of: Optional[str] = None) -> RegimeStatus:
    """Fetch each regime index and verify last close > MA_PERIOD-day MA.

    Live (`as_of=None`): uses today's close.
    Backtest (`as_of="YYYY-MM-DD"`): fetches the historical window and slices
    to bars <= as_of, so the regime decision matches what would have been
    known on that historical EOD.

    Conservative: if ANY index fails fetch, has missing data, or closes at
    or below its MA, the regime is HALTED. We never assume an unknown-state
    market is safe.

    DEMO_MODE=1 short-circuits with an auto-pass; demo fixtures don't include
    the index tickers, and forcing the demo run to permanently halt is not
    useful for end-to-end testing.
    """
    if os.environ.get("DEMO_MODE", "0") == "1":
        return RegimeStatus(
            passed=True,
            checks=[
                RegimeIndexCheck(
                    symbol=sym, close=100.0, ma50=95.0, gap_pct=5.26,
                    passed=True, reason="DEMO_MODE auto-pass (real check skipped)",
                )
                for sym in REGIME_TICKERS
            ],
            summary=(
                f"Regime ON — DEMO_MODE auto-pass ({', '.join(REGIME_TICKERS)})"
            ),
        )

    as_of_date: Optional[_date] = None
    if as_of:
        try:
            as_of_date = _date.fromisoformat(as_of)
        except ValueError:
            as_of_date = None

    checks: list[RegimeIndexCheck] = []
    all_pass = True

    for sym in REGIME_TICKERS:
        try:
            df = history_ohlcv(sym, end=as_of) if as_of_date else history_ohlcv(sym)
        except Exception as e:
            checks.append(RegimeIndexCheck(sym, None, None, None, False, f"fetch error: {e}"))
            all_pass = False
            continue

        if df is None or df.empty or "Close" not in df.columns:
            checks.append(RegimeIndexCheck(sym, None, None, None, False, "no data"))
            all_pass = False
            continue

        if as_of_date is not None:
            df = _slice_to_as_of(df, as_of_date)
            if df.empty:
                checks.append(RegimeIndexCheck(
                    sym, None, None, None, False,
                    f"no bars at or before as_of={as_of}",
                ))
                all_pass = False
                continue

        ma = sma(df["Close"], MA_PERIOD)
        if ma is None:
            checks.append(RegimeIndexCheck(
                sym, None, None, None, False,
                f"insufficient history (<{MA_PERIOD} bars)",
            ))
            all_pass = False
            continue

        close = float(df["Close"].iloc[-1])
        gap_pct = (close / ma - 1) * 100 if ma > 0 else 0.0
        passed = close > ma
        as_of_suffix = f" · as-of {as_of}" if as_of_date else ""
        reason = (
            f"close {close:.2f} {'>' if passed else '<='} {MA_PERIOD}d MA {ma:.2f} "
            f"({gap_pct:+.2f}%){as_of_suffix}"
        )
        checks.append(RegimeIndexCheck(sym, close, ma, gap_pct, passed, reason))
        if not passed:
            all_pass = False

    if all_pass:
        passing = ", ".join(c.symbol for c in checks)
        summary = f"Regime ON — {passing} all above {MA_PERIOD}d MA"
    else:
        failed = ", ".join(c.symbol for c in checks if not c.passed) or "all"
        summary = (
            f"Regime HALTED — {failed} at/below {MA_PERIOD}d MA "
            f"(or unavailable). No buy alerts will issue today."
        )

    return RegimeStatus(passed=all_pass, checks=checks, summary=summary)
