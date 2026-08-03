"""One-off: read hand-copied picks files and summarize the pre-breakout mix.

Read-only. No network. Tells us how many picks are genuinely pre-breakout vs
breakout-confirmed, and what pre-breakout-ish candidates are sitting just below
the line in closest_to_firing — the diagnosis for "increase pre-breakout picks".
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATES = sys.argv[1:] or ["2026-07-31", "2026-08-03"]

PRE_BREAKOUT_STAGES = {
    "DEEP_BASE", "BUILDING_BASE", "COILED_PRE_BREAKOUT", "AT_PIVOT",
    "AT_PIVOT_NO_DEMAND",
}


def g(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default


for date in DATES:
    p = ROOT / "data" / f"picks_{date}.json"
    if not p.exists():
        print(f"\n### {date}: MISSING ({p})")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    picks = d.get("picks") or []
    print(f"\n{'='*70}\n### {date} — {len(picks)} pick(s)   top-level keys: {list(d.keys())}")

    for pk in picks:
        stage = pk.get("entry_stage")
        tier = g(pk, "early_accumulation", "tier")
        elig = g(pk, "pre_breakout_eligibility", "eligible")
        timing = pk.get("entry_timing")
        conf = g(pk, "confirmation", "score")
        comp = pk.get("composite_score")
        is_pre = (stage in PRE_BREAKOUT_STAGES) or (tier == "early")
        print(f"  #{pk.get('rank')!s:>2} {pk.get('symbol'):<16} "
              f"stage={stage!s:<24} tier={tier!s:<6} early?={str(is_pre):<5} "
              f"elig={elig!s:<5} timing={timing!s:<8} conf={conf} comp={comp}")

    stages = Counter((pk.get("entry_stage") or "—") for pk in picks)
    tiers = Counter((g(pk, "early_accumulation", "tier") or "—") for pk in picks)
    n_pre = sum(1 for pk in picks
                if (pk.get("entry_stage") in PRE_BREAKOUT_STAGES)
                or (g(pk, "early_accumulation", "tier") == "early"))
    print(f"  -> pre-breakout picks: {n_pre}/{len(picks)}")
    print(f"  -> entry_stage mix: {dict(stages)}")
    print(f"  -> tier mix: {dict(tiers)}")

    cf = d.get("closest_to_firing") or {}
    if isinstance(cf, dict):
        print(f"  closest_to_firing tabs: { {k: len(v) for k, v in cf.items()} }")
        acc = cf.get("accumulation") or []
        for row in acc[:8]:
            print(f"      ACC near-miss {row.get('symbol'):<16} "
                  f"gap_to_tau={row.get('gap_to_tau')} "
                  f"pulled_down_by={g(row, 'pulled_down_by', 'label') or row.get('pulled_down_by')} "
                  f"comp={row.get('composite_score')}")

    tau = g(d, "regime") and None
    print(f"  composite_threshold_tau (from config, not file): see config/stage_weights.json")
