"""Reconcile today's picks against currently-held portfolio positions.

Two independent code paths write a symbol into `data/picks_<today>.json`
(buy) and compute an action for the same symbol in `data/portfolio.csv`
via positions_view (sell / hold / tighten). Without a reconciliation
step, the UI can recommend selling a stock it is recommending to buy.

This module attaches per-pick annotations. It does NOT drop picks from
the list — callers decide what to render vs what to record:

    Existing row ownership  |  Existing action  |  Annotation on pick
    ─────────────────────── |  ──────────────── |  ─────────────────────
    suggested (never taken) |  any              |  (unchanged)
                                                    portfolio.record_picks
                                                    will supersede the
                                                    old suggested row.
    paper / live (taken)    |  exit_*           |  suppressed_from_ui
                                                    Render must filter
                                                    these; record_picks
                                                    must still persist
                                                    them as a fresh
                                                    "suggested" row so
                                                    the audit trail
                                                    survives.
    paper / live (taken)    |  hold / tighten_* |  already_held
                                                    UI shows the live
                                                    position context.

Fail-open: on any exception the input pick list is returned unchanged.
Reconciliation must never take down the pipeline.

Fix points:
    RECONCILE_HARD_FILTER_ACTION_PREFIXES : action-name prefixes that
        trigger the `suppressed_from_ui` flag (only applied to *taken*
        positions).
    RECONCILE_TAKEN_OWNERSHIPS : ownership values that count as "user
        has real skin in the game" and therefore protect the position
        from replacement.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .positions_view import list_active_positions

IST = ZoneInfo("Asia/Kolkata")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"

log = logging.getLogger("picks_reconcile")

RECONCILE_HARD_FILTER_ACTION_PREFIXES: tuple[str, ...] = ("exit_",)
RECONCILE_TAKEN_OWNERSHIPS: frozenset[str] = frozenset({"paper", "live"})


def _write_suppression_trace(symbol: str, today_iso: str, position: dict) -> None:
    """Append one suppression event to the ticker's trace JSONL."""
    safe = symbol.replace("/", "_").replace(":", "_")
    path = _TRACES_DIR / f"run_{today_iso}_{safe}.jsonl"
    payload = {
        "stage": "PICKS_RECONCILE",
        "event": "pick_suppressed",
        "symbol": symbol,
        "reason": "active_exit_signal_on_taken_position",
        "portfolio_action": position.get("action"),
        "portfolio_action_note": position.get("action_note"),
        "portfolio_pick_id": position.get("pick_id"),
        "portfolio_entry_date": position.get("entry_date"),
        "portfolio_ownership": position.get("ownership"),
        "days_held": position.get("days_held"),
        "ts": datetime.now(IST).isoformat(timespec="seconds"),
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("suppression trace write failed for %s: %s", symbol, e)


def reconcile_picks_against_portfolio(
    pick_payloads: list[dict],
    today_iso: str,
) -> list[dict]:
    """Annotate today's picks with portfolio-position context.

    Returns a new list of the same length as `pick_payloads`. Every pick
    is either unchanged or a shallow copy carrying one of two new keys:

        suppressed_from_ui : {reason, portfolio_action, ...}
            Taken position (paper/live) currently has an exit_* action.
            Render MUST filter these picks; record_picks SHOULD still
            persist them so the fresh signal is tracked as a new
            suggested row alongside the taken one.
        already_held : {pick_id, ownership, days_held, action, ...}
            Taken position with a hold/tighten action. UI displays the
            live-position context alongside the fresh signal.

    On any exception the input is returned unchanged (fail-open).
    """
    if not pick_payloads:
        return pick_payloads

    picks_by_symbol: dict[str, dict] = {p["symbol"]: p for p in pick_payloads}

    def _close_from_picks(sym: str) -> Optional[float]:
        p = picks_by_symbol.get(sym)
        if not p:
            return None
        cp = p.get("current_price")
        try:
            return float(cp) if cp is not None else None
        except (TypeError, ValueError):
            return None

    try:
        active_positions = list_active_positions(_close_from_picks)
    except Exception:
        log.exception(
            "list_active_positions failed during reconcile; "
            "passing picks through unchanged"
        )
        return pick_payloads

    by_symbol: dict[str, dict] = {pos["symbol"]: pos for pos in active_positions}

    reconciled: list[dict] = []
    suppressed_count = 0
    annotated_count = 0
    for pick in pick_payloads:
        sym = pick["symbol"]
        pos = by_symbol.get(sym)
        if pos is None:
            reconciled.append(pick)
            continue

        ownership = (pos.get("ownership") or "suggested").strip()
        action = str(pos.get("action") or "")

        # Suggested-only rows will be superseded at record_picks time.
        # No annotation needed.
        if ownership not in RECONCILE_TAKEN_OWNERSHIPS:
            reconciled.append(pick)
            continue

        # Taken position with active exit signal.
        # Flag for UI suppression, keep in list for portfolio recording.
        if action.startswith(RECONCILE_HARD_FILTER_ACTION_PREFIXES):
            log.info(
                "[reconcile] flagging pick %s suppressed_from_ui: "
                "taken position action=%s",
                sym, action,
            )
            _write_suppression_trace(sym, today_iso, pos)
            annotated = dict(pick)
            annotated["suppressed_from_ui"] = {
                "reason": "active_exit_signal_on_taken_position",
                "portfolio_pick_id": pos.get("pick_id"),
                "portfolio_ownership": ownership,
                "portfolio_action": action,
                "portfolio_action_note": pos.get("action_note"),
                "portfolio_entry_date": pos.get("entry_date"),
                "days_held": pos.get("days_held"),
            }
            reconciled.append(annotated)
            suppressed_count += 1
            continue

        # Taken position with hold/tighten action — annotate but keep visible.
        annotated = dict(pick)
        annotated["already_held"] = {
            "pick_id": pos.get("pick_id"),
            "ownership": ownership,
            "entry_date": pos.get("entry_date"),
            "days_held": pos.get("days_held"),
            "portfolio_action": action,
            "portfolio_action_note": pos.get("action_note"),
            "pnl_pct": pos.get("pnl_pct"),
        }
        reconciled.append(annotated)
        annotated_count += 1

    if suppressed_count or annotated_count:
        log.info(
            "[reconcile] input=%d flagged_ui_suppressed=%d annotated=%d",
            len(pick_payloads), suppressed_count, annotated_count,
        )
    return reconciled


def split_visible_from_suppressed(
    pick_payloads: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split the reconciled list into (visible_for_ui, suppressed).

    Callers that write picks_<date>.json use `visible_for_ui`.
    Callers that write the portfolio ledger use the full list (both
    parts concatenated), so suppressed picks still land as fresh
    "suggested" rows alongside the taken position.
    """
    visible: list[dict] = []
    suppressed: list[dict] = []
    for p in pick_payloads:
        if p.get("suppressed_from_ui"):
            suppressed.append(p)
        else:
            visible.append(p)
    return visible, suppressed
