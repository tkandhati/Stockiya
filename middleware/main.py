"""Stockiya middleware — HTTP API.

Run from the project root:
    uvicorn middleware.main:app --reload --port 8000

This service serves the day's precomputed picks (from `data/picks_<date>.json`)
and the volume-strategy detail panel for any Nifty 100 ticker. The pipeline
itself lives in `backend/orchestrator.py` and `backend/stages/*`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / "backend" / ".env")

from backend.cache import detail_cache  # noqa: E402
from backend.signals import compute as compute_accumulation  # noqa: E402
from backend.universe import UNIVERSE  # noqa: E402
from backend.yahoo import history_6m, history_ohlcv, snapshot  # noqa: E402

from .picks import generate_picks, get_or_generate_picks  # noqa: E402
from .picks_cache import ist_today_iso, read_picks  # noqa: E402
from .schemas import (  # noqa: E402
    AccumulationDTO,
    Pick,
    PicksResponse,
    StockDetail,
    StrategySignalDTO,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="Stockiya", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup_self_heal() -> None:
    """On boot, check what data is current and trigger backfill in background.

    Skipped if SKIP_CATCHUP=1 (handy in tests / dev) or if DEMO_MODE=1.
    """
    if os.environ.get("SKIP_CATCHUP", "0") == "1":
        logging.getLogger("startup").info("SKIP_CATCHUP=1 — catchup disabled")
        return
    if os.environ.get("DEMO_MODE", "0") == "1":
        logging.getLogger("startup").info("DEMO_MODE=1 — catchup not needed")
        return
    import threading

    def _bg() -> None:
        try:
            from backend.catchup import run_catchup
            run_catchup()
        except Exception:
            logging.getLogger("startup").exception("catchup failed")

    t = threading.Thread(target=_bg, daemon=True, name="stockiya-catchup")
    t.start()
    logging.getLogger("startup").info("Catchup launched in background")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "date_ist": ist_today_iso(),
        "demo_mode": os.environ.get("DEMO_MODE", "0") == "1",
    }


@app.get("/api/picks", response_model=PicksResponse)
def get_picks() -> PicksResponse:
    return get_or_generate_picks()


@app.post("/api/picks/refresh", response_model=PicksResponse)
def refresh_picks() -> PicksResponse:
    return generate_picks()


def _todays_pick_for(symbol: str) -> Pick | None:
    today = ist_today_iso()
    cached = read_picks(today)
    if not cached:
        return None
    for p in cached.get("picks", []):
        if p.get("symbol") == symbol:
            return Pick(**p)
    return None


@app.get("/api/stock/{symbol}", response_model=StockDetail)
def stock_detail(symbol: str) -> StockDetail:
    symbol = symbol.upper()
    if symbol not in UNIVERSE:
        raise HTTPException(status_code=404, detail=f"{symbol} not in Nifty 100 universe")

    cached = detail_cache.get(symbol)
    if cached is not None:
        return cached

    snap = snapshot(symbol)
    if snap.get("current") is None:
        raise HTTPException(status_code=502, detail=f"Could not fetch data for {symbol}")

    history = history_6m(symbol)
    ohlcv = history_ohlcv(symbol)
    accum = compute_accumulation(ohlcv, symbol=symbol)

    accumulation = AccumulationDTO(
        days_used=accum.days_used,
        verdict=accum.verdict,
        wyckoff_phase=accum.wyckoff_phase,
        entry_timing=accum.entry_timing,
        entry_timing_note=accum.entry_timing_note,
        accum_score=accum.accum_score,
        one_liner=accum.one_liner,
        vol_recent_10d=accum.vol_recent_10d,
        vol_avg_30d=accum.vol_avg_30d,
        vol_avg_90d=accum.vol_avg_90d,
        vol_trend_pct=accum.vol_trend_pct,
        up_down_vol_ratio=accum.up_down_vol_ratio,
        obv_slope_pct=accum.obv_slope_pct,
        ad_line_slope_pct=accum.ad_line_slope_pct,
        cmf_21d=accum.cmf_21d,
        mfi_14d=accum.mfi_14d,
        price_tightness_pct=accum.price_tightness_pct,
        price_change_30d_pct=accum.price_change_30d_pct,
        vwap_60d=accum.vwap_60d,
        price_vs_vwap_pct=accum.price_vs_vwap_pct,
        weinstein_stage=accum.weinstein_stage,
        weinstein_note=accum.weinstein_note,
        ma_30w=accum.ma_30w,
        ma_30w_slope_pct=accum.ma_30w_slope_pct,
        ma_50d=accum.ma_50d,
        ma_150d=accum.ma_150d,
        ma_200d=accum.ma_200d,
        minervini_template=accum.minervini_template,
        obv_slope_90d_pct=accum.obv_slope_90d_pct,
        obv_slope_180d_pct=accum.obv_slope_180d_pct,
        cmf_60d=accum.cmf_60d,
        up_down_vol_ratio_90d=accum.up_down_vol_ratio_90d,
        base_length_days=accum.base_length_days,
        vol_qoq_growth_pct=accum.vol_qoq_growth_pct,
        price_change_180d_pct=accum.price_change_180d_pct,
        pocket_pivot_count_30d=accum.pocket_pivot_count_30d,
        volume_dry_up=accum.volume_dry_up,
        canslim_breakout=accum.canslim_breakout,
        block_deal_buy_count_30d=accum.block_deal_buy_count_30d,
        block_deal_sell_count_30d=accum.block_deal_sell_count_30d,
        block_deal_net_qty_ratio=accum.block_deal_net_qty_ratio,
        signals=[
            StrategySignalDTO(
                name=s.name, state=s.state, value=s.value,
                label=s.label, description=s.description,
            )
            for s in accum.signals
        ],
    )

    detail = StockDetail(
        symbol=symbol,
        company=snap.get("company") or symbol,
        sector=snap.get("sector"),
        industry=snap.get("industry"),
        current=snap.get("current"),
        day_change_pct=snap.get("day_change_pct"),
        fifty_two_w_high=snap.get("fifty_two_w_high"),
        fifty_two_w_low=snap.get("fifty_two_w_low"),
        ma200=snap.get("ma200"),
        return_3m_pct=snap.get("return_3m_pct"),
        return_1y_pct=snap.get("return_1y_pct"),
        accumulation=accumulation,
        history_6m=history,
        pick_today=_todays_pick_for(symbol),
        demo_mode=os.environ.get("DEMO_MODE", "0") == "1",
    )
    detail_cache.set(symbol, detail)
    return detail
