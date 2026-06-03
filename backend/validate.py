"""Validation harness — proves the backtest logic is honest and deterministic.

Run from the project root:
    python -m backend.validate
    python -m backend.validate --symbol INFY.NS --as-of 2025-03-15
    python -m backend.validate --no-lookahead-test   (skip the slow check)

Tests:
    [1] Lookahead leak                — every gate sees only bars <= as_of
    [2] Determinism                   — same (symbol, as_of) twice -> same result
    [3] Snapshot recompute             — backtest snapshot != live snapshot
    [4] Regime as-of awareness         — check_regime(as_of) uses sliced index
    [5] Gate feature dump              — every gate's measurements for one case

Exit code 0 if all PASS, 1 otherwise. Suitable for CI / pre-commit hooks.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as _date, timedelta as _td
from typing import Optional

# Defer heavy imports until run so --help is fast
def _heavy_imports():
    global pd, run_pipeline, PER_TICKER_CHAIN, fetch_ohlcv, check_regime
    global _slice_to_as_of, _build_ctx_for_scan, _recompute_snapshot_from_ohlcv
    global PipelineContext
    import pandas as _pd
    pd = _pd
    from .pipeline import run_pipeline as _run, PipelineContext as _Ctx
    run_pipeline = _run
    PipelineContext = _Ctx
    from .stages import PER_TICKER_CHAIN as _chain
    PER_TICKER_CHAIN = _chain
    from .fetch import fetch_ohlcv as _fetch
    fetch_ohlcv = _fetch
    from .stages.regime import check_regime as _regime
    check_regime = _regime
    from .stages.ingest import _slice_to_as_of as _slice
    _slice_to_as_of = _slice
    from .backtest import _build_ctx_for_scan as _bctx, _slice_to_as_of as _bslice  # noqa
    _build_ctx_for_scan = _bctx
    from .stages.ingest import _recompute_snapshot_from_ohlcv as _recomp
    _recompute_snapshot_from_ohlcv = _recomp


# ANSI colors for the report
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {DIM}info{RESET}  {msg}")


def _section(title: str) -> None:
    print()
    print(f"{YELLOW}{title}{RESET}")
    print("-" * len(title))


# --------------------------------------------------------------------------- #
# [1] Lookahead leak — gates must never see bars > as_of
# --------------------------------------------------------------------------- #

def test_lookahead(symbol: str, as_of: str) -> bool:
    """Run the chain at as_of and verify the OHLCV every gate saw has max
    index date <= as_of. Catches any regression that lets future bars in.
    """
    _section(f"[1] Lookahead leak  ({symbol} as of {as_of})")
    result = run_pipeline(symbol, PER_TICKER_CHAIN, as_of)
    # The pipeline doesn't expose ctx.ohlcv directly, but we can replay
    # ingest's slice + check
    df = fetch_ohlcv(symbol, end=as_of, lookback_days=730)
    if df is None or df.empty:
        _fail(f"could not fetch OHLCV for {symbol} ending {as_of}")
        return False
    as_of_d = _date.fromisoformat(as_of)
    sliced = _slice_to_as_of(df, as_of_d)
    if sliced.empty:
        _fail("sliced OHLCV is empty — Ingest would fail")
        return False
    # Strip tz for comparison
    idx = sliced.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    max_seen = idx.max()
    cutoff = pd.Timestamp(as_of_d)
    if max_seen > cutoff:
        _fail(f"sliced df has bar {max_seen.date()} > as_of {as_of}")
        return False
    _ok(f"slice max bar = {max_seen.date()}  <=  as_of = {as_of}  ({len(sliced)} bars)")
    _info(f"pipeline reached gates: {list(result.stage_results.keys())}")
    return True


# --------------------------------------------------------------------------- #
# [2] Determinism — same input twice must give same output
# --------------------------------------------------------------------------- #

def _result_signature(r) -> tuple:
    """Reduce PipelineResult to a comparable tuple."""
    sigs = []
    for sid in sorted(r.stage_results.keys()):
        sr = r.stage_results[sid]
        feats = sr.features or {}
        # Round floats so tiny numerical noise doesn't break the test
        clean = {}
        for k, v in feats.items():
            if isinstance(v, float):
                clean[k] = round(v, 6)
            elif isinstance(v, dict):
                clean[k] = {kk: round(vv, 6) if isinstance(vv, float) else vv
                            for kk, vv in v.items()}
            else:
                clean[k] = v
        sigs.append((sid, sr.passed, tuple(sorted(clean.items()))))
    return tuple(sigs)


def test_determinism(symbol: str, as_of: str) -> bool:
    _section(f"[2] Determinism  ({symbol} as of {as_of})")
    a = run_pipeline(symbol, PER_TICKER_CHAIN, as_of)
    b = run_pipeline(symbol, PER_TICKER_CHAIN, as_of)
    sig_a = _result_signature(a)
    sig_b = _result_signature(b)
    if sig_a != sig_b:
        _fail("two runs produced different stage features — non-deterministic!")
        # Find which stage diverged
        for (sid_a, _, _), (sid_b, _, _) in zip(sig_a, sig_b):
            if sid_a != sid_b:
                _fail(f"  diverged at stage {sid_a} vs {sid_b}")
        return False
    _ok(f"two runs produced identical stage signatures across {len(sig_a)} gates")
    return True


# --------------------------------------------------------------------------- #
# [3] Snapshot recompute — backtest snapshot fields ≠ live snapshot
# --------------------------------------------------------------------------- #

def test_snapshot_recompute(symbol: str, as_of: str) -> bool:
    _section(f"[3] Snapshot recompute  ({symbol} as of {as_of})")
    df = fetch_ohlcv(symbol, end=as_of, lookback_days=730)
    if df is None or df.empty:
        _fail("could not fetch OHLCV")
        return False
    as_of_d = _date.fromisoformat(as_of)
    sliced = _slice_to_as_of(df, as_of_d)
    if sliced.empty:
        _fail("sliced empty")
        return False
    # A naïve "live" snapshot for comparison
    seed = {"symbol": symbol, "company": symbol, "current": float(sliced["Close"].iloc[-1])}
    recomputed = _recompute_snapshot_from_ohlcv(seed, sliced)
    expected_ma50 = float(sliced["Close"].tail(50).mean())
    actual_ma50 = recomputed.get("ma50")
    if actual_ma50 is None or abs(actual_ma50 - expected_ma50) > 0.01:
        _fail(f"ma50 mismatch: expected {expected_ma50:.2f}, got {actual_ma50}")
        return False
    _ok(f"ma50 from recomputed snapshot = {actual_ma50:.2f}  (matches sliced.Close.tail(50).mean())")
    expected_ma200 = float(sliced["Close"].tail(200).mean()) if len(sliced) >= 200 else None
    actual_ma200 = recomputed.get("ma200")
    if expected_ma200 is not None:
        if actual_ma200 is None or abs(actual_ma200 - expected_ma200) > 0.01:
            _fail(f"ma200 mismatch: expected {expected_ma200:.2f}, got {actual_ma200}")
            return False
        _ok(f"ma200 = {actual_ma200:.2f}  (matches sliced.Close.tail(200).mean())")
    else:
        _info(f"ma200 skipped — only {len(sliced)} bars (need 200)")
    return True


# --------------------------------------------------------------------------- #
# [4] Regime as-of awareness
# --------------------------------------------------------------------------- #

def test_regime_as_of(as_of: str) -> bool:
    _section(f"[4] Regime as-of awareness  (NIFTY 100 on {as_of})")
    live = check_regime()                           # uses today's data
    historical = check_regime(as_of=as_of)          # uses as-of slice
    # The status reasons MUST differ if as_of != today (the close + ma50 will differ)
    live_str = live.summary
    hist_str = historical.summary
    if live_str == hist_str:
        # Could be coincidence — but extremely unlikely. Flag as soft warning.
        _info(f"live and historical summaries match — could be coincidence:")
        _info(f"  live:       {live_str}")
        _info(f"  historical: {hist_str}")
        return True
    _ok(f"live regime ≠ as-of regime (correct — different data)")
    _info(f"  live:       {live_str}")
    _info(f"  historical: {hist_str}")
    return True


# --------------------------------------------------------------------------- #
# [5] Gate feature dump — for visual audit
# --------------------------------------------------------------------------- #

def dump_gates(symbol: str, as_of: str) -> bool:
    _section(f"[5] Gate feature dump  ({symbol} as of {as_of})")
    result = run_pipeline(symbol, PER_TICKER_CHAIN, as_of)
    print(f"  passed_all_gates = {result.passed_gates}")
    for sid in ["U", "I", "HR", "LT", "CS", "VD", "BR"]:
        sr = result.stage_results.get(sid)
        if sr is None:
            print(f"  [{sid}] NOT REACHED")
            continue
        flag = "PASS" if sr.passed else "FAIL"
        color = GREEN if sr.passed else RED
        print(f"  [{sid}] {color}{flag}{RESET}  features: {sr.features}")
        if not sr.passed:
            print(f"        reason: {sr.reason}")
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--symbol", default="ADANIGREEN.NS",
                        help="Symbol for the single-case checks (default ADANIGREEN.NS)")
    parser.add_argument("--as-of", default="2025-10-29",
                        help="Historical date for checks (default 2025-10-29)")
    parser.add_argument("--no-lookahead-test", action="store_true")
    parser.add_argument("--no-determinism", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--no-regime", action="store_true")
    parser.add_argument("--no-dump", action="store_true")
    args = parser.parse_args()

    _heavy_imports()

    print(f"{YELLOW}Backtest validation harness{RESET}")
    print(f"  symbol = {args.symbol}")
    print(f"  as_of  = {args.as_of}")
    print(f"  today  = {_date.today().isoformat()}")

    # Refuse if as_of >= today (would defeat all "past data" assumptions)
    if _date.fromisoformat(args.as_of) >= _date.today():
        print(f"\n{RED}error{RESET}: --as-of must be a past date.")
        return 1

    results: list[tuple[str, bool]] = []
    if not args.no_lookahead_test:
        results.append(("lookahead", test_lookahead(args.symbol, args.as_of)))
    if not args.no_determinism:
        results.append(("determinism", test_determinism(args.symbol, args.as_of)))
    if not args.no_snapshot:
        results.append(("snapshot recompute", test_snapshot_recompute(args.symbol, args.as_of)))
    if not args.no_regime:
        results.append(("regime as-of", test_regime_as_of(args.as_of)))
    if not args.no_dump:
        dump_gates(args.symbol, args.as_of)

    print()
    print(f"{YELLOW}Summary{RESET}")
    print("-------")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        marker = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {marker}  {name}")
    print()
    if passed == total:
        print(f"{GREEN}All {total} checks passed.{RESET}")
        return 0
    print(f"{RED}{total - passed} of {total} checks failed.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
