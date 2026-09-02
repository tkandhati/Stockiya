"""Small, cached universe service for the price-only scanner."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.fetch import fetch_ohlcv
from backend.universe import UNIVERSE, UNIVERSE_LABEL

from .models import PriceTrendCandidate, PriceTrendLookupResponse, PriceTrendResponse
from .scanner import scan_symbol

log = logging.getLogger("price_trend")

# 0 / unset => scan the FULL universe. The old default (30) silently scanned
# only the alphabetical head of the scan universe (ADANIENT..LT), so the tab
# missed ~90% of the market. A real strategy scan must see the whole universe. A positive
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


def _lookup_symbols(raw_symbol: str) -> tuple[str, list[str]]:
    requested = raw_symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^&._-]{1,30}", requested):
        raise ValueError("Enter a valid stock symbol, for example RELIANCE or AAPL.")
    if "." in requested or requested.startswith("^"):
        return requested, [requested]
    # Stockya is NSE-first, while the literal fallback keeps global Yahoo
    # symbols such as AAPL available when DATA_SOURCE=yahoo.
    return requested, [f"{requested}.NS", requested]


def lookup_price_trend(raw_symbol: str) -> PriceTrendLookupResponse:
    """Run one arbitrary symbol through the existing price-trend scanner."""
    requested, symbols = _lookup_symbols(raw_symbol)
    for symbol in symbols:
        try:
            ohlcv = fetch_ohlcv(symbol)
        except Exception:
            continue
        if ohlcv is None or ohlcv.empty:
            continue

        candidate = scan_symbol(symbol, ohlcv, company=_company_name(symbol))
        if candidate is not None:
            candidate.rank = 0
            return PriceTrendLookupResponse(
                requested_symbol=requested,
                resolved_symbol=symbol,
                price_history_available=True,
                matches_strategy=True,
                message=f"{symbol} matches the existing Price Trend setup.",
                candidate=candidate,
            )
        return PriceTrendLookupResponse(
            requested_symbol=requested,
            resolved_symbol=symbol,
            price_history_available=True,
            matches_strategy=False,
            message=(
                f"{symbol} has price history, but it does not currently meet "
                "the existing Price Trend criteria."
            ),
            candidate=None,
        )

    return PriceTrendLookupResponse(
        requested_symbol=requested,
        resolved_symbol=None,
        price_history_available=False,
        matches_strategy=False,
        message=(
            f"No price history is available for {requested}. Try an exchange "
            "suffix such as .NS or check the configured data source."
        ),
        candidate=None,
    )


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
