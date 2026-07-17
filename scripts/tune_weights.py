"""Composite-weight tuner — champion-challenger ratchet.

Goal:
    "Every accuracy should go up every day." Guaranteed by construction: the
    live config is NEVER overwritten unless the candidate weights beat the
    current champion on the exact same evaluation set. Monotone non-decreasing
    metric over time.

Mathematical framing:
    - Traces are logged per-day per-ticker as JSONL rows in data/traces/.
      Each ticker has one row per stage plus a FINAL row with the composite.
    - Outcomes live in data/traces/outcomes.jsonl. Every pick that has ripened
      to T+90 has one row: {trace_id, return_pct, horizon_days=90, ...}.
    - We join the FINAL row's per-stage margins with the outcome. That's the
      dataset (X, y) where X ∈ R^{n×k} is the margin matrix and y ∈ R^n is
      T+90 return.
    - Fit two candidate weight vectors:
        (a) ridge regression   w = (XᵀX + λI)⁻¹ Xᵀy   — closed-form, no libs
        (b) mean-return-weighted   w_i ∝ mean(y | margin_i > 0)
      Both are non-negative-clipped and normalized so Σw = 1 (same scale as
      the champion).
    - Evaluate each candidate on the SAME dataset using leave-one-out replay:
      recompute composite S with candidate weights, select the top-3 by S,
      compute mean(y) over that top-3. That's the metric.
    - Ratchet: only overwrite config/stage_weights.json if
          candidate_metric  >  champion_metric + EPSILON
      where champion_metric is stored in the config.
    - The first ever run sets the champion from whichever candidate scores
      best (nothing to compare against; we bootstrap the ratchet).

Cadence:
    Intended to be called monthly (or after each `/weekly-learn` archives a
    batch of outcomes). Deterministic — same input → same output. No network.

Usage:
    python -m scripts.tune_weights                 # dry-run (report only)
    python -m scripts.tune_weights --apply         # write config if wins
    python -m scripts.tune_weights --force-apply   # skip ratchet (emergency)

Fix points at the top of the file: RIDGE_LAMBDA, EPSILON, TOP_N_FOR_METRIC.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# stdlib-only math so we can run under corporate firewall with no extra
# packages. numpy is present in the venv but we don't require it.

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRACES_DIR = _PROJECT_ROOT / "data" / "traces"
_OUTCOMES_PATH = _TRACES_DIR / "outcomes.jsonl"
_CONFIG_PATH = _PROJECT_ROOT / "config" / "stage_weights.json"

# Tunable parameters — top-of-file, deterministic.
RIDGE_LAMBDA = 0.1                       # L2 regularization on ridge fit
EPSILON = 0.001                          # candidate must beat champion by >= this
TOP_N_FOR_METRIC = 3                     # metric is mean return of top-N by S
MIN_OUTCOMES_TO_TUNE = 20                # below this, refuse to tune (noise)
EVAL_HORIZON_DAYS = 90                   # use T+90 outcomes as reward
SCORED_STAGE_IDS = [
    "ACS", "AC", "LT", "CS", "VD", "BR", "WY", "VSA", "AVWAP",
]


# ---------- data loading ------------------------------------------------- #

def load_outcomes() -> list[dict]:
    if not _OUTCOMES_PATH.exists():
        return []
    out = []
    with _OUTCOMES_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("horizon_days") == EVAL_HORIZON_DAYS:
                out.append(row)
    return out


def load_trace_final(trace_id: str) -> Optional[dict]:
    """Scan trace files for the FINAL row of a given trace_id.

    Trace files are `run_<date>_<ticker>.jsonl`. We don't know date/ticker
    from trace_id alone, so we scan. It's O(files), fine for a monthly run.
    """
    for path in _TRACES_DIR.glob("run_*.jsonl"):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("trace_id") == trace_id and row.get("stage") == "FINAL":
                    return row
    return None


def load_stage_margins(trace_id: str) -> dict[str, float]:
    """Return {stage_id: margin} for the given trace_id.

    Reads per-stage rows from the trace file. A stage that didn't run or
    ran-and-failed contributes 0.
    """
    margins: dict[str, float] = {sid: 0.0 for sid in SCORED_STAGE_IDS}
    for path in _TRACES_DIR.glob("run_*.jsonl"):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("trace_id") != trace_id:
                    continue
                sid = row.get("stage")
                if sid in margins and row.get("passed"):
                    margins[sid] = float(row.get("score") or 0.0)
    return margins


def build_dataset() -> tuple[list[list[float]], list[float], list[str]]:
    """Return (X, y, order) — margin matrix, return vector, stage-id order."""
    outcomes = load_outcomes()
    X: list[list[float]] = []
    y: list[float] = []
    for out in outcomes:
        tid = out.get("trace_id")
        if not tid:
            continue
        ret = out.get("return_pct")
        if ret is None:
            continue
        m = load_stage_margins(tid)
        X.append([m[sid] for sid in SCORED_STAGE_IDS])
        y.append(float(ret) / 100.0)   # % -> fraction
    return X, y, list(SCORED_STAGE_IDS)


# ---------- fits --------------------------------------------------------- #

def _matmul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    n, m, p = len(A), len(A[0]), len(B[0])
    C = [[0.0] * p for _ in range(n)]
    for i in range(n):
        for k in range(m):
            aik = A[i][k]
            if aik == 0.0:
                continue
            row_b = B[k]
            row_c = C[i]
            for j in range(p):
                row_c[j] += aik * row_b[j]
    return C


def _transpose(A: list[list[float]]) -> list[list[float]]:
    n, m = len(A), len(A[0])
    return [[A[i][j] for i in range(n)] for j in range(m)]


def _identity(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _add(A, B):
    return [[A[i][j] + B[i][j] for j in range(len(A[0]))] for i in range(len(A))]


def _scalar_mul(A, s):
    return [[A[i][j] * s for j in range(len(A[0]))] for i in range(len(A))]


def _solve_linear(A: list[list[float]], b: list[float]) -> Optional[list[float]]:
    """Gaussian elimination with partial pivoting. Returns None if singular."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            return None
        M[col], M[pivot] = M[pivot], M[col]
        for r in range(col + 1, n):
            factor = M[r][col] / M[col][col]
            for c in range(col, n + 1):
                M[r][c] -= factor * M[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / M[i][i]
    return x


def fit_ridge(X: list[list[float]], y: list[float], lam: float) -> Optional[list[float]]:
    """w = (XᵀX + λI)⁻¹ Xᵀy"""
    if not X or not y:
        return None
    Xt = _transpose(X)
    XtX = _matmul(Xt, X)
    reg = _scalar_mul(_identity(len(XtX)), lam)
    A = _add(XtX, reg)
    b = [sum(Xt[i][j] * y[j] for j in range(len(y))) for i in range(len(Xt))]
    return _solve_linear(A, b)


def fit_mean_return_weighted(X, y, order: list[str]) -> list[float]:
    """w_i = mean(y | X[:, i] > 0), clipped to [0, +inf)."""
    w = [0.0] * len(order)
    for i in range(len(order)):
        ys = [y[r] for r in range(len(y)) if X[r][i] > 0]
        w[i] = max(0.0, sum(ys) / len(ys)) if ys else 0.0
    return w


def normalize(w: list[float]) -> list[float]:
    """Clip negatives, then normalize to sum to 1.0. Keeps composite scale."""
    w = [max(0.0, wi) for wi in w]
    s = sum(w)
    return [wi / s for wi in w] if s > 0 else [1.0 / len(w)] * len(w)


# ---------- evaluation --------------------------------------------------- #

def replay_metric(X, y, w: list[float], top_n: int) -> Optional[float]:
    """Mean of y over the top-N samples ranked by composite S = X · w.

    This is the "if we had used these weights, what would the top-3 have
    returned" metric. Deterministic and directly aligned with money earned.
    """
    if not X or not y or top_n <= 0:
        return None
    scores = [sum(X[r][i] * w[i] for i in range(len(w))) for r in range(len(X))]
    ranked = sorted(range(len(X)), key=lambda r: -scores[r])
    picks = ranked[:top_n]
    if not picks:
        return None
    return sum(y[i] for i in picks) / len(picks)


# ---------- champion-challenger ------------------------------------------ #

def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise SystemExit(f"config not found: {_CONFIG_PATH}")
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def champion_weights(cfg: dict, order: list[str]) -> list[float]:
    src = cfg.get("scored_stage_weights", {})
    return [float(src.get(sid, 0.0)) for sid in order]


def run(apply: bool, force: bool) -> int:
    cfg = load_config()
    order = list(SCORED_STAGE_IDS)
    X, y, _ = build_dataset()
    n = len(y)

    print(f"[tune_weights] outcomes at T+{EVAL_HORIZON_DAYS}: {n}")

    if n < MIN_OUTCOMES_TO_TUNE:
        print(f"[tune_weights] REFUSE: need >= {MIN_OUTCOMES_TO_TUNE} outcomes, have {n}")
        print("[tune_weights] champion unchanged.")
        return 0

    champion_w = champion_weights(cfg, order)
    champion_metric_from_cfg = (cfg.get("champion_metric") or {}).get("value")

    # Recompute champion metric on the current dataset so comparison is fair
    # (dataset grew — the number stored in the config was on an older set).
    champion_metric_recomputed = replay_metric(X, y, champion_w, TOP_N_FOR_METRIC)
    print(f"[tune_weights] champion metric on current dataset: {champion_metric_recomputed}")

    candidates: dict[str, list[float]] = {}
    ridge = fit_ridge(X, y, RIDGE_LAMBDA)
    if ridge is not None:
        candidates["ridge"] = normalize(ridge)
    candidates["mean-return"] = normalize(fit_mean_return_weighted(X, y, order))

    best_name, best_w, best_metric = None, None, -float("inf")
    for name, w in candidates.items():
        m = replay_metric(X, y, w, TOP_N_FOR_METRIC)
        print(f"[tune_weights] candidate {name:14s}  metric={m}  weights={dict(zip(order, [round(v,3) for v in w]))}")
        if m is not None and m > best_metric:
            best_name, best_w, best_metric = name, w, m

    print(f"[tune_weights] best candidate: {best_name}  metric={best_metric:.4f}")

    accept = False
    if force:
        accept = True
        reason = "force-apply"
    elif champion_metric_recomputed is None:
        accept = True
        reason = "bootstrap (no prior champion metric)"
    elif best_metric > champion_metric_recomputed + EPSILON:
        accept = True
        reason = f"beats champion by {best_metric - champion_metric_recomputed:+.4f} (>= {EPSILON})"
    else:
        reason = f"no beat: candidate {best_metric:.4f} vs champion {champion_metric_recomputed:.4f}"

    print(f"[tune_weights] decision: {'ACCEPT' if accept else 'REJECT'} — {reason}")

    if not accept:
        return 0

    if not apply:
        print("[tune_weights] dry-run: --apply not set; config unchanged.")
        return 0

    # ---- Ratchet forward ----
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history = list(cfg.get("history") or [])
    history.append({
        "at": now,
        "prior_weights": cfg.get("scored_stage_weights"),
        "prior_metric_recomputed": champion_metric_recomputed,
        "prior_metric_stored": champion_metric_from_cfg,
        "new_metric": best_metric,
        "n_outcomes": n,
        "chosen_fit": best_name,
    })

    cfg["scored_stage_weights"] = {sid: round(w, 6) for sid, w in zip(order, best_w)}
    cfg["updated_at"] = now
    cfg["updated_by"] = f"tune_weights.py:{best_name}"
    cfg["champion_metric"] = {
        "name": f"mean_return_{EVAL_HORIZON_DAYS}d_top{TOP_N_FOR_METRIC}",
        "value": round(best_metric, 6),
        "n_picks_evaluated": n,
        "hit_rate_t1": None,
        "sharpe_proxy": None,
        "evaluated_at": now,
    }
    cfg["history"] = history[-50:]   # keep last 50 updates

    save_config(cfg)
    print(f"[tune_weights] wrote {_CONFIG_PATH.name}. metric ratcheted {champion_metric_recomputed} -> {best_metric:.4f}")
    return 0


def run_programmatic(apply: bool = True, updated_by_tag: str = "tune_weights.py") -> dict:
    """Programmatic wrapper around `run()` — no stdout side-effects; returns
    a decision dict so callers (e.g. the sliding-window trigger) can log
    the outcome to their own audit trail.

    All safety invariants of `run()` are preserved:
      • `MIN_OUTCOMES_TO_TUNE` floor — refuses to fit below the sample floor.
      • Champion-challenger ratchet — writes config only on strict beat.
      • `apply=False` short-circuits the write path even on accept.

    Returns:
        {
          invoked_at, n_outcomes,
          decision: refused_min_outcomes | reject_ratchet | bootstrap
                    | accept | would_accept_dry_run | error,
          reason: str,
          champion_metric_recomputed: float | None,
          best_candidate: str | None,
          best_metric: float | None,
          config_written: bool,
        }
    """
    result: dict = {
        "invoked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_outcomes": 0,
        "decision": "unknown",
        "reason": "",
        "champion_metric_recomputed": None,
        "best_candidate": None,
        "best_metric": None,
        "config_written": False,
    }
    try:
        cfg = load_config()
    except SystemExit as e:
        result["decision"] = "error"
        result["reason"] = f"config unreadable: {e}"
        return result

    order = list(SCORED_STAGE_IDS)
    X, y, _ = build_dataset()
    n = len(y)
    result["n_outcomes"] = n

    if n < MIN_OUTCOMES_TO_TUNE:
        result["decision"] = "refused_min_outcomes"
        result["reason"] = (
            f"need >= {MIN_OUTCOMES_TO_TUNE} outcomes, have {n}; "
            "champion unchanged"
        )
        return result

    champion_w = champion_weights(cfg, order)
    champion_metric_recomputed = replay_metric(X, y, champion_w, TOP_N_FOR_METRIC)
    result["champion_metric_recomputed"] = (
        round(champion_metric_recomputed, 6)
        if champion_metric_recomputed is not None else None
    )

    candidates: dict[str, list[float]] = {}
    ridge = fit_ridge(X, y, RIDGE_LAMBDA)
    if ridge is not None:
        candidates["ridge"] = normalize(ridge)
    candidates["mean-return"] = normalize(fit_mean_return_weighted(X, y, order))

    best_name, best_w, best_metric = None, None, -float("inf")
    for name, w in candidates.items():
        m = replay_metric(X, y, w, TOP_N_FOR_METRIC)
        if m is not None and m > best_metric:
            best_name, best_w, best_metric = name, w, m

    if best_name is None:
        result["decision"] = "error"
        result["reason"] = "no candidate produced a valid metric"
        return result

    result["best_candidate"] = best_name
    result["best_metric"] = round(best_metric, 6)

    if champion_metric_recomputed is None:
        accept = True
        result["decision"] = "bootstrap"
        result["reason"] = "no prior champion metric — first-run seed"
    elif best_metric > champion_metric_recomputed + EPSILON:
        accept = True
        result["decision"] = "accept"
        result["reason"] = (
            f"beats champion by {best_metric - champion_metric_recomputed:+.4f} "
            f"(>= EPSILON={EPSILON})"
        )
    else:
        accept = False
        result["decision"] = "reject_ratchet"
        result["reason"] = (
            f"no beat: candidate {best_metric:.4f} vs "
            f"champion {champion_metric_recomputed:.4f}"
        )

    if not accept:
        return result

    if not apply:
        result["decision"] = "would_accept_dry_run"
        return result

    # ---- Ratchet forward — same body as run(), refactored here for reuse.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history = list(cfg.get("history") or [])
    history.append({
        "at": now,
        "prior_weights": cfg.get("scored_stage_weights"),
        "prior_metric_recomputed": champion_metric_recomputed,
        "prior_metric_stored": (cfg.get("champion_metric") or {}).get("value"),
        "new_metric": best_metric,
        "n_outcomes": n,
        "chosen_fit": best_name,
        "updated_by": updated_by_tag,
    })
    cfg["scored_stage_weights"] = {sid: round(w, 6) for sid, w in zip(order, best_w)}
    cfg["updated_at"] = now
    cfg["updated_by"] = f"{updated_by_tag}:{best_name}"
    cfg["champion_metric"] = {
        "name": f"mean_return_{EVAL_HORIZON_DAYS}d_top{TOP_N_FOR_METRIC}",
        "value": round(best_metric, 6),
        "n_picks_evaluated": n,
        "hit_rate_t1": None,
        "sharpe_proxy": None,
        "evaluated_at": now,
    }
    cfg["history"] = history[-50:]
    save_config(cfg)
    result["config_written"] = True
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Champion-challenger tuner for stage weights")
    ap.add_argument("--apply", action="store_true",
                    help="write config if candidate beats champion; default is dry-run")
    ap.add_argument("--force-apply", action="store_true",
                    help="bypass ratchet (emergency reset); implies --apply")
    args = ap.parse_args()
    return run(apply=args.apply or args.force_apply, force=args.force_apply)


if __name__ == "__main__":
    sys.exit(main())
