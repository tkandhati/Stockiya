# Stockiya — Core Principles

> **Follow the institutions. Confirm, don't predict. Enter early, exit early, on volume alone.**

Volume is the only signal that cannot be faked. Institutions cannot enter or exit a position without leaving a footprint in the daily volume tape. We don't pick stocks — we identify where institutions are already accumulating (Wyckoff Phases A–D), confirm the move has started with a Volume-Spread-Analysis bar, size the position to risk a fixed 1 % of the account, and watch for the earliest volume-based exit signal.

> Status: **design spec — Wyckoff-VPA spine (2026-07).** Supersedes the earlier five-serial-gates and weighted-composite spines.
>
> **Live code (2026-07-04 evening) is an intermediate v3 soft-gate composite:** the same stage IDs `[LT] [CS] [VD] [BR]` still run, but as *soft* gates whose margins feed a weighted composite `S = Σ wᵢ · mᵢ`; only `[U] [I] [HR]` remain hard gates. `[ACS]` and `[AC]` are also live. The Wyckoff-VPA stage files (`wyckoff.py`, `vsa.py`, `avwap.py`, `exit_watch.py`) described below are the next-step target — see AGENT_HANDOFF.md.
>
> **UI truth-in-labelling (2026-07-12):** because a pick can clear the composite `S ≥ τ` while a listed soft leg failed its own boolean, the UI never asserts "all N gates passed" unconditionally. `hypothesis.build_pick_payload` now emits `gate_confirmation_status = {hard_confirmed | composite_qualified, passed[], failed[]}` and the stock-detail page reads it. See CHANGELOG 2026-07-12.
>
> **Volume signal integrity (2026-07-12):** OBV is a signed cumulative that can cross zero, so `% change vs a base bar` is unstable on long horizons (e.g. `n=120` can print ±thousands of %). `indicators.obv_norm_slope_pct` — linear-regression slope normalized by `mean(|OBV|)` — is now the preferred user-facing form. Existing threshold sites (`lt_flow.py`, `rank.py` bonus) keep the % form so strategy math is unchanged; the ranker can be re-anchored on the norm form once outcomes accumulate.

---

## 1. The strategy in one paragraph

We scan **Nifty 100** daily after market close. We require the **market regime** to be on — NIFTY 100 above its 50-day moving average — before any buy alert. For each ticker we apply the **Wyckoff-VPA spine**: detect an accumulation base (Phases A–D), require today's bar to fire a Volume-Spread-Analysis confirmation (Sign-of-Strength, no-supply test, or pocket pivot), and verify price is holding its **anchored VWAP** from the base low. Structural preconditions are **scored, not hard-gated**, so a strong setup with one slightly-loose sub-check still qualifies; only the trigger bar and hard rejects are binary. Survivors are ranked by **confirmation strength** — the pick with the most independent corroborating signals is #1. Held picks are re-scanned daily for **early volume-based exit signals** (OBV divergence, churning, distribution-day count, anchored-VWAP break) so we drop as early as we entered.

The intended holding period is **3–6 months** (T+90 to T+180 outcome horizons in the trace).

---

## 2. The spine — two gates, one score, one trigger

```
[U]  Universe          gate       In Nifty 100
[I]  Ingest            gate       180 daily bars on file
[HR] Hard rejects      gate       Parabolic 30d / extended above 50d / SEBI flag
[WY] Wyckoff phase     SCORED     Phase-C spring or Phase-D SOS detected on daily bars
[VSA] Bar confirmation TRIGGER    Today: SOS bar OR pocket-pivot OR no-supply test
[AVWAP] VWAP hold      SCORED     Close ≥ anchored-VWAP from base low, and rising
[RK] Confirmation rank score      Sum of scored margins + bonus signals
```

**Why scored, not serial:** the earlier 5-AND-gate chain rejected any ticker that missed a single sub-threshold. In volatile markets that killed 90 %+ of otherwise-strong setups. The new spine keeps **[HR] and [VSA] as hard gates** (safety + trigger) and treats [WY]/[AVWAP] as continuous scores that feed the ranker. A single weak structural leg is tolerated if the trigger and other legs compensate.

### 2.1 Wyckoff phase detection ([WY])

Daily bars are classified into a rolling Wyckoff phase using purely volume-based rules:

