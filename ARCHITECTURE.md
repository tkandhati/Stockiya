# Stockiya — Architecture & How It Works

> For someone new to the codebase. Covers loading, processing, every strategy,
> final selection, and the RL instrumentation hooks.

> **⚠ Live code (2026-07-04 evening): v3 soft-gate composite spine.**
> §0-§0.3 below describe the **Wyckoff-VPA target** — that's the design goal.
> The **code as of this commit** runs an intermediate step: the legacy
> `[LT] [CS] [VD] [BR]` stages plus new `[ACS] [AC]` are all wired into
> `PER_TICKER_CHAIN`, but as *soft* gates — failures contribute 0 to the
> weighted composite `S = Σ wᵢ · mᵢ`; only `[U] [I] [HR]` short-circuit.
> Weights are loaded from `config/stage_weights.json` and can be ratcheted
> forward monthly by `scripts/tune_weights.py` (champion-challenger).
> Sections §0.4 onward are archival legacy — do not cite as live behavior.

---

## 0. One-sentence summary — current spine

Stockiya fetches 180 daily bars of OHLCV for every Nifty 100 stock, detects a
Wyckoff-Phase-C-or-D accumulation base using pure volume math, waits for a
Volume-Spread-Analysis trigger bar (Sign-of-Strength, pocket pivot, or no-supply
test), verifies price is holding its anchored VWAP from the base low, and
surfaces the top 0-3 setups each day — each with an entry, ATR-adaptive stop,
1R / 2R target ladder, and volume-based early-exit watch.

**Nothing uses an LLM. Every decision traces to a number, a threshold, and a
file you can open. Same OHLCV in → byte-identical trace out.**

---

## 0.1. Big picture — Wyckoff-VPA spine

```
                    ┌── UNIVERSE (Nifty 100) ──┐
                    │                           │
                    ▼ (parallel per ticker)     │
        ┌──────────── PER-TICKER PIPELINE ────────┐
        │                                          │
   [U]   Universe            gate                  │
   [I]   Ingest (180 bars)   gate    ← finalized-bar hygiene: drops
                                        NaN OHLC, zero-vol, partial-
                                        session (IST-aware) rows
   [HR]  Hard rejects        gate                  │
   [WY]  Wyckoff phase       SCORED (0-1)          │
   [VSA] Bar confirmation    TRIGGER (binary)      │
   [AVWAP] VWAP hold         SCORED (0-1)          │
   [DV]  Distribution veto   HYGIENE (shadow/block) ← anti-institution-
                                        trick guard: weak-close spike,
                                        gap-up bull trap, dist-day
                                        cluster. Default: shadow (trace
                                        only, zero live impact).
        │                                          │
        │ every stage → JSONL trace                │
        └──────────────────────────────────────────┘
                    │
                    ▼ (survivors gathered)
        [RK] Confirmation ranker
             score = wy + avwap + vsa_margin + 0.5 × bonuses
             pick top-N (default 3)
                    │
                    ▼
        [PS] Position sizer  → 1 % account risk, ATR-adaptive stop
        [H]  Hypothesis       → entry / stop / T1 / T2 / time-stops
                              + accumulation_assessment envelope
                                (advisory: level, participant_evidence,
                                 data_confidence, contradictions)
        [R]  Render           → data/picks_<date>.json (schema v7)
                    │
                    ▼
              FastAPI → React UI

        ── ON HELD POSITIONS (daily) ──
        [EX] Exit-watch       ← OBV divergence, churning, dist-days,
                                AVWAP break, climax reversal
                    ▼
              early-exit alert on /api/positions

        ── AT T+90 / T+180 ──
        [O]  Outcome tracker  → outcomes.jsonl (RL reward signal)
```

**Contrast with the retired spine:** [LT], [CS], [VD] were three hard AND-gates
that killed a ticker if any single sub-check failed. Under Wyckoff-VPA, those
structural checks fold into **[WY]** as a *scored* margin — a weak leg is
tolerated when the trigger and other legs compensate. Only [HR] and [VSA]
remain binary.

**Key design choices (unchanged):**
- Every stage is one file in `backend/stages/` with the same signature
  `run(ctx) → StageResult`. Swap a file to swap the logic.
- Every stage result is written to a JSONL file on disk. That file becomes the
  RL training dataset.
- Thresholds are top-of-file `# tunable` constants, ready for a contextual
  bandit once ≥ 90 days of outcomes accumulate.

---

## 0.2. Volume-only vocabulary reference

The spine is built on volume/price-structure primitives. Every stage,
bonus signal, and exit rule uses one of them — nothing else.

| Primitive | Formula | Where used |
|---|---|---|
| **OBV slope** | Linear-regression slope of Granville OBV over N bars, % of level | [WY] score, exit divergence, rank bonus |
| **ADI slope** | Slope of Chaikin Accumulation/Distribution Line | [WY] score |
| **Anchored VWAP** | `Σ(price×vol) / Σ(vol)` from anchor date, price-weighted running | [AVWAP], exit break |
| **ADV(N) / vol ratio** | `sma(volume, N)`; today's vol / ADV50 = vol ratio | [VSA] trigger, exit churning |
| **ATR20 %** | 20-bar avg true range as % of close — the volatility clock | Threshold normalizer everywhere |
| **Close Location Value (CLV)** *(2026-07-17)* | `(2C − H − L) / (H − L)` clamped to [-1, +1] | Signed foundation for pressure metrics; used by `[DV]` weak-close checks |
| **Signed volume pressure** *(2026-07-17)* | `CLV_t × clip(V_t / median(V, N=60), 0, 3.0)` — signed, spike-clipped | Fuels EWM aggregates over 5 / 20 / 60 bars; unwired in composite until shadow-mode outcomes justify a weight |
| **Client-classified deal flow** *(2026-07-17)* | Regex classifier over NSE block/bulk client names → `custodian \| fii \| dii \| prop \| individual \| unknown` | `has_disclosed_large_client` fuels the assessment envelope's `participant_evidence` ladder |

**Anti-institutional-trick property.** Signed volume pressure is the
primitive that lets `[DV]` distinguish an *absorbed* volume spike (buyers
won, CLV positive) from a *distributed* one (sellers won, CLV negative)
— OBV cumulatives cannot make that call. The RV clip at 3.0 plus EWM
aggregation over 5–60 bars means no single engineered print can dominate
the aggregate. The foundation is a distribution of recent bars, not a
reactive read on today's spike.

---

## 0.2b. Exit decision priority — `positions_view._action_for`

