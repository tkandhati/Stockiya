# Stockya — Practical Usage Guide

*How to read the app, pick well, enter, hold, and exit — mapped to the exact fields and badges you see on screen.*

> Every tip below points at a real field name (in `code font`) so you can verify it against what the app shows. Tunable "fix-points" (the constants that set each threshold) are noted where they matter.

---

## 30-second version

- **Buy only** picks tagged `selection_tier = confirmed`, with a healthy `entry_stage`, `level` ≥ `building`, and most `bonuses_fired`.
- **Avoid** anything showing `distribution`, `AT_PIVOT_NO_DEMAND`, `LATE_CHASE`, `FAILED_BREAKOUT_RETEST`, or `selection_tier = lead_watch` (that last one is a *watchlist*, not a buy).
- **Enter** on the trigger — `BREAKOUT_CONFIRMED_TODAY` — near the pivot, not once it's extended. Size off the plan the app hands you (`entry / stop / t1 / t2`, 1% account risk).
- **Hold** while the gauge is green (`HEALTHY`/`STRONG`) and the trajectory is `stable`/`strong`. Don't flinch at an overdue T1 or a single jittery flip.
- **Exit** on `exit_stop`, the profit rungs (`exit_t1`/`exit_t2`), day-180 (`exit_final`), or a *confirmed* multi-day distribution — but **verify a lone `exit_distribution` flip** before acting.

---

## The one mental model that prevents most mistakes

The app runs **two separate systems.** Confusing them is the #1 source of bad decisions.

```
   ┌────────────────────────┐            ┌────────────────────────┐
   │   TODAY'S PICKS LIST    │            │    YOUR PORTFOLIO       │
   │   (candidates to buy)   │            │  (positions you hold)   │
   ├────────────────────────┤            ├────────────────────────┤
   │ picks_<date>.json       │            │ portfolio.csv           │
   │ • selection_tier        │            │ • action (hold/exit_*)  │
   │ • rank / confirmation   │            │ • accumulation_gauge    │
   │ • entry_stage           │            │ • trajectory            │
   │ • bonuses_fired         │            │ • action_label          │
   └────────────────────────┘            └────────────────────────┘
             │                                       │
     "Should I BUY this?"                   "Should I HOLD/SELL this?"
```

**A name leaving the picks list is NOT a sell signal.** Sell decisions come only from your *portfolio* view. The `picks_reconcile` step keeps the two honest:

- You hold it **and** it's a fresh buy again → shown as `already_held` (live context beside the fresh signal).
- You hold it **and** it has an exit signal → the fresh buy is `suppressed_from_ui` (so the app never tells you to buy and sell the same name on the same day).

---

## Part 1 — Identifying the right pick

### Read top-to-bottom; stop at the first red

```
 selection_tier ── "lead_watch" ──▶ WATCHLIST, do not buy yet
      │
   "confirmed"
      │
 accumulation level ── "distribution" ──▶ AVOID (hard override)
      │
 entry_stage ── AT_PIVOT_NO_DEMAND / LATE_CHASE / FAILED_BREAKOUT_RETEST ──▶ AVOID
      │
 (healthy stage)
      │
 rank #1..#3  +  bonuses_fired  +  pre-breakout "eligible"  ──▶ RANK YOUR BUYS
```

### ✅ Green — what a strong, reliable pick looks like

