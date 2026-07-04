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
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, timedelta as _td
from typing import Optional

import pandas as pd

from .fetch import fetch_ohlcv
from .pipeline import PipelineContext, PipelineResult, StageResult, run_pipeline
from .stages import PER_TICKER_CHAIN
from .stages import breakout as _br
from .stages import consolidation as _cs
from .stages import hard_rejects as _hr
from .stages import lt_flow as _lt
from .stages import volume as _vd
from .stages.hypothesis import build_pick_payload
from .stages.ingest import MIN_BARS, _recompute_snapshot_from_ohlcv
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
DEFAULT_HOLD_DAYS = 90        # matches PRINCIPLES.md "3-6 month hold" cadence
DEFAULT_TOP_N = 3

# PRINCIPLES.md Section 3 — fixed risk math, never tuned.
STOP_PCT = 0.08          # -8% stop
T1_PCT = 0.08            # +8% (sell 50%, stop to break-even)
T2_PCT = 0.16            # +16% (sell remaining 50%)
DAY_45_TIGHTEN_PCT = 0.04   # day 45+: stop = entry - 4%
DAY_45_MILESTONE = 45
DAY_90_HARD_EXIT = 90
DAY_180_FINAL_EXIT = 180


def _normalize_symbol(raw: str) -> Optional[str]:
    """Map a user-typed symbol to its canonical Universe form.

    Accepts:
      "infy", "INFY", "INFY.NS"          → "INFY.NS"
      "bajaj-auto", "BAJAJ-AUTO"          → "BAJAJ-AUTO.NS"
      "bajajauto"                         → "BAJAJ-AUTO.NS" (best-effort)
      "m&m"                               → "M&M.NS"

    Returns None if no plausible match exists in UNIVERSE. The caller is
    responsible for surfacing the not-found message to the user.
    """
    if not raw:
        return None
    s = raw.strip().upper()
    if not s:
        return None

    # Already in canonical form?
    if s in UNIVERSE:
        return s
    # Add .NS suffix?
    if not s.endswith(".NS") and f"{s}.NS" in UNIVERSE:
        return f"{s}.NS"
    # Try dropping any trailing exchange tag and re-suffixing
    base = s.rsplit(".", 1)[0]
    if f"{base}.NS" in UNIVERSE:
        return f"{base}.NS"

    # Best-effort: collapse non-alphanumeric in both sides and try again
    def _collapse(x: str) -> str:
        return "".join(c for c in x if c.isalnum())
    collapsed = _collapse(base)
    for sym in UNIVERSE:
        sym_base = sym.split(".", 1)[0]
        if _collapse(sym_base) == collapsed:
            return sym
    return None