The order in which exit checks fire determines which reason gets stamped
onto every outcome label. Getting this wrong poisons the tuner. As of
2026-07-17 the order is (highest priority first):

    1.  close is None            → hold (data safety)
    2.  close ≤ stop             → exit_stop
    3.  close ≥ t2               → exit_t2 (take profit, remaining shares)
    4.  close ≥ t1 (not hit_t1)  → exit_t1 (take profit, partial + BE stop)
    5.  trajectory_flip          → exit_distribution
    6.  days_held ≥ DAY_180      → exit_final (hard time-stop)
    7.  days_held ≥ DAY_90       → exit_time_stop (pre-T1 recycle)
    8.  end_date_reached         → extend_horizon | exit_end_date
    9.  DAY_45 ≤ days_held < DAY_90 (not hit_t1) → tighten_stop_45
    10. else                      → hold

**Historical note (bug fixed 2026-07-17):** the pre-fix order ran
`trajectory_flip` and `DAY_180` BEFORE the price-driven exits. That meant
a position hitting its stop on day ≥ 180 was labeled `exit_final`, and a
T2 hit on a distribution day was labeled `exit_distribution`. Both wrong
— the tuner should see the HARDEST reason, not the first-in-file
reason. The new order fixes label attribution across every outcome
recorded from 2026-07-17 onward. Older `outcomes.jsonl` rows retain
their v1 labels (see label-schema versioning below).

**Anti-flip guardrail (parked in ideas.md as pillar D):** a single
soft-negative session should not fire `exit_distribution` — the plan
requires either (a) two consecutive finalized sessions with the same
warning, or (b) two independent evidence families (price-structure +
volume distribution) agreeing. Persistence of that warning count needs
a new column on `data/portfolio.csv`. Until it lands, `trajectory_flip`
alone still triggers `exit_distribution` at priority #5.

---

## 0.2c. Outcome label schema v2 — mark-to-market vs realized

`data/traces/outcomes.jsonl` rows now carry `label_schema_version: 2`
and separate two ideas the v1 schema conflated:

| Field | Meaning |
|---|---|
| `mtm_return_pct` | Close-at-snapshot / entry − 1. Always defined. **This is what the tuner trains on.** Extension-friendly picks that keep running past their bucket score honestly here — the label doesn't require the position to be closed. |
| `return_pct` | Legacy alias for `mtm_return_pct`. Kept so v1 readers (existing tuner) work unchanged. |
| `is_open` | Position still open at snapshot day? `False` iff portfolio row's `status ∈ _CLOSED_STATUSES`. |
| `realized_return_pct` | Populated only when `is_open=False`. Reads `exit_price` from the portfolio row (authoritative — the enforcer wrote it). Lifecycle-accurate ladder P&L is v3 (parked). |
| `exit_reason_final` | The terminal exit reason if closed; `null` otherwise. |

**Extension-friendly-by-design property.** A stock held past its
`end_date` bucket, running to +40% by T+180, gets `mtm_return_pct = +40`
at the T+180 snapshot even though the position is still open. The tuner
correctly rewards setups that extend well. Realized P&L is written once,
when the position eventually closes — decoupled from the snapshot
horizon. See `CHANGELOG.md → 2026-07-17 (later-3)` for the full contract.

---

## 0.2d. Learning loop — sliding-window ⇒ champion-challenger

```
    outcome append (T+90)
              │
              ▼
    sliding_window_learn.maybe_fire_event()
              │
              ├─► per-stage IC (Pearson r vs return over last 5)
              │
              └─► scripts.tune_weights.run_programmatic(apply=True)
                                                            │
                          ┌─────────────────────────────────┴───┐
                          ▼                                     ▼
                   n < MIN_OUTCOMES_TO_TUNE=20            n ≥ 20
                          │                                     │
                          ▼                                     ▼
                   decision = refused_min_outcomes        ridge + mean-return fits
                   config unchanged                       replay metric per candidate
                                                                │
                                                                ▼
                                                      candidate_metric >
                                                      champion_metric + EPSILON=0.001 ?
                                                                │
                                              yes ──► config_written = True
                                              no  ──► config_written = False
```

**Two floors gate every write to `config/stage_weights.json`:**

  a. `MIN_OUTCOMES_TO_TUNE = 20` — below this the tuner refuses. Prevents
     noise-chasing at small n.
  b. Strict-beat ratchet — even above the floor, only strict improvement
     over the incumbent metric writes. Accuracy cannot regress.

Every fire writes one event file to `data/learning_events/sliding_*.json`
with both the IC block (visibility) and the CC decision (action). Full
audit trail without touching git or the config log.

Set `CHAMPION_CHALLENGER_MODE = "dry_run"` in
`backend/sliding_window_learn.py` to observe decisions without writing.
Set `"disabled"` to skip the CC step entirely and emit diagnostic-only
events.

---

## 0.3. Retired sections — archival reference only

Everything below this line was written for the earlier weighted-composite spine
(LT=50%, TT=15%, MT=20%, DD=10%, BR=5%) and the intermediate 5-serial-gates
spine. It's kept for historical context and because parts of the file map
(§11) and the API surface (§8) are still current. **When in doubt, defer to
PRINCIPLES.md and §0.1 above.**

---

## 0.4. One-sentence summary (retired spine)

Stockiya fetches a year of daily OHLCV data for every Nifty 100 stock, runs it
through a deterministic scoring pipeline that measures institutional-volume
accumulation over multiple time horizons, and surfaces the top 0–3 picks each
day — each with a full entry/target/stop plan and auditable reasoning.

**Nothing uses an LLM. Every decision traces to a number, a threshold, and a
file you can open.**

---

## 1. Big picture — how the system hangs together

```
                            ┌──── UNIVERSE (100 tickers) ────┐
                            │                                 │
                            ▼ (parallel, one thread/ticker)   │
         ┌──────────── PER-TICKER PIPELINE ─────────────┐    │
         │                                               │    │
 Yahoo Finance / NSE                                     │    │
      ↓                                                  │    │
 [I] Ingest ──── AccumulationSignals (50+ indicators) ──►│    │
      ↓                                                  │    │
 [HR] Hard Rejects ──── fail → skip ticker               │    │
      ↓                                                  │    │
 [LT] LongTerm  (50%) ── score 0–1                       │    │
 [TT] TrendTemplate (15%) ── score 0–1                   │    │
 [MT] MidTerm   (20%) ── score 0–1                       │    │
 [DD] DirectDeals (10%) ── score 0–1                     │    │
 [BR] Breakouts  (5%) ── score 0–1                       │    │
      ↓                                                  │    │
      │ stage results + features → JSONL trace           │    │
      └──────────────────────────────────────────────────┘    │
                            │                                 │
                            ▼ (all tickers gathered)          │
                    [S] Score & Rank                          │
                    Composite = Σ(weight × score) × 100       │
                    Pick top-3 where composite ≥ 60           │
                            │                                 │
                            ▼                                 │
                    [H] Hypothesis + Exit Plan                │
                    [R] Render → picks_<date>.json            │
                            │                                 │
                            ▼                                 │
                      FastAPI → React UI                      │
                            │                                 │
                            ▼ (at T+90 and T+180)             │
                    [O] Outcome tracker                       │
                    Logs realized return to outcomes.jsonl    │
                    (this is the RL reward signal)            │
```

