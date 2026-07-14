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

SCHEMA_VERSION = 3   # v3 = soft-gate composite spine (LLR detector)

# --------------------------------------------------------------------------- #
# Stage weights — legacy weighted-composite mapping. Kept for backwards
# compatibility with old trace rows that reference these five keys. The live
# picker no longer reads this dict; see COMPOSITE_WEIGHTS below.
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
# Composite / soft-gate config — the LIVE control surface.
#
# Loaded from config/stage_weights.json at import time. If the file is missing
# or malformed, we fall back to conservative defaults that still admit picks.
# The tuner (scripts/tune_weights.py) is the only writer of that JSON, and it
# uses a champion-challenger ratchet — the file is only overwritten if a new
# candidate strictly beats the current champion on backtest metric.
# --------------------------------------------------------------------------- #

_CONFIG_PATH = _PROJECT_ROOT / "config" / "stage_weights.json"

# Hard gates short-circuit the chain on failure (safety + data availability).
# Everything else is a soft gate: if it fails, its margin contributes 0 to the
# composite, but the chain continues and downstream stages still run.
_DEFAULT_HARD_GATES: frozenset[str] = frozenset({"U", "I", "HR"})

# Seed defaults — mirror config/stage_weights.json so behaviour is identical
# when the JSON is unreadable. Adjust the JSON, not this dict, for live tuning.
_DEFAULT_COMPOSITE_WEIGHTS: dict[str, float] = {
    "ACS": 0.05, "AC":  0.20,
    "LT":  0.15, "CS":  0.10, "VD":  0.15, "BR":  0.20,
    "WY":  0.00, "VSA": 0.00, "AVWAP": 0.00,
}
_DEFAULT_TAU: float = 0.35


def _load_weight_config() -> tuple[frozenset[str], dict[str, float], float]:
    """Read config/stage_weights.json. Fall back to seed defaults on any error.

    Returning a frozenset + immutable copy makes it safe to share across
    threads; the orchestrator runs stages in parallel.
    """
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        hard = frozenset(raw.get("hard_gate_stage_ids", []) or _DEFAULT_HARD_GATES)
        weights = {k: float(v) for k, v in (raw.get("scored_stage_weights") or {}).items()}
        if not weights:
            weights = dict(_DEFAULT_COMPOSITE_WEIGHTS)
        tau = float(raw.get("composite_threshold_tau", _DEFAULT_TAU))
        return hard, weights, tau
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as e:
        log.warning("stage_weights.json unreadable (%s); using seed defaults", e)
        return _DEFAULT_HARD_GATES, dict(_DEFAULT_COMPOSITE_WEIGHTS), _DEFAULT_TAU


HARD_GATE_IDS, COMPOSITE_WEIGHTS, COMPOSITE_TAU = _load_weight_config()


# --------------------------------------------------------------------------- #
# Trigger-contextual reweighting (pre-breakout Pocket-Pivot exemption).
#
# Pocket Pivot and No-Supply Test are pre-breakout setups: *by construction*
# their mid-term flow (VD) is quiet. Penalizing them with a full 15% weight
# for having low VD score is a category error — it punishes them for the
# property that defines them. Full-weight VD is correct only for the SOS
# breakout case (BR pass), where quiet mid-term flow really is a bull-trap.
#
# Implementation: at composite time, classify the trigger regime from stage
# results and rebalance the weight vector. Sum stays exactly 1.0 — we only
# move weight between stages, never mint or destroy it.
#
# Fix points:
#     TRIGGER_MT_STAGE_ID     : the mid-term-flow stage id (default "VD")
#     TRIGGER_MT_SHRINK_FRAC  : fraction of MT weight redistributed on
#                                pre-breakout (default 0.5 = halve MT)
#     TRIGGER_MT_REDISTRIBUTE : ordered stage ids to receive the freed weight;
#                                split equally (default ("LT", "AC"))
#     TRIGGER_AC_MIN_SCORE    : minimum AC.score for a pre_breakout admission
#                                (default 0.6 — the marginal-base filter).
#                                AC.passed alone is not enough; the coil must
#                                be genuinely tight + dry + rising-ADI. See
#                                CHANGELOG 2026-07-14 for the Bajaj-Auto
#                                incident that motivated this filter.
# --------------------------------------------------------------------------- #

TRIGGER_MT_STAGE_ID: str = "VD"
TRIGGER_MT_SHRINK_FRAC: float = 0.5
TRIGGER_MT_REDISTRIBUTE: tuple[str, ...] = ("LT", "AC")
TRIGGER_AC_MIN_SCORE: float = 0.6

TriggerRegime = str  # "pre_breakout" | "sos_breakout" | "neutral"


def classify_trigger(stage_results: dict[str, "StageResult"]) -> TriggerRegime:
    """Derive the trigger regime from which gates fired.

    pre_breakout : AC passed AND AC.score >= TRIGGER_AC_MIN_SCORE AND BR fail
                    (coiled spring — accumulation is not just present but
                    strong; a marginal AC pass no longer earns weight relief)
    sos_breakout : BR passed                       (resistance cleared today)
    neutral      : neither                         (no reweighting)

    The AC-score floor is the practical guard against admitting fragile
    pre-breakouts on borderline evidence. It filters at the trigger classifier
    rather than adding a new threshold to AC itself, so the ranker keeps
    seeing every AC-passer while only the strong ones qualify for the VD
    weight cut.
    """
    br = stage_results.get("BR")
    ac = stage_results.get("AC")
    br_pass = br is not None and br.passed
    ac_strong = (
        ac is not None
        and ac.passed
        and float(ac.score or 0.0) >= TRIGGER_AC_MIN_SCORE
    )
    if br_pass:
        return "sos_breakout"
    if ac_strong:
        return "pre_breakout"
    return "neutral"