def _resolve_symbol_loose(raw: str) -> tuple[Optional[str], bool]:
    """Like _normalize_symbol, but accepts any plausible Yahoo ticker even
    when it is not in the strict Nifty 100 universe.

    Returns (canonical_symbol_or_None, in_universe_bool).

    Used by single-symbol views ("Symbol check") so the user can backtest
    any ticker — mid-caps, small-caps, or even US stocks — without editing
    `universe.py`. The `in_universe=False` flag lets the UI surface an
    advisory banner about gate tuning.
    """
    in_univ = _normalize_symbol(raw)
    if in_univ is not None:
        return in_univ, True
    if not raw:
        return None, False
    s = raw.strip().upper()
    if not s:
        return None, False

    # Accept anything that looks like a Yahoo ticker. Yahoo accepts:
    #   - bare letters/digits  (e.g. AAPL, TSLA)
    #   - ".NS" / ".BO" / ".BSE" / "^" prefixed indices
    #   - hyphens (BAJAJ-AUTO) and ampersands (M&M)
    # If the user typed something without a suffix and it isn't pure indices,
    # default to .NS for the Indian-equity context this app is scoped to.
    has_exchange_suffix = "." in s
    base = s.rsplit(".", 1)[0]
    if not base or not any(c.isalnum() for c in base):
        return None, False

    canonical = s if has_exchange_suffix else f"{s}.NS"
    return canonical, False


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
    end: Optional[str] = None,
    overrides: Optional[dict] = None,
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
    unresolved: list[str] = []
    in_universe_map: dict[str, bool] = {}
    if symbols:
        raw_in = [s for s in symbols if s and s.strip()]
        resolved: list[str] = []
        for raw in raw_in:
            sym, in_univ = _resolve_symbol_loose(raw)
            if sym is None:
                unresolved.append(raw)
            else:
                resolved.append(sym)
                in_universe_map[sym] = in_univ
        # de-duplicate preserving order
        seen: set[str] = set()
        symbols = [s for s in resolved if not (s in seen or seen.add(s))]
        if not symbols:
            return {
                "error": (
                    f"Could not parse symbol(s): {', '.join(unresolved)}. "
                    f"Try canonical form like INFY.NS, BAJAJ-AUTO.NS, M&M.NS."
                ),
                "unresolved": unresolved,
            }
        mode = "A" if len(symbols) <= 2 else "B"
    else:
        symbols = list(UNIVERSE)
        for s in symbols:
            in_universe_map[s] = True
        mode = "B"

    # ---- Mode C — scan range (single symbol OR full universe) ----
    if end and end != as_of:
        # Validate window vs hold_days
        try:
            _start_d = _date.fromisoformat(as_of)
            _end_d = _date.fromisoformat(end)
            window_days = (_end_d - _start_d).days
        except ValueError:
            window_days = 0
        if window_days < hold_days:
            return {
                "error": (
                    f"Scan window ({window_days} days) is shorter than hold_days "
                    f"({hold_days}). Pick a wider window or a smaller hold."
                ),
            }

        # Did the caller pass exactly one symbol? Single-symbol gate timeline.
        # Otherwise (zero, or many): universe historical-picks scan.
        if len(symbols) == 1 and len(UNIVERSE) > 1 and symbols != list(UNIVERSE):
            return scan_symbol(
                symbol=symbols[0],
                start=as_of,
                end=end,
                hold_days=hold_days,
                capital=capital,
                in_universe=in_universe_map.get(symbols[0], True),
                overrides=overrides,
            )
        return scan_universe(
            start=as_of,
            end=end,
            hold_days=hold_days,
            top_n=top_n,
            capital=capital,
            symbols=symbols if symbols and symbols != list(UNIVERSE) else None,
            max_workers=max_workers,
            overrides=overrides,
        )

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
        result = run_pipeline(sym, PER_TICKER_CHAIN, as_of, overrides=overrides)

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
            in_universe_map=in_universe_map,
            overrides=overrides,
        )
    return _assemble_mode_b(
        per_symbol, selected, as_of, hold_days, top_n, capital, regime_dict,
        overrides=overrides,
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


def _assemble_mode_a(
    per_symbol, as_of, hold_days, top_n, capital, regime,
    in_universe_map: Optional[dict[str, bool]] = None,
    overrides: Optional[dict] = None,
) -> dict:
    in_universe_map = in_universe_map or {}
    blocks = [_build_per_symbol_block(e, capital) for e in per_symbol]
    for b in blocks:
        b["in_universe"] = in_universe_map.get(b.get("symbol"), True)
    return {
        "mode": "A",
        "as_of": as_of,
        "regime": regime,
        "assumptions": _assumptions(hold_days, top_n, capital, overrides),
        "symbols": blocks,
    }


def _assemble_mode_b(
    per_symbol, selected, as_of, hold_days, top_n, capital, regime,
    overrides: Optional[dict] = None,
) -> dict:
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
        "assumptions": _assumptions(hold_days, top_n, capital, overrides),
        "funnel": [
            {"stage_id": sid, "label": _GATE_LABEL[sid], **funnel[sid]}
            for sid in _GATE_ORDER
        ],
        "selected": selected_blocks,
        "summary": summary,
    }


_CANONICAL_OVERRIDES = {
    "hr_parabolic_30d_max_pct": 25.0,
    "hr_extended_vs_ma50_max": 1.25,
    "lt_obv_90d_slope_min": 3.0,
    "cs_atr_pct_max": 4.0,
    "vd_dryup_ratio": 0.50,
    "br_volume_mult": 1.3,
    "br_resistance_break_pct_min": 0.0,
    "br_upper_third_ratio_min": 0.67,
}


def _assumptions(
    hold_days: int,
    top_n: int,
    capital: float,
    overrides: Optional[dict] = None,
) -> dict:
    eff: dict = {}
    deviated: dict = {}
    for k, canonical in _CANONICAL_OVERRIDES.items():
        val = (overrides or {}).get(k, canonical)
        eff[k] = val
        if val != canonical:
            deviated[k] = {"value": val, "canonical": canonical}
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
        "thresholds": eff,
        "thresholds_deviated": deviated,
    }


# --------------------------------------------------------------------------- #
# Mode C — Gate Timeline scan (single symbol, date range)
# --------------------------------------------------------------------------- #
#
# Why this exists: the [VD] gate (and [BR]) fire on roughly 2-5 % of days for
# any single Nifty 100 symbol, so a one-day sample almost always shows a
# rejection. The scan walks every trading day in a window and surfaces the
# rare days where each gate fires — turning "I never see VD pass" into
# "VD passed on these 11 days; here's the trace and forward outcome."
#
# Performance note: we fetch the full OHLCV window ONCE upfront. For each
# as-of day we slice locally and run the gate functions directly against
# the in-memory frame. That skips ~N yfinance round-trips and brings a
# 1-year scan from minutes down to ~15 seconds.

_SCAN_CHAIN: list = [_hr.run, _lt.run, _cs.run, _vd.run, _br.run]
_SCAN_GATE_IDS: list[str] = ["HR", "LT", "CS", "VD", "BR"]


def _build_ctx_for_scan(
    symbol: str,
    as_of: _date,
    sliced: pd.DataFrame,
    overrides: Optional[dict] = None,
) -> PipelineContext:
    """Construct a PipelineContext as if [I] Ingest had run on the sliced
    OHLCV. Reuses ingest's snapshot recompute helper so the snapshot fields
    seen by downstream gates match what live-mode produces.
    """
    ctx = PipelineContext(
        symbol=symbol,
        trace_id=str(uuid.uuid4()),
        today_iso=as_of.isoformat(),
        overrides=dict(overrides) if overrides else {},
    )
    ctx.ohlcv = sliced
    seed_snap = {
        "symbol": symbol, "company": symbol, "sector": None, "industry": None,
        "current": float(sliced["Close"].iloc[-1]),
    }
    snap = _recompute_snapshot_from_ohlcv(seed_snap, sliced)
    snap["current"] = float(sliced["Close"].iloc[-1])
    ctx.snapshot = snap
    return ctx