**Key design choices:**
- Every stage is one file in `backend/stages/` with the same signature
  `run(ctx) → StageResult`. Swap a file to swap the logic.
- Weights live in one dict in `backend/pipeline.py:STAGE_WEIGHTS`. Tune without
  touching any strategy code.
- Every stage result is written to a JSONL file on disk. That file becomes the
  RL training dataset.

---

## 2. Stage-by-stage: data loading

### Stage [U] Universe — `backend/stages/universe.py`

**What it does:** Gate. Checks if the ticker is in the Nifty 100 list.

```
Input:  ticker symbol (e.g. "HDFCBANK.NS")
Check:  symbol in backend/universe.py:UNIVERSE  (100 tickers hardcoded)
Output: passed=True → continue | passed=False → skip forever
```

**Fix point:** `backend/universe.py` — edit the list to add/remove tickers.

---

### Stage [I] Ingest — `backend/stages/ingest.py`

**What it does:** Gate + data fetch. Pulls OHLCV history and computes all
indicators. If this stage passes, every downstream stage reads from the already-
computed `ctx.signals` — no repeated network calls or math.

**Data sources:**

| Source | When | File |
|--------|------|------|
| Yahoo Finance (yfinance) | default | `backend/yahoo.py` |
| NSE bhavcopy | `DATA_SOURCE=bhavcopy` (future) | `backend/fetch.py` |
| Synthetic demo fixtures | `DEMO_MODE=1` | `backend/demo_data.py` |

**What is fetched:**

```python
# backend/yahoo.py
ohlcv = yfinance.history(ticker, period="1y")   # ~260 rows of OHLCV
snapshot = {
    "current_price": ...,
    "company": ...,
    "sector": ...,
    "52w_high": ...,
    "52w_low": ...,
}
```

**NSE block/bulk deals (separate fetch):**

```python
# backend/block_deals.py
# Downloads https://archives.nseindia.com/content/equities/block.csv
# and bulk.csv, caches locally in data/deals/
# Aggregates to per-ticker 30-day buy/sell counts and net qty ratio
```

**After fetch: signal computation**

All 50+ indicators are computed once in `backend/volume_signals.py:compute()`.
The result is stored in `ctx.signals` as an `AccumulationSignals` dataclass.
Every scoring stage reads from it; nothing is recomputed.

**Fix point:** If ingest fails (network error, bad ticker), the stage sets
`passed=False` and writes the reason to the trace. The ticker is skipped.

---

## 3. Signal computation — the math engine

**File:** `backend/volume_signals.py`  
**Input:** ~260 rows of OHLCV (1 year of daily data)  
**Output:** `AccumulationSignals` dataclass with ~50 fields

All indicators fall into four groups:

### Group A — Long-term lens (3–6 month horizon)

These answer: *"Have institutions been accumulating for months?"*

| Indicator | Window | Formula summary | Why it matters |
|-----------|--------|-----------------|----------------|
| **OBV slope (% form)** | 90d, 180d | Cumulative sum of (volume × sign of price change); `(OBV_now / OBV_t−n − 1) × 100` | Legacy % form — kept for existing thresholds. Unstable when the base bar is near zero (OBV can cross zero). |
| **OBV norm-slope (2026-07-12)** | 90d, 180d | Linear-regression slope over the window, normalized by `mean(|OBV|)`, scaled to % / window | Zero-crossing-safe. Preferred for user-facing display; ranker thresholds will migrate here once outcomes accumulate. |
| **CMF** | 60d | Σ[ (C-L)-(H-C) / (H-L) × Vol ] / Σ Vol (Chaikin Money Flow) | Close-proximity-weighted flow; ≥ +0.05 = sustained buying |
| **Up/Down vol ratio** | 90d | Sum of up-day volumes / sum of down-day volumes | > 1.3 = net buying over 3 months |
| **A/D line slope** | 30d | Chaikin Accumulation/Distribution cumulative | Direction of institutional money |
| **30-week MA + slope** | 150d MA, 50d slope window | Simple moving average and its linear slope | Weinstein's primary trend anchor |
| **MA stack** | 50d, 150d, 200d | Check 50 > 150 > 200 and all slopes > 0 | Minervini Trend Template structure |
| **Base length** | Dynamic | Count days where price stays in a ±10% band | Institutions build bases over months; < 60d = too short |
| **QoQ vol growth** | 63d vs prior 63d | (vol_63d_avg / vol_prev_63d_avg - 1) × 100 | Growing interest = broadening accumulation |
| **Weinstein Stage** | Full history | Stage 1 base / 1→2 breakout / 2 advance / 3 top / 4 decline | Long-term classification: we only buy Stage 2 or 1→2 |

### Group B — Mid-term lens (recent weeks)

These answer: *"Is the buying still happening this month?"*

| Indicator | Window | Formula summary | Why it matters |
|-----------|--------|-----------------|----------------|
| **OBV slope** | 30d | Same as above, 30-day window | Is the accumulation still active today? |
| **CMF** | 21d | Same as above, 21-day window | Month-to-date money flow confirmation |
| **MFI** | 14d | RSI applied to (typical price × volume) instead of price | Volume-weighted RSI; 50–80 = healthy accumulation zone |
| **VWAP** | 60d rolling | Price > 60d VWAP = buyers in control | Institutional benchmark: above VWAP = net positive |
| **Up/Down vol ratio** | 30d | Same formula, 1-month window | Short-term confirmation |
| **Vol trend** | 10d vs 30d | (vol_10d_avg / vol_30d_avg - 1) × 100 | Is volume accelerating right now? |
| **Wyckoff phase** | Recent price/vol pattern | Accumulation / Markup / Distribution / Markdown | Schematics from Richard Wyckoff; identifies institutional activity phase |

### Group C — Breakout patterns (entry timing)

These answer: *"Is there a fresh trigger to enter today?"*

| Pattern | Logic | Source |
|---------|-------|--------|
| **Pocket Pivot** | Up-day volume > max of prior 10 down-day volumes, on a tight base | Morales & Kacher |
| **Volume Dry-Up (VDU)** | 5d avg vol < 50% of 50d avg, within a ±10% price band | Minervini |
| **CAN SLIM breakout** | Price within 2% of 20d high AND volume ≥ 1.4× 50d avg | William O'Neil |

