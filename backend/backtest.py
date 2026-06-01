"""Backtest engine — single-day, as-of replay of the live pipeline.

The job of this module is to answer one question for a historical date:

  "If I'd run the picker on YYYY-MM-DD with the same code I run today,
   what would it have alerted, and what would have happened next?"

It re-uses the live engine. There is no separate "backtest pipeline" —
the gates are the gates. What's new is:

  1. A full-window fetch (history BEFORE as_of for the stages, bars AFTER
     as_of for the forward walk) so we don't double-call Yahoo.
  2. A forward-walk simulator that applies the PRINCIPLES exit ladder
     (stop, T1+break-even, T2, time stops) on real EOD bars.
  3. Two modes:
       Mode A — 1-2 symbols, deep explanation, counterfactual for failed
                tickers, full forward-walk chart.
       Mode B — universe scan, funnel + selected picks + outcomes.

Honest defaults (declared, see PRINCIPLES.md and the design conversation):
  • Fill = next-day open
  • Stop-first ordering on same bar
  • Costs = 0% (badge: "costs not modeled")
  • Concurrency cap = top_n slots (not enforced here; the caller picks top_n)
  • Survivorship = today's universe (banner shown in UI)
  • Block as_of < 2022-01-01 (universe drift too large to defend)
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, timedelta as _td
from typing import Optional

import pandas as pd

from .fetch import fetch_ohlcv
from .pipeline import PipelineResult, run_pipeline
from .stages import PER_TICKER_CHAIN
from .stages.hypothesis import build_pick_payload
from .stages.rank import rank_survivors
from .stages.regime import check_regime
from .universe import UNIVERSE

log = logging.getLogger("backtest")

# Hard floor on as_of to keep survivorship bias bounded. Universe drift
# beyond this is too large to defend without a point-in-time index file.
MIN_AS_OF = _date(2022, 1, 1)

# Default lookback for the BACKTEST fetch — needs history for the stages
# (>=200 bars for MA200) AND enough cushion forward for the hold window.
DEFAULT_LOOKBACK_DAYS = 730
DEFAULT_HOLD_DAYS = 20
DEFAULT_TOP_N = 3

# PRINCIPLES.md Section 3 — fixed risk math, never tuned.
STOP_PCT = 0.08          # -8% stop
T1_PCT = 0.08            # +8% (sell 50%, stop to break-even)
T2_PCT = 0.16            # +16% (sell remaining 50%)
DAY_45_TIGHTEN_PCT = 0.04   # day 45+: stop = entry - 4%
DAY_45_MILESTONE = 45
DAY_90_HARD_EXIT = 90
DAY_180_FINAL_EXIT = 180


def _slice_to_as_of(df: pd.DataFrame, as_of: _date) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of)
    idx = df.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return df[idx <= cutoff]


def _bars_after(df: pd.DataFrame, as_of: _date) -> pd.DataFrame:
    cutoff = pd.Timestamp(as_of)
    idx = df.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return df[idx > cutoff]


# --------------------------------------------------------------------------- #
# Forward-walk simulator
# --------------------------------------------------------------------------- #

def forward_walk(
    forward_bars: pd.DataFrame,
    entry_px: float,
    hold_days: int = DEFAULT_HOLD_DAYS,
) -> dict:
    """Simulate the exit ladder bar-by-bar on the bars AFTER as_of.

    Assumes the caller passed `forward_bars` already trimmed to bars strictly
    after as_of. Bar 0 in this frame is the fill bar (entry at its open).

    Exit ladder (PRINCIPLES.md Sections 3 & 4):
      - Open at bar 0's open == entry_px (caller computes this)
      - Stop = entry × (1 - 0.08); checked FIRST on each bar
      - T1   = entry × (1 + 0.08); on hit, sell 50%, raise stop to entry
      - T2   = entry × (1 + 0.16); on hit, sell remaining 50%
      - Day 45+ without T1: tighten stop to entry × (1 - 0.04)
      - Day 90+ without T1: forced exit at that day's open
      - Day 180 or `hold_days` end: forced final exit at close

    Stop-first convention: if low <= stop AND high >= target on same bar,
    we assume the worst — stop fills first. Conservative & matches broker
    order priority under gap-downs.

    Returns:
        {
          "entry_px": float,
          "stop_px": float,
          "t1_px": float,
          "t2_px": float,
          "exit_reason": "stop" | "t1_then_be_stop" | "t1_then_t2"
                        | "t1_then_time" | "time_45_tightened_stop"
                        | "day_90_hard_exit" | "expiry_flat",
          "exit_day": int,       # trading-day offset from entry (1-based)
          "exit_px_avg": float,  # weighted average across half-exits
          "return_pct": float,
          "hit_t1_day": int|None,
          "hit_t2_day": int|None,
          "hit_stop_day": int|None,
          "daily_path": [
              {"day": 1, "date": "...", "open", "high", "low", "close",
               "event": "t1" | "stop" | "be_stop" | "t2" | "tighten" | None}
          ],
        }
    """
    stop_px = entry_px * (1 - STOP_PCT)
    t1_px = entry_px * (1 + T1_PCT)
    t2_px = entry_px * (1 + T2_PCT)
    be_px = entry_px  # break-even, after T1 hit

    # If we have zero forward bars, exit at entry (couldn't enter).
    if forward_bars is None or forward_bars.empty:
        return {
            "entry_px": round(entry_px, 2),
            "stop_px": round(stop_px, 2),
            "t1_px": round(t1_px, 2),
            "t2_px": round(t2_px, 2),
            "exit_reason": "no_forward_data",
            "exit_day": 0,
            "exit_px_avg": round(entry_px, 2),
            "return_pct": 0.0,
            "hit_t1_day": None,
            "hit_t2_day": None,
            "hit_stop_day": None,
            "daily_path": [],
        }

    bars = forward_bars.iloc[:hold_days]
    current_stop = stop_px
    state = "open"  # "open" | "half" | "closed"
    half_shares = 0.5
    realized = 0.0   # in fraction-of-entry terms (e.g. 0.04 = +4%)
    hit_t1_day = None
    hit_t2_day = None
    hit_stop_day = None
    exit_reason = "expiry_flat"
    exit_day = len(bars)
    daily_path: list[dict] = []

    for i, (ts, row) in enumerate(bars.iterrows(), start=1):
        bar_open = float(row["Open"])
        bar_high = float(row["High"])
        bar_low = float(row["Low"])
        bar_close = float(row["Close"])
        date_str = ts.strftime("%Y-%m-%d")
        event: Optional[str] = None

        # Day-45 stop tighten (only while still fully open, T1 not yet hit)
        if state == "open" and i >= DAY_45_MILESTONE:
            tightened = entry_px * (1 - DAY_45_TIGHTEN_PCT)
            if tightened > current_stop:
                current_stop = tightened
                event = "tighten"

        # Day-90 hard exit (T1 still not hit)
        if state == "open" and i >= DAY_90_HARD_EXIT:
            realized += 1.0 * (bar_open - entry_px) / entry_px
            state = "closed"
            exit_reason = "day_90_hard_exit"
            exit_day = i
            daily_path.append({
                "day": i, "date": date_str,
                "open": bar_open, "high": bar_high,
                "low": bar_low, "close": bar_close,
                "event": "day_90_exit",
            })
            break

        if state == "open":
            # Stop first
            if bar_low <= current_stop:
                hit_stop_day = i
                realized += 1.0 * (current_stop - entry_px) / entry_px
                state = "closed"
                exit_reason = "stop"
                exit_day = i
                event = "stop"
            elif bar_high >= t1_px:
                hit_t1_day = i
                realized += half_shares * (t1_px - entry_px) / entry_px
                current_stop = be_px
                state = "half"
                event = "t1"
        elif state == "half":
            # Break-even stop first
            if bar_low <= current_stop:
                realized += half_shares * (current_stop - entry_px) / entry_px
                state = "closed"
                exit_reason = "t1_then_be_stop"
                exit_day = i
                event = "be_stop"
            elif bar_high >= t2_px:
                hit_t2_day = i
                realized += half_shares * (t2_px - entry_px) / entry_px
                state = "closed"
                exit_reason = "t1_then_t2"
                exit_day = i
                event = "t2"

        daily_path.append({
            "day": i, "date": date_str,
            "open": bar_open, "high": bar_high,
            "low": bar_low, "close": bar_close,
            "event": event,
        })

        if state == "closed":
            break

    # If we ran out of bars while half-open or fully-open, close at last close.
    if state != "closed" and not bars.empty:
        last_close = float(bars["Close"].iloc[-1])
        last_day = len(bars)
        if state == "open":
            realized += 1.0 * (last_close - entry_px) / entry_px
            exit_reason = "expiry_flat"
        elif state == "half":
            realized += half_shares * (last_close - entry_px) / entry_px
            exit_reason = "t1_then_time"
        exit_day = last_day

    exit_px_avg = entry_px * (1 + realized)

    return {
        "entry_px": round(entry_px, 2),
        "stop_px": round(stop_px, 2),
        "t1_px": round(t1_px, 2),
        "t2_px": round(t2_px, 2),
        "exit_reason": exit_reason,
        "exit_day": exit_day,
        "exit_px_avg": round(exit_px_avg, 2),
        "return_pct": round(realized * 100, 2),
        "hit_t1_day": hit_t1_day,
        "hit_t2_day": hit_t2_day,
        "hit_stop_day": hit_stop_day,
        "daily_path": daily_path,
    }


# --------------------------------------------------------------------------- #
# OHLCV cache — we fetch once per symbol per run, then slice for both the
# stages (≤ as_of) and the forward walk (> as_of). Avoids hitting Yahoo twice.
# --------------------------------------------------------------------------- #

def _fetch_full_window(symbol: str, as_of: _date, hold_days: int) -> pd.DataFrame:
    """Fetch one window that contains both history (for the gates) and future
    (for the forward walk). Lookback: DEFAULT_LOOKBACK_DAYS calendar days
    back; lookahead: hold_days * 1.6 calendar days forward (accounts for
    weekends/holidays).
    """
    end_d = as_of + _td(days=int(hold_days * 1.6) + 5)
    # Use the regular fetch_ohlcv() — it now accepts (end, lookback_days).
    lookback = DEFAULT_LOOKBACK_DAYS + int(hold_days * 1.6)
    return fetch_ohlcv(symbol, end=end_d.isoformat(), lookback_days=lookback)


def _resolve_entry(forward_bars: pd.DataFrame) -> tuple[float, str] | None:
    """The next-day open is our fill price. Returns (entry_px, entry_date_iso)
    or None if there are no bars after as_of.
    """
    if forward_bars is None or forward_bars.empty:
        return None
    first = forward_bars.iloc[0]
    ts = forward_bars.index[0]
    return float(first["Open"]), ts.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def run_backtest(
    as_of: str,
    symbols: Optional[list[str]] = None,
    hold_days: int = DEFAULT_HOLD_DAYS,
    top_n: int = DEFAULT_TOP_N,
    capital: float = 100000.0,
    max_workers: int = 10,
) -> dict:
    """Run the pipeline at a historical date and walk each result forward.

    Mode A (1-2 symbols): returns per-symbol deep blocks including a
        counterfactual hypothesis even on failed gates.
    Mode B (>2 symbols or None → full universe): returns a funnel + the
        selected top-N with their forward outcomes.

    Returns a dict shaped for the SimulationPage frontend.
    """
    # ---- Validate as_of ----
    try:
        as_of_d = _date.fromisoformat(as_of)
    except ValueError:
        return {"error": f"invalid as_of date '{as_of}' (expected YYYY-MM-DD)"}
    if as_of_d < MIN_AS_OF:
        return {
            "error": (
                f"as_of {as_of} is before {MIN_AS_OF.isoformat()} — universe "
                "drift too large to defend without point-in-time data"
            )
        }
    if as_of_d >= _date.today():
        return {"error": "as_of must be a past date (no peeking at today/future)"}

    # ---- Resolve symbol set + mode ----
    if symbols:
        symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        # Universe membership check — same constraint live picks have
        unknown = [s for s in symbols if s not in UNIVERSE]
        if unknown:
            log.warning("symbols not in UNIVERSE: %s", unknown)
        symbols = [s for s in symbols if s in UNIVERSE]
        if not symbols:
            return {"error": "no valid symbols (must be in Nifty 100 universe)"}
        mode = "A" if len(symbols) <= 2 else "B"
    else:
        symbols = list(UNIVERSE)
        mode = "B"

    # ---- Regime check at as_of ----
    regime = check_regime(as_of=as_of)
    regime_dict = regime.as_dict()

    # ---- Per-symbol: pipeline + forward walk ----
    per_symbol: list[dict] = []
    results_for_rank: list[PipelineResult] = []

    def _one(sym: str) -> dict:
        try:
            full = _fetch_full_window(sym, as_of_d, hold_days)
        except Exception as e:
            return {"symbol": sym, "error": f"fetch failed: {e}"}

        if full is None or full.empty:
            return {"symbol": sym, "error": "no OHLCV data"}

        forward_bars = _bars_after(full, as_of_d)

        # Run the live chain with today_iso = as_of. ingest.py and the rest
        # of the chain are responsible for the ≤ as_of slicing.
        result = run_pipeline(sym, PER_TICKER_CHAIN, as_of)

        entry_info = _resolve_entry(forward_bars)
        if entry_info is None:
            forward = None
        else:
            entry_px, entry_date = entry_info
            forward = forward_walk(forward_bars, entry_px, hold_days=hold_days)
            forward["entry_date"] = entry_date

        return {
            "symbol": sym,
            "result": result,
            "forward": forward,
        }

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, s): s for s in symbols}
        for fut in as_completed(futures):
            try:
                per_symbol.append(fut.result())
            except Exception:
                log.exception("backtest crashed for %s", futures[fut])

    # Collect PipelineResults for the ranker
    for entry in per_symbol:
        if "result" in entry and entry["result"].passed_gates:
            results_for_rank.append(entry["result"])

    # ---- Rank survivors and build payloads ----
    selected = rank_survivors(results_for_rank, top_n=top_n) if regime.passed else []

    # ---- Assemble response shape ----
    if mode == "A":
        return _assemble_mode_a(
            per_symbol, as_of, hold_days, top_n, capital, regime_dict,
        )
    return _assemble_mode_b(
        per_symbol, selected, as_of, hold_days, top_n, capital, regime_dict,
    )


# --------------------------------------------------------------------------- #
# Response assembly
# --------------------------------------------------------------------------- #

_GATE_ORDER = ["U", "I", "HR", "LT", "CS", "VD", "BR"]
_GATE_LABEL = {
    "U": "Universe", "I": "Ingest", "HR": "Hard rejects",
    "LT": "Long-term flow", "CS": "Consolidation",
    "VD": "Volume/Divergence", "BR": "Breakout",
}


def _stage_to_dict(sr) -> dict:
    return {
        "stage_id": sr.stage_id,
        "label": _GATE_LABEL.get(sr.stage_id, sr.stage_id),
        "passed": sr.passed,
        "score": sr.score,
        "features": sr.features,
        "evidence": list(sr.evidence or []),
        "reason": sr.reason,
        "fix_point": sr.fix_point,
    }


def _build_per_symbol_block(entry: dict, capital: float) -> dict:
    """One symbol's deep view: gate trace + counterfactual + forward walk."""
    from .explain import explain_stage  # local import to avoid circular at top

    sym = entry["symbol"]
    if "error" in entry:
        return {"symbol": sym, "error": entry["error"]}

    result: PipelineResult = entry["result"]
    forward = entry.get("forward")

    chain = []
    last_failed_id: Optional[str] = None
    for sid in _GATE_ORDER:
        if sid not in result.stage_results:
            chain.append({
                "stage_id": sid,
                "label": _GATE_LABEL[sid],
                "passed": None,
                "evidence": [],
                "reason": "(not reached)",
                "explanation": "Earlier gate failed; this gate was not evaluated.",
            })
            continue
        sr = result.stage_results[sid]
        d = _stage_to_dict(sr)
        d["explanation"] = explain_stage(sr)
        chain.append(d)
        if not sr.passed and last_failed_id is None:
            last_failed_id = sid

    # Counterfactual: build the pick payload regardless of whether the chain
    # passed. The hypothesis builder is defensive — falls back to snapshot
    # current price if no breakout fired.
    counterfactual = None
    is_counterfactual = not result.passed_gates
    try:
        counterfactual = build_pick_payload(
            result, result.snapshot or {},
            account_value=capital,
        )
        counterfactual["is_counterfactual"] = is_counterfactual
    except Exception as e:
        counterfactual = {"error": f"hypothesis build failed: {e}"}

    return {
        "symbol": sym,
        "passed_all_gates": result.passed_gates,
        "killing_gate": last_failed_id,
        "killing_gate_label": _GATE_LABEL.get(last_failed_id) if last_failed_id else None,
        "chain": chain,
        "counterfactual": counterfactual,
        "forward": forward,
        "snapshot": {
            "company": (result.snapshot or {}).get("company"),
            "sector": (result.snapshot or {}).get("sector"),
            "industry": (result.snapshot or {}).get("industry"),
        },
    }


