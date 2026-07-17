"""9-state action-label mapping — advisory human-facing enum.

Maps the raw action strings produced by `positions_view._action_for` +
optional soft-signal counts into the honest 9-state ladder the user
asked for:

    MAINTAIN_HEALTHY               — support intact, no distribution
    MAINTAIN_DRY_UP                — volume falling but range tight
    MONITOR_EARLY_WEAKNESS         — one soft indicator deteriorating
    REVIEW_WEAKNESS_CONFIRMED      — two sessions or two independent signals
    EXTEND_5D                      — review date reached, thesis intact
    TAKE_PROFIT_T1                 — 1R target hit
    TAKE_PROFIT_T2                 — 2R target hit
    EXIT_STOP                      — stop hit
    EXIT_DISTRIBUTION              — confirmed distribution
    DATA_UNAVAILABLE               — retain prior state

Advisory only — this file does not enforce any decision. The enforcer
is still `positions_view._action_for`. This module is a labeling layer
so the UI can show the human-readable state alongside the raw action.

The MONITOR / REVIEW / MAINTAIN_DRY_UP states depend on soft-signal
history (two-session hysteresis, persisted warning count). That state
is not yet persisted anywhere — until it is (see ideas.md), those
labels only fire when the caller passes an explicit `soft_signal_count`
or `is_dry_up=True` argument. Otherwise the mapping degrades gracefully
to the raw-action labels.

Fix points:
    _ACTION_TO_LABEL    — hard-mapping table from raw action → label
"""
from __future__ import annotations

from typing import Optional

# Public label enum. Kept as string constants so JSON serialization is
# trivial and no additional Pydantic model is needed to round-trip.
MAINTAIN_HEALTHY = "MAINTAIN_HEALTHY"
MAINTAIN_DRY_UP = "MAINTAIN_DRY_UP"
MONITOR_EARLY_WEAKNESS = "MONITOR_EARLY_WEAKNESS"
REVIEW_WEAKNESS_CONFIRMED = "REVIEW_WEAKNESS_CONFIRMED"
EXTEND_5D = "EXTEND_5D"
TAKE_PROFIT_T1 = "TAKE_PROFIT_T1"
TAKE_PROFIT_T2 = "TAKE_PROFIT_T2"
EXIT_STOP = "EXIT_STOP"
EXIT_DISTRIBUTION = "EXIT_DISTRIBUTION"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

# Raw-action → label. Extended by _resolve() below for the soft-state
# labels that need caller-supplied context.
_ACTION_TO_LABEL: dict[str, str] = {
    "exit_stop":         EXIT_STOP,
    "exit_t2":           TAKE_PROFIT_T2,
    "exit_t1":           TAKE_PROFIT_T1,
    "exit_distribution": EXIT_DISTRIBUTION,
    "exit_final":        EXIT_STOP,           # hard time-stop labeled as an exit
    "exit_time_stop":    EXIT_STOP,           # ditto for day-90 no-T1
    "exit_end_date":     REVIEW_WEAKNESS_CONFIRMED,
    "extend_horizon":    EXTEND_5D,
    "tighten_stop_45":   MONITOR_EARLY_WEAKNESS,
    "hold":              MAINTAIN_HEALTHY,
}


def action_label(
    raw_action: str,
    *,
    close_available: bool = True,
    soft_signal_count: int = 0,
    is_dry_up: bool = False,
) -> str:
    """Map a raw action string to a 9-state ladder label.

    `soft_signal_count` — number of consecutive soft-negative sessions
        (persistence not yet implemented; caller passes 0 today).
    `is_dry_up` — caller's detection of the constructive-dry-up pattern
        (also not persisted; caller passes False today unless explicitly
        detected on today's tape).

    Returns one of the ten public label constants. Always returns a
    label — never None — so downstream renderers don't need null checks.
    """
    if not close_available:
        return DATA_UNAVAILABLE

    # Hard exits + hard take-profits — no hysteresis, always latched to
    # the raw action.
    hard_labels = {
        EXIT_STOP, EXIT_DISTRIBUTION, TAKE_PROFIT_T1, TAKE_PROFIT_T2,
    }
    label = _ACTION_TO_LABEL.get(raw_action, MAINTAIN_HEALTHY)
    if label in hard_labels:
        return label

    # Soft states — layered on top of the raw action.
    if raw_action == "hold":
        if soft_signal_count >= 2:
            return REVIEW_WEAKNESS_CONFIRMED
        if soft_signal_count >= 1:
            return MONITOR_EARLY_WEAKNESS
        if is_dry_up:
            return MAINTAIN_DRY_UP
        return MAINTAIN_HEALTHY

    return label