| Phase | Signature |
|---|---|
| **A — Selling climax** | Highest 60d volume + widest 60d range + close in lower third; marks trend exhaustion |
| **B — Building cause** | 30-60 sessions of range-bound trading with declining volume trend |
| **C — Spring / test** | Narrow-range bar undercutting Phase-A low on volume < 70 % of 60d avg |
| **D — Sign of Strength** | Wide-range up-close on ≥ 1.5 × 90d avg volume, above 150d MA |
| **E — Markup** | Sustained follow-through (already trending — too late for us) |

We buy in **Phase C or Phase D**, never Phase E. The [WY] score is the confidence in the phase call (0-1) times a phase-preference weight (C = 1.0, D = 0.9, B late-stage = 0.5).

### 2.2 VSA bar confirmation ([VSA])

The trigger is one of three:

| Bar | Rule |
|---|---|
| **Sign of Strength (SOS)** | Close ≥ 20d high, volume ≥ 1.5 × ADV(50), close in upper third of range |
| **Pocket pivot** | Today up-day, today's volume > max down-day volume in prior 10 sessions |
| **No-supply test** | Down-day inside Phase-C low, volume < 60 % of prior 10-day avg, close in upper half |

Any one fires the trigger. Pocket pivot in particular catches the move **5–15 sessions before** the classic 20d-high breakout — that's the "enter early" lever.

### 2.3 Anchored VWAP ([AVWAP])

Anchor at the lowest close of the last 90 sessions. Score = fraction of the last 20 bars whose close is above the anchored VWAP, times sign(slope of AVWAP over 20 bars). Institutional cost-basis holding = strong hand still in control.

### 2.4 Volatility adaptation

**Per-ticker adaptive scan windows** (2026-07-05, live code): the accumulation stages `[ACS]` and `[AC]` no longer use a fixed 20-bar lookback. Each ticker's window triplet is sized from its own realized ATR:

```
atr20_pct = ATR(20) / close × 100
scale     = clamp(2.0 / atr20_pct, 0.5, 2.0)     # 2% ≈ "normal" Nifty large-cap
W_c       = round(20 × scale)                     # per-ticker anchor
windows   = (W_c/2, W_c, 2·W_c)                   # clamped to [5, 60]
```

High-vol stocks (fast tape) scan short windows like `(8, 16, 32)`. Low-vol stocks (slow tape) scan long windows like `(20, 40, 60)`. Each stage takes the max-margin window per ticker — provably ≥ any single-window rule. Deterministic; pure function of the OHLCV frame.

Every threshold that involves a **volume ratio** or a **range %** is normalized by ATR(20):

- Tight-range threshold: `range_pct ≤ 2.5 × ATR20_pct` (was fixed 4 %)
- Breakout volume: `vol ≥ 1.5 × ADV50` in low-vol regime, `≥ 2.0 × ADV50` in high-vol (VIX > 20)
- Dry-up: `adv(5) / adv(50) < 0.60` in normal, `< 0.75` in volatile

The regime multiplier is set once per day from a NIFTY 100 realized-vol reading, not per-ticker.

### 2.5 Trigger-contextual weighting + OBV flow velocity (2026-07-13)

Two changes that target pre-breakout accuracy without loosening any gate.

**Trigger-contextual composite reweighting** — the composite scorer used a
fixed weight vector for every setup. That penalised pre-breakouts (Pocket
Pivot / No-Supply Test) for the property that *defines* them: quiet
mid-term flow. At composite time we now classify the trigger regime from
which gates fired and rebalance the weights (sum-preserving):

```
pre_breakout   AC.score ≥ 0.6  AND  BR fail   →  VD × 0.5; freed share → LT + AC
sos_breakout   BR pass                        →  no change (a new-high on weak flow IS a trap)
neutral        neither                        →  no change
```

The `AC.score ≥ 0.6` floor (added 2026-07-14 after the Bajaj-Auto incident)
is the fragility guard: only strong-base coils earn the VD weight relief. A
marginal AC pass no longer earns short-window benefit-of-the-doubt.

Implementation is in `backend/pipeline.py::classify_trigger` and
`_reweight_for_trigger`. Fix points at the top of the file:
`TRIGGER_MT_STAGE_ID`, `TRIGGER_MT_SHRINK_FRAC`, `TRIGGER_MT_REDISTRIBUTE`,
`TRIGGER_AC_MIN_SCORE`.

