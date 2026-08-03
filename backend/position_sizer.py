"""Position sizing — the risk math.

Pure function. Given an account value and an entry price, returns the
exact shares to buy, stop level, T1, and T2 so the trade strictly risks
1 % of the account with an 8 % hard stop.

Per PRINCIPLES Section 3:
    risk_per_trade = account * 0.01
    stop  = entry * 0.92            (-8 %)
    shares = floor(risk / (entry - stop))
    T1    = entry * 1.08            (+8 %, = 1R)
    T2    = entry * 1.16            (+16 %, = 2R)

Sell-50 % ladder: half exits at T1 with stop raised to entry on the
remainder; the second half exits at T2. Worst case after T1 hits is -4 %
on the trade, half the spec maximum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
# Tunable constants — every dial is a one-line edit
# --------------------------------------------------------------------------- #

ACCOUNT_RISK_PCT: float = 0.01       # tunable — 1 % of account per trade
STOP_PCT: float = 0.08                # tunable — 8 % hard-stop FLOOR below entry
T1_PCT: float = 0.08                  # tunable — fixed-mode first target (= 1R at 8%)
T2_PCT: float = 0.16                  # tunable — fixed-mode second target (= 2R at 8%)
LADDER_T1_FRACTION: float = 0.50      # tunable — fraction sold at T1

# Volatility-adaptive stop (PRINCIPLES §3): stop = max(STOP_PCT, ATR_STOP_MULT ×
# ATR20/close). Only engaged when the caller supplies `atr_pct` (a FRACTION, e.g.
# 0.025 for a 2.5% ATR); otherwise sizing is the fixed-8% model, byte-identical to
# before. When engaged, the target ladder is expressed in R (= entry − stop):
# T1 = entry + T1_R_MULT·R, T2 = entry + T2_R_MULT·R, so targets scale with the
# stock's own volatility instead of a flat 8/16 %.
ATR_STOP_MULT: float = 2.0            # tunable — stop = 2 × ATR20 when wider than the floor
T1_R_MULT: float = 1.0                # tunable — first target in R
T2_R_MULT: float = 2.0                # tunable — second target in R


@dataclass
class PositionPlan:
    account_value: float
    entry: float
    stop: float
    t1: float
    t2: float
    shares_total: int
    shares_at_t1: int
    shares_at_t2: int
    risk_amount: float            # realised currency at risk (shares * per_share_risk)
    risk_pct_of_account: float    # realised fraction of account
    notes: list[str]

    def as_dict(self) -> dict:
        return {
            "account_value": round(self.account_value, 2),
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "t1": round(self.t1, 2),
            "t2": round(self.t2, 2),
            "shares_total": self.shares_total,
            "shares_at_t1": self.shares_at_t1,
            "shares_at_t2": self.shares_at_t2,
            "risk_amount": round(self.risk_amount, 2),
            "risk_pct_of_account": round(self.risk_pct_of_account * 100, 3),
            "notes": list(self.notes),
        }


def size_position(
    account_value: float,
    entry: float,
    *,
    risk_pct: float = ACCOUNT_RISK_PCT,
    stop_pct: float = STOP_PCT,
    t1_pct: float = T1_PCT,
    t2_pct: float = T2_PCT,
    ladder_t1_fraction: float = LADDER_T1_FRACTION,
    atr_pct: Optional[float] = None,
    atr_stop_mult: float = ATR_STOP_MULT,
    t1_r_mult: float = T1_R_MULT,
    t2_r_mult: float = T2_R_MULT,
) -> PositionPlan:
    """Compute the position plan for one trade.

    Returns a `PositionPlan` even if shares come out to 0 (so callers can
    still log the attempt). Validate `shares_total > 0` before placing any
    real order — a zero-share plan means the stock is too expensive for the
    risk budget.

    `atr_pct` (a FRACTION, e.g. 0.025) engages the volatility-adaptive stop
    (PRINCIPLES §3): stop = max(stop_pct, atr_stop_mult × atr_pct) below entry,
    and the target ladder becomes R-based (T1 = entry + t1_r_mult·R, T2 =
    entry + t2_r_mult·R, R = entry − stop). Left as None (the default), sizing is
    the fixed model — stop = entry·(1−stop_pct), T1/T2 = entry·(1+t1_pct/t2_pct)
    — byte-identical to the pre-2026-08-03 behaviour, so every existing caller
    and test is unaffected.
    """
    notes: list[str] = []

    if account_value <= 0 or entry <= 0:
        return PositionPlan(
            account_value=account_value, entry=entry,
            stop=0.0, t1=0.0, t2=0.0,
            shares_total=0, shares_at_t1=0, shares_at_t2=0,
            risk_amount=0.0, risk_pct_of_account=0.0,
            notes=["invalid account_value or entry"],
        )

    if atr_pct is not None and atr_pct > 0:
        # Volatility-adaptive: widen the stop to the ATR floor when the stock is
        # more volatile than the flat 8%, then express targets in R so they scale
        # with the stock's own risk unit (§3).
        eff_stop_pct = max(stop_pct, atr_stop_mult * atr_pct)
        stop = entry * (1 - eff_stop_pct)
        r = entry - stop
        t1 = entry + t1_r_mult * r
        t2 = entry + t2_r_mult * r
        if eff_stop_pct > stop_pct + 1e-9:
            notes.append(
                f"volatility-adaptive stop {eff_stop_pct*100:.1f}% "
                f"(= {atr_stop_mult:.1f} × ATR {atr_pct*100:.1f}%), R-based ladder"
            )
    else:
        stop = entry * (1 - stop_pct)
        t1 = entry * (1 + t1_pct)
        t2 = entry * (1 + t2_pct)

    per_share_risk = entry - stop
    if per_share_risk <= 0:
        return PositionPlan(
            account_value=account_value, entry=entry, stop=stop, t1=t1, t2=t2,
            shares_total=0, shares_at_t1=0, shares_at_t2=0,
            risk_amount=0.0, risk_pct_of_account=0.0,
            notes=["zero per-share risk; check stop_pct"],
        )

    risk_budget = account_value * risk_pct
    shares_total = int(math.floor(risk_budget / per_share_risk))

    if shares_total == 0:
        notes.append(
            f"computed shares = 0 — entry {entry:.2f} too high vs account "
            f"{account_value:.0f} at {risk_pct*100:.2f}% risk"
        )

    shares_at_t1 = int(round(shares_total * ladder_t1_fraction))
    shares_at_t2 = shares_total - shares_at_t1

    # Realised risk is slightly under the budget due to integer rounding;
    # never over.
    realised_risk = shares_total * per_share_risk
    realised_risk_pct = (
        realised_risk / account_value if account_value > 0 else 0.0
    )

    if shares_total > 0:
        notes.append(
            f"buy {shares_total} shares at {entry:.2f}; "
            f"sell {shares_at_t1} at T1 {t1:.2f}, "
            f"{shares_at_t2} at T2 {t2:.2f}; "
            f"stop {stop:.2f}"
        )
        notes.append(
            f"risk: {realised_risk:.2f} = {realised_risk_pct*100:.2f}% of account "
            f"(target {risk_pct*100:.2f}%)"
        )

    return PositionPlan(
        account_value=account_value,
        entry=entry,
        stop=stop,
        t1=t1,
        t2=t2,
        shares_total=shares_total,
        shares_at_t1=shares_at_t1,
        shares_at_t2=shares_at_t2,
        risk_amount=realised_risk,
        risk_pct_of_account=realised_risk_pct,
        notes=notes,
    )
