"""Nightly orchestrator.

Run as a cron / Windows Task Scheduler entry shortly after IST market close
(say, 19:00 IST):

    python -m backend.nightly

Outputs a single JSON artifact per ticker per day under
`data/prepared/YYYY-MM-DD/<SYMBOL>.json`, plus a manifest
`data/prepared/YYYY-MM-DD/_manifest.json` listing which tickers succeeded.

The middleware then reads from these files instead of recomputing.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Ensure the project root is on sys.path so `from backend...` works when this
# file is invoked directly (e.g. `python backend/nightly.py`).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / "backend" / ".env")

from backend.fetch import fetch_ohlcv, fetch_snapshot  # noqa: E402
from backend.headwinds import headwind_for  # noqa: E402
from backend.universe import UNIVERSE  # noqa: E402
from backend.volume_signals import compute as compute_accumulation  # noqa: E402
from backend.volume_signals import suggest_target_window  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] nightly: %(message)s",
)
log = logging.getLogger("nightly")


def _ist_today() -> str:
    return datetime.now(IST).date().isoformat()


def _prepared_dir(date_iso: str) -> Path:
    p = _PROJECT_ROOT / "data" / "prepared" / date_iso
    p.mkdir(parents=True, exist_ok=True)
    return p


def _process_one(symbol: str, out_dir: Path) -> dict:
    """Fetch + compute + persist a single ticker. Returns a small summary row."""
    try:
        snap = fetch_snapshot(symbol)
        if not snap.get("current"):
            return {"symbol": symbol, "ok": False, "reason": "no current price"}

        ohlcv = fetch_ohlcv(symbol)
        if ohlcv is None or ohlcv.empty:
            return {"symbol": symbol, "ok": False, "reason": "no OHLCV"}

        accum = compute_accumulation(ohlcv, symbol=symbol)
        target = suggest_target_window(accum)

        artifact = {
            "symbol": symbol,
            "computed_at": datetime.now(IST).isoformat(timespec="seconds"),
            "snapshot": snap,
            "accumulation": _accum_to_dict(accum),
            "target_window": {
                "center_months": target.center_months,
                "tolerance_months": target.tolerance_months,
                "label": target.label,
                "rationale": target.rationale,
            },
            "headwind": headwind_for(symbol),
        }
        (out_dir / f"{symbol}.json").write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "symbol": symbol, "ok": True,
            "stage": accum.weinstein_stage,
            "timing": accum.entry_timing,
            "score": accum.accum_score,
        }
    except Exception as e:  # belt + braces — the loop must not fail
        log.exception("failed: %s", symbol)
        return {"symbol": symbol, "ok": False, "reason": str(e)}


def _accum_to_dict(a) -> dict:
    """Flatten the AccumulationSignals dataclass into a JSON-safe dict."""
    return {
        "days_used": a.days_used,
        "verdict": a.verdict,
        "wyckoff_phase": a.wyckoff_phase,
        "entry_timing": a.entry_timing,
        "entry_timing_note": a.entry_timing_note,
        "accum_score": a.accum_score,
        "one_liner": a.one_liner,
        "weinstein_stage": a.weinstein_stage,
        "weinstein_note": a.weinstein_note,
        "ma_30w": a.ma_30w,
        "ma_30w_slope_pct": a.ma_30w_slope_pct,
        "ma_50d": a.ma_50d,
        "ma_150d": a.ma_150d,
        "ma_200d": a.ma_200d,
        "minervini_template": a.minervini_template,
        "obv_slope_pct": a.obv_slope_pct,
        "obv_slope_90d_pct": a.obv_slope_90d_pct,
        "obv_slope_180d_pct": a.obv_slope_180d_pct,
        "ad_line_slope_pct": a.ad_line_slope_pct,
        "cmf_21d": a.cmf_21d,
        "cmf_60d": a.cmf_60d,
        "mfi_14d": a.mfi_14d,
        "vol_recent_10d": a.vol_recent_10d,
        "vol_avg_30d": a.vol_avg_30d,
        "vol_avg_90d": a.vol_avg_90d,
        "vol_trend_pct": a.vol_trend_pct,
        "up_down_vol_ratio": a.up_down_vol_ratio,
        "up_down_vol_ratio_90d": a.up_down_vol_ratio_90d,
        "vwap_60d": a.vwap_60d,
        "price_vs_vwap_pct": a.price_vs_vwap_pct,
        "price_tightness_pct": a.price_tightness_pct,
        "price_change_30d_pct": a.price_change_30d_pct,
        "price_change_180d_pct": a.price_change_180d_pct,
        "base_length_days": a.base_length_days,
        "vol_qoq_growth_pct": a.vol_qoq_growth_pct,
        "pocket_pivot_count_30d": a.pocket_pivot_count_30d,
        "volume_dry_up": a.volume_dry_up,
        "canslim_breakout": a.canslim_breakout,
        "signals": [
            {"name": s.name, "state": s.state, "value": s.value,
             "label": s.label, "description": s.description}
            for s in a.signals
        ],
    }


def run_nightly() -> dict:
    """Run the full pipeline for today. Returns the manifest summary."""
    date_iso = _ist_today()
    out_dir = _prepared_dir(date_iso)
    log.info("Starting nightly run for %s -> %s", date_iso, out_dir)
    log.info("Universe: %d tickers (DATA_SOURCE=%s, DEMO_MODE=%s)",
             len(UNIVERSE),
             os.environ.get("DATA_SOURCE", "yahoo"),
             os.environ.get("DEMO_MODE", "0"))

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_process_one, sym, out_dir): sym for sym in UNIVERSE}
        for fut in as_completed(futures):
            rows.append(fut.result())

    rows.sort(key=lambda r: r["symbol"])
    manifest = {
        "date": date_iso,
        "completed_at": datetime.now(IST).isoformat(timespec="seconds"),
        "universe_size": len(UNIVERSE),
        "successes": sum(1 for r in rows if r["ok"]),
        "failures": [r for r in rows if not r["ok"]],
        "results": rows,
    }
    (out_dir / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Done. %d/%d succeeded, %d failed",
             manifest["successes"], manifest["universe_size"],
             len(manifest["failures"]))

    # Optional: hit the middleware to generate today's picks and log them to
    # portfolio.csv. Skipped if middleware isn't running locally.
    _record_todays_picks_if_available()

    return manifest


def _record_todays_picks_if_available() -> None:
    """Best-effort: ask the local middleware for today's picks and append new
    ones to portfolio.csv. Silent no-op if middleware is unreachable."""
    try:
        import urllib.request
        from backend.portfolio import record_picks  # noqa: E402
        with urllib.request.urlopen("http://127.0.0.1:8000/api/picks", timeout=30) as resp:
            payload = json.loads(resp.read())
        added = record_picks(payload)
        if added:
            log.info("portfolio.csv: appended %d new picks", added)
    except Exception as e:
        log.info("Skipped portfolio logging (middleware not running?): %s", e)


if __name__ == "__main__":
    run_nightly()