def scan_symbol(
    symbol: str,
    start: str,
    end: str,
    hold_days: int = DEFAULT_HOLD_DAYS,
    capital: float = 100000.0,
    in_universe: Optional[bool] = None,
    overrides: Optional[dict] = None,
) -> dict:
    """Walk every trading day in [start, end] and record each gate's verdict.

    `in_universe` lets the caller signal whether the symbol is in the tuned
    Nifty 100 universe; the response echoes it so the UI can show an advisory
    banner. If None, we figure it out via loose resolution.

    Returns:
        {
          "mode": "C", "scope": "symbol",
          "symbol": str, "in_universe": bool,
          "start": str, "end": str, "trading_days": int,
          "counts": {gate_id: {eval, pass, fail}, ...},
          "timeline": [{date, gates: {U, I, HR, LT, CS, VD, BR}, killed_at}],
          "pass_dates_by_gate": {gate_id: [iso_date, ...]},
          "full_passes": [{as_of, forward}],
          "assumptions": {...},
        }
    """
    # ---- Validate inputs (loose: accept any plausible Yahoo ticker) ----
    sym_norm, resolved_in_univ = _resolve_symbol_loose(symbol)
    if sym_norm is None:
        return {"error": f"could not parse symbol '{symbol}'"}
    symbol = sym_norm
    if in_universe is None:
        in_universe = resolved_in_univ

    try:
        start_d = _date.fromisoformat(start)
        end_d = _date.fromisoformat(end)
    except ValueError:
        return {"error": "invalid date(s); expected YYYY-MM-DD"}

    if start_d < MIN_AS_OF:
        return {
            "error": (
                f"start {start} is before {MIN_AS_OF.isoformat()} — universe "
                "drift too large to defend without point-in-time data"
            ),
        }
    if start_d >= end_d:
        return {"error": "start must be strictly before end"}
    if end_d >= _date.today():
        return {"error": "end must be a past date"}

    # ---- Fetch one wide window: history + scan range + forward buffer ----
    fetch_end = end_d + _td(days=int(hold_days * 1.6) + 5)
    lookback = DEFAULT_LOOKBACK_DAYS + (end_d - start_d).days + int(hold_days * 1.6) + 10
    full = fetch_ohlcv(symbol, end=fetch_end.isoformat(), lookback_days=lookback)
    if full is None or full.empty:
        return {"error": f"no OHLCV data for {symbol}"}

    # ---- Trading days strictly inside [start, end] ----
    idx = full.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    in_range_mask = (idx >= pd.Timestamp(start_d)) & (idx <= pd.Timestamp(end_d))
    range_bars = full[in_range_mask]
    if range_bars.empty:
        return {"error": f"no trading days between {start} and {end}"}

    timeline: list[dict] = []
    counts = {sid: {"eval": 0, "pass": 0, "fail": 0} for sid in _SCAN_GATE_IDS}
    pass_dates_by_gate: dict[str, list[str]] = {sid: [] for sid in _SCAN_GATE_IDS}
    full_passes: list[dict] = []

    # We also surface where the gate fired with one click of context — the
    # 5d/50d volume ratio and OBV sparkline over the scan range. Captured
    # cheaply during the walk.
    vol_ratio_series: list[dict] = []

    for ts in range_bars.index:
        as_of = ts.date()
        as_of_iso = as_of.isoformat()
        sliced = _slice_to_as_of(full, as_of)
        if len(sliced) < MIN_BARS:
            # Treat as "not enough history" — surface in timeline but skip stages
            timeline.append({
                "date": as_of_iso,
                "gates": {"U": True, "I": False, "HR": None, "LT": None,
                          "CS": None, "VD": None, "BR": None},
                "killed_at": "I",
                "note": f"only {len(sliced)} bars (<{MIN_BARS})",
            })
            continue

        ctx = _build_ctx_for_scan(symbol, as_of, sliced, overrides=overrides)
        day_gates: dict[str, Optional[bool]] = {
            "U": True, "I": True,
            "HR": None, "LT": None, "CS": None, "VD": None, "BR": None,
        }
        killed_at: Optional[str] = None
        passed_all = True
        per_stage_features: dict[str, dict] = {}

        for stage_fn in _SCAN_CHAIN:
            try:
                sr = stage_fn(ctx)
            except Exception as e:
                sr = StageResult(
                    stage_id=getattr(stage_fn, "stage_id", "?"),
                    passed=False, reason=f"crash: {e}",
                )
            ctx.stage_results[sr.stage_id] = sr
            day_gates[sr.stage_id] = sr.passed
            counts[sr.stage_id]["eval"] += 1
            counts[sr.stage_id]["pass" if sr.passed else "fail"] += 1
            per_stage_features[sr.stage_id] = dict(sr.features or {})
            if sr.passed:
                pass_dates_by_gate[sr.stage_id].append(as_of_iso)
            else:
                killed_at = sr.stage_id
                passed_all = False
                break

        # capture 5d/50d ratio for the sparkline (whether or not VD ran)
        vd_feat = per_stage_features.get("VD") or {}
        vol_ratio_series.append({
            "date": as_of_iso,
            "ratio_5_50": vd_feat.get("vol_ratio_5_50"),
            "vd_passed": day_gates.get("VD"),
        })

        if killed_at:
            timeline_features = dict(per_stage_features.get(killed_at) or {})
        else:
            # Day passed all gates — keep the VD summary for the pass-list tooltip
            timeline_features = {
                "vol_ratio_5_50": vd_feat.get("vol_ratio_5_50"),
                "divergence_form": (vd_feat.get("divergence") or {}).get("form"),
            }

        timeline.append({
            "date": as_of_iso,
            "gates": day_gates,
            "killed_at": killed_at,
            "features": timeline_features,
        })

        if passed_all:
            forward_bars = _bars_after(full, as_of)
            forward = None
            entry_info = _resolve_entry(forward_bars)
            if entry_info:
                entry_px, entry_date = entry_info
                forward = forward_walk(forward_bars, entry_px, hold_days=hold_days)
                forward["entry_date"] = entry_date
            full_passes.append({"as_of": as_of_iso, "forward": forward})

    return {
        "mode": "C",
        "scope": "symbol",
        "symbol": symbol,
        "in_universe": in_universe,
        "start": start,
        "end": end,
        "trading_days": len(timeline),
        "counts": counts,
        "timeline": timeline,
        "pass_dates_by_gate": pass_dates_by_gate,
        "full_passes": full_passes,
        "vol_ratio_series": vol_ratio_series,
        "assumptions": _assumptions(hold_days, DEFAULT_TOP_N, capital, overrides),
    }