### Group D — Direct institutional deals

| Metric | Source | Logic |
|--------|--------|-------|
| **Block deals** | NSE archives | Trades > 5 lakh shares or > ₹5 cr; named buyer/seller |
| **Bulk deals** | NSE archives | Trades > 0.5% of total shares; named buyer/seller |
| **Net qty ratio** | Aggregated 30d | (buy_qty - sell_qty) / (buy_qty + sell_qty) in [-1, +1] |

**Fix point for math:** `backend/volume_signals.py`. All thresholds that the
stages check are defined per-stage in the stage files, not here — this file
just computes the raw numbers.

### Group E — Pre-breakout advisory metrics (2026-07-12)

Emitted by `backend/stages/breakout.py` into its features dict; **no gate
threshold currently consumes them**. Companions to (not replacements for)
the multi-lookback machinery in Group B, added so a quietly-accumulating
name can be surfaced by the tuner without loosening any existing check.

| Metric | Formula | Reads as |
|--------|---------|----------|
| **`vol_robust_z_50d`** | `0.6745 · (v_today − median₅₀) / MAD₅₀` | Robust per-ticker z. \|z\|≥2 = anomaly |
| **`dry_up_streak_days_p25`** | Consecutive trailing bars with `v < p25(v, 50)` | Streak of quiet accumulation |
| **`anomaly_cluster_count_15d`** | Count of `z ≥ 2` days in trailing 15 | Institutional-footprint cluster |

Root feedback: `Stockya-tuner/scripts/test_prebreakout_feedback.py` reproduces
ABB.NS's readings against these metrics.

### OBV computation — single source of truth (2026-07-12)

Historical bug: `volume_signals.py` and `indicators.py` both cumsum'd their
own OBV series, and `signal_trajectory.py` / `backtest.py` computed their
own % slopes off different bases. That's how the card and detail page for
the same ticker could disagree (e.g. OBV-90d +357 % vs +198 %).

- `backend/indicators.py:obv()` is now the only OBV cumsum in the engine.
- `backend/volume_signals.py` imports it (no local copy).
- The old `obv_slope_pct(obv, n)` is retained for existing threshold sites
  (`lt_flow.py` LT gate; `rank.py` bonus signal) that reference documented
  numeric thresholds — but marked with a WARNING docstring.
- New `indicators.obv_norm_slope_pct(obv, n)` is the preferred user-facing
  form: bounded across zero crossings, sign-consistent with the % form.

---

## 4. Processing pipeline — the gate stages

Before scoring begins, three gate stages filter the universe.

### Stage [HR] Hard Rejects — `backend/stages/hard_rejects.py`

Any **one** condition kills the ticker immediately:

```
┌─────────────────────────────────────────────────────────────────┐
│ HARD REJECT conditions (any one → skip, no score)               │
├─────────────────────────────────────────────────────────────────┤
│ 1. Wyckoff phase = "distribution" OR "markdown"                 │
│    (institutions are LEAVING — why buy?)                        │
│                                                                 │
│ 2. Weinstein Stage = "stage_4_decline"                          │
│    (clear long-term downtrend)                                  │
│                                                                 │
│ 3. 30-week MA slope < -0.5%                                     │
│    (primary trend anchor is broken)                             │
│                                                                 │
│ 4. OBV-90d slope < 0 AND CMF-60d < 0                           │
│    (both long-term flow metrics confirm institutions leaving)   │
│                                                                 │
│ 5. Price change over 30d > +25%                                 │
│    (parabolic — retail FOMO, institutions selling TO us)        │
└─────────────────────────────────────────────────────────────────┘
```

**Why before scoring:** Cheap. Saves compute on 40–50% of the universe every day.
The failed ticker still gets a trace row with the rejection reason.

---

## 5. Scoring strategies — how each stage works

Each scoring stage returns a `score` in [0.0, 1.0]. The score is the fraction of
its component checks that passed. More checks passing → higher score.

### Strategy LT — Long-Term Volume (weight: 50%)
**File:** `backend/stages/lt_volume.py`  
**Question:** "Have institutions been accumulating for months?"

```
Components (each = 1.0 if condition met, 0.0 if not):

  1. Weinstein stage ∈ {"stage_2_advance", "stage_1_to_2"}   → 1/0
  2. 30-week MA slope ≥ 0%                                    → 1/0
  3. OBV-90d slope ≥ +5%                                      → 1/0
  4. OBV-180d slope ≥ 0%                                      → 1/0
  5. CMF-60d ≥ +0.05                                          → 1/0
  6. Up/Down vol ratio (90d) ≥ 1.3×                           → 1/0
  7. Base length ≥ 60 days                                    → 1/0
  8. QoQ volume growth ≥ +15%                                 → 1/0

  LT score = mean(components 1–8)  →  range [0.0, 1.0]
```

**Example:** 6 of 8 pass → LT score = 0.75.  
**Contribution to composite:** 0.75 × 0.50 × 100 = 37.5 points.

**Fix point:** `backend/stages/lt_volume.py` — thresholds named as module
constants (e.g. `CMF_60D_MIN = 0.05`).

---

### Strategy TT — Trend Template (weight: 15%)
**File:** `backend/stages/trend_template.py`  
**Question:** "Is the Minervini price structure healthy?"

```
Components:

  1. MA stack intact: 50d MA > 150d MA > 200d MA              → 1/0
  2. Price > 50d MA                                           → 1/0
  3. 30-week MA slope > 0                                     → 1/0
  4. Full Minervini template (all 4 criteria together)        → 1/0

  TT score = mean(components 1–4)
```

**Why 15%:** Structure is a tailwind, not the signal. A stock can have great
long-term volume accumulation with a sloppy MA structure (common in early
Stage 1→2 transitions).

---

### Strategy MT — Mid-Term Volume (weight: 20%)
**File:** `backend/stages/mt_volume.py`  
**Question:** "Is the buying still active THIS month?"

```
Components:

  1. Wyckoff phase ∈ {"accumulation", "markup"}               → 1/0
  2. OBV-30d slope ≥ +5%                                      → 1/0
  3. CMF-21d ≥ +0.10                                          → 1/0
  4. Up/Down vol ratio (30d) ≥ 1.4×                           → 1/0
  5. Vol trend (10d vs 30d) ≥ 0%                              → 1/0
  6. MFI-14d ∈ [50, 80]  (healthy, not overbought)           → 1/0
  7. Price > 60d VWAP                                         → 1/0

  MT score = mean(components 1–7)
```

