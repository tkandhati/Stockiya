# Ideas parking lot

Deferred ideas that have been discussed but deliberately not shipped. Each entry states the idea, why it was parked, and the concrete signal that would justify revisiting it.

## Guiding principle

Before entertaining any new scoring-logic change (tilts, new stages, threshold shifts), first check whether `data/traces/` + `learnings.md` already show the current regime is under- or over-scoring in that direction. Change-now without trace evidence is a bet; wait-and-measure is the same signal, cheaper. If the traces show no signal after a reasonable window, the honest move is usually to delete or redesign the dormant machinery, not to bolt more logic on top.

---

## Pre-breakout volume-based fine-tune

**Date parked:** 2026-07-15
**Status:** Deferred pending trace evidence

### The idea

Two phases proposed to strengthen the pre-breakout regime using volume-native signals.

**Phase 1 — Quiet Accumulation Tilt.** Combine dormant Group E metrics into a bounded advisory tilt on the composite score:

- `dry_up_streak_days_p25 >= 3` (already emitted by `[BR]`)
- `anomaly_cluster_count_15d >= 1` (already emitted by `[BR]`)
- Optional: `sma(delivery_pct, 30) / sma(delivery_pct, 90) >= 1.15` (blocked — no `data/delivery/`, firewall)
- Gate: `AC.score >= TRIGGER_AC_MIN_SCORE` (0.6)
- Tilt: up to +5% on composite when both/all fire

**Phase 2 — Wyckoff Spring / No-Supply trigger.** Populate the currently-stubbed `[VSA]` slot with real structural detection:

- Spring: undercut of base support + reclaim + no volume expansion + strong close
- No-Supply Test: narrow spread + dry volume + close in upper half + holding support
- Wire into `classify_trigger` so `pre_breakout` requires structural evidence, not just AC strength

### Why parked

1. **Pre-breakout path is already recently tuned.** The `AC.score >= 0.6` floor in `classify_trigger` was added after the Bajaj-Auto incident. VD-weight is already redistributed to LT/AC on `pre_breakout`. Layering another tilt on top of a recent fix, before that fix has proven itself, risks re-introducing the same over-admission failure from a new angle.

2. **No evidence of under-firing.** Adding a +5% tilt is only worth it if pre-breakout picks are currently ranked systematically too low. That claim needs data, not intuition.

3. **Group E is already traced.** The metrics flow into `data/traces/` daily. `weekly-learn` can measure their predictive value against T+90/T+180 outcomes with zero code changes. Do that first.

4. **Phase 2's wiring assumes a `[VSA]` stage that doesn't exist.** `"VSA": 0.00` in `COMPOSITE_WEIGHTS` is a stub placeholder; no code populates `stage_results["VSA"]`. Would require building a new stage from scratch OR folding into `[BR]` features.

### Signal to revisit

Revisit **only** when all of the following are true:

- [ ] 4–6 weeks of `weekly-learn` output has regressed `dry_up_streak_days_p25` and `anomaly_cluster_count_15d` against T+90/T+180 outcomes.
- [ ] The digest shows a measurable, positive correlation between these metrics and forward returns on names that are otherwise borderline (composite near tau).
- [ ] The tilt strength (currently guessed at 5%) can be calibrated to the *measured* effect size, not chosen arbitrarily.

If Group E shows **no** predictive value after that window, the right move is to delete or redesign it — not to add a tilt.

### Fix-points if it ever ships

- Tilt home: `backend/pipeline.py` (new small helper) or `backend/composite_tilts.py`. Not `breakout.py` — that's [BR] gate math.
- Constants: `QUIET_TILT_MAX = 0.05`, `DRY_STREAK_MIN = 3`, `DELIV_RATIO_MIN = 1.15`, `CLUSTER_MIN = 1`.
- Trace key: `quiet_accum_tilt_applied` in the [S] trace payload so weekly-learn can measure the tilt against outcomes.
- Phase 2 first-cut: option (B) — emit `vsa_event` as advisory feature on `[BR]`, do not touch `classify_trigger` until measured.
- Delivery data: separate ingest problem (firewall + NSE); do not couple to this fine-tune.