# --------------------------------------------------------------------------- #
# Mode C — Universe historical picks (multi-symbol, walk every trading day)
# --------------------------------------------------------------------------- #
#
# Answers the "last 1 year, last quarter — what did our strategy pick?"
# question. Walks every trading day in [start, end]; for each day runs the
# full pipeline + ranker on the universe; records the top-N selections and
# how each unfolded over `hold_days`.
#
# Performance: 100 symbols × ~250 days × ~5 ms (in-memory gates) ≈ 125 s
# single-threaded. With max_workers=10 the per-day fan-out brings it to
# ~15–25 s for a full year, ~5 s for a quarter. The OHLCV fetch is the
# bottleneck — done ONCE per symbol upfront.

def _prefetch_universe_ohlcv(
    symbols: list[str],
    end_d: _date,
    lookback_days: int,
    max_workers: int = 10,
) -> dict[str, pd.DataFrame]:
    """Fetch each symbol's full window once. Returns a dict; failed symbols
    are simply absent (and silently skipped by the day loop).
    """
    cache: dict[str, pd.DataFrame] = {}

    def _one(sym: str) -> tuple[str, Optional[pd.DataFrame]]:
        try:
            df = fetch_ohlcv(
                sym,
                end=end_d.isoformat(),
                lookback_days=lookback_days,
            )
            if df is None or df.empty:
                return sym, None
            return sym, df
        except Exception:
            log.exception("prefetch failed for %s", sym)
            return sym, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for sym, df in pool.map(_one, symbols):
            if df is not None and not df.empty:
                cache[sym] = df
    return cache