**OBV flow-velocity inflection** — a negative 30d OBV tells you flow is
weak, not *when* it got weak. Comparing a 10d slope against a 30d slope
separates healing accumulation from continued distribution:

```
healing        30d slope < 0  AND  10d slope > 0     (multi-week weakness, last 2w up)
hemorrhaging   30d slope < 0  AND  10d slope < 0     (both windows negative)
neutral        anything else
```

Implemented as pure `indicators.obv_flow_inflection(...)` using
`obv_norm_slope_pct` so short and long slopes are on the same scale.
Wired into `[VD]` as a bounded **±5% advisory margin tilt** (reduced from
±10% on 2026-07-14) — not a hard gate, not decision-sized. It can tiebreak
between strong candidates but cannot tip a marginal pick over the composite
threshold. Fix points at the top of `backend/stages/volume.py`:
`VELOCITY_SHORT_WIN`, `VELOCITY_LONG_WIN`, `VELOCITY_MARGIN_BONUS`. Surfaces
`obv_flow_inflection`, `obv_slope_short_pct`, `obv_slope_long_pct` in
StageResult features, so every trace row records the classification.

### 2.6 Advisory pre-breakout volume metrics (2026-07-12)

Additive companions to the multi-lookback machinery above. **No live gate
consumes them yet** — they surface in `backend/stages/breakout.py`'s features
dict and traces, so the tuner can weight them once we have enough outcome
history. They exist because "quiet accumulation before the trigger fires" is
the pre-breakout footprint we want to catch earlier without loosening any
existing threshold.

- **`vol_robust_z_50d`** — `0.6745 · (v_today − median₅₀) / MAD₅₀`. Robust
  to volume's fat right tail; comparable across sleepy large-caps and
  hyperactive small-caps.
- **`dry_up_streak_days_p25`** — consecutive trailing sessions whose volume
  sat below the 25th percentile of the last 50 bars. A streak, not a
  single-day snapshot — a 6-day quiet run inside a tight range is a
  stronger tell than one dry bar.
- **`anomaly_cluster_count_15d`** — count of `|z| ≥ 2` days in the trailing
  15 sessions. Catches "the pocket pivot fired 12 days ago, not today."

---

## 3. Risk math (fixed, not optimised)

- **Account risk per trade:** 1 %. Hard cap.
- **Stop loss:** `max(8 %, 2 × ATR20)` below entry. Volatility-adaptive, but never tighter than 8 %.
- **Shares:** `floor(account_value × 0.01 / (entry − stop))`. Set at entry; never adjusted upward.
- **Target ladder:** sell 50 % at **T1 = entry + 1R** (matches the risk); raise stop to break-even on the remainder; sell 50 % at **T2 = entry + 2R**.
- **Worst case after T1 hits:** net −0.5R on the half stopped at break-even.

---

## 4. Adaptive time stop — timeframe scales with progress

| Day since entry | Action |
|---|---|
| 0–45 | Hold normally |
| 45+, T1 not hit | Tighten stop to entry − 0.5R (bank partial progress) |
| 90+, T1 not hit | Exit at market (capital frozen is opportunity cost) |
| 90+, T1 hit | Keep T2 leg open up to day 180, then unconditional exit |

Combined with §5 exit-watch, "less-risky profit" means **banking 1R early on price, and exiting the whole position early on volume**.

---

## 5. Exit-watch — the volume-based early-exit stage

Runs daily against every open pick. **Any one** of the following fires an exit alert (sell all remaining shares at next open):

| Signal | Rule |
|---|---|
| **OBV negative divergence** | Price makes new 20d high, OBV(20d) makes lower high |
| **Churning bar** | Volume in top 20 % of 50d range, spread in bottom 20 %, close near open |
| **Distribution-day count** | ≥ 3 down-days in 15 sessions each with volume > prior close's volume |
| **AVWAP breakdown** | Two consecutive closes below the anchored VWAP that had held |
| **Climax volume + reversal** | Highest volume in 60d followed by a reversal bar closing in lower third |

Exit-watch is the mirror of [WY] + [VSA]: same volume language, opposite direction. We don't wait for the 8 % stop when the tape says institutions are leaving.

