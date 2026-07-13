# Stockya — Wishlist

Items analyzed but not shipped. `PROCESS_FLOW.md`, `ARCHITECTURE.md`,
`PRINCIPLES.md`, and `CHANGELOG.md` document **what the system does now**;
this file collects **what it might do next** with the reasoning captured
so future decisions don't re-run the same analysis.

Rules for this file:

- Add an entry when a proposal is analyzed but not shipped.
- When a proposal ships, remove the entry from here and add a shipped
  entry to `CHANGELOG.md`.
- When a proposal is definitively closed, leave a one-line note in the
  "Closed — reviewed and set aside" section below with the reason.

---

## Active — approved, awaiting the right window

### Step 5 — `trigger_state` trace enrichment

Add `trigger_state ∈ {sos, pocket_pivot, none}` to the FINAL trace row
so `outcomes.jsonl` (once T+90 / T+180 cohorts land) has an explicit
column for which of the three `[VSA]` triggers preceded each entry.
Bumps `SCHEMA_VERSION 3 → 4`. Old v3 rows remain readable.

**Why deferred:** zero effect on the pick set today. Ships when the
tuner's first outcome cohort is near — ~2026-10-02 (T+90 first landing)
or ~2026-12-31 (T+180 first landing). Approved in the 2026-07-12
per-step review of the PB/BR split proposal.

**Scope:** ~15 lines across `backend/pipeline.py:append_final_trace`
plus promotion of `_check_pocket_pivot_today` in `stages/rank.py` to a
public predicate (avoids a circular import with `pipeline.py`).

**Risk:** none — trace-only enrichment, no gate change, no zero-picks
risk.

---

## Under consideration — need explicit approval before starting

### Delivery-% overlay from bhavcopy

NSE bhavcopy already writes `data/delivery/<SYMBOL>.csv`. Wire as a
filter or a rank multiplier — high-delivery days are a stronger
institutional-accumulation signal than volume alone. Strategy-touching;
needs explicit approval.

### Block/bulk-deal net-buy from bonus to rank multiplier

Currently a `+1` bonus in `rank.py` (threshold `BONUS_BLOCK_DEAL_MIN = 0.30`,
minimum 2 deals). Promoting to a rank multiplier gives named institutional
trades more weight for confirmation. Wait for outcome data before choosing
the multiplier value.

### Sector-relative volume z-score

Instead of `vol / ADV50` per-ticker, compute z against the sector's
same-day median volume. Catches ticker-level anomalies vs. the peer
group instead of vs. its own history. Adds a sector-metadata dependency
that would need to be cached.

---

## Closed — reviewed and set aside

### PB / BR split into pre-breakout + strict-SOS routes

**Reviewed 2026-07-12.** Six-step proposal validated against
`PRINCIPLES.md` before any code touched disk.

- Steps 1, 4, 6: no standalone work.
  - Step 1: `stages/breakout.py:111-150` already implements SOS bar
    (close ≥ 20d high AND vol ≥ 1.3× ADV50 AND upper-third close);
    nothing else folded in.
  - Step 4: reuse `ClosestToFiringPanel` — only meaningful if step 3
    ships; step 3 rejected.
  - Step 6: validation against `data/ohlcv/ABB.csv` has nothing to
    check without steps 2/3.
- Steps 2, 3: rejected.
  - **Step 2** (build a PB score from `vol_robust_z_50d`,
    `dry_up_streak_days_p25`, `anomaly_cluster_count_15d`, CS
    tightness, pocket-pivot, no-supply). Hand-designed weights on 6
    metrics violates PRINCIPLES §9 ("thresholds evolved by the tuner
    once ≥ 90d of outcomes accumulate — never hand-tuned to last
    quarter") and §2.5 ("no live gate consumes them yet — the tuner
    picks weights once we have enough outcome history"). The current
    spine has zero T+90 outcomes for the post-2026-07-04 pivot; first
    outcome cohort lands ~2026-10-02 (T+90) and ~2026-12-31 (T+180).
  - **Step 3** (route `clears τ ∧ BR fires` → Active Alert;
    `clears τ ∧ BR misses` → Watchlist ranked by PB). Uses BR as the
    trigger, but per PRINCIPLES §2.2 the `[VSA]` trigger is *any of*
    SOS bar / pocket-pivot / no-supply test. Under this rule, a
    pocket-pivot day (the "5–15 sessions earlier" lever §2.2
    explicitly names) gets silently downgraded to Watchlist. Also
    adds a fourth UI surface (ClosestToFiringPanel + Active Alert +
    Watchlist + Positions) for information a single badge could carry.
- Step 5: approved as trace-only enrichment — see the "Active" section
  above.

**Won't ship unless the ratchet path fails.** If by end of 2027 we
still don't have enough outcome history to weight advisory metrics via
the tuner, revisit whether a manual PB score is worth the §9 tradeoff.
