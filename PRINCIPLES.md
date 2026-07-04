# Stockiya — Core Principles

> **Follow the institutions. Confirm, don't predict. Enter early, exit early, on volume alone.**

Volume is the only signal that cannot be faked. Institutions cannot enter or exit a position without leaving a footprint in the daily volume tape. We don't pick stocks — we identify where institutions are already accumulating (Wyckoff Phases A–D), confirm the move has started with a Volume-Spread-Analysis bar, size the position to risk a fixed 1 % of the account, and watch for the earliest volume-based exit signal.

> Status: **design spec — Wyckoff-VPA spine (2026-07).** Supersedes the earlier five-serial-gates and weighted-composite spines.
>
> **Live code (2026-07-04 evening) is an intermediate v3 soft-gate composite:** the same stage IDs `[LT] [CS] [VD] [BR]` still run, but as *soft* gates whose margins feed a weighted composite `S = Σ wᵢ · mᵢ`; only `[U] [I] [HR]` remain hard gates. `[ACS]` and `[AC]` are also live. The Wyckoff-VPA stage files (`wyckoff.py`, `vsa.py`, `avwap.py`, `exit_watch.py`) described below are the next-step target — see AGENT_HANDOFF.md.

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

Every threshold that involves a **volume ratio** or a **range %** is normalized by ATR(20):

- Tight-range threshold: `range_pct ≤ 2.5 × ATR20_pct` (was fixed 4 %)
- Breakout volume: `vol ≥ 1.5 × ADV50` in low-vol regime, `≥ 2.0 × ADV50` in high-vol (VIX > 20)
- Dry-up: `adv(5) / adv(50) < 0.60` in normal, `< 0.75` in volatile

The regime multiplier is set once per day from a NIFTY 100 realized-vol reading, not per-ticker.

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