**Why separate from LT:** A pick with strong 6-month accumulation but
deteriorating recent flow is a "watchlist" not a "buy today". MT catches that.

---

### Strategy DD — Direct Deals (weight: 10%)
**File:** `backend/stages/direct_deals.py`  
**Question:** "Did named institutions actually trade this in the last 30 days?"

```
Input: NSE block + bulk deal aggregates (30-day window)

If total_deals < 2:
    score = 0.5   ← neutral (no signal, not a negative)
Else:
    ratio = (buy_qty - sell_qty) / (buy_qty + sell_qty)  ← range [-1, +1]
    clamped = clamp(ratio, -0.30, +0.30)
    score = 0.5 + 0.5 × (clamped / 0.30)              ← range [0.0, 1.0]

    Examples:
      ratio = +0.30 (all buying)  → score = 1.0
      ratio =  0.00 (balanced)    → score = 0.5
      ratio = -0.30 (all selling) → score = 0.0
```

**Why 10%:** Block/bulk deals are rare and sparse. A stock that never shows up
in block deals isn't being sold — there's just no large-lot signal. So the
neutral default (0.5) avoids penalizing good setups for data absence.

---

### Strategy BR — Breakout Triggers (weight: 5%)
**File:** `backend/stages/breakouts.py`  
**Question:** "Is there a specific entry trigger firing today?"

```
Three binary patterns:

  Pocket Pivot (Morales):
    Up-day volume > largest down-day volume in prior 10 sessions
    AND price in a ±10% consolidation band
    → fired = True/False

  Volume Dry-Up (Minervini):
    5-day avg volume < 50% of 50-day avg volume
    AND price range tight (within ±10% band)
    → fired = True/False

  CAN SLIM (O'Neil):
    Price within 2% of 20-day high
    AND today's volume ≥ 1.4× 50-day avg volume
    → fired = True/False

  BR score = count(fired) / 3        → 0.0, 0.33, 0.67, or 1.0
```

**Why only 5%:** These are timing signals, not thesis signals. A pocket pivot
on a stock with mediocre LT volume accumulation is noise. A pocket pivot on
a stock scoring 0.85 on LT is a clean entry opportunity.

---

## 6. Final selection — scoring and ranking

**File:** `backend/stages/score.py`  
**Called by:** `backend/orchestrator.py:run_universe()`

After all 100 tickers run their per-ticker pipelines, the orchestrator gathers
all results and calls `rank_and_select()`.

### Composite score formula

```
composite = (
    0.50 × LT.score +
    0.15 × TT.score +
    0.20 × MT.score +
    0.10 × DD.score +
    0.05 × BR.score
) × 100

Result: float in [0, 100]
```

Worked example:

```
LT = 0.75  →  0.50 × 0.75 = 0.375
TT = 0.50  →  0.15 × 0.50 = 0.075
MT = 0.86  →  0.20 × 0.86 = 0.172
DD = 1.00  →  0.10 × 1.00 = 0.100
BR = 0.33  →  0.05 × 0.33 = 0.017

Composite = (0.375 + 0.075 + 0.172 + 0.100 + 0.017) × 100 = 73.9
```

### Selection algorithm

```python
# 1. Collect all results that cleared the gates (U, I, HR all passed)
candidates = [r for r in results if r.passed_gates]

# 2. Sort by composite descending
candidates.sort(key=lambda r: r.composite_score, reverse=True)

# 3. Select top-N (default 3) that clear the composite floor (default 60)
selected = []
for r in candidates:
    if r.composite_score >= 60.0 and len(selected) < 3:
        r.selected = True
        r.rank = len(selected) + 1
        selected.append(r)

# Result: 0, 1, 2, or 3 picks
# "Nothing actionable today" is a valid and frequent outcome
```

### How to tune selection

| Parameter | Where | Effect |
|-----------|-------|--------|
| `min_composite` | `orchestrator.py:DEFAULT_MIN_COMPOSITE` (60.0) | Raise = stricter; lower = more picks |
| `top_n` | `orchestrator.py:DEFAULT_TOP_N` (3) | Max picks per day |
| Stage weights | `pipeline.py:STAGE_WEIGHTS` | Rebalance strategy importance |
| Stage thresholds | Each `stages/*.py` file, top-level constants | Tune per-component sensitivity |

---

## 7. Pick payload — hypothesis and exit plan

**File:** `backend/stages/hypothesis.py`

For each selected ticker, this stage builds the user-facing pick object:

### Price levels

```
best_buy_at  = current_price × 0.98    (2% pullback entry zone)
sell_target  = best_buy_at  × 1.20    (+20% target over 3–6 months)
stop_loss    = best_buy_at  × 0.90    (−10% hard stop)
upside_pct   = (sell_target / current - 1) × 100
downside_pct = (stop_loss   / current - 1) × 100
```

### Headline (one-liner thesis)

Priority waterfall:
1. If OBV-90d ≥ +5%: `"OBV +12% over 90 days — Stage 2"`
2. Else if CMF-60d ≥ +0.05: `"CMF +0.08 over 60 days"`
3. Else if Minervini template: `"Trend-template aligned"`
4. Fallback: `"Composite 73/100"`

### Four-scenario exit plan (from PRINCIPLES §4)

```
A. Target hit (happy path)
   → Exit at sell_target, or trim 50% and trail the rest

B1. Volume distribution (PRIMARY exit trigger — highest priority)
    EXIT IMMEDIATELY if any one of:
    - OBV-30d rolls into a downslope
    - CMF-21d crosses below zero
    - Down-day volume dominates (up/down ratio < 0.85)
    - Wyckoff phase flips to Distribution
    Reason: "Volume turns before price"

B2. Hard price stop (backstop)
    → −10% from best_buy_at, OR
    → Two consecutive daily closes below the 200d MA

C. Time stop
   → 6 months without target or stop → exit and re-evaluate

D. Hypothesis broken
   → Regulatory action, fraud, auditor exit, or core thesis fails
```

### Target window (holding horizon)

| Signal pattern | Window |
|----------------|--------|
| Fresh Stage 1→2 transition + VDU on tight base | 3 months ± 1 month |
| Stage 1 long base or early Stage 2, no immediate trigger | 6 months ± 2 months |
| Mixed signals (default) | 4–6 months |

### Reasoning checklist (auditable)

The pick payload includes one `ReasoningPoint` per scoring stage:

```json
{
  "stage": "LT",
  "label": "Long-term volume (50%)",
  "score": 0.75,
  "evidence": ["Weinstein stage_2_advance", "OBV-90d +15%", "Base 120d"],
  "fix_point": "backend/stages/lt_volume.py — raise OBV_90D_MIN to tighten",
  "why": "3-to-6 month institutional accumulation pattern confirmed"
}
```

