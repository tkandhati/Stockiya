"""Stockiya pipeline contracts + orchestrator.

The whole engine is a chain of stages with one shared signature:

    def run(ctx: PipelineContext) -> StageResult

Gate stages set `passed=False` to drop a ticker; scoring stages emit a
`score` in [0.0, 1.0] that is later combined with a fixed weight.

Run order (volume-only, deterministic, no LLM):

    [U] Universe ─► [I] Ingest ─► [HR] Hard Rejects ─►
    [LT] LongTerm (50%) ─► [TT] Trend Template (15%) ─►
    [MT] MidTerm (20%) ─► [DD] Direct Deals (10%) ─► [BR] Breakouts (5%) ─►
    [S] Score & Rank ─► [H] Hypothesis+Exit ─► [R] Render ─► UI
                                                            │
                                                            └─► [O] Outcome
                                                                  feeds RL

Every stage's result is appended to a JSONL trace under data/traces/. That
file is the RL dataset — paired later with outcome rows from stage [O].
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"
_TRACES_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("pipeline")


# --------------------------------------------------------------------------- #
# Trace schema version — bumped to 2 for the gates-based spine.
# v1 rows from the old weighted-composite spine remain readable.
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = 2

# --------------------------------------------------------------------------- #
# Stage weights — DEPRECATED. Retained so the old per-ticker chain still runs
# during the rebuild. The new spine (regime → CS → VD → BR → rank) uses hard
# gates and a confirmation-strength ranker, not weighted composites.
# --------------------------------------------------------------------------- #

STAGE_WEIGHTS: dict[str, float] = {
    "LT": 0.50,   # Long-term volume — primary signal
    "TT": 0.15,   # Trend template — structure check
    "MT": 0.20,   # Mid-term volume — today's confirmation
    "DD": 0.10,   # Direct deals (NSE block/bulk) — named institutional trades
    "BR": 0.05,   # Breakout triggers — timing
}
assert abs(sum(STAGE_WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"


# --------------------------------------------------------------------------- #
# Shared contract — every stage emits this shape.
# --------------------------------------------------------------------------- #

@dataclass
class StageResult:
    stage_id: str                      # "U", "I", "HR", "LT", "TT", "MT", "DD", "BR"
    passed: bool                       # gate decision (True for non-gates)
    score: float = 0.0                 # 0.0–1.0 for scoring stages; 0 for gates
    features: dict = field(default_factory=dict)   # raw measurements
    evidence: list[str] = field(default_factory=list)   # human-readable bullets
    fix_point: str = ""                # "stages/lt_volume.py:thresholds.CMF_60D"
    reason: str = ""                   # one-line why (especially for failed gates)
    elapsed_ms: int = 0


@dataclass
class PipelineContext:
    """Mutable per-ticker context threaded through every stage."""
    symbol: str
    trace_id: str
    today_iso: str
    ohlcv: Optional[pd.DataFrame] = None    # filled by [I] Ingest
    snapshot: dict = field(default_factory=dict)   # current price + headline
    signals: Any = None                     # AccumulationSignals, filled by [I]
    stage_results: dict[str, StageResult] = field(default_factory=dict)
    composite_score: float = 0.0            # DEPRECATED (old spine) — 0–100 from [S]
    selected: bool = False                  # filled by [S] / [RK]
    rank: Optional[int] = None              # filled by [S] / [RK]
    pick_payload: dict = field(default_factory=dict)  # filled by [H]/[R]
    # --- New gates-based spine additions (orchestrator-populated) ---
    regime_passed: Optional[bool] = None    # set by orchestrator before per-ticker run
    account_value: float = 100000.0         # for [PS] Position Sizer; default 1 lakh
    confirmation_score: float = 0.0         # filled by [RK]
    confirmation_components: dict = field(default_factory=dict)  # margin + bonuses, filled by [RK]
    # Backtest-only: per-request overrides for high-control gate thresholds.
    # Stages read `ctx.overrides.get("<key>", CANONICAL_DEFAULT)`. Empty in live.
    overrides: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Per-ticker outcome of a single pipeline run."""
    symbol: str
    trace_id: str
    passed_gates: bool                 # cleared all gate stages in the chain
    composite_score: float             # DEPRECATED (old spine) — 0–100
    selected: bool
    rank: Optional[int]
    stage_results: dict[str, StageResult]
    pick_payload: dict
    snapshot: dict = field(default_factory=dict)   # carried over from ingest
    signals: Any = None                # AccumulationSignals from ingest
    # --- New gates-based spine additions ---
    confirmation_score: float = 0.0    # filled by [RK]
    confirmation_components: dict = field(default_factory=dict)
    ohlcv: Optional[pd.DataFrame] = None   # last EOD-closed bars; consumed by [RK] and [PS]


