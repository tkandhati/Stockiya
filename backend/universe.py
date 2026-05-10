"""Stock universe (Nifty 100 = Nifty 50 + Nifty Next 50) + sector peer map.

The peer map is hand-curated. yfinance can be flaky for Indian tickers, so a
static map keeps the detail page deterministic and the sector-comparison
table meaningful.
"""

# ----- Nifty 50 (large-caps) -----
NIFTY_50 = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS",
    "AXISBANK.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "BEL.NS", "BPCL.NS", "BHARTIARTL.NS", "BRITANNIA.NS",
    "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS", "EICHERMOT.NS",
    "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS",
    "ITC.NS", "INDUSINDBK.NS", "INFY.NS", "JSWSTEEL.NS",
    "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
    "NTPC.NS", "NESTLEIND.NS", "ONGC.NS", "POWERGRID.NS",
    "RELIANCE.NS", "SBILIFE.NS", "SHRIRAMFIN.NS", "SBIN.NS",
    "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TATAMOTORS.NS",
    "TATASTEEL.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS",
    "ULTRACEMCO.NS", "WIPRO.NS",
]

# ----- Nifty Next 50 (upper mid-caps) -----
# These are the stocks ranked 51-100 by Nifty 100 market cap as of late 2025.
# Constituent list shifts with NSE rebalances (~bi-annually) — adjust as needed.
NIFTY_NEXT_50 = [
    "ABB.NS", "ACC.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", "AMBUJACEM.NS",
    "BAJAJHLDNG.NS", "BANKBARODA.NS", "BERGEPAINT.NS", "BOSCHLTD.NS", "CANBK.NS",
    "CGPOWER.NS", "CHOLAFIN.NS", "COLPAL.NS", "DABUR.NS", "DIVISLAB.NS",
    "DLF.NS", "DMART.NS", "GAIL.NS", "GODREJCP.NS", "HAL.NS",
    "HAVELLS.NS", "HINDPETRO.NS", "ICICIGI.NS", "ICICIPRULI.NS", "INDIANB.NS",
    "INDIGO.NS", "INDUSTOWER.NS", "IOC.NS", "IRCTC.NS", "IRFC.NS",
    "JINDALSTEL.NS", "JIOFIN.NS", "LICI.NS", "LODHA.NS", "LTIM.NS",
    "MARICO.NS", "NAUKRI.NS", "NHPC.NS", "PFC.NS", "PIDILITIND.NS",
    "PNB.NS", "RECLTD.NS", "SHREECEM.NS", "SIEMENS.NS", "SRF.NS",
    "TATAPOWER.NS", "TVSMOTOR.NS", "UNITDSPR.NS", "VBL.NS", "VEDL.NS",
]

# Combined Nifty 100 — what the picker actually scans.
UNIVERSE = NIFTY_50 + NIFTY_NEXT_50


# ----- Sector buckets used to derive peers -----
_BANKS_PRIVATE = [
    "HDFCBANK.NS", "ICICIBANK.NS", "AXISBANK.NS", "KOTAKBANK.NS", "INDUSINDBK.NS",
]
_BANKS_PSU = [
    "SBIN.NS", "BANKBARODA.NS", "CANBK.NS", "INDIANB.NS", "PNB.NS",
]
_NBFC = [
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "SHRIRAMFIN.NS", "BAJAJHLDNG.NS",
    "CHOLAFIN.NS", "JIOFIN.NS",
]
_PSU_FINANCE = ["IRFC.NS", "PFC.NS", "RECLTD.NS"]  # PSU lending / infra finance
_INSURANCE = ["HDFCLIFE.NS", "SBILIFE.NS", "ICICIGI.NS", "ICICIPRULI.NS", "LICI.NS"]

_IT = ["TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS"]
_INTERNET = ["NAUKRI.NS"]  # tech-enabled discretionary

_AUTO_OEM = [
    "MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS",
    "EICHERMOT.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS",
]
_AUTO_ANCILLARY = ["BOSCHLTD.NS"]

_PHARMA = ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS"]
_HOSPITALS = ["APOLLOHOSP.NS"]

_FMCG = [
    "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS",
    "DABUR.NS", "GODREJCP.NS", "MARICO.NS", "COLPAL.NS",
]
_BEVERAGES_ALCO = ["UNITDSPR.NS", "VBL.NS"]

_METALS = [
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "COALINDIA.NS",
    "JINDALSTEL.NS", "VEDL.NS",
]

_OIL_GAS = [
    "RELIANCE.NS", "ONGC.NS", "BPCL.NS", "HINDPETRO.NS", "IOC.NS", "GAIL.NS",
]

_POWER = [
    "NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS", "NHPC.NS",
    "ADANIPOWER.NS", "ADANIGREEN.NS",
]

_CEMENT = ["ULTRACEMCO.NS", "GRASIM.NS", "ACC.NS", "AMBUJACEM.NS", "SHREECEM.NS"]

_TELECOM = ["BHARTIARTL.NS", "INDUSTOWER.NS"]

_INFRA = ["LT.NS", "ADANIPORTS.NS", "ADANIENT.NS"]
_INDUSTRIALS = ["ABB.NS", "SIEMENS.NS", "CGPOWER.NS"]
_DEFENCE = ["BEL.NS", "HAL.NS"]

_PAINTS_DECOR = ["ASIANPAINT.NS", "BERGEPAINT.NS"]
_BUILDING_PRODUCTS = ["HAVELLS.NS", "PIDILITIND.NS", "SRF.NS"]
_RETAIL_LIFESTYLE = ["TITAN.NS", "TRENT.NS", "DMART.NS"]
_TRAVEL_HOSPITALITY = ["INDIGO.NS", "IRCTC.NS"]
_REAL_ESTATE = ["DLF.NS", "LODHA.NS"]


_SECTOR_BUCKETS = [
    _BANKS_PRIVATE, _BANKS_PSU, _NBFC, _PSU_FINANCE, _INSURANCE,
    _IT, _INTERNET,
    _AUTO_OEM, _AUTO_ANCILLARY,
    _PHARMA, _HOSPITALS,
    _FMCG, _BEVERAGES_ALCO,
    _METALS, _OIL_GAS, _POWER, _CEMENT, _TELECOM,
    _INFRA, _INDUSTRIALS, _DEFENCE,
    _PAINTS_DECOR, _BUILDING_PRODUCTS, _RETAIL_LIFESTYLE,
    _TRAVEL_HOSPITALITY, _REAL_ESTATE,
]


def peers_for(symbol: str, max_peers: int = 4) -> list[str]:
    """Return up to `max_peers` same-sector tickers (excluding the symbol itself)."""
    for bucket in _SECTOR_BUCKETS:
        if symbol in bucket:
            return [p for p in bucket if p != symbol][:max_peers]
    return []
