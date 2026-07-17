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
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

log = logging.getLogger("block_deals")


# --------------------------------------------------------------------------- #
# Client classifier (2026-07-17)
#
# NSE block/bulk CSVs carry a free-text Client Name string — no tag for
# custodian / FII / DII / prop / individual. This regex table maps common
# institutional keywords to coarse buckets. It is deliberately CONSERVATIVE:
# a name that matches nothing returns "unknown", NEVER "institutional".
# Unclassified names (many HNI individuals, family offices) must not be
# credited as institutional flow — that would be the exact mislabeling the
# plan warns against ("participant evidence: inferred" vs "disclosed").
# --------------------------------------------------------------------------- #

ClientClass = Literal["custodian", "fii", "dii", "prop", "individual", "unknown"]

_CLIENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bCUSTODIAN\b", re.I), "custodian"),
    (re.compile(r"\b(?:FII|FPI)\b", re.I), "fii"),
    (re.compile(r"\bFOREIGN\s+(?:INSTITUTIONAL|PORTFOLIO)\b", re.I), "fii"),
    (re.compile(r"\bMUTUAL\s*FUND\b", re.I), "dii"),
    (re.compile(r"\bMF\b", re.I), "dii"),
    (re.compile(r"\bLIFE\s+INSURANCE\b", re.I), "dii"),
    (re.compile(r"\bINSURANCE\s+CO\b", re.I), "dii"),
    (re.compile(r"\bLIC\b", re.I), "dii"),
    (re.compile(r"\bPENSION\s+FUND\b", re.I), "dii"),
    (re.compile(r"\bASSET\s+MANAGEMENT\b", re.I), "dii"),
    (re.compile(r"\bAMC\b", re.I), "dii"),
    (re.compile(r"\bAIF\b", re.I), "dii"),
    (re.compile(r"\bPORTFOLIO\s+MANAGE", re.I), "dii"),
    (re.compile(r"\bPMS\b", re.I), "dii"),
    (re.compile(r"\bPROP\.?(?:\s+A/C|\s+ACCOUNT)?\b", re.I), "prop"),
    (re.compile(r"\bPROPRIETARY\b", re.I), "prop"),
)


def classify_client(name: Optional[str]) -> ClientClass:
    """Best-effort client classification from the NSE 'Client Name' string.

    Case-insensitive, deterministic. Returns 'unknown' on anything that does
    not clearly match an institutional keyword — includes numbered HNI
    accounts and generic "PVT LTD" investment companies. Being wrong-quiet
    here is the design: false institutional labels poison the downstream
    "participant_evidence: disclosed_large_client" claim in the accumulation
    envelope, and that claim's whole value is that it's rarely applied.
    """
    if not name:
        return "unknown"
    s = name.strip()
    if not s:
        return "unknown"
    for pattern, cls in _CLIENT_PATTERNS:
        if pattern.search(s):
            return cls  # type: ignore[return-value]
    return "unknown"


# Buckets we treat as "large disclosed client" for the accumulation envelope.
# Prop desks and individuals are excluded on purpose — the plan wants this
# label to mean genuine third-party institutional flow.
_DISCLOSED_INSTITUTIONAL: frozenset[str] = frozenset({"custodian", "fii", "dii"})

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEALS_DIR = _PROJECT_ROOT / "data" / "deals"
_DEALS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DealAggregate:
    """30-day aggregate of block + bulk deals for one ticker.

    Classified fields (2026-07-17) are additive — existing readers that
    only touch buy/sell/net_qty continue to work unchanged. The classified
    fields default to zero when no classifier match was found, so a ticker
    with only unknown-client deals gets classified totals of 0 and
    has_disclosed_large_client=False (the "participant_evidence: inferred"
    case in the accumulation envelope).
    """
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
    # ---- Classified participant flow (added 2026-07-17)
    institutional_buy_qty: int = 0    # sum over custodian + fii + dii
    institutional_sell_qty: int = 0
    institutional_net_qty: int = 0
    institutional_client_count: int = 0  # distinct classified clients seen
    has_disclosed_large_client: bool = False
    client_class_counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Live fetch — NSE block/bulk deal CSVs into the local cache.
# --------------------------------------------------------------------------- #

NSE_BULK_URL = "https://archives.nseindia.com/content/equities/bulk.csv"
NSE_BLOCK_URL = "https://archives.nseindia.com/content/equities/block.csv"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_and_cache_nse_deals() -> tuple[Path, Path]:
    """Download the rolling NSE block + bulk deal CSVs and merge into all.csv.

    Skipped if DEMO_MODE=1. Returns (block_path, bulk_path) raw cache files.
    NSE serves a single CSV per file covering the last ~3 months — we download
    daily, parse, normalize, and merge unique (date, symbol, client, side, qty)
    rows into `data/deals/all.csv` (the file `aggregate_30d` reads).
    """
    if os.environ.get("DEMO_MODE", "0") == "1":
        log.info("DEMO_MODE=1 — skipping NSE download")
        return _DEALS_DIR / "block.csv", _DEALS_DIR / "bulk.csv"

    import urllib.request

    block_path = _DEALS_DIR / "block.csv"
    bulk_path = _DEALS_DIR / "bulk.csv"

    for url, dest in [(NSE_BLOCK_URL, block_path), (NSE_BULK_URL, bulk_path)]:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            dest.write_bytes(data)
            log.info("downloaded %s -> %s (%d bytes)", url, dest.name, len(data))
        except Exception as e:
            log.warning("NSE download failed for %s: %s", url, e)

    _merge_into_all_csv(block_path, bulk_path)
    return block_path, bulk_path


