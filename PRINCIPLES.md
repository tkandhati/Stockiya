# Stockiya — Core Principles

> **Follow the institutions. Don't predict, confirm. Take less-risky profit.**

Volume is the only signal that cannot be faked. Institutions cannot enter or exit a position without leaving a trail in the daily volume tape. We don't pick stocks — we identify where institutions are already buying, confirm the move has started, size the position to risk a fixed 1% of the account, and let the trade resolve on its own clock.

> Status: **design spec for the gates-based spine.** A prior weighted-composite spine is being retired; see PROCESS_FLOW.md for operational detail.

---

## 1. The strategy in one paragraph

We scan **Nifty 100** daily after market close. We require the **market regime** to be on — NIFTY 50 *and* BANKNIFTY both above their 50-day moving averages — before any buy alert. For each ticker, **five hard gates must all pass**: 3+ months of long-term institutional flow, a 5–8 week tight consolidation above the 150-day MA, a volume dry-up paired with bullish OBV–price divergence, and an end-of-day breakout closing in the upper third of its candle on 1.5× average volume. Survivors are ranked by **confirmation strength** — the pick with the most independent corroborating signals is #1. The "Today's buy alerts" list naturally churns daily; the **My Positions** page tracks what was bought and what to do with it each day.

---

## 2. Five gates, one chain

| Gate | What passes |
|---|---|
| **Regime** | NIFTY 50 *and* BANKNIFTY both close above their 50d MA |
| **Long-term flow** | OBV-90d slope ≥ +3 %, up/down vol (90d) ≥ 1.1×, 150d MA slope ≥ 0 % (the 6-month-evidence check) |
| **Consolidation** | ATR(14)/Close ≤ 4 %, close > 150d MA, range held for 25–40 trading days |
| **Volume / Divergence** | 5d avg volume < 50 % of 50d avg **and** bullish OBV–price divergence in last 20 days |
| **Breakout** | Close > rolling 20d high (excl. today), volume ≥ 1.5× 50d avg, (close−low)/(high−low) ≥ 0.67 |

Gate failure = no alert. **No weighted scores, no fudge factors.** A composite can compromise. Five hard gates cannot.

---

## 3. Risk math (fixed, not optimised)

- **Account risk per trade:** 1 %. Hard cap.
- **Stop loss:** 8 % below entry. Hard cap.
- **Shares:** `floor(account_value × 0.01 / (entry − stop))`. Set at entry; never adjusted.
- **Target ladder:** sell 50 % at **T1 = entry × 1.08** (1R); raise stop to break-even on the remainder; sell 50 % at **T2 = entry × 1.16** (2R).
- **Worst case after T1 hits:** net −4 % on the half stopped at break-even — half the spec maximum.

---

## 4. Adaptive time stop — timeframe scales with progress

| Day since entry | Action |
|---|---|
| 0–45 | Hold normally |
| 45+, T1 not hit | Tighten stop to entry − 4 % (bank partial progress) |
| 90+, T1 not hit | Exit at market (capital frozen in a non-moving trade is opportunity cost) |
| 90+, T1 hit | Keep T2 leg open up to day 180, then unconditional exit |

Less-risky profit means **banking 1R early, letting only the freed-up half ride for 2R**.

---

## 5. Confirmation ranking — most-confirmed is #1

Among survivors of the four gates, rank by:

```
confirmation = margin_past_thresholds_z_score + 0.5 × bonus_signals_fired
```

**Bonus signals (each +1):**
- 50d MA > 150d MA > 200d MA stacked
- OBV-90d slope ≥ +5 %
- NSE block-/bulk-deal net-buying in last 30 days
- Pocket-pivot fires today
- Top-30 relative-strength rank vs Nifty 100

More bonuses firing = the same setup has more independent confirmations = less-likely false trigger = less-risky entry.

---

## 6. Hard rejects — never alert

- Either regime index below its 50d MA
- Any one of the four gates fails
- Parabolic 30-day move (> +25 %) — institutions are selling to retail
- Auditor exit / open SEBI action / promoter pledge > 50 %

---

## 7. What we will NOT do

- Day-trade or look at intraday charts. Cadence is once-daily, EOD only.
- Override the volume signal with fundamentals.
- Move the stop down or "double down" on a broken hypothesis.
- Trade outside Nifty 100.
- Use leverage, options, or shorts.
- Hide outcomes — every pick is logged with its realised return at T+90 and T+180.

---

## 8. Coding rules that protect the strategy

- **No lookahead.** Decisions for day D use only data up to and including day D's EOD close.
- **No static support / resistance.** All levels are rolling N-day high/low.
- **Split-adjusted data only** (`yfinance auto_adjust=True`, asserted at startup).
- **No curve-fitting.** Thresholds are top-of-file constants tagged `# tunable`, evolved by an RL contextual bandit once ≥ 90 days of outcomes have accumulated — never hand-tuned to last quarter.
- **Every decision is traced.** Each gate writes a JSONL row with its features, score, and evidence. The trace dataset *is* the RL training set.

---

## Disclaimer

Educational. Algorithmic picks are **not financial advice**. Markets are risky; past patterns don't guarantee future returns. Paper-trade the first 10–15 picks before deploying real capital.
