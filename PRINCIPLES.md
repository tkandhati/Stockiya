# Stockiya — Core Investing Principles

> **Motto: Don't invent. Follow the institutions. Pick one.**
>
> Volume is the institutional footprint. Smart money cannot enter or exit a
> position without leaving a trail in the volume tape. Our job is not to be
> clever; it is to read the tape and ride along. Everything below is in service
> of that motto.
>
> **Cadence: investing, not trading.** We are buying for 3–6 months. Daily
> close-of-market data is enough — no intraday charts, no live feeds, no
> minute-by-minute volume bars. Open the app once a day, review the three
> cards, hold or rotate. If your hand reaches for the refresh button more than
> once a day, you have switched from investing to trading. Stop.

---

## 0. Volume is the primary signal — on the **investing horizon**, not the trader horizon

**The rule:** We only buy stocks where institutions are visibly accumulating
**over months, not days**. We hold for 3–6 months minimum, so the volume
analysis must look back at least that far. A 5-day burst of buying after 6
months of distribution is not a signal — it's noise.

### Long-term (PRIMARY — the investing-horizon lens)

A candidate must clear the long-term gate first:

- **Stan Weinstein Stage = `stage_2_advance` or `stage_1_to_2`** (the only
  stages we buy). Stage 1 base is "watchlist". Stage 3 is exit. Stage 4 is
  reject.
- **30-week (150-day) MA slope positive** — Weinstein's anchor metric
- **OBV (90-day) and ideally OBV (180-day) rising** — sustained cumulative
  buying for 3–6 months
- **Chaikin Money Flow (60-day) ≥ +0.05** — durable money flow, not a 1-week fluke
- **Up/down volume ratio (90-day) ≥ 1.3** — sustained net buying
- **Base length ≥ 60 days** — institutional bases form over months, not weeks
- **Quarter-over-quarter volume growth ≥ 15%** — interest is broadening
- **Minervini Trend Template** (50d > 150d > 200d, all rising, price > 50d)
  is a strong tailwind when present

### Medium-term (CONFIRMATION — not the decision)

These confirm that the long-term setup is still intact today:

