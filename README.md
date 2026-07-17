# Stockiya

> **Don't invent. Follow the institutions. Enter early, exit early — on volume alone.**

Local web app that surfaces up to **3 Indian (Nifty 100) stock picks per day** for a 3–6 month swing hold. Pure volume strategy: picks are stocks where **institutions are visibly building a Wyckoff accumulation base and today's tape confirms it**. Deterministic, no LLM, RL-ready.

> Educational use only. **Not financial advice.**

---

## What it does

1. **Screens Nifty 100** every day for Wyckoff Phase-C or Phase-D accumulation footprints — institutional buying *before* price runs.
2. **Waits for a Volume-Spread-Analysis trigger bar** — Sign-of-Strength, pocket pivot, or no-supply test. Any one fires the entry.
3. **Requires anchored-VWAP hold** — price above the institutional cost-basis line from the base low.
4. **Picks 0–3 setups** that clear all three checks. Quality over quantity — if nothing qualifies, you see *"nothing actionable today"*, plus a "closer-to-passing" watchlist and near-misses so you can see what the chain is doing.
5. **Every pick ships with** entry, ATR-adaptive stop, 1R / 2R target ladder, day-45 / 90 / 180 milestones, and an auditable point-by-point volume checklist.
6. **Daily exit-watch** on every held pick: OBV divergence, churning, ≥3 distribution days, anchored-VWAP break, or climax reversal → early exit alert. We drop as early as we entered.
7. **Every decision is traced** to `data/traces/run_<date>_<ticker>.jsonl` — the RL training dataset for tomorrow.

Read [PRINCIPLES.md](./PRINCIPLES.md) for the investing rule-set.
Read [ARCHITECTURE.md](./ARCHITECTURE.md) §0-§0.3 for the current-spine walkthrough.

---

## Architecture — pipeline of swappable stages

**Live spine (2026-07-04): v3 soft-gate composite.**

```
                       ┌─ rejected ─► trace log ─► RL replay buffer
                       │
[U] Universe ─► [I] Ingest ─► [HR] Hard Rejects ─►     ← hard gates: short-circuit
    ▲                                                    (I gains finalized-bar
    │  drops NaN OHLC / zero-vol /                        hygiene — 2026-07-17)
    │  partial-session bars
[ACS] Accum-Screen ─► [AC] Accumulation ─►
[LT] Long-Term ─► [CS] Consolidation ─► [VD] Volume/Div ─► [BR] Breakout ─►
[DV] Distribution Veto (shadow-mode default; block-mode auto-promotes to hard gate)
                                                          ↓
                            S = Σ wᵢ · mᵢ                composite score
                            ↓
                            filter: S ≥ COMPOSITE_TAU
                            ↓
[RK] Rank ─► [PS] Position Size ─► [H] Hypothesis+Exit ─► [R] Render ─► UI
                                    │                     (attaches           │
                                    │              accumulation_assessment    │
                                    │                     envelope)           │
                                                                              │
                                                                     └─► [O] Outcome
                                                                           T+90 / T+180
                                                                           feeds tuner
```

Every stage = one file in `backend/stages/` with the same `run(ctx) -> StageResult` signature. **Replace any file to swap that stage's logic; nothing else changes.**

Weights (`wᵢ`) and threshold (`COMPOSITE_TAU`) live in `config/stage_weights.json`. `scripts/tune_weights.py` updates them monthly via a **champion-challenger ratchet** — the file is only overwritten if a candidate strictly beats the current champion's replay metric. Accuracy cannot regress.

**Target spine (next milestone, docs describe this as the design goal):**
`[U] → [I] → [HR] → [WY] Wyckoff Phase → [VSA] Bar Confirmation → [AVWAP] Anchored-VWAP → [RK]`
plus a daily `[EX] Exit-watch` on held picks. See AGENT_HANDOFF.md for the wire-up plan.

Two hard gates ([HR], [VSA]) and two scored stages ([WY], [AVWAP]) feed the ranker:

| Stage | Type | Role |
|---|---|---|
| [HR] Hard Rejects | gate | Parabolic / extended / SEBI / promoter-pledge — never override |
| [WY] Wyckoff Phase | scored 0-1 | Confidence that the ticker is in Phase C (spring) or Phase D (SOS) |
| [VSA] Bar Confirmation | trigger | Today: SOS bar OR pocket-pivot OR no-supply test |
| [AVWAP] VWAP Hold | scored 0-1 | Fraction of last 20 bars closing above the anchored VWAP from the base low |
| [RK] Rank | — | `wy_score + avwap_score + vsa_margin + 0.5 × bonus_count`, sort desc |
| [EX] Exit-watch | daily | OBV divergence / churning / dist-days / AVWAP break / climax reversal |