### 5.1 Trigger-aware exit rules (2026-07-13)

The base B1 rule ("any volume signal inverts → exit") is unchanged, but two
additive rules close accuracy gaps opened by the pre-breakout entry work in
§2.5. Both live in `backend/signal_trajectory.py` and integrate into the
existing `TrajectoryReport.exit_recommendation` bit, which already drives
`positions_view._action_for(trajectory_flip=True)`.

**Healing-velocity override (B1')** — for picks whose entry-time VD stage
recorded `obv_flow_inflection == "healing"`. Their 30d OBV was already
negative at entry, so the standard "OBV rolls over" rule would mark them
for exit on day 1. Instead:

```
Within HEALING_GRACE_TRADING_DAYS (default 10 sessions):
    current == hemorrhaging   →  flipped  (10d rolled negative → exit)
    current == healing        →  strong   (thesis intact and firming)
    current == neutral        →  stable
After the grace expires: no more benefit-of-the-doubt; standard indicators own it.
```

**Failed-breakout micro-stop (B1.5)** — armed for SOS-breakout picks
during the first `FAILED_BR_WINDOW_TRADING_DAYS` sessions (default 5). Reads
the 20d resistance level from the entry-day BR trace:

```
if close < resistance_20d AND today's volume >= FAILED_BR_VOLUME_MULT × ADV50:
    exit at next open
```

A breakout that closes back below its own resistance on heavy volume is
distribution-into-FOMO. B1.5 fires strictly earlier than the -8% B2 stop
and only inside the small window where a genuine breakout must hold its
level. Outside the window the rule disarms; B2 owns the trade again.

Fix points at the top of `signal_trajectory.py`:
`HEALING_GRACE_TRADING_DAYS`, `FAILED_BR_WINDOW_TRADING_DAYS`,
`FAILED_BR_VOLUME_MULT`. Both rules cost one trace lookup and one
`obv_flow_inflection` recompute per open position per daily refresh.

---

## 6. Confirmation ranking — most-confirmed is #1

Among survivors of [VSA]:

```
confirmation = wy_score + avwap_score + vsa_margin + 0.5 × bonus_signal_count
```

**Bonus signals (each +1):**
- 50d MA > 150d MA > 200d MA stacked
- OBV-90d slope ≥ +5 %
- Chaikin Money Flow (60d) ≥ +0.15
- NSE block-/bulk-deal net-buying in last 30 days
- Sector-relative volume: today's vol / sector median vol ≥ 1.5
- Top-30 relative-strength rank vs Nifty 100

More bonuses = more independent confirmations = less-likely false trigger.

---

## 7. Hard rejects — never alert

- Regime index below its 50d MA
- Parabolic 30-day move (> +25 %) — institutions are selling to retail
- Extended > 15 % above 50d MA — chasing chase
- Auditor exit / open SEBI action / promoter pledge > 50 %
- Failed [VSA] trigger today (no valid entry bar)

---

## 8. What we will NOT do

- Day-trade or look at intraday charts. Cadence is once-daily, EOD only.
- Override the volume signal with fundamentals.
- Move the stop down or "double down" on a broken hypothesis.
- Trade outside Nifty 100.
- Use leverage, options, or shorts.
- Hide outcomes — every pick is logged with realised return at T+90 and T+180.

---

## 9. Coding rules that protect the strategy

- **No lookahead.** Decisions for day D use only data up to and including day D's EOD close.
- **No static support / resistance.** All levels are rolling N-day high/low or anchored-VWAP.
- **Split-adjusted data only** (`yfinance auto_adjust=True`, asserted at startup).
- **No curve-fitting.** Thresholds are top-of-file constants tagged `# tunable`, evolved by an RL contextual bandit once ≥ 90 days of outcomes have accumulated — never hand-tuned to last quarter.
- **Every decision is traced.** Each stage writes a JSONL row with its features, score, and evidence. The trace dataset *is* the RL training set.
- **Deterministic.** Same OHLCV in → byte-identical trace out. No randomness, no LLM calls.

---

## Disclaimer

Educational. Algorithmic picks are **not financial advice**. Markets are risky; past patterns don't guarantee future returns. Paper-trade the first 10–15 picks before deploying real capital.
