"""Per-run data-health summary — how many of the scan universe actually
ingested cleanly, and why the rest didn't.

Motivation
----------
The orchestrator already computes an ``ingest_failed`` count, but only to fire
the ">=90% failed => data source misconfigured" alarm (backend/orchestrator.py).
For any failure rate *below* 90% the per-ticker outcome was discarded: a
transient yfinance timeout on, say, 30 of ~750 tickers left no durable trace.
Those 30 silently dropped out of consideration and nobody was notified.

This module turns each ticker's ``[I]`` Ingest ``StageResult`` into a durable,
JSON-serializable report that is written into the picks file and the per-day
summary. It answers the operator's question directly: *"of the N I scanned,
how many produced a complete indicator set, and where did the rest go?"*

Read-only: it never influences selection, scoring, ranking, or exits. Pure and
deterministic — no I/O, no clock, no network.
"""
from __future__ import annotations

from typing import Optional

# Ingest failure reason strings are produced by backend/stages/ingest.py.
# Map each to a stable bucket (matched case-insensitively on a substring so a
# reason-text tweak upstream degrades to "other" rather than crashing).
_FAIL_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("network",        ("fetch failed",)),                 # yfinance raised
    ("empty",          ("no ohlcv from data source",)),    # yfinance returned nothing
    ("missing_source", ("data source missing ohlcv",)),    # bhavcopy cache absent
    ("no_price",       ("no current price",)),             # snapshot had no price
    ("no_bars_asof",   ("no bars at or before",)),         # backtest slice empty
    ("short_history",  ("only ", "bars, need")),           # < MIN_BARS (legit exclusion)
)


def classify_ingest_failure(reason: Optional[str]) -> str:
    """Bucket an ``[I]`` Ingest failure reason string. Unknown -> ``"other"``."""
    r = (reason or "").strip().lower()
    if not r:
        return "other"
    for bucket, needles in _FAIL_BUCKETS:
        if any(n in r for n in needles):
            return bucket
    return "other"


def summarize_data_health(
    results: list,
    attempted: int,
    *,
    max_symbols: int = 40,
) -> dict:
    """Build the data-health block from the per-ticker pipeline results.

    Parameters
    ----------
    results     : list of PipelineResult (whatever the pool returned — futures
                  that raised were dropped by the orchestrator, so this can be
                  shorter than ``attempted``).
    attempted   : number of tickers submitted to the pool (len of the universe).
    max_symbols : cap on symbols listed per failure bucket (keeps the JSON small).

    ``short_history`` is a legitimate exclusion (the ticker is simply too new to
    score), so it is reported but excluded from ``silent_failures`` — the number
    that represents genuinely lost coverage the operator should worry about.
    """
    results = list(results or [])
    returned = len(results)
    crashed = max(0, attempted - returned)  # futures that raised & were dropped

    ingested_ok = 0
    failed_by_reason: dict[str, int] = {}
    failed_symbols: dict[str, list[str]] = {}

    for r in results:
        try:
            sr = r.stage_results.get("I")
        except Exception:
            sr = None
        if sr is not None and getattr(sr, "passed", False):
            ingested_ok += 1
            continue
        reason = (
            getattr(sr, "reason", None)
            if sr is not None
            else "no ingest stage result"
        )
        bucket = classify_ingest_failure(reason)
        failed_by_reason[bucket] = failed_by_reason.get(bucket, 0) + 1
        sym = getattr(r, "symbol", None) or "?"
        bucket_syms = failed_symbols.setdefault(bucket, [])
        if len(bucket_syms) < max_symbols:
            bucket_syms.append(sym)

    if crashed:
        failed_by_reason["crashed"] = failed_by_reason.get("crashed", 0) + crashed

    failed_total = returned - ingested_ok + crashed
    silent_failures = sum(
        v for k, v in failed_by_reason.items() if k != "short_history"
    )

    return {
        "attempted": attempted,
        "results_returned": returned,
        "crashed": crashed,
        "ingested_ok": ingested_ok,
        "failed_total": failed_total,
        "silent_failures": silent_failures,   # excludes the benign short_history bucket
        "failed_by_reason": failed_by_reason,
        "failed_symbols": failed_symbols,
        "coverage_pct": (
            round(100.0 * ingested_ok / attempted, 1) if attempted else None
        ),
    }