No weighted composites. No "must clear all five." The scored stages are continuous so a strong setup with one soft leg still qualifies.

---

## The volume vocabulary

All math is pure and lives in `backend/indicators.py` (primitive functions) with legacy engine helpers still in `backend/volume_signals.py`. Stages import — nothing recomputes.

### Five primitives the whole spine is built on
- **OBV slope** (Granville) — normalized regression slope of On-Balance Volume
- **ADI slope** (Chaikin Accumulation/Distribution Line) — money-flow-weighted volume
- **Anchored VWAP** — `Σ(price × vol) / Σ(vol)` from an anchor date; institutional cost-basis line
- **ADV(N) / volume ratio** — today's volume vs 50-day average, ATR-normalized
- **ATR20 %** — 20-bar average true range as % of close; the volatility clock that normalizes every threshold

### Wyckoff-phase inputs ([WY])
- Range % of last N bars (coil detector)
- Volume-dryness ratio (recent vol vs prior vol)
- ADI positive divergence (rising ADI while price flat = quiet buying)
- Position vs 150d MA (Phase C-vs-D discriminator)

### VSA trigger inputs ([VSA])
- Sign-of-Strength bar: close > 20d high, vol ≥ 1.5×–2× ADV50 (regime-adaptive), upper-third close
- Pocket pivot (Kacher/Morales): today up-day, today vol > max down-day vol in prior 10
- No-supply test: down-day inside base low on vol < 60 % of prior 10-day avg

### Ranking bonuses ([RK])
- 50d > 150d > 200d MA stack
- OBV-90d slope ≥ +5 %
- Chaikin Money Flow (60d) ≥ +0.15
- NSE block + bulk deal 30d net-buy ratio
- Sector-relative volume today vs sector median
- Top-30 relative-strength rank vs Nifty 100

### Exit-watch inputs ([EX])
- OBV-20d negative divergence at fresh price high
- Churning bar (top-20 % vol × bottom-20 % spread × close near open)
- Distribution-day count over 15 sessions
- Anchored-VWAP breakdown (two consecutive closes below)
- Climax volume + reversal candle

---

## Layout

```
backend/
├── pipeline.py             ← StageResult contract + run_pipeline()
├── orchestrator.py         ← run_universe() — entry point
├── stages/                 ← one file per stage (the swap points)
│   ├── universe.py    [U]      gate — Nifty 100 membership
│   ├── ingest.py      [I]      gate — 180 bars + as-of slice
│   ├── hard_rejects.py [HR]    gate — parabolic / extended / SEBI / pledge
│   ├── wyckoff.py     [WY]     scored — Phase C / Phase D confidence  *(new, in-progress)*
│   ├── vsa.py         [VSA]    trigger — SOS / pocket-pivot / no-supply *(new, in-progress)*
│   ├── avwap.py       [AVWAP]  scored — anchored-VWAP hold             *(new, in-progress)*
│   ├── distribution_veto.py [DV] anti-trick hygiene (shadow/block)    *(new 2026-07-17)*
│   ├── rank.py        [RK]     confirmation-strength ranker + bonuses
│   ├── hypothesis.py  [H]      entry / ATR-stop / 1R / 2R / exits + accumulation_assessment envelope + date_labels
│   ├── render.py      [R]      writes data/picks_<date>.json (schema v7)
│   ├── exit_watch.py  [EX]     daily volume-based early-exit scan       *(new, in-progress)*
│   └── outcome.py     [O]      T+90 / T+180 outcome — label schema v2 (mtm + realized + is_open)
│
├── action_labels.py            9-state advisory ladder (MAINTAIN / MONITOR / REVIEW / EXTEND / TAKE_PROFIT / EXIT_*)  *(new 2026-07-17)*
├── sliding_window_learn.py     event-driven CC trigger every 5 T+90 outcomes                                          *(new 2026-07-17)*
├── positions_view.py           list_active_positions + _action_for (priority-corrected 2026-07-17)
│
│   Legacy stages (retired but still on disk during the rewire):
│   ├── lt_flow.py, consolidation.py, volume.py, breakout.py
├── signals/                ← facade over volume_signals.py
├── volume_signals.py       ← all indicator math (1100 lines, one engine)
├── block_deals.py          ← NSE block/bulk CSV downloader + 30d aggregator
├── universe.py             ← Nifty 100 list
├── yahoo.py, fetch.py      ← data source adapters
├── demo_data.py            ← DEMO_MODE=1 synthetic OHLCV
├── nightly.py, weekly.py   ← cron entry points
├── catchup.py              ← self-heal on middleware boot
└── portfolio.py            ← pick ledger + weekly close ledger

middleware/
├── main.py                 ← FastAPI app (/api/health, /api/picks, /api/stock/{symbol})
├── picks.py                ← thin: read picks_<date>.json or call run_universe()
├── schemas.py              ← Pydantic DTOs
└── picks_cache.py          ← file-based daily cache

frontend/
└── src/                    ← Vite + React + TypeScript + Tailwind

data/
├── picks_<YYYY-MM-DD>.json ← today's picks (served to UI)
├── traces/                 ← per-ticker JSONL stage trace — the RL dataset
│   ├── run_<date>_<ticker>.jsonl
│   └── outcomes.jsonl      ← T+90 / T+180 realized returns
├── deals/                  ← cached NSE block + bulk deal CSVs
├── portfolio.csv           ← every pick the engine ever surfaced
└── portfolio_weekly.csv    ← Friday closes for open picks

test_data/                  ← manual drop-zone for NSE historical CSVs (offline testing)
└── <SYMBOL>.csv            ← raw NSE export (DATE, SERIES, OPEN, HIGH, LOW, …) — see test_data/README.md
```

