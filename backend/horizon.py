"""Volume-based holding-horizon estimator.

Replaces the fixed 6-month target_date with a discrete bucketed horizon
derived from confirmation strength + Weinstein stage + entry timing. When
a position reaches its end_date, a lightweight revalidation decides
whether to extend (bump to the next bucket) or exit (trajectory flipped).

Deterministic: same inputs always yield the same bucket. No network,
no live data — safe under the firewall constraint.

Fix points:
    HORIZON_BUCKETS         : ordered tuple of allowed holding days.
                              Change here to add/remove bucket sizes.
    HORIZON_CONF_THRESHOLDS : cumulative confirmation-score bands that
                              map to base score points 0..4.
    HORIZON_STAGE_BONUS     : set of Weinstein stages worth +1.
    HORIZON_TIMING_BONUS    : set of entry_timing values worth +1.
"""

from __future__ import annotations

from typing import Optional

HORIZON_BUCKETS: tuple[int, ...] = (30, 60, 90, 120, 180)

# Confirmation-score bands. Highest matching band wins.
# score >= threshold[i]  →  base points = i
HORIZON_CONF_THRESHOLDS: tuple[tuple[float, int], ...] = (
    (3.0, 4),
    (2.5, 3),
    (2.0, 2),
    (1.5, 1),
)

HORIZON_STAGE_BONUS: frozenset[str] = frozenset({"stage_2_advance"})
HORIZON_TIMING_BONUS: frozenset[str] = frozenset({"early"})


def _base_points_from_conf(conf: float) -> int:
    for threshold, pts in HORIZON_CONF_THRESHOLDS:
        if conf >= threshold:
            return pts
    return 0


def estimated_horizon_days(pick_payload: dict) -> tuple[int, str]:
    """Return (days, basis) at pick time. `days` is one of HORIZON_BUCKETS.

    `basis` is a compact human/machine-readable string describing which
    inputs produced the bucket — it's stored on the portfolio row so the
    reason is auditable months later.
    """
    conf = float(
        (pick_payload.get("confirmation") or {}).get("score") or 0.0
    )
    stage = (pick_payload.get("weinstein_stage") or "").strip()
    timing = (pick_payload.get("entry_timing") or "").strip()

    score = _base_points_from_conf(conf)
    if stage in HORIZON_STAGE_BONUS:
        score += 1
    if timing in HORIZON_TIMING_BONUS:
        score += 1

    idx = max(0, min(score, len(HORIZON_BUCKETS) - 1))
    days = HORIZON_BUCKETS[idx]
    basis = (
        f"conf={conf:.2f}|stage={stage or 'na'}|timing={timing or 'na'}"
        f"|score={score}|bucket={days}d"
    )
    return days, basis


def revalidated_horizon_days(
    current_horizon: int,
    trajectory_healthy: bool,
) -> tuple[Optional[int], str]:
    """Decide what to do when a position reaches its end_date.

    Returns (new_horizon_days_or_None, basis).
      new_horizon_days is None -> recommend exit.
      Otherwise -> extend to the next bucket up.

    A position that is already at HORIZON_BUCKETS[-1] cannot extend
    further and returns None — the caller enforces the terminal exit.
    """
    if not trajectory_healthy:
        return None, "trajectory_flipped_at_end_date"

    if current_horizon in HORIZON_BUCKETS:
        idx = HORIZON_BUCKETS.index(current_horizon)
    else:
        idx = 0

    if idx + 1 >= len(HORIZON_BUCKETS):
        return None, "max_bucket_reached_no_extension"
    new_days = HORIZON_BUCKETS[idx + 1]
    return new_days, f"extended_{current_horizon}d_to_{new_days}d"
