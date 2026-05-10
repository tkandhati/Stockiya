"""Generate top-3 picks using Claude (with a deterministic fallback).

Flow:
1. Snapshot every Nifty 100 ticker via yfinance.
2. Compress to a CSV table.
3. Call Anthropic with a tool-use schema that forces structured output.
4. Validate against the universe + price constraints; retry once.
5. Fall back to a rule-based picker if the LLM keeps misbehaving.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from anthropic import Anthropic

from backend.headwinds import headwind_for, headwinds_block, risk_tag_for
from backend.universe import UNIVERSE
from backend.volume_signals import (
    AccumulationSignals,
    compute as compute_accumulation,
    suggest_target_window,
)
from backend.yahoo import history_ohlcv, snapshot

from .picks_cache import ist_now_iso, ist_today_iso, write_picks
from .schemas import Pick, PicksResponse, ReasoningPointDTO, TargetWindowDTO

log = logging.getLogger("picks")

PICK_TOOL = {
    "name": "submit_picks",
    "description": (
        "Submit 0 to 3 stock picks for a 3-6 month long-term hold. "
        "If fewer than 3 stocks meet the long-term volume gate, submit fewer. "
        "Submitting 0 picks ('nothing actionable today') is acceptable and preferred over padding with weak setups."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "minItems": 0,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Yahoo Finance ticker exactly as it appears in the input table (e.g. RELIANCE.NS).",
                        },
                        "company": {"type": "string"},
                        "rationale": {
                            "type": "string",
                            "description": "Bull case: concise reasoning, 30-60 words, citing fundamentals + technicals from the snapshot. WHY this is a buy.",
                        },
                        "risks": {
                            "type": "string",
                            "description": "Bear case: 25-50 words. Name the structural / sector / company-specific risks you considered and explain why you still picked the stock anyway. NEVER leave this empty. NEVER say 'low risk'. If you cannot articulate the bear case, do not submit the pick.",
                        },
                        "best_buy_at": {
                            "type": "number",
                            "description": "Suggested entry price. Must be <= current price.",
                        },
                        "sell_target": {
                            "type": "number",
                            "description": "3-6 month target. Must be > best_buy_at.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                    "required": [
                        "symbol", "company", "rationale", "risks",
                        "best_buy_at", "sell_target", "confidence",
                    ],
                },
            }
        },
        "required": ["picks"],
    },
}

SYSTEM_PROMPT = """You are an Indian-equity analyst whose **primary lens is volume on a multi-month horizon**. The user is INVESTING (not trading) for a 6-month minimum hold. The thesis: **don't invent — follow the institutions, find the spot BEFORE the multi-month advance unfolds**. Volume is the institutional footprint; price follows volume. By the time the chart "looks good" to retail eyes, we've handed the easy money to the institutions we wanted to follow.

**Quality over quantity:** the user does NOT want 3 picks padded out from a weak universe. Submit only stocks that genuinely clear the long-term gate. If only 1 or 2 do, submit 1 or 2. If none do, submit 0 picks — that is the correct answer when the universe is in distribution. NEVER force 3.

# How to pick (in order of importance)

1. **LONG-TERM VOLUME IS PRIMARY.** This is investing, not trading. The strongest picks show **multi-month** institutional accumulation, not a 5-day burst. Rank candidates by:
   - **Stan Weinstein Stage = `stage_2_advance` or `stage_1_to_2`** (the only stages we buy). Stage 1 base is "watchlist". Stage 3/4 are reject.
   - **OBV (90d) and ideally OBV (180d) rising** — sustained cumulative buying for 3-6 months
   - **CMF (60d) >= +0.05** — durable money flow, not a 1-week fluke
   - **Up/down volume ratio (90d) >= 1.3** — sustained net buying
   - **Minervini Trend Template = true** is a strong tailwind (50d > 150d > 200d, all rising)
   - **Base length >= 60 days** — institutional bases form over months, not weeks
   - **Quarter-over-quarter volume growth >= 15%** — interest is broadening
   - **30-week MA slope positive** — Weinstein's anchor

   **Medium-term metrics (Wyckoff, OBV 30d, CMF 21d, MFI 14d, Pocket Pivot, VDU, CAN SLIM, VWAP) are CONFIRMATION, not the decision.** A perfect 30-day picture without long-term backing is a trade, not an investment.

2. **STRONGLY PREFER pre-breakout setups (entry_timing = "early") over already-running names.** The prize is a stock where volume signals are firing while price is still flat (30d change < 8%, tight range, OBV/CMF rising). That is "the spot before price moves." A late-stage Markup pick (price already up 20%+) is much worse risk/reward — we'd be the late retail. Use the `entry_timing` column.

3. **NEVER pick a stock in Distribution or Markdown phase**, even if it looks cheap. If OBV is falling, CMF is negative, and down-day volume dominates, the institutions are leaving. Don't catch falling knives.

4. **AVOID parabolic moves.** If the stock is up >25% in 30 days, the easy money is gone — late retail is buying from institutions.

