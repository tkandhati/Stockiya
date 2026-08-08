"""Small, cached universe service for the price-only scanner."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.fetch import fetch_ohlcv
from backend.universe import UNIVERSE, UNIVERSE_LABEL

from .models import PriceTrendCandidate, PriceTrendResponse
from .scanner import scan_symbol

log = logging.getLogger("price_trend")

# 0 / unset => scan the FULL universe. The old default (30) silently scanned
# only the alphabetical head of Nifty 300 (ADANIENT..LT), so the tab missed ~90%
# of the market. A real strategy scan must see the whole universe. A positive
# PRICE_TREND_SCAN_LIMIT still caps the scan (dev / latency), and the cap is
# surfaced in the response (scan_limit vs universe size) and logged — never silent.
DEFAULT_SCAN_LIMIT = 0
MAX_RESULTS = 8
CACHE_SECONDS = 15 * 60

_cache_lock = threading.Lock()
_cache_at = 0.0
_cache_value: PriceTrendResponse | None = None


def _scan_limit() -> int:
    """How many symbols to scan. Full universe unless PRICE_TREND_SCAN_LIMIT>0."""
    raw = os.environ.get("PRICE_TREND_SCAN_LIMIT")
    try:
        requested = int(raw) if raw else DEFAULT_SCAN_LIMIT
    except ValueError:
        requested = DEFAULT_SCAN_LIMIT
    return len(UNIVERSE) if requested <= 0 else min(requested, len(UNIVERSE))


def _company_name(symbol: str) -> str:
    if os.environ.get("DEMO_MODE", "0") == "1":
        from backend.demo_data import DEMO_SNAPSHOTS

        item = DEMO_SNAPSHOTS.get(symbol) or {}
        return str(item.get("company") or symbol.removesuffix(".NS"))
    return symbol.removesuffix(".NS")


def _scan_one(symbol: str) -> PriceTrendCandidate | None:
    # Route through the app's data-source abstraction (fetch_ohlcv) so the
    # scanner honors DEMO_MODE / DATA_SOURCE / the local bhavcopy cache and never
    # makes a raw Yahoo call the corporate firewall blocks. The scanner still
    # firewalls Volume out internally, so behaviour is unchanged — only the
    # source of the bars changes.
    return scan_symbol(symbol, fetch_ohlcv(symbol), company=_company_name(symbol))


def get_price_trends(*, force: bool = False) -> PriceTrendResponse:
    """Scan a deliberately small universe slice and return the best structures."""
    global _cache_at, _cache_value
    now = time.monotonic()
    with _cache_lock:
        if not force and _cache_value is not None and now - _cache_at < CACHE_SECONDS:
            return _cache_value

    limit = _scan_limit()
    symbols = list(UNIVERSE[:limit])
    if limit < len(UNIVERSE):
        log.warning(
            "price-trend scan is PARTIAL: %d of %d symbols (PRICE_TREND_SCAN_LIMIT=%s)",
            limit, len(UNIVERSE), os.environ.get("PRICE_TREND_SCAN_LIMIT"),
        )
    candidates: list[PriceTrendCandidate] = []
    skipped = 0

    # Price history calls dominate latency; a small worker pool keeps V1 usable.
    workers = min(6, len(symbols))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="price-trend") as pool:
        futures = {pool.submit(_scan_one, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            try:
                candidate = future.result()
            except Exception:
                candidate = None
            if candidate is None:
                skipped += 1
            else:
                candidates.append(candidate)

    status_order = {"ready": 0, "forming": 1, "watch": 2}
    candidates.sort(
        key=lambda item: (
            status_order[item.status],
            -item.score,
            item.distance_to_breakout_pct,
        )
    )
    selected = candidates[:MAX_RESULTS]
    for rank, candidate in enumerate(selected, start=1):
        candidate.rank = rank

    response = PriceTrendResponse(
        generated_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        as_of=max((item.as_of for item in selected), default=None),
        universe=UNIVERSE_LABEL,
        scan_limit=limit,
        scanned_count=len(symbols),
        eligible_count=len(candidates),
        skipped_count=skipped,
        demo_mode=os.environ.get("DEMO_MODE", "0") == "1",
        candidates=selected,
    )
    with _cache_lock:
        _cache_at = time.monotonic()
        _cache_value = response
    return response