def _assemble_mode_a(per_symbol, as_of, hold_days, top_n, capital, regime) -> dict:
    blocks = [_build_per_symbol_block(e, capital) for e in per_symbol]
    return {
        "mode": "A",
        "as_of": as_of,
        "regime": regime,
        "assumptions": _assumptions(hold_days, top_n, capital),
        "symbols": blocks,
    }


def _assemble_mode_b(per_symbol, selected, as_of, hold_days, top_n, capital, regime) -> dict:
    # Funnel counts per gate
    funnel = {sid: {"eval": 0, "pass": 0, "fail": 0, "top_reason": ""} for sid in _GATE_ORDER}
    fail_reasons: dict[str, dict[str, int]] = {sid: {} for sid in _GATE_ORDER}

    for entry in per_symbol:
        if "result" not in entry:
            continue
        result: PipelineResult = entry["result"]
        for sid, sr in result.stage_results.items():
            if sid not in funnel:
                continue
            funnel[sid]["eval"] += 1
            if sr.passed:
                funnel[sid]["pass"] += 1
            else:
                funnel[sid]["fail"] += 1
                key = (sr.reason or "").split(";")[0].strip()[:60] or "(no reason)"
                fail_reasons[sid][key] = fail_reasons[sid].get(key, 0) + 1

    for sid, fr in fail_reasons.items():
        if fr:
            top = sorted(fr.items(), key=lambda x: -x[1])[0]
            funnel[sid]["top_reason"] = f"{top[1]}× {top[0]}"

    # Map symbol → forward (for selected picks)
    fwd_by_sym = {e["symbol"]: e.get("forward") for e in per_symbol}

    selected_blocks = []
    for pick in selected:
        try:
            payload = build_pick_payload(
                pick, pick.snapshot or {},
                account_value=capital,
                today_iso=as_of,
            )
        except Exception as e:
            payload = {"error": str(e)}
        selected_blocks.append({
            "rank": pick.rank,
            "symbol": pick.symbol,
            "company": (pick.snapshot or {}).get("company"),
            "confirmation": {
                "score": pick.confirmation_score,
                **(pick.confirmation_components or {}),
            },
            "payload": payload,
            "forward": fwd_by_sym.get(pick.symbol),
        })

    # Aggregate outcome stats
    rets = [b["forward"]["return_pct"] for b in selected_blocks
            if b.get("forward") and "return_pct" in b["forward"]]
    wins = sum(1 for r in rets if r > 0)
    summary = {
        "n_picks": len(selected_blocks),
        "hit_rate_pct": round(100 * wins / len(rets), 1) if rets else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "sum_return_pct": round(sum(rets), 2) if rets else None,
    }

    return {
        "mode": "B",
        "as_of": as_of,
        "regime": regime,
        "assumptions": _assumptions(hold_days, top_n, capital),
        "funnel": [
            {"stage_id": sid, "label": _GATE_LABEL[sid], **funnel[sid]}
            for sid in _GATE_ORDER
        ],
        "selected": selected_blocks,
        "summary": summary,
    }


def _assumptions(hold_days: int, top_n: int, capital: float) -> dict:
    return {
        "hold_days": hold_days,
        "top_n": top_n,
        "capital": capital,
        "fill_model": "next-day open",
        "stop_pct": int(STOP_PCT * 100),
        "t1_pct": int(T1_PCT * 100),
        "t2_pct": int(T2_PCT * 100),
        "costs_modeled": False,
        "survivorship_note": "Universe = today's Nifty 100; historical drift not corrected",
    }