def _quarter_key(iso: str) -> str:
    """2024-04-15 → 2024-Q2."""
    d = _date.fromisoformat(iso)
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _safe_round(x, digits: int = 2):
    """Round but return None for NaN/inf/missing."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, digits)


# Documents the indicator columns embedded in each price_history row.
# Used to populate the JSONL meta record so the file is self-describing.
PRICE_HISTORY_SCHEMA = {
    "date": "ISO trading day",
    "open": "open price",
    "high": "high price",
    "low": "low price",
    "close": "close price",
    "volume": "volume (shares)",
    "ma50": "50-day SMA of close",
    "ma150": "150-day SMA of close (Weinstein floor)",
    "ma200": "200-day SMA of close",
    "atr14_pct": "ATR(14) / close × 100 — volatility",
    "adv5": "5-day avg volume",
    "adv50": "50-day avg volume",
    "vol_ratio_5_50": "adv5 / adv50 — recent vs long volume",
    "obv": "On-Balance Volume (cumulative)",
    "obv_30d_slope_pct": "OBV % change over trailing 30 days",
    "pct_above_ma150": "(close / ma150 − 1) × 100",
    "ret_5d_pct": "5-day return %",
    "ret_30d_pct": "30-day return %",
    "rolling_high_20d": "highest high over prior 20 bars (BR resistance)",
    "up_down": "+1 / -1 / 0 — today's close direction vs prior",
}


def _price_history_for_feedback(
    df: pd.DataFrame,
    start_iso: Optional[str],
    scan_end: _date,
) -> list[dict]:
    """Slice OHLCV + per-bar indicators from `start_iso` through `scan_end`.

    Each bar carries the indicators the gates actually use, so the file is
    self-contained for ML training — setup, hold, and post-exit drift, with
    full indicator context. Indicators are computed on the FULL df (so the
    rolling windows are populated even at the start of the slice), then we
    slice to the requested window.

    Empty list if start_iso is None or df is None.
    """
    if df is None or df.empty or not start_iso:
        return []

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # ---- Moving averages on close ----
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    # ---- ATR(14) ----
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    atr14_pct = (atr14 / close) * 100

    # ---- Volume averages ----
    adv5 = volume.rolling(5).mean()
    adv50 = volume.rolling(50).mean()
    vol_ratio_5_50 = adv5 / adv50

    # ---- OBV (cumulative) and 30d slope ----
    diff = close.diff().fillna(0)
    direction = diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv_series = (volume * direction).cumsum()
    # 30-day slope as percent change vs OBV 30 bars ago, scaled to magnitude
    obv_30_ago = obv_series.shift(30)
    obv_30d_slope_pct = ((obv_series - obv_30_ago) / obv_30_ago.abs().replace(0, pd.NA)) * 100

    # ---- Returns ----
    ret_5d_pct = (close / close.shift(5) - 1) * 100
    ret_30d_pct = (close / close.shift(30) - 1) * 100

    # ---- 20d rolling high (BR resistance) — exclude today by shifting ----
    rolling_high_20d = high.shift(1).rolling(20).max()

    # ---- Position relative to 150d MA ----
    pct_above_ma150 = (close / ma150 - 1) * 100

    # ---- Slice to feedback window ----
    idx = df.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    start_ts = pd.Timestamp(start_iso)
    end_ts = pd.Timestamp(scan_end)
    mask = (idx >= start_ts) & (idx <= end_ts)

    out: list[dict] = []
    for ts in df[mask].index:
        try:
            out.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": _safe_round(df.at[ts, "Open"], 2),
                "high": _safe_round(df.at[ts, "High"], 2),
                "low": _safe_round(df.at[ts, "Low"], 2),
                "close": _safe_round(close.at[ts], 2),
                "volume": int(volume.at[ts]) if pd.notna(volume.at[ts]) else None,
                "ma50": _safe_round(ma50.at[ts], 2),
                "ma150": _safe_round(ma150.at[ts], 2),
                "ma200": _safe_round(ma200.at[ts], 2),
                "atr14_pct": _safe_round(atr14_pct.at[ts], 2),
                "adv5": int(adv5.at[ts]) if pd.notna(adv5.at[ts]) else None,
                "adv50": int(adv50.at[ts]) if pd.notna(adv50.at[ts]) else None,
                "vol_ratio_5_50": _safe_round(vol_ratio_5_50.at[ts], 3),
                "obv": int(obv_series.at[ts]) if pd.notna(obv_series.at[ts]) else None,
                "obv_30d_slope_pct": _safe_round(obv_30d_slope_pct.at[ts], 2),
                "pct_above_ma150": _safe_round(pct_above_ma150.at[ts], 2),
                "ret_5d_pct": _safe_round(ret_5d_pct.at[ts], 2),
                "ret_30d_pct": _safe_round(ret_30d_pct.at[ts], 2),
                "rolling_high_20d": _safe_round(rolling_high_20d.at[ts], 2),
                "up_down": int(direction.at[ts]) if pd.notna(direction.at[ts]) else 0,
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _bar_n_back(df: pd.DataFrame, as_of: _date, n: int) -> Optional[str]:
    """Return the iso date of the bar n trading bars before as_of (inclusive).
    Used to surface the setup-window dates so the user can visually verify
    each pick against the actual price chart. Never reads future data —
    we just step back through the same `df` the gates already saw.
    """
    idx = df.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    in_range = df[idx <= pd.Timestamp(as_of)]
    if len(in_range) <= n:
        return None
    return in_range.index[-(n + 1)].strftime("%Y-%m-%d")


def _gate_inputs(pick: PipelineResult) -> dict:
    """Per-gate feature dict for the feedback file. These are the raw
    measurements each gate computed; an RL bandit trains on these.
    """
    out: dict = {}
    for sid in ("HR", "LT", "CS", "VD", "BR"):
        sr = pick.stage_results.get(sid)
        out[sid] = dict(sr.features) if (sr and sr.features) else {}
    return out


def _misses(plan: dict, forward: Optional[dict]) -> dict:
    """Plan-vs-reality gaps for one pick.

    `mfe` (max favorable excursion) = best % above entry achieved during hold.
    `mae` (max adverse excursion) = worst % below entry. Together they tell
    you how close the trade got to T1/T2 even if it didn't formally hit.
    """
    if not forward:
        return {"available": False}

    hit_t1_day = forward.get("hit_t1_day")
    hit_t2_day = forward.get("hit_t2_day")
    hit_stop_day = forward.get("hit_stop_day")
    entry_px = forward.get("entry_px") or 0.0
    path = forward.get("daily_path") or []

    mfe = 0.0
    mae = 0.0
    if entry_px > 0:
        for bar in path:
            high = float(bar.get("high") or 0)
            low = float(bar.get("low") or 0)
            if high > 0:
                mfe = max(mfe, (high - entry_px) / entry_px * 100)
            if low > 0:
                mae = min(mae, (low - entry_px) / entry_px * 100)

    out = {
        "available": True,
        "hit_t1": hit_t1_day is not None,
        "hit_t2": hit_t2_day is not None,
        "hit_stop": hit_stop_day is not None,
        "stopped_before_t1": hit_stop_day is not None and hit_t1_day is None,
        "mfe_pct": round(mfe, 2),
        "mae_pct": round(mae, 2),
    }
    # Delta between expected and actual T1 hit time, if T1 actually hit
    if hit_t1_day is not None and plan and plan.get("t1_expected_days"):
        out["t1_delta_days"] = hit_t1_day - int(plan["t1_expected_days"])
    if hit_t2_day is not None and plan and plan.get("t2_expected_days"):
        out["t2_delta_days"] = hit_t2_day - int(plan["t2_expected_days"])
    return out


def _setup_windows(pick: PipelineResult, df: pd.DataFrame, as_of: _date) -> dict:
    """Per-pick map of when each gate's lookback began. For visual verification:
    open the chart, zoom to base_start..trigger_date and check the algorithm's
    claims against your eyes.
    """
    cs = pick.stage_results.get("CS")
    base_days = (cs.features.get("days_in_band") if cs and cs.features else None) or 30
    return {
        "lt_lookback_start": _bar_n_back(df, as_of, 90),
        "base_start": _bar_n_back(df, as_of, int(base_days)),
        "base_days": int(base_days),
        "dryup_start": _bar_n_back(df, as_of, 5),
        "trigger_date": as_of.isoformat(),
    }


def _strategy_plan(pick: PipelineResult) -> dict:
    """Estimate expected horizons for this pick's T1/T2 hits.

    Heuristic — NOT a guarantee. Uses the consolidation base length and the
    confirmation score to roughly forecast how quickly the trade reaches T1
    (+8%) and T2 (+16%). Rule of thumb from base-and-breakout literature
    (O'Neill, Minervini): post-breakout move duration ≈ ½ × base length,
    accelerated by stronger confirmation.

    Returns:
        {
          "t1_expected_days": int,
          "t2_expected_days": int,
          "setup_strength": "tight" | "normal" | "loose",
          "rationale": str,
        }
    """
    cs = pick.stage_results.get("CS")
    base_days = (cs.features.get("days_in_band") if cs and cs.features else None) or 30
    atr_pct = (cs.features.get("atr_pct") if cs and cs.features else None) or 4.0
    conf = pick.confirmation_score or 1.0

    # Base half-life: half the base length, clamped to a realistic range.
    base_half = max(15, min(120, base_days // 2))

    # Confirmation accelerator: each unit above 2.0 shaves 10% off.
    accel = max(0.6, 1.0 - max(0.0, conf - 2.0) * 0.10)

    t1_days = int(round(base_half * accel))
    t2_days = int(round(t1_days * 2.2))   # T2 is typically ~2× T1 time
    # Respect the day-90 hard exit if T1 doesn't hit — cap T1 estimate at 60.
    t1_days = max(10, min(60, t1_days))
    t2_days = max(45, min(180, t2_days))

    if atr_pct <= 2.5:
        strength = "tight"
    elif atr_pct <= 4.0:
        strength = "normal"
    else:
        strength = "loose"

    rationale = (
        f"{base_days}-day base, ATR {atr_pct:.1f}% ({strength}), "
        f"confirmation {conf:.2f}"
    )

    return {
        "t1_expected_days": t1_days,
        "t2_expected_days": t2_days,
        "t1_target_pct": 8,
        "t2_target_pct": 16,
        "stop_pct": -8,
        "setup_strength": strength,
        "rationale": rationale,
    }


def _month_key(iso: str) -> str:
    return iso[:7]   # "YYYY-MM"


def scan_universe(
    start: str,
    end: str,
    hold_days: int = DEFAULT_HOLD_DAYS,
    top_n: int = DEFAULT_TOP_N,
    capital: float = 100000.0,
    symbols: Optional[list[str]] = None,
    max_workers: int = 10,
    overrides: Optional[dict] = None,
) -> dict:
    """Walk every trading day in [start, end] and collect the picks the live
    strategy would have alerted, with each pick's forward outcome.
    """
    # ---- Validate dates ----
    try:
        start_d = _date.fromisoformat(start)
        end_d = _date.fromisoformat(end)
    except ValueError:
        return {"error": "invalid date(s); expected YYYY-MM-DD"}
    if start_d < MIN_AS_OF:
        return {
            "error": (
                f"start {start} is before {MIN_AS_OF.isoformat()} — universe "
                "drift too large to defend without point-in-time data"
            ),
        }
    if start_d >= end_d:
        return {"error": "start must be strictly before end"}
    if end_d >= _date.today():
        return {"error": "end must be a past date"}

    window_days = (end_d - start_d).days
    if window_days < hold_days:
        return {
            "error": (
                f"Scan window ({window_days} days) is shorter than hold_days "
                f"({hold_days}). Pick a wider window or a smaller hold."
            ),
        }

    sym_set = list(symbols) if symbols else list(UNIVERSE)

    # ---- Prefetch all OHLCV in parallel ----
    fetch_end = end_d + _td(days=int(hold_days * 1.6) + 5)
    lookback = DEFAULT_LOOKBACK_DAYS + window_days + int(hold_days * 1.6) + 10
    log.info(
        "scan_universe: prefetching %d symbols (%s..%s, lookback=%d)",
        len(sym_set), start, end, lookback,
    )
    cache = _prefetch_universe_ohlcv(sym_set, fetch_end, lookback, max_workers)
    if not cache:
        return {"error": "could not fetch OHLCV for any symbol"}
    sym_set = sorted(cache.keys())

    # ---- Resolve the set of trading days in [start, end] using a benchmark ----
    # Use the first available symbol as the trading-day calendar.
    benchmark = cache[sym_set[0]]
    idx = benchmark.index.normalize()
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    in_range = (idx >= pd.Timestamp(start_d)) & (idx <= pd.Timestamp(end_d))
    trading_days = list(benchmark[in_range].index)
    if not trading_days:
        return {"error": f"no trading days between {start} and {end}"}

    # Per-gate aggregate funnel — so even zero-pick scans tell the user
    # WHICH gate is the chokepoint instead of just "no picks today".
    funnel_counts = {sid: {"eval": 0, "pass": 0, "fail": 0} for sid in _SCAN_GATE_IDS}
    fail_reason_top: dict[str, dict[str, int]] = {sid: {} for sid in _SCAN_GATE_IDS}

    # ---- Per-day: run the gate chain on every symbol, rank, take top_n ----
    def _run_one_day_one_symbol(sym: str, as_of: _date) -> tuple[Optional[PipelineResult], dict[str, StageResult]]:
        df = cache.get(sym)
        if df is None:
            return None, {}
        sliced = _slice_to_as_of(df, as_of)
        if len(sliced) < MIN_BARS:
            return None, {}
        ctx = _build_ctx_for_scan(sym, as_of, sliced, overrides=overrides)
        passed = True
        evaluated: dict[str, StageResult] = {}
        for stage_fn in _SCAN_CHAIN:
            try:
                sr = stage_fn(ctx)
            except Exception as e:
                sr = StageResult(
                    stage_id=getattr(stage_fn, "stage_id", "?"),
                    passed=False, reason=f"crash: {e}",
                )
            ctx.stage_results[sr.stage_id] = sr
            evaluated[sr.stage_id] = sr
            if not sr.passed:
                passed = False
                break
        if not passed:
            return None, evaluated
        # Build a PipelineResult so rank_survivors can score it
        return PipelineResult(
            symbol=sym,
            trace_id=ctx.trace_id,
            passed_gates=True,
            composite_score=0.0,
            selected=False,
            rank=None,
            stage_results=ctx.stage_results,
            pick_payload={},
            snapshot=ctx.snapshot,
            signals=None,
            ohlcv=ctx.ohlcv,
        ), evaluated

    picks_chronological: list[dict] = []
    regime_halt_days = 0
    days_with_picks = 0

    for ts in trading_days:
        as_of = ts.date()
        as_of_iso = as_of.isoformat()

        # Regime check using the as_of date — same gate live picks must pass
        regime = check_regime(as_of=as_of_iso)
        if not regime.passed:
            regime_halt_days += 1
            continue

        survivors: list[PipelineResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_run_one_day_one_symbol, s, as_of) for s in sym_set]
            for fut in as_completed(futures):
                try:
                    r, evaluated = fut.result()
                except Exception:
                    r, evaluated = None, {}
                # Aggregate funnel counts regardless of whether the symbol survived
                for sid, sr in evaluated.items():
                    funnel_counts[sid]["eval"] += 1
                    if sr.passed:
                        funnel_counts[sid]["pass"] += 1
                    else:
                        funnel_counts[sid]["fail"] += 1
                        key = (sr.reason or "").split(";")[0].strip()[:60] or "(no reason)"
                        fail_reason_top[sid][key] = fail_reason_top[sid].get(key, 0) + 1
                if r is not None:
                    survivors.append(r)

        if not survivors:
            continue

        selected = rank_survivors(survivors, top_n=top_n)
        if not selected:
            continue

        days_with_picks += 1
        for pick in selected:
            df = cache.get(pick.symbol)
            forward = None
            entry_date_iso: Optional[str] = None
            if df is not None:
                forward_bars = _bars_after(df, as_of)
                entry_info = _resolve_entry(forward_bars)
                if entry_info:
                    entry_px, entry_date_iso = entry_info
                    forward = forward_walk(forward_bars, entry_px, hold_days=hold_days)
                    forward["entry_date"] = entry_date_iso

            try:
                payload = build_pick_payload(
                    pick, pick.snapshot or {},
                    account_value=capital,
                    today_iso=as_of_iso,
                )
            except Exception as e:
                payload = {"error": str(e)}

            # Setup window dates — for visual verification on a chart
            windows = _setup_windows(pick, df, as_of) if df is not None else None
            # Exit date from forward walk (if any)
            exit_date_iso = None
            if forward and forward.get("daily_path"):
                eday = forward.get("exit_day") or len(forward["daily_path"])
                idx_off = max(0, eday - 1)
                if idx_off < len(forward["daily_path"]):
                    exit_date_iso = forward["daily_path"][idx_off].get("date")
            # Full price history for ML training context — setup → exit → drift
            history_start = (windows or {}).get("lt_lookback_start") if windows else None
            # Extend the history window 90d past scan end so post-exit drift is captured
            history_end_d = end_d + _td(days=90)
            price_history = _price_history_for_feedback(df, history_start, history_end_d) if df is not None else []

            plan_dict = _strategy_plan(pick)
            picks_chronological.append({
                "as_of": as_of_iso,
                "entry_date": entry_date_iso,
                "exit_date": exit_date_iso,
                "rank": pick.rank,
                "symbol": pick.symbol,
                "company": (pick.snapshot or {}).get("company"),
                "sector": (pick.snapshot or {}).get("sector"),
                "confirmation_score": pick.confirmation_score,
                "confirmation_components": pick.confirmation_components or {},
                "bonuses_fired": (pick.confirmation_components or {}).get("bonuses_fired") or [],
                "headline": payload.get("headline") if isinstance(payload, dict) else None,
                "entry_px": payload.get("best_buy_at") if isinstance(payload, dict) else None,
                "stop_px": payload.get("stop_loss") if isinstance(payload, dict) else None,
                "target_px": payload.get("sell_target") if isinstance(payload, dict) else None,
                "plan": plan_dict,
                "windows": windows,
                "forward": forward,
                "gate_inputs": _gate_inputs(pick),
                "misses": _misses(plan_dict, forward),
                "price_history": price_history,
            })

    # ---- Aggregates ----
    by_symbol: dict[str, dict] = {}
    by_quarter: dict[str, dict] = {}
    by_month: dict[str, dict] = {}
    total_return = 0.0
    wins = 0
    rets: list[float] = []

    for p in picks_chronological:
        sym = p["symbol"]
        bs = by_symbol.setdefault(sym, {"symbol": sym, "company": p["company"], "n": 0, "returns": []})
        bs["n"] += 1
        if p["forward"] and p["forward"].get("return_pct") is not None:
            r = p["forward"]["return_pct"]
            bs["returns"].append(r)
            rets.append(r)
            total_return += r
            if r > 0:
                wins += 1

        qk = _quarter_key(p["as_of"])
        qb = by_quarter.setdefault(qk, {"key": qk, "n": 0, "returns": []})
        qb["n"] += 1
        if p["forward"] and p["forward"].get("return_pct") is not None:
            qb["returns"].append(p["forward"]["return_pct"])

        mk = _month_key(p["as_of"])
        mb = by_month.setdefault(mk, {"key": mk, "n": 0, "returns": []})
        mb["n"] += 1
        if p["forward"] and p["forward"].get("return_pct") is not None:
            mb["returns"].append(p["forward"]["return_pct"])

    def _finalize(bucket: dict) -> dict:
        r = bucket.pop("returns", [])
        bucket["avg_return_pct"] = round(sum(r) / len(r), 2) if r else None
        bucket["hit_rate_pct"] = round(100 * sum(1 for x in r if x > 0) / len(r), 1) if r else None
        return bucket

    by_symbol_list = sorted(
        (_finalize(v) for v in by_symbol.values()),
        key=lambda x: -x["n"],
    )
    by_quarter_list = sorted((_finalize(v) for v in by_quarter.values()), key=lambda x: x["key"])
    by_month_list = sorted((_finalize(v) for v in by_month.values()), key=lambda x: x["key"])

    summary = {
        "trading_days": len(trading_days),
        "regime_halt_days": regime_halt_days,
        "active_days": len(trading_days) - regime_halt_days,
        "days_with_picks": days_with_picks,
        "total_picks": len(picks_chronological),
        "unique_symbols_picked": len(by_symbol_list),
        "hit_rate_pct": round(100 * wins / len(rets), 1) if rets else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "sum_return_pct": round(total_return, 2) if rets else None,
    }

    # Build funnel rows with top failure reason per gate
    funnel_rows = []
    for sid in _SCAN_GATE_IDS:
        c = funnel_counts[sid]
        reasons = fail_reason_top[sid]
        top_reason = ""
        if reasons:
            t = sorted(reasons.items(), key=lambda x: -x[1])[0]
            top_reason = f"{t[1]}× {t[0]}"
        funnel_rows.append({
            "stage_id": sid,
            "label": {
                "HR": "Hard rejects", "LT": "Long-term flow",
                "CS": "Consolidation", "VD": "Volume/Divergence",
                "BR": "Breakout",
            }[sid],
            "eval": c["eval"], "pass": c["pass"], "fail": c["fail"],
            "top_reason": top_reason,
        })

    return {
        "mode": "C",
        "scope": "universe",
        "symbol": None,
        "start": start,
        "end": end,
        "universe_size": len(sym_set),
        "picks": picks_chronological,
        "summary": summary,
        "by_symbol": by_symbol_list,
        "by_quarter": by_quarter_list,
        "by_month": by_month_list,
        "funnel": funnel_rows,
        "assumptions": _assumptions(hold_days, top_n, capital, overrides),
    }