This is also what the React UI renders as the expandable "Reasoning" panel on
each pick card.

---

## 8. Rendering and serving picks

### Pre-render pipeline (2026-07-15)

Between `[H] Hypothesis` (which produces `pick_payloads`) and
`[R] Render` (which writes the file), the orchestrator runs three
additive steps. All are deterministic, fail-open, and touch no gate
scoring — they only annotate, filter, and record.

```
pick_payloads (from [H])
        │
        │  1. estimated_horizon_days (backend/horizon.py)
        │     Bucketed volume-based holding window from
        │     (confirmation.score, weinstein_stage, entry_timing).
        │     Buckets: (30, 60, 90, 120, 180) days.
        ▼
pick.holding_horizon = {days, basis, source}
        │
        │  2. reconcile_picks_against_portfolio (backend/picks_reconcile.py)
        │     Cross-references each pick against the current portfolio
        │     via positions_view.list_active_positions:
        │       taken row + exit_*  → suppressed_from_ui flag
        │       taken row + hold/tighten/extend → already_held annotation
        │       suggested-only row → pass through (record_picks supersedes)
        ▼
pick.suppressed_from_ui | pick.already_held  (as applicable)
        │
        │  3. attach_change_diffs (backend/picks_diff.py)
        │     For every pick with a prior appearance within
        │     PICK_DIFF_LOOKBACK_DAYS (30), compute delta:
        │       confirmation_score, bonuses, entry_timing, weinstein_stage,
        │       headline_changed, price_plan_delta, rank_change.
        ▼
pick.change_since_prev_pick = { prev_date, days_ago, ... deltas ... }
        │
        │  4. split_visible_from_suppressed
        │     Two lists: visible (→ picks_<date>.json) and
        │     full (→ portfolio.record_picks).
        ▼
render_picks_response(visible_picks, ...) → picks_<date>.json
record_picks(full pick list)              → portfolio.csv
```

**Why the split**: the UI must never show "sell ABB" and "buy ABB" on
the same day, but the fresh signal on a taken-position exit day is
still a legitimate audit-worthy event. Suppressed picks land as new
`suggested` rows in `portfolio.csv` with a different `entry_date`,
alongside the user's taken row — so the record survives even though
the recommendation is hidden.

### Stage [R] Render — `backend/stages/render.py`

Writes one JSON file per day:

```
data/picks_2026-05-18.json
{
  "date": "2026-05-18",
  "generated_at": "...",
  "source": "pipeline",
  "demo_mode": false,
  "schema_version": 6,
  "picks": [ { ...pick payload... }, ... ]
}
```

`schema_version` was bumped 5 → 6 on 2026-07-15 for four additive
per-pick fields: `holding_horizon`, `already_held`,
`suppressed_from_ui`, and `change_since_prev_pick`. Tolerant readers on
the middleware side ignore unknown fields, so old UI code renders v6
files without change.

### Frontend rendering (schema-v6)

`frontend/src/types.ts` carries the TypeScript definitions for the new
fields (`HoldingHorizon`, `AlreadyHeld`, `ChangeSincePrevPick`,
`PickDelta<T>`, `BonusDiff`, `RankChange`). `PickCard.tsx` renders three
blocks derived from them:

- **Already-held banner** (amber) — surfaces when `pick.already_held`
  is populated. Shows ownership, entry date, days held, P&L, and the
  current portfolio action + note. Fires when reconcile has annotated
  the pick because the symbol is a live position with a hold / tighten
  / extend_horizon action.
- **Since-last-pick diff panel** (sky) — surfaces when
  `pick.change_since_prev_pick` is populated. Shows the previous pick
  date + days ago, plus deltas: confirmation score (color-coded green
  for up / red for down), bonuses added and lost, entry_timing and
  weinstein_stage transitions, rank movement, and a headline-changed
  flag. Empty diff means the symbol is new to the 30-day lookback.
- **Horizon pill** — surfaces when `pick.holding_horizon` is
  populated. Shows the bucketed holding window (30/60/90/120/180 days)
  and a "volume-based bucket" subtitle.

Trust-safety on the positions side is enforced by
`backend/positions_view._symbols_in_todays_picks`: any open-suggested
row for a symbol appearing in today's picks is hidden from the
positions view (unless it is a same-day fresh row), closing the mid-day
transient window between pipeline runs.

### Multi-day `pick_history` trail (2026-07-15)

`change_since_prev_pick` only carries the delta vs the SINGLE most
recent prior appearance. For symbols picked N days in a row, users
want the whole trajectory. `backend/picks_diff.py` gained
`compute_pick_history` + `attach_pick_history`:

- Walks `data/picks_<date>.json` backwards up to
  `PICK_HISTORY_LOOKBACK_DAYS` (default 30) days.
- Collects up to `PICK_HISTORY_MAX_ENTRIES` (default 7) prior
  appearances, newest first.
- Each entry: `date, rank, score, entry, bonus_count, headline,
  direction, score_delta`.
- `direction` compares the entry's score to the older neighbour and
  is one of `positive` / `negative` / `neutral` / `first_appearance`.

Wired into `orchestrator.py` alongside `attach_change_diffs`, before
`split_visible_from_suppressed`. Middleware `Pick` DTO carries it as
`Optional[list]`. Frontend renders it as a color-coded monospace
table.

### Middleware pass-through of schema-v6 fields (2026-07-15)

`middleware/schemas.py:Pick` was silently stripping all schema-v6
additions because Pydantic drops undeclared fields by default. Fixed
by adding five `Optional` fields on the DTO: `holding_horizon`,
`already_held`, `change_since_prev_pick`, `suppressed_from_ui`,
`pick_history`. Kept as loose `dict` / `list` typing so the API
remains tolerant of backend sub-field additions without a schema bump.

### Daily diagnostic snapshot — `data/daily_diagnostic.md` (2026-07-15)

`backend/daily_diagnostic.py` writes a self-contained markdown file at
the end of every pipeline run (Phase 6 in `orchestrator.py`, after
`record_picks`). It overwrites in place. Uploading this single file
gives full context for remote diagnostics: environment, code
fingerprints (proves which portfolio.py / horizon.py / render.py is
actually loaded), pipeline summary, reconcile events, portfolio state
including duplicate-open-symbol detection, per-pick presence of
schema-v6 fields, and any errors captured. Fail-open — a diagnostic
write failure never breaks the pipeline.

### Trajectory metric-mirror flip thresholds (2026-07-15)

