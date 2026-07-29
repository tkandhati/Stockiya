# Institutional Flow — bulk/block deals + delivery % (scoring-neutral)

Bulk & block deals and NSE delivery % are the **least-fakeable** signals the
engine has: literal records of large trades, and the deliverable-vs-traded ratio
that separates real accumulation from intraday churn. They are also the
**flakiest to obtain** — behind the corporate firewall they arrive by
hand-copying `data/deals/all.csv` and `data/delivery/*.csv`, and are often
absent.

That tension drives the core rule:

> **Institutional flow is scoring-neutral.** It never touches composite `S`, the
> confirmation ranker, gate survival, or which stocks are picked. A signal that
> is frequently missing must not be able to move a score (it would disturb
> working picks and could zero out dataless days).

It is used in three ways, all scoring-neutral: **guide** what to analyze,
**explain** dropped candidates, and — added 2026-07-29 — **flag** an
OBV-vs-delivery contradiction on a *selected* pick. The third
(`flow_interest.obv_delivery_divergence`) fires when the tape reads accumulation
but delivery is **weak and falling**, and writes an advisory string to
`accumulation_assessment.contradictions` only. Like the other two it never
touches composite `S`, the confirmation ranker, gate survival, or which stocks
are picked — it can annotate, never gate. (The USE 1 / USE 2 diagram below shows
the two original roles; USE 3 is annotation-only on picks already chosen.)

```
   deals + delivery corpora
            │
            ▼
   flow_interest.py  ── combined strength (0–100), scoring-neutral ──┐
            │                                                          │
   ┌────────┴─────────┐                                               │
   ▼                  ▼                                               ▼
 USE 1: watchlist   USE 2: dropped view                       (per-pick badge +
 "analyze these"    strength vs normal + why dropped           presentation_rank,
 build_watchlist()  closest_to_firing[*].flow_interest         picked_reason)

   ── price/volume scoring flow (composite S, τ, ranker) is UNTOUCHED ──
```

## The combined strength

One headline `institutional_flow` score in **0–100**, blending two legs that stay
individually visible (each on its own rolling average):

| Leg | Source | Rolling average | Maps to |
|---|---|---|---|
| **Deals** | `block_deals.aggregate_30d` | 7d-vs-30d net-buy **trend** | rising 1.0 / flat 0.6 / falling 0.2, `+0.2` if a disclosed institution is on record; silent unless ≥2 deals **and** net buying |
| **Delivery** | `delivery.delivery_advisory` | today / week / 15d / 30d means (20d internal for trend+level), 5d-vs-20d trend | band 40%→0 … 60%→1, `±0.15` for rising/falling |

Blend renormalizes over whichever legs are present ("if found"). Level bands:
`strong ≥ 66`, `moderate ≥ 33`, else `low`. **"Strength against the normal"** =
`vs_normal.delivery_percentile`, the percentile of the name's delivery % against
today's market cross-section (`delivery.latest_market_pcts()`).

Fix-points live at the top of `backend/flow_interest.py`
(`DEAL_SUBWEIGHT`/`DELIV_SUBWEIGHT`, `DEAL_TREND_SCORE`, `DEAL_DISCLOSED_BOOST`,
`DELIV_TREND_ADJ`, `STRONG_INTEREST`/`MODERATE_INTEREST`, `MIN_DEAL_COUNT`,
`WATCHLIST_TOP_N`). All display-only — none can change which stocks are picked.

## Use 1 — guide selection (watchlist)

`build_watchlist()` scores every name that actually carries flow (deal symbols ∪
delivery symbols), filters to **moderate+**, ranks by strength, and returns
`[{symbol, flow_interest}]`. Surfaced as `watchlist` in the picks response — pure
guidance on what to analyze. It does **not** enter the scan and never changes a
gate. (Scope decision: watchlist-only — the scan universe is *not* augmented,
because behind the firewall a name outside the universe usually has no OHLCV to
analyze.)

## Use 2 — explain dropped candidates

`closest_to_firing` (`{accumulation, breakout, overall}`) is computed on **every**
run — not just zero-pick days — so near-misses are always visible alongside picks.
Each row now carries:

- **`flow_interest`** — the combined strength + `vs_normal` percentile ("what
  strength it carries against the normal").
- **`pulled_down_by`** — the single gate that, if it fired, would move `S` the
  most, with its reason ("why the pick was dropped").

So a name institutions are clearly accumulating that the pipeline dropped shows up
with both its strength and the exact reason — the drop is auditable, not silent.

## Per-pick annotations (selected picks)

Attached in orchestrator Phase 3 (after selection, scoring-neutral):

- `pick.flow_interest` — strength block, incl. `suppressed` (no/weak flow) and
  `picked_reason` (`why_picked()` — the price/volume basis, shown beside a
  suppressed pick that got in on technicals alone).
- `pick.presentation_rank` — orders picks for display by flow strength; the
  canonical confirmation `rank` is untouched. Falls back to confirmation order
  when no flow data exists, so offline runs present picks exactly as the flow
  chose them. Suppressed/no-flow picks sort to the bottom.

## Data dependencies & degradation

Everything reads files only (no network, deterministic, never raises). With no
`data/deals/all.csv` and no `data/delivery/*.csv` (the common offline state):
watchlist is empty, `flow_interest` is `available:false / suppressed:true`,
`vs_normal` is `null`, and picks present in pure confirmation order. **DEMO scoring
is byte-identical** to a run without any of this.

## Files

| File | Role |
|---|---|
| `backend/flow_interest.py` | The scoring-neutral layer: strength, watchlist, presentation rank, why-picked |
| `backend/delivery.py` | `delivery_advisory`, batch `all_advisories`, `latest_market_pcts` |
| `backend/block_deals.py` | `aggregate_30d`, `deal_symbols` |
| `backend/orchestrator.py` | Phase 3 annotation; `_closest_row`/`_collect_closest_to_firing` enrichment; watchlist build |
| `backend/stages/render.py` | Attaches `closest_to_firing` (always) + `watchlist` |
| `middleware/schemas.py` | `ClosestRow.flow_interest`, `PicksResponse.watchlist` |
| `backend/stages/rank.py` | Confirmation score — **pure price/volume**, no deal/delivery term |

## Frontend follow-up (not implemented here)

Backend always emits the data; the UI still needs to: render the
`closest_to_firing` panel in the **non-empty** state, add a flow-strength badge on
picks, and show the `watchlist`. Read: `pick.presentation_rank`,
`pick.flow_interest.{level,score,vs_normal,picked_reason}`,
`closest_to_firing.*[].flow_interest`, `watchlist[].flow_interest`.
