# Stockiya — Feedback Loops

The pipeline gets smarter only if its *decisions* are tracked against *outcomes*. This file is the operating cadence — what to look at, how often, why.

> **Rule of thumb:** every metric below is computable from two files already produced by the pipeline:
> - `data/traces/run_<date>_<ticker>.jsonl` — per-stage features + scores
> - `data/traces/outcomes.jsonl` — realized return at T+90 / T+180 (written by stage `[O]`)

Together they form a `(features, decision, reward)` dataset. That's the RL training set, and that's also what we eyeball weekly.

---

## Cadence map

```
   DAILY          WEEKLY (Fri PM)        BI-WEEKLY            T+90 / T+180
   ─────          ───────────────        ─────────            ─────────────
   pipeline       open-pick health       universe coverage    realized returns
   runs           exit triggers          stage distributions  weight retune
   trace files    portfolio MTM          threshold sweeps     threshold sweeps
```

---

## WEEKLY — every Friday after market close (~10 minutes)

These are the five things to look at every Friday. Goal: catch a broken pick *this* week, not at the 90-day review.

| # | Metric | What "good" looks like | How to compute |
|---|---|---|---|
| 1 | **Open-pick MTM** | All open picks > entry, or losers < −5% | `data/portfolio_weekly.csv` — most recent `week_ending` row |
| 2 | **Volume-distribution exits firing?** | None firing on open picks | Re-run pipeline; for each open ticker check `MT` features: `obv_30d_pct < 0`, `cmf_21d < 0`, OR `up_down_30d < 0.85`. **Any one** ⇒ exit per PRINCIPLES.md §4 B1 |
| 3 | **LT score drift** | Open positions' LT scores still ≥ 0.6 | Latest trace JSONL per open ticker → `stage:"LT", score` |
| 4 | **Universe coverage** | ≥ 5 tickers cleared the 60-floor (else we're starving) | `orchestrator.diagnostics()` — `HR` passed count + composite ≥ 60 count |
| 5 | **Block-deal coverage** | ≥ 2 of the top-10 ranked tickers had named institutional flow | `DD` stage features: `total_deals ≥ 2` |
| 6 | **`[DV]` shadow-veto candidates on today's picks** *(new 2026-07-17)* | 0 — a picked ticker also flagged for distribution is a red flag the composite missed something | Latest trace JSONL per picked ticker → `stage:"DV", features.would_veto`. Also `pick_payload.accumulation_assessment.would_veto_shadow`. Cross-check the outcome after T+30 to see whether the veto would have saved you the trade — that correlation is the promotion signal for flipping `distribution_veto_mode` to `"block"`. |
| 7 | **`participant_evidence` distribution on picks** *(new 2026-07-17)* | Most picks show `inferred`; occasional `disclosed_large_client` on the strongest names — that's the honest split | `pick_payload.accumulation_assessment.participant_evidence`. If every pick shows `disclosed_large_client`, the classifier regex is over-catching — inspect `client_class_counts` in the aggregator output. |
| 8 | **Sliding-window IC trend across stages** *(new 2026-07-17)* | A stage's IC (Pearson r vs T+90 return) has consistent sign across ≥ 3 consecutive `sliding_*.json` events → real signal. Constant thrashing (+0.6, −0.4, +0.5) → noise at n=5, ignore. | `data/learning_events/sliding_*.json → ic_by_stage`. Also read the `learning_hints` array — hints only appear when \|IC\| ≥ 0.5. Never touch `stage_weights.json` from this alone; run `python -m scripts.tune_weights --apply` manually once the cumulative matured count clears 20. |

### Output: weekly-report.md (5 minutes)

Append one row per week to a running log:

```
2026-W19 (week ending 2026-05-08)
  Open: ICICIBANK +6.4%, BHARTIARTL +2.1%, HDFCBANK +1.1%
  Exits firing: none
  LT drift: ICICIBANK 0.82→0.78 (OK), others stable
  Coverage: 12 tickers cleared 60-floor
  Block deals: 4 of top 10 had ≥2 buy deals
  Action: hold all. Watch BHARTIARTL — MFI tipping above 80.
```

If something would have hit a stop or shifted conviction, write that down. **A short, honest weekly note is more valuable than a dashboard.**

---

## BI-WEEKLY — every other Friday (~20 minutes)

These look across two weeks of pipeline runs, not just open positions.

| # | Metric | What it tells you | Where it lives |
|---|---|---|---|
| 1 | **Stage gate funnel** | "Out of 100 Nifty tickers, where did we cull?" | `orchestrator.diagnostics()` → per-stage pass/fail counts |
| 2 | **Score distribution per stage** | Are any stages always scoring high (worthless) or always low (broken)? | aggregate `score` field across the last 10 trace files per stage |
| 3 | **Threshold sensitivity** | "If LT.CMF_60D_MIN was 0.03 instead of 0.05, would we have included winners?" | grep traces where score was barely missed |
| 4 | **Direct-deals signal strength** | Is the new `[DD]` stage *adding* unique information or just echoing OBV/CMF? | corr(DD.score, LT.score) — close to 1.0 = redundant |
| 5 | **Pipeline latency** | `elapsed_ms` per stage; full universe under 60s? | `orchestrator.diagnostics()` |

### Action triggers
- If a stage's pass rate is < 5% over 2 weeks → threshold likely too strict, soften by one unit and re-run history.
- If two stages correlate > 0.9 → one is redundant; consider folding them.
- If pipeline latency > 90s → parallelism cap is probably wrong; bump `max_workers`.

---

## T+90 / T+180 — whenever an outcome lands (per-pick, append-only)

This is the RL training signal. Stage `[O]` writes `data/traces/outcomes.jsonl` automatically when an open pick reaches its 90- or 180-day mark. **Do nothing weekly here**; just let the file grow. Then once a quarter:

| # | Metric | Use it for |
|---|---|---|
| 1 | **Hit rate** | % of picks where `return_pct ≥ target_pct` at T+180 |
| 2 | **Per-stage predictive power** | For each scoring stage, correlation between that stage's score and realized 180d return. The stage with the highest IC deserves more weight. |
| 3 | **False-positive forensics** | Picks where every stage said "buy" but return < −10%. Read the trace; find the feature pattern; add a hard-reject for it. |
| 4 | **Weight retune candidate** | If LT has IC = 0.4 but TT has IC = 0.05, TT's 15% weight is too high — propose 50 / 25 / 5 / 15 / 5 as the next config |

A **contextual bandit** (sklearn / VowpalWabbit) reads `outcomes.jsonl` keyed by `trace_id`, joins to the per-stage scores, and retunes the weights. ~50 lines of Python. Don't write it before T+180 of the first cohort.

---

## What NOT to track weekly
- **Per-day pick count.** Zero picks is the *correct* answer most days. Tracking this creates pressure to soften gates. Don't.
- **Daily P&L on closed positions.** Doesn't influence open decisions.
- **Anything that needs a chart instead of a number.** If you can't write it in one line, it's not actionable yet.

---

## Implementation status

Already in the code:
- `data/traces/run_<date>_<ticker>.jsonl` — written by every pipeline run
- `data/traces/outcomes.jsonl` — written by `backend.stages.outcome.run_outcome_tracker`
- `data/portfolio.csv`, `data/portfolio_weekly.csv` — written by `backend.portfolio`
- `backend.orchestrator.diagnostics()` — returns the funnel + per-stage averages

Still to write (small):
- `scripts/weekly_report.py` — opens `portfolio_weekly.csv`, prints the 5-row weekly card.
- `scripts/biweekly_report.py` — calls `diagnostics()` over the last 10 days, prints the funnel + correlations.
- These should be runnable as `python -m scripts.weekly_report` and scheduled (or just double-clicked Friday afternoons).

---

## Disclaimer

These metrics are decision aids, not decisions. **Trust your eyes over a dashboard** when the volume tape says one thing and a number says another — the dashboard summarizes; the tape is the source.