| On screen | You want | Why |
|---|---|---|
| `selection_tier` | `confirmed` | Cleared every gate **and** the confirmation ranker. The only real buy tier. |
| `rank` / confirmation strength | #1 is best (app selects **top 3**, up to 5 when a pre-breakout name is in play) | `confirmation = Σ weighted gate margins + 0.5 × bonus_count` |
| `bonuses_fired` | **more is better** — especially *"Slow+durable accumulation (OBV 90d & 180d positive)"* **and** *"Genuine early entry — not extended"* | An early setup earns **two** bonuses, so genuine-early names outrank mature ones. This is the north-star profile. |
| accumulation `level` | `ready` (best) → `strong` → `building` | `ready` also needs a live breakout trigger — the fully-loaded state |
| `entry_stage` | `COILED_PRE_BREAKOUT`, `AT_PIVOT`, `BREAKOUT_CONFIRMED_TODAY`, `POST_BREAKOUT_HEALTHY` | These render **bullish** (green) in the reasoning checklist |
| pre-breakout badge | `eligible = true` | Passed all 3 guards: no self-veto, coherent institutional flow, real stealth demand (ratio ≥ 1.5) at the right edge |
| `participant_evidence` | `disclosed_large_client` | Higher data confidence (0.85 vs 0.60 for `inferred`) — a real disclosed deal, not an inference |
| Reasoning checklist | CS + VD + BR all green | The actual price/volume gates that fired |

### 🚫 Red — walk away, regardless of rank

- **`level = distribution`** — hard override; the tape is being *distributed into strength*. Exactly the false-breakout profile the engine exists to reject.
- **`entry_stage = AT_PIVOT_NO_DEMAND`** — at the pivot but volume is dry. No demand → no follow-through.
- **`entry_stage = LATE_CHASE`** (>10% over the 20-day MA) or **`FAILED_BREAKOUT_RETEST`** — you're late, or it already failed.
- **`selection_tier = lead_watch`** — below the confirmation threshold. The app is literally saying "wait for the trigger, size cautiously." Watchlist only.
- **delivery-divergence contradiction** — "OBV accumulation not confirmed by delivery — weak & falling." Accumulation the tape won't back up.

### ⚡ React immediately — the entry trigger

The only "act now" entry event is **`entry_stage = BREAKOUT_CONFIRMED_TODAY`** on a `confirmed` pick: close above the 20-day high, on ≥1.5× volume, closing in the upper third of the day's range. That's the pivot firing today.

> **Don't front-run it.** For a `lead_watch` or pre-breakout `eligible` name the whole point is you *wait* for that trigger. Buying the coil early is what the no-demand and self-veto guards protect you from.

---

## Part 2 — Reappearance & persistence (what re-firing signifies)

When a symbol is picked again on a later day, the app attaches two things you should read:

- **`change_since_prev_pick`** — the delta vs its **last** appearance (within a 30-day lookback → fix-point `PICK_DIFF_LOOKBACK_DAYS = 30`). Always carries `prev_date` + `days_ago`; carries `confirmation_score`, `bonuses` (added/removed), `rank_change`, and `price_plan_delta` **only when they actually changed**.
- **`pick_history`** — a newest-first trail of up to 7 prior appearances (`PICK_HISTORY_MAX_ENTRIES = 7`). Each entry has a `direction` tag: `positive` / `negative` / `neutral` / `first_appearance`, plus its `score_delta`.