> **Offline testing:** live Yahoo/NSE fetches are blocked by the corporate firewall,
> so any pipeline test runs against CSVs the user pastes into
> [`test_data/`](./test_data/README.md). No synthetic data is generated — if a
> ticker's CSV is missing, the test asks the user to paste it first.

---

## HTTP API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{status, date_ist, demo_mode}` |
| `GET /api/picks` | Today's picks (served from disk; runs pipeline if file missing) |
| `POST /api/picks/refresh` | Force re-run the pipeline |
| `GET /api/stock/{symbol}` | Detail panel (volume signals, sparkline, today's pick if any) |

Interactive docs at <http://localhost:8000/docs> when middleware is running.

---

## Setup & run

See [INSTALL.md](./INSTALL.md) — `setup.bat` then `start.bat`. ~5 minutes.

## Operating cadence

See [WEEKLY_TRACKING.md](./WEEKLY_TRACKING.md) — what to monitor weekly, bi-weekly, and at T+90/T+180 to keep the system honest.

---

## Roadmap

| Status | Item |
|---|---|
| ✅ done | Pipeline of swappable stages |
| ✅ done | Per-ticker JSONL traces (RL dataset) |
| ✅ done | NSE block + bulk deals downloader |
| ✅ done | Outcome tracker (T+90 / T+180 writes to `outcomes.jsonl`) |
| ✅ done | Wyckoff-VPA spec documented (PRINCIPLES.md, ARCHITECTURE.md §0-§0.3) |
| ✅ done | v3 soft-gate composite spine (ACS + AC wired, composite `S ≥ τ`) |
| ✅ done | Champion-challenger tuner (`scripts/tune_weights.py`, monotone metric) |
| ✅ done | NIFTY 500 universe + `STOCKYA_UNIVERSE=custom` file loader |
| ✅ done | Empty-state UI collapsed to one tabbed `ClosestToFiringPanel` |
| ✅ done | Precision-first refit dark-launch (2026-07-17): signed-pressure primitives, ingest hygiene, distribution veto `[DV]` in shadow, classified deal flow, `accumulation_assessment` envelope. See CHANGELOG for the full contract; flip `distribution_veto_mode` in `config/stage_weights.json` to activate. |
| ✅ done | Balanced-holding foundation (2026-07-17): action-priority correction (URGENT bug fix — stop/T2/T1 now precede distribution/day_180/end_date), outcome label v2 (mtm/realized split with is_open flag), split-date advisory labels, 9-state action ladder, sliding-window trigger every 5 T+90 outcomes with auto champion-challenger invocation (tuner's `MIN_OUTCOMES_TO_TUNE=20` floor + strict-beat ratchet gate every write). 13 medium/larger items parked in `ideas.md` D–P. |
| ⏳ next | Wire `stages/wyckoff.py`, `vsa.py`, `avwap.py` into `PER_TICKER_CHAIN` (see AGENT_HANDOFF.md for step-by-step) |
| ⏳ next | `stages/exit_watch.py` daily scan on held picks |
| ⏳ next | ATR20-normalized thresholds + regime vol-clock in `stages/regime.py` |
| ⏳ next | Backtest v3 spine over trailing 12 months to seed champion metric |
| ⏳ next | Contextual-bandit tuner (once ~3 months of outcomes accumulated) |
| ⏳ next | NSE bhavcopy adapter (replaces yfinance for India-native data) |
| ⏳ later | Offline RL (CQL/IQL) for per-stage threshold tuning |

---

## Disclaimer

Educational. Picks are algorithmic and **not financial advice**. The pipeline has **not been backtested** on multi-year Indian data — paper-trade the first 10–15 picks before deploying real capital.
