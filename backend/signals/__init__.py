"""Pure-math indicator library — the computation engine the stages read from.

This package is a thin facade over `backend/volume_signals.py`, which contains
the canonical implementations of OBV, CMF, MFI, A/D line, VWAP, MAs, slopes,
pocket-pivot, VDU, CAN SLIM, Wyckoff-lite, Weinstein stage, Minervini template,
and the composite AccumulationSignals dataclass.

Stages call `compute(ohlcv, symbol)` once per ticker per day and then slice
features out of the returned AccumulationSignals object — no recomputation in
stages, no math duplication.

To replace the entire math layer (e.g. switch from yfinance-derived OBV to a
true block-level OBV), replace `volume_signals.compute()` and keep the
AccumulationSignals shape — stages and the orchestrator do not change.
"""

from ..volume_signals import (  # noqa: F401
    AccumulationSignals,
    StrategySignal,
    TargetWindow,
    compute,
    suggest_target_window,
)