- Wyckoff phase = Accumulation or early Markup
- OBV (30d) rising, CMF (21d) ≥ +0.10, Up/Down (30d) ≥ 1.4×
- Recent 10-day avg vol above 30-day avg
- Pocket Pivot (Morales) firing
- Volume Dry-Up (Minervini) on a tight base
- CAN SLIM-style breakout (O'Neil)
- Price above 60-day VWAP
- MFI (14d) in the healthy 50–80 zone

A perfect 30-day picture without long-term backing is a trade, not an
investment.

### Hard rejects (regardless of fundamentals)

- Wyckoff Distribution or Markdown phase
- Stan Weinstein Stage 4 decline
- 30-week MA sloping down
- OBV (90d) slope negative AND CMF (60d) negative — institutions have been leaving for months
- Parabolic 30-day moves (>+25%) — late retail is buying from institutions

### Target window (set at entry)

Every pick ships with a suggested holding window derived from the long-term
setup, not a single fixed number:

- **3 months ± 1 month** — fresh Stage 1→2 transition with VDU on a tight base,
  or a fresh CAN SLIM breakout. Breakout is pending; the move usually
  resolves in ~3 months.
- **6 months ± 2 months** — Stage 1 long base or early Stage 2 with sustained
  accumulation but no immediate trigger. Slow compounder; expect a longer
  unfold.
- **4–6 months** — default when long-term data is mixed.

---

## 1. Buffett–Munger filter (only after the volume signal is in)

Once volume says institutions are buying, **then** we check the business is
worth holding:

- **Durable moat** — brand, scale, network, regulatory licence, switching costs
- **Quality fundamentals** — ROE ≥ 15%, manageable debt-to-equity, real cash
  flow (not financialised earnings)
- **Honest, capable management** — clean disclosures, no governance flags
- **Circle of competence** — we can explain the business in one paragraph

If volume confirms but the business fails the Buffett filter, we **do not**
override the filter. Bad business + smart money buying often means a short-term
trade, not a 3–6 month hold.

> "It's far better to buy a wonderful company at a fair price than a fair
> company at a wonderful price." — Warren Buffett

---

## 2. Pick carefully, then stick to it

- We pick **0 to 3 stocks per day** — never padded. If only 1 stock clears the
  long-term volume gate, we surface 1. If none clear it, the answer is "nothing
  actionable today" and we sit out. Quality over quantity.
- Every pick we do surface gets a written hypothesis built from the volume
  tape, plus a point-by-point reasoning checklist the user can independently
  verify (Stage, OBV-90d, CMF-60d, base length, etc. — each with a "how to
  verify" instruction).
- Each pick gets the four-scenario exit framework planned **before** entry,
  AND a target window (3m±1m / 6m±2m / 4–6m) so the holding horizon is
  explicit at entry.
- Once entered (notionally), we hold through normal volatility. We exit only
  when the volume picture inverts (Principle 4) or a hard exit triggers.
- Never average down on a broken hypothesis. Average down only on price weakness
  when the volume signal is still intact.
- Concentration is fine. Diversification is for ignorance. If the volume tape
  says all 3 best setups today are in banks, we hold 3 banks. We follow the
  institutions, we don't out-clever them by forcing balance.

---

## 3. Hypothesis-first selection (every pick has a "because")

Every pick must answer all four:

| # | Question | Evidence we look at |
|---|---|---|
| 1 | **What is the volume tape saying?** | Wyckoff phase, OBV, A/D, CMF, MFI, pocket pivots, VDU, CAN SLIM, VWAP |
| 2 | **What is the business?** | Sector, industry, moat, fundamentals (ROE, D/E) |
| 3 | **What is the catalyst in 3–6 months?** | Why is the institutional accumulation happening NOW — earnings, sector rotation, news flow |
| 4 | **Is the company ethical and clean?** | No governance / fraud flags, no regulator action, no controversial product lines |

**Ethics filter (hard reject):**

- Active SEBI / RBI enforcement
- Recent auditor resignation or qualified audit opinion
- Promoter pledge > 50% of holding
- Repeated related-party-transaction concerns
- Industries the user opts out of (defaults: tobacco, gambling, predatory lending)

---

## 4. Clear exit criteria — the volume rule overrides everything

Every pick ships with `best_buy_at`, `sell_target`, **and** the conditions under
which we exit early. We plan four exits:

### A. Target hit (the happy path)
- Exit at `sell_target` (the 3–6 month price set at entry).
- Or trim 50% at target and trail the remainder if the volume is still in.

### B1. Volume distribution (PRIMARY exit trigger)
**This is the rule.** Exit immediately if **any one** of:
- OBV (30d) rolls over into a downslope
- Chaikin Money Flow (21d) crosses below zero
- Down-day volume starts dominating up-day volume (ratio < 0.85)
- Wyckoff phase flips to Distribution

Volume turns before price. We bought because institutions were accumulating;
we sell the moment that signal inverts. Do not wait for price to "confirm."

### B2. Hard price stop (backstop only)
- −10% to −12% from `best_buy_at`, OR a daily close below the 200-day moving
  average for two consecutive sessions.
- This catches us if we missed the volume turn.

### C. Time stop (the patience cap)
- 6 months without target or stop firing → exit and re-evaluate from scratch.
- Capital tied up in a directionless trade is opportunity cost.

### D. Hypothesis-broken (the mind-changer)
- Material adverse event (regulatory action, fraud, auditor exit, customer loss)
- Valuation re-rates above 1.15× sector median before the price target is hit
- The catalyst we bought for fails to appear by the half-way mark
- A substantially better opportunity (≥30% higher expected upside) clears every
  gate including the volume gate

### Discipline rules
- Stops and targets are written **before** entry, never after.
- Stops are not moved down. Targets only move up if the volume signature
  *strengthens*.
- Position sizing is set at entry. We do not "double down to break even."

---

## What we will not do

- Day-trade or chase intraday noise.
- Pick small-caps we cannot verify (universe is Nifty 100 = Nifty 50 + Nifty Next 50).
- Recommend leverage, derivatives, or shorting.
- Override the volume signal with a "but the fundamentals are great" argument.
- Hide losing trades — every closed trade is logged with the actual exit reason.
- Treat the LLM's output as gospel — every pick is sanity-checked against the
  volume signals AND this file.

---

## Disclaimer

This document and the Stockiya app are educational. Picks are algorithmic and
**not financial advice**. Markets are risky; past patterns don't guarantee
future returns. Always do your own research and consult a SEBI-registered
advisor before investing real money.
