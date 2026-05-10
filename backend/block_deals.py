"""Block + bulk deal ingestion (NSE).

Block and bulk deals are EOD reports from NSE listing every large institutional
trade on a given day:

  Block deals: trades > 0.5% of equity capital, executed in a special window.
  Bulk deals:  trades > 0.5% of trading volume, executed during regular session.

These are LITERAL records of institutional trades — much harder to fake than
aggregated volume, and exactly what the user's "follow institutions" thesis
needs.

NSE archives URL pattern (subject to change):
  https://archives.nseindia.com/content/equities/bulk.csv
  https://archives.nseindia.com/content/equities/block.csv

Both files are EOD updates — they accumulate the latest few months of deals
in a single CSV. Re-download daily, parse, aggregate per ticker per N-day window.

DEMO_MODE returns a small synthetic block-deal set so the engine runs without
network access.
"""

from __future__ import annotations

import csv
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("block_deals")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEALS_DIR = _PROJECT_ROOT / "data" / "deals"
_DEALS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DealAggregate:
    """30-day aggregate of block + bulk deals for one ticker."""
    symbol: str
    days_used: int
    buy_count: int = 0
    sell_count: int = 0
    buy_qty: int = 0
    sell_qty: int = 0
    net_qty: int = 0
    net_qty_ratio: float = 0.0  # net / (buy+sell), in [-1, +1]
    last_buy_date: Optional[str] = None
    last_sell_date: Optional[str] = None


# --------------------------------------------------------------------------- #
# Live fetch (TODO — not yet implemented)
# --------------------------------------------------------------------------- #

def fetch_and_cache_nse_deals() -> tuple[Path, Path]:
    """Download today's NSE block + bulk deal CSVs and cache them locally.

    Skipped if DEMO_MODE=1. Returns (block_path, bulk_path).
    Not yet implemented — wire when running on a network with NSE access.
    """
    raise NotImplementedError(
        "NSE block deal download not yet wired. "
        "URL pattern: https://archives.nseindia.com/content/equities/{block|bulk}.csv"
    )


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def aggregate_30d(symbol: str) -> DealAggregate:
    """Return the 30-day buy/sell aggregate for `symbol`.

    Reads cached deals if available (data/deals/all.csv), or falls back to
    the demo set if DEMO_MODE=1.
    """
    if os.environ.get("DEMO_MODE", "0") == "1":
        return _demo_aggregate(symbol)

    deals_csv = _DEALS_DIR / "all.csv"
    if not deals_csv.exists():
        return DealAggregate(symbol=symbol, days_used=0)

    cutoff = date.today() - timedelta(days=30)
    return _aggregate_from_csv(deals_csv, symbol, cutoff)


def _aggregate_from_csv(path: Path, symbol: str, cutoff: date) -> DealAggregate:
    agg = DealAggregate(symbol=symbol, days_used=30)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("symbol") != symbol:
                continue
            try:
                d = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if d < cutoff:
                continue
            qty = int(row.get("qty", 0) or 0)
            side = (row.get("side") or "").upper()
            if side == "BUY":
                agg.buy_count += 1
                agg.buy_qty += qty
                agg.last_buy_date = row["date"]
            elif side == "SELL":
                agg.sell_count += 1
                agg.sell_qty += qty
                agg.last_sell_date = row["date"]
    agg.net_qty = agg.buy_qty - agg.sell_qty
    total = agg.buy_qty + agg.sell_qty
    if total > 0:
        agg.net_qty_ratio = round(agg.net_qty / total, 3)
    return agg


# --------------------------------------------------------------------------- #
# Demo set — synthetic deals for DEMO_MODE
# --------------------------------------------------------------------------- #

# A small curated demo dataset. Mostly bullish (net buying) on the early-stage
# names so the demo picks stay consistent.
_DEMO_DEALS: dict[str, dict] = {
    "HDFCBANK.NS":   {"buy_count": 4, "sell_count": 1, "buy_qty": 8_500_000, "sell_qty": 1_200_000},
    "ICICIBANK.NS":  {"buy_count": 5, "sell_count": 0, "buy_qty": 12_000_000, "sell_qty": 0},
    "BAJFINANCE.NS": {"buy_count": 6, "sell_count": 1, "buy_qty": 1_900_000, "sell_qty": 250_000},
    "MARUTI.NS":     {"buy_count": 3, "sell_count": 0, "buy_qty": 380_000, "sell_qty": 0},
    "DRREDDY.NS":    {"buy_count": 2, "sell_count": 1, "buy_qty": 410_000, "sell_qty": 180_000},
    "BHARTIARTL.NS": {"buy_count": 4, "sell_count": 0, "buy_qty": 2_400_000, "sell_qty": 0},
    "AXISBANK.NS":   {"buy_count": 2, "sell_count": 1, "buy_qty": 1_600_000, "sell_qty": 700_000},
    "SBIN.NS":       {"buy_count": 3, "sell_count": 1, "buy_qty": 4_300_000, "sell_qty": 800_000},
    "TCS.NS":        {"buy_count": 0, "sell_count": 4, "buy_qty": 0, "sell_qty": 1_400_000},
    "INFY.NS":       {"buy_count": 0, "sell_count": 5, "buy_qty": 0, "sell_qty": 2_900_000},
    "WIPRO.NS":      {"buy_count": 1, "sell_count": 3, "buy_qty": 350_000, "sell_qty": 1_700_000},
    "ASIANPAINT.NS": {"buy_count": 0, "sell_count": 2, "buy_qty": 0, "sell_qty": 480_000},
    "HINDUNILVR.NS": {"buy_count": 1, "sell_count": 2, "buy_qty": 220_000, "sell_qty": 590_000},
    "TATAMOTORS.NS": {"buy_count": 1, "sell_count": 4, "buy_qty": 1_000_000, "sell_qty": 5_300_000},
}


def _demo_aggregate(symbol: str) -> DealAggregate:
    raw = _DEMO_DEALS.get(symbol)
    if not raw:
        return DealAggregate(symbol=symbol, days_used=30)
    buy_qty = raw["buy_qty"]
    sell_qty = raw["sell_qty"]
    net = buy_qty - sell_qty
    total = buy_qty + sell_qty
    ratio = round(net / total, 3) if total > 0 else 0.0
    return DealAggregate(
        symbol=symbol,
        days_used=30,
        buy_count=raw["buy_count"],
        sell_count=raw["sell_count"],
        buy_qty=buy_qty,
        sell_qty=sell_qty,
        net_qty=net,
        net_qty_ratio=ratio,
    )