`backend/signal_trajectory.py` now aligns its exit-classifier thresholds
with the LT gate's admission floors, eliminating the hair-trigger
zero-crossing that caused day-to-day flipped/stable oscillation on
borderline setups. Three module-level constants, each mirroring a
corresponding `backend/stages/lt_flow.py` admission floor:

- `FLIP_THRESHOLD_OBV_90D_PCT = -3.0`  mirrors `OBV_90D_SLOPE_MIN=3.0`
- `FLIP_THRESHOLD_UP_DOWN_RATIO = 0.9`  mirrors `UPDOWN_90D_MIN=1.1`
  (0.1 below neutral 1.0 = symmetric with 0.1 above)
- `FLIP_THRESHOLD_MA150_PCT = -0.5`  mirrors `MA150_SLOPE_MIN=0.0`
  plus a small buffer so a barely-flat MA doesn't trip a flip

Classifier signatures gained a keyword-only `flip_threshold`
parameter. Defaults (`0.0` / `1.0`) reproduce the original
zero-crossing behaviour, so any legacy caller is unaffected.

Entry-value provenance is per-pick-date and already correct:
`_load_stage_features(symbol, entry_date_iso, "LT")` reads
`data/traces/run_<entry_date>_<symbol>.jsonl` and `positions_view`
passes each row's own `entry_date`. Every open row's trajectory
anchors to its own scoring day's captured OBV / MA / ratio values —
important for symbols with multiple open rows (taken paper + fresh
suggested) or for a symbol re-picked days later.

### HTTP API — `middleware/main.py`

```
GET  /api/health               → { status, date_ist, demo_mode }
GET  /api/picks                → reads picks_<today>.json (runs pipeline if missing)
POST /api/picks/refresh        → deletes cache, re-runs pipeline
GET  /api/stock/{symbol}       → full signal panel + pick if selected today
```

The middleware never runs the pipeline at request time unless the file is
missing. Normal serving is a simple file read.

### Cache behaviour — `middleware/picks_cache.py`

```
Does picks_<today>.json exist on disk?
  YES → serve it instantly
  NO  → call run_universe() → write file → serve result
```

This means the first GET of the day triggers a pipeline run (~30–60 seconds).
All subsequent requests are instant.

---

## 9. RL instrumentation — the full picture

The system is built from day one to produce a labeled dataset for reinforcement
learning weight and threshold tuning. Here is every piece of that
instrumentation:

### 9.1 Per-ticker stage traces (features)

**Written by:** `backend/pipeline.py:_append_trace()`  
**Location:** `data/traces/run_<date>_<ticker>.jsonl`

Every stage appends one JSON line:

```jsonc
// [I] Ingest row
{"ts": "2026-05-18T17:30:00+0530", "trace_id": "uuid-abc", "symbol": "HDFCBANK.NS",
 "stage": "I", "passed": true,
 "features": {"obv_90d_pct": 15.2, "cmf_60d": 0.09, "base_length_days": 120, ...},
 "evidence": ["OBV-90d +15%", "CMF-60d 0.09"], "elapsed_ms": 340}

// [LT] score row
{"ts": "...", "trace_id": "uuid-abc", "symbol": "HDFCBANK.NS",
 "stage": "LT", "passed": true, "score": 0.875,
 "features": {"obv_90d_pct": 15.2, "cmf_60d": 0.09, "base_length_days": 120,
              "weinstein_stage": "stage_2_advance", ...},
 "evidence": ["Weinstein stage_2_advance", "OBV-90d +15%", ...], "elapsed_ms": 2}

// FINAL summary row (added after ranking)
{"ts": "...", "trace_id": "uuid-abc", "symbol": "HDFCBANK.NS",
 "stage": "FINAL", "selected": true, "rank": 1,
 "composite": 78.5,
 "weights": {"LT": 0.50, "TT": 0.15, "MT": 0.20, "DD": 0.10, "BR": 0.05}}
```

**One file per (date, ticker).** About 8–10 rows per file. 100 tickers × 250
trading days = ~25,000 files per year = the feature dataset.

### 9.2 Outcome tracking (reward signal)

**File:** `backend/stages/outcome.py`  
**Runs:** Nightly, checks all open picks  
**Written to:** `data/traces/outcomes.jsonl`

At T+90 and T+180 from entry:

```jsonc
{"ts": "2026-08-17T17:30:00+0530",
 "trace_id": "uuid-abc",           // ← links back to the stage trace
 "symbol": "HDFCBANK.NS",
 "entry_date": "2026-05-18",
 "entry_price": 1813.50,
 "horizon_days": 90,
 "exit_price": 2100.00,
 "return_pct": 15.8,
 "hit_target": true,
 "hit_stop": false,
 "exit_reason": "target"}
```

**The `trace_id` is the join key.** Given a `trace_id`, you can:
1. Look up the stage trace → get all features and scores at time of pick
2. Look up the outcome row → get the realized return

That (features, scores, weights, return) tuple is one training sample.

### 9.3 Portfolio ledger (open positions)

**File:** `data/portfolio.csv`  
**Written by:** `backend/portfolio.py:record_picks()`

Stores every pick ever surfaced with `entry_date`, `entry_price`, `stop_loss`,
`sell_target`, and `trace_id`. The outcome stage reads this to know which picks
to check.

### 9.4 Planned RL training loop (future)

```
Phase 1 — Contextual bandit (3 months of outcomes)
  Input:  (stage features) at pick time
  Action: (weights: LT, TT, MT, DD, BR)
  Reward: return_pct at T+90
  Method: LinUCB or Thompson Sampling over weight space
  Goal:   Find weight allocation that maximizes expected T+90 return

Phase 2 — Offline RL for threshold tuning (6+ months)
  Dataset: all trace rows (including rejected tickers) + outcomes
  Method:  Conservative Q-Learning (CQL) or Implicit Q-Learning (IQL)
  Actions: per-stage threshold adjustments
           e.g. LT_OBV_90D_MIN: currently 5%, try 3% or 8%
  Reward:  T+90 / T+180 return for selected tickers;
           0 (or small penalty) for rejected tickers that would have hit target

Phase 3 — Full offline-to-online RL
  Use trained policy weights to initialize pipeline weights
  Run paper-trade loop with online updates
```

**What to NOT change for RL compatibility:**
- The `trace_id` UUID assigned in `run_pipeline()` — it is the join key across files
- The `stage` field names ("LT", "MT", "TT", "DD", "BR", "FINAL") — they are the column names in the feature matrix
- The `features` dict schema in each stage — adding keys is fine; renaming breaks old traces
- The `outcomes.jsonl` schema — the RL reward label