# --------------------------------------------------------------------------- #
# Tracing — JSONL on disk. One file per run-date per ticker.
# --------------------------------------------------------------------------- #

def _trace_path(today_iso: str, symbol: str) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return _TRACES_DIR / f"run_{today_iso}_{safe}.jsonl"


def _append_trace(ctx: PipelineContext, payload: dict) -> None:
    p = _trace_path(ctx.today_iso, ctx.symbol)
    payload = {"ts": datetime.now(IST).isoformat(timespec="seconds"),
               "trace_id": ctx.trace_id, "symbol": ctx.symbol,
               "schema_version": SCHEMA_VERSION, **payload}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _stage_result_dict(r: StageResult) -> dict:
    d = asdict(r)
    # features may contain numpy types — coerce best-effort
    return d


# --------------------------------------------------------------------------- #
# Public orchestrator — runs a chain of stage callables on one ticker.
# --------------------------------------------------------------------------- #

def run_pipeline(
    symbol: str,
    stages: list,
    today_iso: Optional[str] = None,
    overrides: Optional[dict] = None,
) -> PipelineResult:
    """Run the configured stages on one symbol. Stages are callables with the
    signature `(ctx: PipelineContext) -> StageResult`.

    Gate stages whose result.passed is False short-circuit the rest of the
    chain; their failure is still traced.

    `overrides` (backtest only) maps tunable-threshold keys to user-supplied
    values; live callers pass None and gates use their canonical defaults.
    """
    today_iso = today_iso or datetime.now(IST).date().isoformat()
    ctx = PipelineContext(
        symbol=symbol,
        trace_id=str(uuid.uuid4()),
        today_iso=today_iso,
        overrides=dict(overrides) if overrides else {},
    )

    # Clean any previous trace for this (date, symbol) so a re-run is idempotent.
    p = _trace_path(today_iso, symbol)
    if p.exists():
        p.unlink()

    passed_gates = True
    for stage_fn in stages:
        t0 = time.time()
        try:
            result = stage_fn(ctx)
        except Exception as e:
            log.exception("stage %s crashed for %s", getattr(stage_fn, "__name__", stage_fn), symbol)
            result = StageResult(
                stage_id=getattr(stage_fn, "stage_id", "?"),
                passed=False,
                reason=f"crash: {e}",
                fix_point=f"{getattr(stage_fn, '__module__', '?')}",
            )
        result.elapsed_ms = int((time.time() - t0) * 1000)
        ctx.stage_results[result.stage_id] = result
        _append_trace(ctx, {"stage": result.stage_id, **_stage_result_dict(result)})
        if not result.passed:
            passed_gates = False
            break

    return PipelineResult(
        symbol=symbol,
        trace_id=ctx.trace_id,
        passed_gates=passed_gates,
        composite_score=ctx.composite_score,
        selected=ctx.selected,
        rank=ctx.rank,
        stage_results=ctx.stage_results,
        pick_payload=ctx.pick_payload,
        snapshot=ctx.snapshot,
        signals=ctx.signals,
        confirmation_score=ctx.confirmation_score,
        confirmation_components=ctx.confirmation_components,
        ohlcv=ctx.ohlcv,
    )


def append_final_trace(result: PipelineResult, today_iso: str) -> None:
    """After ranking, append a `FINAL` row to each ticker's trace.

    Logs both the legacy composite score (if any) and the new confirmation
    score so the RL replay buffer can distinguish v1 vs v2 ranking decisions.
    """
    ctx = PipelineContext(symbol=result.symbol, trace_id=result.trace_id, today_iso=today_iso)
    _append_trace(ctx, {
        "stage": "FINAL",
        "selected": result.selected,
        "rank": result.rank,
        "composite": result.composite_score,
        "confirmation": result.confirmation_score,
        "confirmation_components": result.confirmation_components,
        "weights": STAGE_WEIGHTS,
    })