def _merge_into_all_csv(block_path: Path, bulk_path: Path) -> int:
    """Parse raw NSE CSVs and append unique rows to all.csv.

    NSE CSV columns (both files):
        Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price
    Our normalized schema (the only one aggregate_30d reads):
        date,symbol,side,qty,client,price,source
    """
    all_csv = _DEALS_DIR / "all.csv"
    existing_keys: set[tuple] = set()
    existing_rows: list[dict] = []
    if all_csv.exists():
        with all_csv.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                existing_keys.add(_dedupe_key(row))

    new_rows: list[dict] = []
    for src_path, src_label in [(block_path, "block"), (bulk_path, "bulk")]:
        if not src_path.exists() or src_path.stat().st_size == 0:
            continue
        for raw in _read_nse_csv(src_path):
            norm = _normalize_nse_row(raw, src_label)
            if norm is None:
                continue
            key = _dedupe_key(norm)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            new_rows.append(norm)

    if not new_rows and not existing_rows:
        # Touch an empty file so aggregate_30d can read without erroring.
        with all_csv.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=_ALL_FIELDS).writeheader()
        return 0

    out_rows = existing_rows + new_rows
    with all_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_ALL_FIELDS)
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in _ALL_FIELDS})
    log.info("all.csv: %d existing + %d new rows", len(existing_rows), len(new_rows))
    return len(new_rows)


_ALL_FIELDS = ["date", "symbol", "side", "qty", "client", "price", "source"]


def _dedupe_key(row: dict) -> tuple:
    return (
        row.get("date", ""),
        row.get("symbol", ""),
        row.get("side", ""),
        row.get("qty", ""),
        (row.get("client") or "")[:80],
    )


def _read_nse_csv(path: Path) -> list[dict]:
    """NSE CSVs sometimes have a 1-line preamble before the header. Skip blanks."""
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        lines = [ln for ln in f if ln.strip()]
    if not lines:
        return rows
    # Find the header line — it always contains "Symbol" and "Buy/Sell"
    start = 0
    for i, ln in enumerate(lines[:5]):
        if "Symbol" in ln and "Buy/Sell" in ln:
            start = i
            break
    body = lines[start:]
    reader = csv.DictReader(body)
    for r in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in r.items()})
    return rows


def _normalize_nse_row(raw: dict, source: str) -> Optional[dict]:
    """Map NSE columns to our schema. Returns None on parse failure."""
    sym = raw.get("Symbol") or raw.get("SYMBOL")
    if not sym:
        return None

    # Date format on NSE: typically DD-MMM-YYYY (e.g. 09-May-2026)
    date_raw = raw.get("Date") or raw.get("DATE") or ""
    try:
        d = datetime.strptime(date_raw, "%d-%b-%Y").date()
    except ValueError:
        try:
            d = datetime.strptime(date_raw, "%d/%m/%Y").date()
        except ValueError:
            return None

    side = (raw.get("Buy/Sell") or raw.get("BUY/SELL") or "").upper().strip()
    if side.startswith("B"):
        side = "BUY"
    elif side.startswith("S"):
        side = "SELL"
    else:
        return None

    qty_raw = raw.get("Quantity Traded") or raw.get("QUANTITY TRADED") or "0"
    try:
        qty = int(qty_raw.replace(",", "").replace(" ", ""))
    except ValueError:
        return None

    client = raw.get("Client Name") or raw.get("CLIENT NAME") or ""
    price_raw = (
        raw.get("Trade Price / Wght. Avg. Price")
        or raw.get("TRADE PRICE / WGHT. AVG. PRICE")
        or raw.get("Price")
        or "0"
    )
    try:
        price = float(price_raw.replace(",", ""))
    except ValueError:
        price = 0.0

    # NSE symbols are bare (e.g. "RELIANCE"). Our internal universe is
    # Yahoo-style ("RELIANCE.NS"). Append the suffix so aggregate_30d matches.
    if not sym.endswith(".NS"):
        sym = sym + ".NS"

    return {
        "date": d.isoformat(),
        "symbol": sym,
        "side": side,
        "qty": str(qty),
        "client": client,
        "price": f"{price:.2f}",
        "source": source,
    }


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
    class_counts: dict[str, int] = {}
    classified_clients: set[str] = set()

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
            client = row.get("client") or ""
            cls = classify_client(client)

            if side == "BUY":
                agg.buy_count += 1
                agg.buy_qty += qty
                agg.last_buy_date = row["date"]
                if cls in _DISCLOSED_INSTITUTIONAL:
                    agg.institutional_buy_qty += qty
            elif side == "SELL":
                agg.sell_count += 1
                agg.sell_qty += qty
                agg.last_sell_date = row["date"]
                if cls in _DISCLOSED_INSTITUTIONAL:
                    agg.institutional_sell_qty += qty

            class_counts[cls] = class_counts.get(cls, 0) + 1
            if cls in _DISCLOSED_INSTITUTIONAL:
                classified_clients.add(client.strip().upper())

    agg.net_qty = agg.buy_qty - agg.sell_qty
    total = agg.buy_qty + agg.sell_qty
    if total > 0:
        agg.net_qty_ratio = round(agg.net_qty / total, 3)

    agg.institutional_net_qty = agg.institutional_buy_qty - agg.institutional_sell_qty
    agg.institutional_client_count = len(classified_clients)
    agg.has_disclosed_large_client = agg.institutional_client_count > 0
    agg.client_class_counts = class_counts
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