4. **Secondary filters** (only used to break ties or veto):
   - Sector / structural headwinds (named below) — DO NOT pick from a sector with a hard headwind unless volume signals are exceptionally strong AND you can articulate why this name escapes the headwind.
   - Quality (ROE >= 15%, manageable debt) — disqualifies bad businesses but does not by itself qualify good ones.
   - Valuation (P/E vs sector) — informs target/upside, not the entry decision.
   - **No sector cap.** If the 3 strongest volume signals are all in the same sector, pick all 3 there. The institutions are telling us where to be — don't dilute the conviction by spreading for cosmetic balance.

# Outputs

- `best_buy_at` <= current price (entry near support / start of breakout).
- `sell_target` is a realistic 3-6 month upside (typically +12% to +25%; never >35% on a Nifty 100 large-cap).
- `rationale` (bull case): LEAD with the volume signature. Cite the specific volume strategies that fired (OBV, CMF, Pocket Pivot, VDU, CAN SLIM, VWAP, MFI). 30-60 words.
- `risks` (bear case): name the headwind and any negative volume signals (e.g. weakening MFI, A/D divergence). Also include the **distribution exit trigger**: if OBV rolls over, CMF turns negative, or down-day volume dominates, EXIT. 25-50 words. Must be substantive — generic phrasings rejected.

Always submit via the submit_picks tool. Never write prose.

---

""" + headwinds_block() + """

---