**The core principle** (this is what the app's own anti-churn study, Rule A vs Rule B, encodes): *a name that keeps qualifying across sessions is stronger evidence than a one-day flash.* Rule B (enter only after a pick **persists ≥2 sessions**) exists precisely to discard one-day-only episodes.

### Scenario table

| What you observe | What it signifies | What to do |
|---|---|---|
| **Reappears, score rising** (`pick_history direction = positive`, `bonuses.added` non-empty, `rank_change.delta < 0` = better rank) | Thesis is **strengthening** — accumulation is compounding, more bonuses firing. Highest conviction. | Strongest buy candidates. If `confirmed` + trigger, act. |
| **Reappears "unchanged"** (`change_since_prev_pick` has only `prev_date`/`days_ago`, no score/price deltas) | **Stable but not improving** — still clearing every gate, no new fuel. | Fine to hold on watchlist; enter on the breakout, not on the mere re-appearance. |
| **Reappears but price stays flat for days** (`price_plan_delta.entry` ≈ 0, small `delta_pct`; score steady) | A **base/coil holding its ground** — the stock keeps qualifying while going nowhere. For a `COILED_PRE_BREAKOUT` / pre-breakout `eligible` name this is *exactly right*: quiet accumulation, not yet triggered. | Be patient — this is the setup working. Enter on the BR trigger (`BREAKOUT_CONFIRMED_TODAY`), not before. Flat price ≠ weakness here. |
| **Appears on alternate / intermittent days** (on the list, off the next, back again) | **Borderline** — sitting right on the composite threshold (τ) and rank cutoff, tipping in and out. This is the "next-day flip" the app tracks. Evidence is not yet decisive. | Treat as watchlist. Wait for it to **persist ≥2 clean sessions** (Rule B) or for a decisive breakout. Don't chase an on-off name. |
| **Reappears, score falling** (`direction = negative`, `bonuses.removed` non-empty, `rank_change.delta > 0` = worse rank) | **Weakening even while still a pick** — bonuses dropping off, rank slipping. The base may be deteriorating. | Downgrade conviction. Prefer a rising-score name at the same rank. If already held, check the trajectory. |
| **Appears once, then disappears** (a one-day-only episode; nothing in `pick_history` next day) | **Weakest evidence** — a single session's qualification that didn't hold. Often a one-day volume spike with no follow-through, or a marginal cross of τ. Rule B discards these entirely. | Do **not** enter on a lone appearance. Wait for it to come back and persist. And remember: if you *already* hold it, its dropping off the picks list is **not** a sell signal — see below. |

> **Key reminder:** disappearing from the picks list only means "no longer a fresh *buy* candidate." It never means "sell." Exit decisions live in your portfolio view only.

---

## Part 3 — Watchlist → entry

- **Where watchlist names live:** the app's `closest_to_firing` panel (top 5 each by `accumulation`, `breakout`, `overall`) and the `watchlist` list. Park your `lead_watch` and pre-breakout `eligible` names here, plus any intermittent/flat-base names from Part 2.
- **The trigger to graduate to a buy:** the name flips `lead_watch → confirmed`, or prints `BREAKOUT_CONFIRMED_TODAY`. For an intermittent name, wait for it to persist ≥2 sessions first.
- **Enter near the pivot** (`AT_PIVOT` / fresh `BREAKOUT_CONFIRMED_TODAY`) — **not** once it's `POST_BREAKOUT_EXTENDED` or `LATE_CHASE`.
- **Size off the app's plan, don't freelance:** `entry_price`, `stop_price` (8%, or 2× ATR if wider), `t1_price` (+1R ≈ +8%), `t2_price` (+2R ≈ +16%), and `shares_total` / `shares_at_t1` (50% at T1) / `shares_at_t2`. The plan is sized to risk **1% of account** per trade — respect it.

---

## Part 4 — Holding confidently

### The accumulation gauge is your "how loud is this?" meter

| Gauge | Meaning | `buffer_text` you'll see |
|---|---|---|
| **5 STRONG** (dark green) | Dips likely bought | "Can skip a check or two — normal hold" |
| **4 HEALTHY** (green) | Thesis intact | normal hold |
| **3 CAUTION** (yellow) | Marginal, watch it | "Reassess at your next check today" |
| **2 WARNING** (orange) | Weakening, decide now | "Act on this check" |
| **1 FLIPPED** (red) | At / through stop | "Exit at next open" |

Pair it with `trajectory.overall` (`strong`/`stable` = hold) and `action_label` (`MAINTAIN_HEALTHY`, `MAINTAIN_DRY_UP`, `EXTEND_5D` = hold). **Green + `stable`/`strong` = sit still.**

### The hold clock (so you don't sell out of impatience)

- **T1 expected ~21 trading days** (`expected_t1_trading_days = 21`, ≈ 3 weeks). Typical swing hold is **3 weeks to 3 months**.
- **Day 45** — stop tightens to ≈ entry − 0.5R (`tighten_stop_45`). This is a *hold*, not an exit.
- **Day 90** — time-stop *only if T1 was never hit* (the dead-money rule).
- **Day 180** — the outer hard cap (`exit_final`). A **ceiling, not a target.** Most working trades resolve long before it.

---

## Part 5 — Exits: when to obey, when to verify

### 🔴 Always obey (the hard exits — don't override)

| Signal | Meaning |
|---|---|
| `exit_stop` (`close ≤ stop`) | The hardest signal; nothing overrides it. Take it. |
| `exit_t1` / `exit_t2` | Mechanical profit rungs: sell 50% at T1 (stop → break-even), sell the rest at T2. |
| `exit_final` (day 180) | Unconditional. |
| **Confirmed distribution** | ≥3 distribution days in 15 sessions, **anchored-VWAP breakdown** (2 closes below the 90-day-anchored VWAP = institutional cost basis lost), or a **bearish volume climax.** Structural — obey. |

### 🟢 Do NOT exit on these (they look scary but aren't sell orders)

1. **`t1_status = overdue`** — T1 is just running late. Advisory only; prompts a trajectory recheck, never an exit. A genuine slow-accumulation base is *supposed* to be slow.
2. **Gauge at 3 CAUTION / 2 WARNING with "can skip / act at next check"** — you still have adversity buffer (`buffer_sessions` = headroom-to-stop ÷ ATR). If the buffer text says you can wait, don't panic-sell on yellow/orange. Let the stop do its job.
3. **A single `exit_distribution` / trajectory flip on one noisy day** — ⚠️ **the one signal to verify before obeying.** A single indicator flip fires `exit_distribution` *immediately* (the two-session hysteresis is not implemented), so a one-day OBV or up/down-volume wobble can trip it. Confirm it's a **real** distribution event (the "always obey" list above) before acting — not one jittery bar.
4. **Neutral entry stages** — `DEEP_BASE`, `BUILDING_BASE`, `POST_BREAKOUT_EXTENDED` are neutral, not sells. A pullback below the 20-day MA *inside* the post-breakout window is classified `POST_BREAKOUT_HEALTHY` — a healthy shakeout, not a failure.
5. **A name disappearing from the picks list** — that's the picks system, not your portfolio. Not a sell signal. (See Part 2.)
6. **Flow-interest pill dropping to `low`, or delivery % fading** — these are **scoring-neutral, display-only.** They never enter selection and are never a valid exit reason.

---

## Field cheat-sheet

**Picks list (buy candidates)**
- `selection_tier` — `confirmed` (buy) vs `lead_watch` (watchlist only)
- `rank` / confirmation strength — #1 best; top 3 selected (up to 5 with a pre-breakout name)
- `bonuses_fired` — the strength signals; more = stronger; genuine-early earns two
- `entry_stage` — where in the base/breakout it is (green vs bearish states above)
- accumulation `level` — `ready` > `strong` > `building` > `emerging`; `distribution` = avoid
- `participant_evidence` — `disclosed_large_client` (high confidence) vs `inferred`
- `change_since_prev_pick` / `pick_history` — reappearance deltas & trail (Part 2)

**Portfolio (holdings)**
- `action` — `hold`, `tighten_stop_45`, `exit_t1`, `exit_t2`, `exit_stop`, `exit_distribution`, `exit_final`, `exit_time_stop`, `exit_end_date`, `extend_horizon`
- `accumulation_gauge` — 1 FLIPPED → 5 STRONG, with `buffer_text`
- `trajectory.overall` — `strong` / `stable` / `weakening` / `flipped`
- `action_label` — plain-language hold/exit state
- `t1_status` — `on_track` / `overdue` / `hit` (advisory, not an exit)

---

## Golden rules

1. **Picks list ≠ portfolio.** Buy from one; sell from the other. Disappearing ≠ sell.
2. **`confirmed` is the only buy tier.** `lead_watch` is a watchlist.
3. **Persistence beats a flash.** A name that keeps qualifying (rising score, ≥2 sessions) outranks a one-day appearance.
4. **Flat price on a coil is the setup working** — patience, enter on the trigger.
5. **Obey stops and confirmed distribution; verify a lone flip.**
6. **Day-180 is a ceiling, not a target.** Overdue-T1 is not an exit.
7. **Flow/delivery are display-only** — never let them drive a buy or a sell.
