"""Peer comparison, valuation signals, volume signals."""

from __future__ import annotations

import statistics
from typing import Optional

from .cache import snapshot_cache
from .schemas import Peer, ValuationSignals, VolumeSignals
from .universe import peers_for
from .yahoo import snapshot


def _cached_snapshot(symbol: str) -> dict:
    cached = snapshot_cache.get(symbol)
    if cached is not None:
        return cached
    snap = snapshot(symbol)
    snapshot_cache.set(symbol, snap)
    return snap


def build_peers(target_symbol: str, target_snap: dict) -> list[Peer]:
    out: list[Peer] = []
    out.append(
        Peer(
            symbol=target_symbol,
            company=target_snap.get("company") or target_symbol,
            current=target_snap.get("current"),
            pe=target_snap.get("pe"),
            pb=target_snap.get("pb"),
            market_cap_cr=target_snap.get("market_cap_cr"),
            return_1y_pct=target_snap.get("return_1y_pct"),
            is_target=True,
        )
    )
    for sym in peers_for(target_symbol):
        try:
            s = _cached_snapshot(sym)
        except Exception:
            continue
        out.append(
            Peer(
                symbol=sym,
                company=s.get("company") or sym,
                current=s.get("current"),
                pe=s.get("pe"),
                pb=s.get("pb"),
                market_cap_cr=s.get("market_cap_cr"),
                return_1y_pct=s.get("return_1y_pct"),
                is_target=False,
            )
        )
    out.sort(key=lambda p: (p.pe is None, p.pe if p.pe is not None else 0))
    return out


def valuation_signals(target_snap: dict, peers: list[Peer]) -> ValuationSignals:
    sector_pe_values = [p.pe for p in peers if not p.is_target and p.pe and p.pe > 0]
    sector_median = statistics.median(sector_pe_values) if sector_pe_values else None

    pe_verdict: Optional[str] = None
    target_pe = target_snap.get("pe")
    if sector_median and target_pe and target_pe > 0:
        if target_pe < 0.85 * sector_median:
            pe_verdict = "cheap"
        elif target_pe > 1.15 * sector_median:
            pe_verdict = "expensive"
        else:
            pe_verdict = "fair"

    price_vs_200dma_pct: Optional[float] = None
    current = target_snap.get("current")
    ma200 = target_snap.get("ma200")
    if current and ma200:
        price_vs_200dma_pct = round((current / ma200 - 1) * 100, 2)

    band_pct: Optional[float] = None
    hi = target_snap.get("fifty_two_w_high")
    lo = target_snap.get("fifty_two_w_low")
    if current and hi and lo and hi > lo:
        band_pct = round((current - lo) / (hi - lo) * 100, 1)

    return ValuationSignals(
        pe_vs_sector=pe_verdict,
        sector_pe_median=round(sector_median, 2) if sector_median else None,
        price_vs_200dma_pct=price_vs_200dma_pct,
        price_in_52w_band_pct=band_pct,
    )


def volume_signals(target_snap: dict) -> VolumeSignals:
    today = target_snap.get("vol_today")
    avg30 = target_snap.get("vol_avg30")
    if not today or not avg30:
        return VolumeSignals()
    ratio = today / avg30
    if ratio >= 1.5:
        label = "surge"
    elif ratio <= 0.6:
        label = "weak"
    else:
        label = "normal"
    return VolumeSignals(
        today=today,
        avg_30d=avg30,
        ratio=round(ratio, 2),
        label=label,
    )