def _reweight_for_trigger(
    weights: dict[str, float],
    regime: TriggerRegime,
) -> dict[str, float]:
    """Return an adjusted copy of `weights` given the trigger regime.

    Only pre_breakout adjusts today. sos_breakout and neutral are pass-through.
    The sum is preserved to within 1e-9 (invariant we assert on).
    """
    if regime != "pre_breakout":
        return dict(weights)
    mt_id = TRIGGER_MT_STAGE_ID
    w_mt = float(weights.get(mt_id, 0.0))
    if w_mt <= 0.0:
        return dict(weights)
    receivers = [s for s in TRIGGER_MT_REDISTRIBUTE if weights.get(s, 0.0) > 0.0]
    if not receivers:
        return dict(weights)
    freed = w_mt * TRIGGER_MT_SHRINK_FRAC
    share = freed / len(receivers)
    adjusted = dict(weights)
    adjusted[mt_id] = w_mt - freed
    for r in receivers:
        adjusted[r] = float(adjusted.get(r, 0.0)) + share
    return adjusted


def compute_composite(stage_results: dict[str, "StageResult"]) -> float:
    """S = Σᵢ wᵢ · mᵢ  over scored stages.

    A stage that didn't run, or ran-and-failed, contributes 0. A stage with
    no configured weight is ignored (weight = 0). Mathematically this is the
    LLR detector under Gaussian noise assumptions on the margins.

    Weights are trigger-contextual: for a pre-breakout setup (AC pass, BR
    fail) the mid-term-flow weight is halved and redistributed to LT/AC. See
    _reweight_for_trigger for the exact mapping and rationale.
    """
    regime = classify_trigger(stage_results)
    weights = _reweight_for_trigger(COMPOSITE_WEIGHTS, regime)
    s = 0.0
    for sid, w in weights.items():
        if w == 0.0:
            continue
        sr = stage_results.get(sid)
        if sr is None or not sr.passed:
            continue
        s += w * float(sr.score or 0.0)
    return s


def hard_gates_passed(stage_results: dict[str, "StageResult"]) -> bool:
    """A ticker is a valid candidate iff every hard gate that ran, passed.

    We do NOT require every hard gate to have run — that would kill tickers
    whose upstream stage short-circuited before the later hard gate could
    execute. Instead: for each hard-gate stage that produced a result, it
    must have passed.
    """
    for sid in HARD_GATE_IDS:
        sr = stage_results.get(sid)
        if sr is not None and not sr.passed:
            return False
    return True


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

    Semantics (v3 soft-gate spine):
      - Stages whose `stage_id` is in HARD_GATE_IDS short-circuit the chain on
        failure. Data-availability and safety only ([U], [I], [HR]).
      - All other stages ("soft gates") always run; their failure sets that
        stage's contribution to the composite S to 0 but the chain continues.
      - `passed_gates` in the returned result means "every stage that ran,
        passed". `hard_gates_passed(...)` is the real selection precondition.

    `overrides` (backtest only) maps tunable-threshold keys to user-supplied
    values; live callers pass None and stages use their canonical defaults.
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

    import sys
    passed_gates = True
    for stage_fn in stages:
        t0 = time.time()
        # Look up the module-level stage_id BEFORE running, so that even if
        # the stage crashes we can attribute the failure to the correct stage.
        # (`stage_id` is a module attribute, not a function attribute, so
        # `getattr(stage_fn, 'stage_id', ...)` used to always return "?".)
        _mod = sys.modules.get(getattr(stage_fn, "__module__", ""))
        _fallback_sid = getattr(_mod, "stage_id", "?") if _mod else "?"
        try:
            result = stage_fn(ctx)
        except Exception as e:
            log.exception("stage %s crashed for %s", _fallback_sid, symbol)
            result = StageResult(
                stage_id=_fallback_sid,
                passed=False,
                reason=f"crash: {e}",
                fix_point=f"{getattr(stage_fn, '__module__', '?')}",
            )
        result.elapsed_ms = int((time.time() - t0) * 1000)
        ctx.stage_results[result.stage_id] = result
        _append_trace(ctx, {"stage": result.stage_id, **_stage_result_dict(result)})
        if not result.passed:
            passed_gates = False
            if result.stage_id in HARD_GATE_IDS:
                break   # hard gate failed -> stop; downstream can't be trusted

    # Compute composite once, at end of chain, so soft-gate margins compose.
    ctx.composite_score = compute_composite(ctx.stage_results)

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
        "spine": "v3-soft-gate-composite",
        "selected": result.selected,
        "rank": result.rank,
        "composite": result.composite_score,
        "composite_threshold_tau": COMPOSITE_TAU,
        "confirmation": result.confirmation_score,
        "confirmation_components": result.confirmation_components,
        "composite_weights": dict(COMPOSITE_WEIGHTS),
        "legacy_weights": STAGE_WEIGHTS,
    })