**What is safe to change:**
- Weights in `STAGE_WEIGHTS` (just add a new FINAL row with the new weights)
- Thresholds inside stages (just update the features dict to log old + new values)
- Stage order (won't affect traces since rows are keyed by stage_id, not position)

---

## 10. End-to-end data flow diagram

```
Yahoo Finance (yfinance)                NSE Archives
       │                                     │
       │ 260 rows OHLCV                      │ block.csv / bulk.csv
       ▼                                     ▼
 backend/yahoo.py                  backend/block_deals.py
       │                                     │
       └──────────────┬──────────────────────┘
                      │
                      ▼
          backend/volume_signals.py:compute()
          ┌────────────────────────────────────────────────┐
          │ OBV-30/90/180d  CMF-21/60d  MFI-14d           │
          │ VWAP-60d  Up/Down-30/90d  Vol-trend-10/30d    │
          │ MA-50/150/200d  30w-MA + slope                 │
          │ Wyckoff phase  Weinstein stage                 │
          │ Base length  QoQ vol growth                    │
          │ Pocket pivot count  VDU flag  CAN SLIM flag    │
          │ Block/bulk net qty ratio                       │
          └────────────────────────────────────────────────┘
                      │  AccumulationSignals dataclass
                      │  (read-only from here on)
                      ▼
          ┌─ [HR] Hard Rejects ──────────────────────────┐
          │   Wyckoff distribution? Stage 4? Parabolic?  │
          │   OBV-90d<0 AND CMF-60d<0?                   │
          └──────────────────────────────────────────────┘
                      │ (survivors only)
            ┌─────────┼─────────┬──────────┬───────────┐
            ▼         ▼         ▼          ▼           ▼
          [LT]      [TT]      [MT]       [DD]        [BR]
          50%       15%       20%        10%          5%
          score     score     score      score       score
            │         │         │          │           │
            └─────────┴─────────┴──────────┴───────────┘
                                │
                     Each stage appends to JSONL trace
                                │
                                ▼
                    Composite = Σ(weight × score) × 100
                    Sort all 100 tickers descending
                    Pick top-3 where composite ≥ 60
                                │
                                ▼
                    [H] Build pick payload
                        - entry / target / stop prices
                        - 4-scenario exit plan
                        - target window
                        - per-stage reasoning checklist
                                │
                                ▼
                    [R] Write data/picks_<date>.json
                                │
                                ▼
               ┌────────────────┴────────────────┐
               │  FastAPI /api/picks              │
               │  React UI (pick cards)           │
               └─────────────────────────────────┘
                                │
                         (T+90 / T+180)
                                ▼
                    [O] Fetch current price
                        Compute return_pct
                        Append to outcomes.jsonl
                        (RL reward signal)
```

---

## 11. File map — where to look for what

| If you want to… | Look here |
|-----------------|-----------|
| Add a ticker to the universe | `backend/universe.py` |
| Change the data source | `backend/fetch.py`, `backend/yahoo.py` |
| Change signal computation | `backend/volume_signals.py` |
| Tune hard-reject conditions | `backend/stages/hard_rejects.py` |
| Tune LT thresholds | `backend/stages/lt_volume.py` (top constants) |
| Tune TT thresholds | `backend/stages/trend_template.py` |
| Tune MT thresholds | `backend/stages/mt_volume.py` |
| Tune DD thresholds | `backend/stages/direct_deals.py` |
| Tune BR patterns | `backend/stages/breakouts.py` |
| Change stage weights | `backend/pipeline.py:STAGE_WEIGHTS` |
| Change min composite floor | `backend/orchestrator.py:DEFAULT_MIN_COMPOSITE` |
| Change max daily picks | `backend/orchestrator.py:DEFAULT_TOP_N` |
| Change entry/target/stop % | `backend/stages/hypothesis.py` |
| Add a new stage | New file `backend/stages/xyz.py` → add to `backend/stages/__init__.py:PER_TICKER_CHAIN` |
| Read pick data | `data/picks_<date>.json` |
| Read stage traces (RL features) | `data/traces/run_<date>_<ticker>.jsonl` |
| Read outcomes (RL rewards) | `data/traces/outcomes.jsonl` |
| Read open positions | `data/portfolio.csv` |
| Run the pipeline manually | `python -m backend.nightly` |
| Run in demo mode | `DEMO_MODE=1 uvicorn middleware.main:app` |

---

## 12. Running the system

```bash
# First-time setup (Windows)
setup.bat

# Start everything (middleware + frontend)
start.bat

# Or individually:
cd backend && uvicorn middleware.main:app --reload --port 8000
cd frontend && npm run dev

# Force a pipeline re-run (useful in dev)
curl -X POST http://localhost:8000/api/picks/refresh

# Run nightly job manually
python -m backend.nightly

# Diagnostics: how many tickers cleared each stage
python -c "from backend.orchestrator import diagnostics; import json; print(json.dumps(diagnostics(), indent=2))"
```

---

## 13. Extending the system — common tasks

### Add a new scoring stage

1. Create `backend/stages/my_stage.py`:

```python
# backend/stages/my_stage.py
from backend.pipeline import PipelineContext, StageResult

stage_id = "MY"

def run(ctx: PipelineContext) -> StageResult:
    sig = ctx.signals
    score = 1.0 if sig.some_metric > threshold else 0.0
    return StageResult(
        stage_id=stage_id,
        passed=True,
        score=score,
        features={"some_metric": sig.some_metric},
        evidence=[f"some_metric {sig.some_metric:.2f}"],
        fix_point="backend/stages/my_stage.py — adjust threshold",
    )
```

2. Add the weight to `backend/pipeline.py:STAGE_WEIGHTS` (must still sum to 1.0).

3. Add `my_stage.run` to `PER_TICKER_CHAIN` in `backend/stages/__init__.py`.

That's it. The stage is automatically traced to JSONL and included in RL data.

### Swap the data source

Implement the interface in `backend/fetch.py:DataSource` and set `DATA_SOURCE=myname`
in the environment. No pipeline code changes needed.

### Plug in RL-tuned weights

The weights dict in `backend/pipeline.py:STAGE_WEIGHTS` is the only place to
change. Load your trained weights from a file and override at startup:

```python
import json
from backend import pipeline
with open("data/rl_weights.json") as f:
    pipeline.STAGE_WEIGHTS.update(json.load(f))
```

The FINAL trace row logs `weights` on every run, so you always know which weight
set produced which picks.

---

## 14. Disclaimer

Educational use only. Picks are algorithmic and **not financial advice**. The
pipeline has not been backtested on multi-year Indian data. Paper-trade the
first 10–15 picks before deploying real capital.