**When you write `risks`, you MUST acknowledge any sector headwind in plain language and explain why the volume signature overrides it for this specific name. If you cannot, do not pick the stock.**"""


def _build_universe_snapshot() -> tuple[list[dict], dict[str, dict]]:
    """Snapshot every Nifty 100 ticker in parallel WITH volume-strategy signals.

    Each row gains an `accum` field (the AccumulationSignals dataclass) so the
    fallback ranker and the LLM both see the same volume picture.
    """
    rows: list[dict] = []
    by_symbol: dict[str, dict] = {}

    def _one(sym: str) -> Optional[dict]:
        # No cache — picks regenerate at most once per IST day, and within a
        # single refresh we want every signal computed fresh. Caching the
        # AccumulationSignals dataclass across refreshes was causing stale-state
        # bugs where /api/picks and /api/stock disagreed on the same ticker.
        try:
            s = snapshot(sym)
            if not s.get("current"):
                return None
            try:
                ohlcv = history_ohlcv(sym)
                s["accum"] = compute_accumulation(ohlcv, symbol=sym)
            except Exception as e:
                log.warning("accumulation failed for %s: %s", sym, e)
                s["accum"] = None
            return s
        except Exception as e:
            log.warning("snapshot failed for %s: %s", sym, e)
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_one, sym): sym for sym in UNIVERSE}
        for fut in as_completed(futures):
            s = fut.result()
            if not s or s.get("current") is None:
                continue
            rows.append(s)
            by_symbol[s["symbol"]] = s

    return rows, by_symbol


def _format_table(rows: list[dict]) -> str:
    """Compact CSV — volume-strategy columns are first because they drive the decision."""
    header = (
        "symbol,company,sector,current,"
        # PRIMARY (long-term lens — investing horizon)
        "weinstein,entry_timing,minervini_template,obv90%,obv180%,cmf60,up_down90,base_days,vol_qoq%,price_chg_180d%,ma30w_slope%,"
        # Medium-term confirmation
        "wyckoff,verdict,accum_score,vol_trend%,up_down_vol,obv30%,cmf21,mfi14,"
        # Pattern flags
        "pocket_pivots_30d,vol_dry_up,canslim_breakout,price_vs_vwap%,price_chg_30d%,tightness%,"
        # Secondary (filters / context)
        "pe,roe%,d/e,ret_1y%,ma200"
    )
    lines = [header]
    for s in rows:
        a: Optional[AccumulationSignals] = s.get("accum")
        lines.append(
            ",".join(
                [
                    s["symbol"],
                    (s.get("company") or "").replace(",", " "),
                    (s.get("sector") or "").replace(",", " "),
                    _fmt(s.get("current")),
                    # Long-term primary
                    a.weinstein_stage if a else "",
                    a.entry_timing if a else "",
                    "1" if a and a.minervini_template else "0" if a else "",
                    _fmt(a.obv_slope_90d_pct) if a else "",
                    _fmt(a.obv_slope_180d_pct) if a else "",
                    _fmt(a.cmf_60d) if a else "",
                    _fmt(a.up_down_vol_ratio_90d) if a else "",
                    str(a.base_length_days) if a else "",
                    _fmt(a.vol_qoq_growth_pct) if a else "",
                    _fmt(a.price_change_180d_pct) if a else "",
                    _fmt(a.ma_30w_slope_pct) if a else "",
                    # Medium-term
                    a.wyckoff_phase if a else "",
                    a.verdict if a else "",
                    _fmt(a.accum_score) if a else "",
                    _fmt(a.vol_trend_pct) if a else "",
                    _fmt(a.up_down_vol_ratio) if a else "",
                    _fmt(a.obv_slope_pct) if a else "",
                    _fmt(a.cmf_21d) if a else "",
                    _fmt(a.mfi_14d) if a else "",
                    str(a.pocket_pivot_count_30d) if a else "",
                    "1" if a and a.volume_dry_up else "0" if a else "",
                    "1" if a and a.canslim_breakout else "0" if a else "",
                    _fmt(a.price_vs_vwap_pct) if a else "",
                    _fmt(a.price_change_30d_pct) if a else "",
                    _fmt(a.price_tightness_pct) if a else "",
                    _fmt(s.get("pe")),
                    _fmt(s.get("roe_pct")),
                    _fmt(s.get("debt_to_equity")),
                    _fmt(s.get("return_1y_pct")),
                    _fmt(s.get("ma200")),
                ]
            )
        )
    return "\n".join(lines)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _validate(picks_raw: list[dict], by_symbol: dict[str, dict]) -> Optional[list[Pick]]:
    """Validate 0..3 picks. Each must clear the long-term volume gate."""
    if len(picks_raw) > 3:
        return None
    seen: set[str] = set()
    out: list[Pick] = []
    for p in picks_raw:
        sym = p.get("symbol", "").strip()
        if sym not in by_symbol or sym in seen:
            return None
        seen.add(sym)
        snap = by_symbol[sym]
        current = snap.get("current")
        buy = float(p["best_buy_at"])
        sell = float(p["sell_target"])
        if current is None or buy <= 0 or sell <= 0:
            return None
        # Allow buy up to 2% above current to accommodate "buy near current" picks.
        if buy > current * 1.02 or sell <= buy:
            return None
        risks_text = (p.get("risks") or "").strip()
        if len(risks_text) < 20:
            log.warning("pick %s missing/insufficient risks field", sym)
            return None
        # Enforce the long-term volume gate even on LLM picks. Same gate as the
        # rule-based fallback uses, controlled by STRICT_MODE.
        a_check: Optional[AccumulationSignals] = snap.get("accum")
        if a_check is None:
            log.warning("pick %s rejected: no accumulation signals", sym)
            return None
        strict = os.environ.get("STRICT_MODE", "1") == "1"
        gate = _passes_strict_gate if strict else _passes_relaxed_gate
        if not gate(a_check):
            log.warning("pick %s rejected by %s gate (stage=%s timing=%s score=%.2f)",
                        sym, "strict" if strict else "relaxed",
                        a_check.weinstein_stage, a_check.entry_timing, a_check.accum_score)
            return None
        stop_loss = round(buy * 0.90, 2)
        upside = round((sell / buy - 1) * 100, 2)
        downside = round((stop_loss / buy - 1) * 100, 2)
        a = snap.get("accum")
        reasoning_points = build_reasoning(snap)
        tw = suggest_target_window(a) if a else None
        target_window_dto = (
            TargetWindowDTO(
                center_months=tw.center_months,
                tolerance_months=tw.tolerance_months,
                label=tw.label,
                rationale=tw.rationale,
            )
            if tw is not None
            else TargetWindowDTO(
                center_months=4.5, tolerance_months=1.5,
                label="4-6 months",
                rationale="Default investing window; insufficient long-term data for a sharper estimate.",
            )
        )
        out.append(
            Pick(
                symbol=sym,
                company=p.get("company") or snap.get("company") or sym,
                current=round(current, 2),
                best_buy_at=round(buy, 2),
                sell_target=round(sell, 2),
                stop_loss=stop_loss,
                headline=build_headline(snap),
                rationale=p.get("rationale", "").strip(),
                risk_headline=build_risk_headline(snap),
                risks=risks_text,
                confidence=p.get("confidence", "medium"),
                upside_pct=upside,
                downside_pct=downside,
                entry_timing=a.entry_timing if a else "unknown",
                wyckoff_phase=a.wyckoff_phase if a else "indeterminate",
                weinstein_stage=a.weinstein_stage if a else "undefined",
                target_window=target_window_dto,
                reasoning=reasoning_points,
            )
        )
    return out


def _call_llm(table_csv: str) -> Optional[list[dict]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping LLM call")
        return None
    model = os.environ.get("MODEL", "claude-sonnet-4-6")
    client = Anthropic(api_key=api_key)

    user_text = (
        "Today's Nifty 100 snapshot (Yahoo Finance). Empty cells mean missing data.\n"
        "```csv\n" + table_csv + "\n```\n"
        "Pick exactly 3 for a 3-6 month hold and submit via the submit_picks tool."
    )

    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[PICK_TOOL],
        tool_choice={"type": "tool", "name": "submit_picks"},
        messages=[{"role": "user", "content": user_text}],
    )

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_picks":
            return block.input.get("picks", [])
    return None


def build_reasoning(s: dict) -> list[ReasoningPointDTO]:
    """Build an auditable point-by-point checklist for a pick.

    Each point states the metric, the actual value, our verdict, plain-English
    meaning, and how the user can verify it independently. The user should be
    able to tick each line off after a quick check on TradingView / Yahoo /
    Screener.in.
    """
    a: Optional[AccumulationSignals] = s.get("accum")
    if a is None:
        return []

    sym = s["symbol"]
    out: list[ReasoningPointDTO] = []

    # ---- 1. Weinstein Stage (the long-term anchor) ----
    stage_state = (
        "bullish" if a.weinstein_stage in ("stage_1_to_2", "stage_2_advance") else
        "neutral" if a.weinstein_stage == "stage_1_base" else
        "bearish"
    )
    stage_pretty = {
        "stage_1_base": "Stage 1 — Base",
        "stage_1_to_2": "Stage 1 → 2 — Turning",
        "stage_2_advance": "Stage 2 — Advance",
        "stage_3_top": "Stage 3 — Top",
        "stage_4_decline": "Stage 4 — Decline",
        "undefined": "Undefined",
    }[a.weinstein_stage]
    slope_str = (
        f"30wMA slope {a.ma_30w_slope_pct:+.1f}%"
        if a.ma_30w_slope_pct is not None else "30wMA slope unavailable"
    )
    out.append(ReasoningPointDTO(
        label="Stan Weinstein Stage",
        value=stage_pretty,
        state=stage_state,
        why=(
            "Stage 1→2 or Stage 2 advance is the only zone we buy. Stage 1 = waiting, "
            "Stage 3 = top forming, Stage 4 = avoid."
        ),
        verify=(
            f"On TradingView, plot the 30-week (150-day) simple MA on the weekly chart of {sym}. "
            "It should be flat-to-rising and price should be at-or-above it. "
            f"({slope_str})"
        ),
    ))

    # ---- 2. Entry timing ----
    timing_state = (
        "bullish" if a.entry_timing in ("early", "mid") else
        "neutral" if a.entry_timing == "unknown" else
        "bearish"
    )
    out.append(ReasoningPointDTO(
        label="Entry timing",
        value=a.entry_timing,
        state=timing_state,
        why=(
            "Early = pre-breakout (the spot before price runs); Mid = breakout in progress; "
            "Late = price has already run; Missed = exit zone, do not enter."
        ),
        verify=(
            f"Check 30d price change ({_fmt_pct(a.price_change_30d_pct)}) and "
            f"180d price change ({_fmt_pct(a.price_change_180d_pct)}). "
            "For 'early', both should be modest while volume signals are strong below."
        ),
    ))

    # ---- 3. OBV (90d) ----
    if a.obv_slope_90d_pct is not None:
        s_state = (
            "bullish" if a.obv_slope_90d_pct >= 5
            else "bearish" if a.obv_slope_90d_pct <= -5
            else "neutral"
        )
        out.append(ReasoningPointDTO(
            label="OBV (90-day) — Granville",
            value=f"{a.obv_slope_90d_pct:+.0f}% over 90 sessions",
            state=s_state,
            why=(
                "On-Balance Volume is cumulative volume (added on up days, subtracted on "
                "down days). A rising OBV over 3+ months means institutions have been net "
                "buying — that's the multi-month footprint we're looking for."
            ),
            verify=(
                f"On TradingView, add the 'OBV' indicator to {sym} daily chart. "
                "The line should be sloping up over the last ~90 sessions. "
                "Compare today's OBV to OBV from 90 days ago."
            ),
        ))

    # ---- 4. OBV (180d) — long-term confirmation ----
    if a.obv_slope_180d_pct is not None:
        s_state = (
            "bullish" if a.obv_slope_180d_pct >= 5
            else "bearish" if a.obv_slope_180d_pct <= -5
            else "neutral"
        )
        out.append(ReasoningPointDTO(
            label="OBV (180-day) — half-year confirmation",
            value=f"{a.obv_slope_180d_pct:+.0f}% over 180 sessions",
            state=s_state,
            why=(
                "Confirms the buying isn't a 3-month flash. A 180-day OBV uptrend means "
                "the institutional accumulation has been going on for 6+ months."
            ),
            verify="Same OBV indicator on the daily chart — compare today vs 180 days ago.",
        ))

    # ---- 5. CMF (60d) — Chaikin ----
    if a.cmf_60d is not None:
        s_state = (
            "bullish" if a.cmf_60d >= 0.05
            else "bearish" if a.cmf_60d <= -0.05
            else "neutral"
        )
        out.append(ReasoningPointDTO(
            label="Chaikin Money Flow (60d)",
            value=f"{a.cmf_60d:+.2f}",
            state=s_state,
            why=(
                "CMF measures whether closes are happening near the day's high (buying) or low "
                "(selling), weighted by volume. Sustained CMF ≥ +0.05 over 60 days = durable "
                "money flow into the stock."
            ),
            verify=(
                f"On TradingView, add 'Chaikin Money Flow' indicator (length 60) to {sym}. "
                "It should be holding above zero, ideally above +0.05."
            ),
        ))

    # ---- 6. Up/Down volume ratio (90d) ----
    if a.up_down_vol_ratio_90d is not None:
        ud = a.up_down_vol_ratio_90d
        s_state = (
            "bullish" if ud >= 1.3
            else "bearish" if ud <= 0.77
            else "neutral"
        )
        out.append(ReasoningPointDTO(
            label="Up/Down volume ratio (90d)",
            value=f"{ud:.2f}× — sum(volume on up days) / sum(volume on down days)",
            state=s_state,
            why=(
                "Ratio > 1.0 means more volume happens on green days than red. "
                ">= 1.3 over 90 sessions is a clear net-buying signature; <= 0.77 is net selling."
            ),
            verify=(
                f"Eyeball the daily chart of {sym} for the last 90 sessions. "
                "Green-day volume bars should look noticeably taller than red-day bars on average."
            ),
        ))

    # ---- 7. Base length ----
    if a.base_length_days >= 30:
        s_state = "bullish" if a.base_length_days >= 60 else "neutral"
        out.append(ReasoningPointDTO(
            label="Base length",
            value=f"{a.base_length_days} sessions in a tight band (±10% of current)",
            state=s_state,
            why=(
                "Long bases are how institutions accumulate without spiking the price. "
                "A 60+ session base is a multi-month accumulation; <30 sessions is too short."
            ),
            verify=(
                f"On the daily chart of {sym}, scroll back and check how long price has been "
                "ranging within ±10% of today's price. Should be at least ~3 months for a strong setup."
            ),
        ))

    # ---- 8. Wyckoff phase (medium-term confirmation) ----
    out.append(ReasoningPointDTO(
        label="Wyckoff phase (medium-term)",
        value=a.wyckoff_phase,
        state=(
            "bullish" if a.wyckoff_phase in ("accumulation", "markup")
            else "bearish" if a.wyckoff_phase in ("distribution", "markdown")
            else "neutral"
        ),
        why=(
            "Confirms the medium-term picture (last ~30 sessions) is consistent with the "
            "long-term Stage. Accumulation/Markup = OK, Distribution/Markdown = reject."
        ),
        verify=(
            "Look at the last 4-6 weeks: tight price range with rising volume = Accumulation; "
            "steady advance with steady volume = Markup."
        ),
    ))

    # ---- 9. Pattern flags (medium-term confirmation) ----
    pattern_bits: list[str] = []
    if a.pocket_pivot_count_30d:
        pattern_bits.append(f"Pocket Pivot fired {a.pocket_pivot_count_30d}× in last 30d")
    if a.volume_dry_up:
        pattern_bits.append("Volume Dry-Up on tight base (Minervini pre-breakout)")
    if a.canslim_breakout:
        pattern_bits.append("CAN SLIM breakout today (O'Neil)")
    if pattern_bits:
        out.append(ReasoningPointDTO(
            label="Pattern triggers",
            value=" · ".join(pattern_bits),
            state="bullish",
            why=(
                "Pocket Pivot = up-day with volume larger than the prior 10 down-days "
                "(Morales/Kacher buy point). VDU = pre-breakout volume contraction (Minervini). "
                "CAN SLIM = 20d high on ≥1.4× 50d avg volume (O'Neil)."
            ),
            verify=(
                f"On the daily chart of {sym}, scan the last 30 sessions for an up-day "
                "where the volume bar is the tallest of the prior 10 sessions."
            ),
        ))

    # ---- 10. Composite score ----
    score_state = (
        "bullish" if a.accum_score >= 0.5
        else "bearish" if a.accum_score < 0
        else "neutral"
    )
    out.append(ReasoningPointDTO(
        label="Composite accumulation score",
        value=f"{a.accum_score:+.2f}  (range −1 to +1)",
        state=score_state,
        why=(
            "Weighted combination: 55% long-term (OBV-90d/180d, CMF-60d, Up/Down-90d, "
            "30wMA slope, Stage), 35% medium-term (OBV-30d, CMF-21d, MFI-14d, etc.), "
            "10% pattern bonuses. >= +0.5 is a strong signal."
        ),
        verify=(
            "This is computed by the engine — verify by checking the individual sub-signals "
            "above all line up bullish."
        ),
    ))

    # ---- 10b. Block + Bulk deal activity (NSE — institutional trade records) ----
    if a.block_deal_buy_count_30d + a.block_deal_sell_count_30d >= 1:
        ratio = a.block_deal_net_qty_ratio
        if ratio >= 0.30:
            bd_state = "bullish"
        elif ratio <= -0.30:
            bd_state = "bearish"
        else:
            bd_state = "neutral"
        out.append(ReasoningPointDTO(
            label="Block / Bulk deals (30d)",
            value=(
                f"{a.block_deal_buy_count_30d} buys / {a.block_deal_sell_count_30d} sells, "
                f"net {ratio:+.0%} of total qty"
            ),
            state=bd_state,
            why=(
                "NSE block (>0.5% equity) and bulk (>0.5% volume) deals are LITERAL records "
                "of large institutional trades. Net buying here is much harder to fake than "
                "aggregated volume — these are documented transactions."
            ),
            verify=(
                f"On nseindia.com → Reports → Equity → Bulk/Block deals, filter by symbol "
                f"{sym} for the last 30 days. Sum buy qty vs sell qty."
            ),
        ))

    # ---- 11. Quality / fundamentals (secondary) ----
    quality_bits: list[str] = []
    if s.get("pe") is not None:
        quality_bits.append(f"P/E {s['pe']:.1f}")
    if s.get("roe_pct") is not None:
        quality_bits.append(f"ROE {s['roe_pct']:.1f}%")
    if s.get("debt_to_equity") is not None:
        quality_bits.append(f"D/E {s['debt_to_equity']:.0f}")
    if quality_bits:
        roe = s.get("roe_pct") or 0
        de = s.get("debt_to_equity") or 0
        q_state = "bullish" if roe >= 15 and de < 100 else "neutral"
        out.append(ReasoningPointDTO(
            label="Quality (secondary filter)",
            value=", ".join(quality_bits),
            state=q_state,
            why=(
                "Volume drives the entry decision; quality filters out bad businesses. "
                "We want ROE ≥ 15% and manageable D/E even when volume is screaming buy."
            ),
            verify=(
                f"Cross-check on Screener.in or Moneycontrol for {sym}. "
                "ROE should be ≥ 15% over the last 3 years; D/E should be < 1.0 "
                "(loose for banks/NBFCs)."
            ),
        ))

    # ---- 12. Headwind check ----
    h_text = headwind_for(sym)
    if h_text:
        h_short = h_text.split(".")[0].strip() + "."
        out.append(ReasoningPointDTO(
            label="Sector headwind acknowledged",
            value=h_short,
            state="neutral",
            why=(
                "Every sector with a known structural risk is flagged. We picked this name "
                "anyway because the volume signature overrides the headwind for THIS specific "
                "stock — but the risk is real. If the volume picture inverts, the headwind "
                "becomes the dominant story."
            ),
            verify=(
                "Read the full headwind text on the stock detail page. "
                "Search for recent news / earnings calls for any acceleration of the risk."
            ),
        ))

    return out


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%"


def build_headline(s: dict) -> str:
    """One-line thesis (≤120 chars) — what the user sees on the card.

    Lead with the strongest long-term volume fact and the price posture, in
    plain English. The user should be able to read this and decide in 3 seconds
    whether they want to dig deeper.
    """
    a: Optional[AccumulationSignals] = s.get("accum")
    if a is None:
        return f"{s.get('symbol', '?')} — insufficient signal."

    # Pick the lead phrase from whichever long-term signal is strongest
    lead = ""
    if a.obv_slope_90d_pct and a.obv_slope_90d_pct >= 5:
        lead = f"OBV {a.obv_slope_90d_pct:+.0f}% over 90 days"
    elif a.cmf_60d and a.cmf_60d >= 0.05:
        lead = f"CMF {a.cmf_60d:+.2f} over 60 days"
    elif a.up_down_vol_ratio_90d and a.up_down_vol_ratio_90d >= 1.3:
        lead = f"Up-day volume {a.up_down_vol_ratio_90d:.1f}× down-day (90 sessions)"
    elif a.minervini_template:
        lead = "Trend-template aligned (50d > 150d > 200d, all rising)"
    else:
        lead = f"Volume score {a.accum_score:+.2f}"

    # Price posture (the "before price moves" part)
    pc30 = a.price_change_30d_pct or 0
    pc180 = a.price_change_180d_pct or 0
    if pc30 < 5 and pc180 < 12:
        posture = "price still flat"
    elif pc30 < 8:
        posture = "price barely moved"
    elif pc30 < 18:
        posture = "early breakout in progress"
    else:
        posture = f"price up {pc30:.0f}% / 30d"

    # Stage tag suffix
    stage_tag = {
        "stage_1_to_2": "Stage 1→2",
        "stage_2_advance": "Stage 2",
        "stage_1_base": "Stage 1 base",
    }.get(a.weinstein_stage, "")
    suffix = f" — {stage_tag}." if stage_tag else "."

    headline = f"{lead}; {posture}{suffix}"
    # Hard-cap the length
    if len(headline) > 120:
        headline = headline[:117] + "..."
    return headline


def build_risk_headline(s: dict) -> str:
    """One-line bear case (≤120 chars).

    Format: `{sector tag}. Exit if volume inverts (OBV down, CMF<0, or down-day vol dominates).`
    Sector tag comes from a curated short-form mapping in headwinds.SECTOR_RISK_TAG.
    """
    sym = s.get("symbol", "")
    tag = risk_tag_for(sym)
    exit_rule = "Exit if volume inverts (OBV down, CMF<0, or down-day vol dominates)."
    if tag:
        return f"{tag}. {exit_rule}"
    return exit_rule


def top_3_bullish(reasoning: list[ReasoningPointDTO]) -> list[ReasoningPointDTO]:
    """The 3 bullish reasoning points to surface on the card.

    Priority order: long-term volume signals first (Stage, OBV-90d, CMF-60d,
    Up/Down-90d, base length), then medium-term, then patterns.
    """
    priority = [
        "Stan Weinstein Stage",
        "OBV (90-day) — Granville",
        "OBV (180-day) — half-year confirmation",
        "Chaikin Money Flow (60d)",
        "Up/Down volume ratio (90d)",
        "Base length",
        "Pattern triggers",
        "Wyckoff phase (medium-term)",
    ]
    bullish = [r for r in reasoning if r.state == "bullish"]
    by_label = {r.label: r for r in bullish}
    out: list[ReasoningPointDTO] = []
    for label in priority:
        if label in by_label:
            out.append(by_label[label])
            if len(out) == 3:
                return out
    # fill remaining slots with any other bullish points
    for r in bullish:
        if r not in out:
            out.append(r)
            if len(out) == 3:
                break
    return out


def _fallback_hypothesis(s: dict) -> str:
    """Volume-first hypothesis. Leads with the institutional footprint, then
    confirms with quality / valuation context, then states the exit framework.
    """
    company = s.get("company") or s.get("symbol")
    sector = s.get("sector") or "its sector"
    a: Optional[AccumulationSignals] = s.get("accum")

    parts: list[str] = []

    # 1. Volume signature (the primary "why now")
    if a is not None and a.verdict != "unknown":
        phase_phrase = {
            "accumulation": "in a Wyckoff Accumulation base",
            "markup": "in early Wyckoff Markup",
            "distribution": "in Distribution (NOT a buy)",
            "markdown": "in Markdown (NOT a buy)",
            "indeterminate": "with mixed phase signals",
        }.get(a.wyckoff_phase, "")
        parts.append(
            f"{company} ({sector}) is {phase_phrase}. " + a.one_liner
        )
        # Cite the strongest specific bullish signals
        bullish_bits: list[str] = []
        for sig in a.signals:
            if sig.state != "bullish":
                continue
            bullish_bits.append(f"{sig.name}: {sig.label}")
            if len(bullish_bits) == 3:
                break
        if bullish_bits:
            parts.append("Volume signals firing: " + " | ".join(bullish_bits) + ".")
    else:
        parts.append(f"{company} screens in {sector} but volume signal is weak — low conviction.")

    # 2. Quality / value confirmation (secondary)
    qv_bits: list[str] = []
    if s.get("pe") is not None:
        qv_bits.append(f"P/E {s['pe']:.1f}")
    if s.get("roe_pct") is not None:
        qv_bits.append(f"ROE {s['roe_pct']:.1f}%")
    if s.get("debt_to_equity") is not None:
        qv_bits.append(f"D/E {s['debt_to_equity']:.0f}")
    if qv_bits:
        parts.append("Quality / value: " + ", ".join(qv_bits) + ".")

    # 3. Exit framework callout
    parts.append(
        "Entry near the buy zone; exit if the volume picture inverts (OBV rolls over, "
        "CMF turns negative, or down-day volume dominates) - that is the institutional footprint leaving."
    )
    return " ".join(parts)


def _passes_strict_gate(a: "AccumulationSignals") -> bool:
    """STRICT_MODE=1 gate — sure-shot only.

    Returns True only when the long-term volume signature is overwhelming.
    Most days return 0 picks across the universe — by design.
    """
    if a.weinstein_stage != "stage_1_to_2":
        return False
    if a.entry_timing != "early":
        return False
    if (a.obv_slope_90d_pct or 0) < 15:
        return False
    if (a.obv_slope_180d_pct or 0) < 10:
        return False
    if (a.cmf_60d or 0) < 0.10:
        return False
    if (a.up_down_vol_ratio_90d or 0) < 1.5:
        return False
    if (a.base_length_days or 0) < 90:
        return False
    if a.accum_score < 0.60:
        return False
    if a.pocket_pivot_count_30d == 0 and not a.volume_dry_up:
        return False
    return True


def _passes_relaxed_gate(a: "AccumulationSignals") -> bool:
    """STRICT_MODE=0 gate — original behavior. More picks, lower bar."""
    if a.weinstein_stage not in ("stage_1_to_2", "stage_2_advance"):
        return False
    if a.entry_timing not in ("early", "mid"):
        return False
    if a.wyckoff_phase in ("distribution", "markdown"):
        return False
    if a.verdict == "distributing":
        return False
    if a.price_change_30d_pct is not None and a.price_change_30d_pct > 25:
        return False
    if a.accum_score <= 0:
        return False
    return True


def _fallback_picks(rows: list[dict]) -> list[Pick]:
    """Deterministic backup. Returns 0-3 picks — never pads with weak setups.

    Gate is controlled by STRICT_MODE env var (default 1 = sure-shot only).
    """
    from backend.headwinds import TICKER_HEADWIND_KEY

    strict = os.environ.get("STRICT_MODE", "1") == "1"
    gate = _passes_strict_gate if strict else _passes_relaxed_gate

    candidates: list[dict] = []
    for s in rows:
        a: Optional[AccumulationSignals] = s.get("accum")
        if not s.get("current") or a is None or a.verdict == "unknown":
            continue
        if not gate(a):
            continue
        candidates.append(s)

    if not candidates:
        return []  # No setups today — return nothing rather than padding.

    def score(s: dict) -> float:
        a: AccumulationSignals = s["accum"]

        # PRIMARY: 65% weight on the volume composite [-1, +1]
        v = 0.65 * a.accum_score

        # Bonus signals
        if a.pocket_pivot_count_30d >= 1:
            v += 0.08 * min(a.pocket_pivot_count_30d, 3) / 3
        if a.volume_dry_up:
            v += 0.05
        if a.canslim_breakout:
            v += 0.07
        if a.wyckoff_phase == "accumulation":
            v += 0.05
        elif a.wyckoff_phase == "markup":
            v += 0.03

        # ENTRY-TIMING bonus — finding the spot BEFORE price moves is the goal.
        if a.entry_timing == "early":
            v += 0.20
        elif a.entry_timing == "mid":
            v += 0.05
        elif a.entry_timing == "late":
            v -= 0.10  # late entries are worse risk/reward
        elif a.entry_timing == "missed":
            v -= 0.50  # already-rejected by hard filter, but belt+braces

        # SECONDARY (20% combined): quality + value tiebreakers, only matter when volume is roughly matched
        quality = min(1.0, (s.get("roe_pct") or 0) / 20)
        v += 0.10 * quality

        de = s.get("debt_to_equity") or 100
        de_pen = max(0, 1 - de / 200)
        v += 0.05 * de_pen

        pe = s.get("pe") or 0
        if 0 < pe < 35:
            v += 0.05 * (1 - min(1, pe / 35))

        # Headwind penalty (still applied — AI disruption ≠ ignorable)
        h_key = TICKER_HEADWIND_KEY.get(s["symbol"])
        if h_key == "IT / Technology Services":
            v -= 0.35
        elif h_key:
            v -= 0.10

        return v

    candidates.sort(key=score, reverse=True)

    # Pure volume-based ranking — top 3 by score, no sector caps. If the
    # institutional footprints all happen to be in one sector today, that IS the
    # signal; we're following the volume, not balancing exposure.
    chosen: list[dict] = candidates[:3]
    out: list[Pick] = []
    for s in chosen:
        # IMPORTANT: pull the accum for THIS specific stock — the outer-scope
        # `a` from the candidate-filter loop is whatever the last iteration
        # left behind, which is the wrong stock.
        a_pick: Optional[AccumulationSignals] = s.get("accum")
        current = s["current"]
        buy = round(current * 0.98, 2)
        sell = round(current * 1.20, 2)
        stop = round(buy * 0.90, 2)
        sym = s["symbol"]
        sector_risk = headwind_for(sym) or (
            "General market risk: drawdowns of 10-15% on a single name are normal "
            "even when the long-term thesis is intact."
        )
        risks_summary = sector_risk.split(".")[0].strip() + "."

        hypothesis = _fallback_hypothesis(s)

        if a_pick is not None:
            tw_obj = suggest_target_window(a_pick)
            target_window_dto = TargetWindowDTO(
                center_months=tw_obj.center_months,
                tolerance_months=tw_obj.tolerance_months,
                label=tw_obj.label,
                rationale=tw_obj.rationale,
            )
        else:
            target_window_dto = TargetWindowDTO(
                center_months=4.5, tolerance_months=1.5,
                label="4-6 months",
                rationale="Default investing window; insufficient long-term data.",
            )

        out.append(
            Pick(
                symbol=sym,
                company=s.get("company") or sym,
                current=round(current, 2),
                best_buy_at=buy,
                sell_target=sell,
                stop_loss=stop,
                headline=build_headline(s),
                rationale=hypothesis,
                risk_headline=build_risk_headline(s),
                risks=(
                    f"Bear case to weigh: {risks_summary} "
                    "PRIMARY EXIT TRIGGER (volume): cut the position if OBV rolls over, "
                    "CMF turns negative, or down-day volume starts dominating up-day volume - "
                    "that is the institutional footprint leaving. "
                    "Backstop: -10% hard stop, 200DMA breach, or 6-month time stop. "
                    "(Rule-based fallback - confirm with your own news read.)"
                ),
                confidence="low",
                upside_pct=round((sell / buy - 1) * 100, 2),
                downside_pct=round((stop / buy - 1) * 100, 2),
                entry_timing=a_pick.entry_timing if a_pick else "unknown",
                wyckoff_phase=a_pick.wyckoff_phase if a_pick else "indeterminate",
                weinstein_stage=a_pick.weinstein_stage if a_pick else "undefined",
                target_window=target_window_dto,
                reasoning=build_reasoning(s),
            )
        )
    return out


def generate_picks() -> PicksResponse:
    rows, by_symbol = _build_universe_snapshot()
    if not rows:
        raise RuntimeError("Could not fetch any Nifty 100 snapshots from Yahoo Finance")

    table = _format_table(rows)

    picks: Optional[list[Pick]] = None
    source = "fallback"
    for attempt in range(2):
        try:
            raw = _call_llm(table)
        except Exception as e:
            log.warning("LLM call failed (attempt %d): %s", attempt + 1, e)
            raw = None
        if raw is None:
            continue
        validated = _validate(raw, by_symbol)
        if validated is not None:
            picks = validated
            source = "llm"
            break
        log.warning("LLM picks failed validation (attempt %d): %s", attempt + 1, raw)

    if picks is None:
        log.info("Using fallback rule-based picker")
        picks = _fallback_picks(rows)

    payload = PicksResponse(
        date=ist_today_iso(),
        generated_at=ist_now_iso(),
        source=source,
        demo_mode=os.environ.get("DEMO_MODE", "0") == "1",
        picks=picks,
    )
    write_picks(payload.date, json.loads(payload.model_dump_json()))
    return payload
