"""Stock universes — Nifty 100 (default) and Nifty 200 (opt-in via env var).

The pipeline scans whichever universe is selected by `STOCKYA_UNIVERSE`:

    STOCKYA_UNIVERSE=nifty100   (default)   — Nifty 50 + Nifty Next 50
    STOCKYA_UNIVERSE=nifty200              — Nifty 100 + Nifty Midcap 100

NSE rebalances these indices ~twice a year. Bump the lists below when the
rebalance lands, and confirm symbols via `python -m backend.check_universe`.

Known dead / corporate-action affected (kept as comments so future-you
knows why they were removed):

    TATAMOTORS.NS  — demerged into TATAMOTORS (CV) + TATAMOTORS-PV/DVR.
                     Yahoo's mapping is currently broken; revisit after the
                     rebalance settles.
    LTIM.NS        — L&T-Mindtree merger; Yahoo intermittently 404s. Use
                     LTI.NS / MINDTREE.NS if you need historical data.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Nifty 50 (large-caps)
# --------------------------------------------------------------------------- #
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
    "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS",
    # TATAMOTORS.NS removed -- Yahoo mapping broken after demerger
    "TATASTEEL.NS", "TECHM.NS", "TITAN.NS", "TRENT.NS",
    "ULTRACEMCO.NS", "WIPRO.NS",
]

# --------------------------------------------------------------------------- #
# Nifty Next 50 (upper mid-caps)
# --------------------------------------------------------------------------- #
NIFTY_NEXT_50 = [
    "ABB.NS", "ACC.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", "AMBUJACEM.NS",
    "BAJAJHLDNG.NS", "BANKBARODA.NS", "BERGEPAINT.NS", "BOSCHLTD.NS", "CANBK.NS",
    "CGPOWER.NS", "CHOLAFIN.NS", "COLPAL.NS", "DABUR.NS", "DIVISLAB.NS",
    "DLF.NS", "DMART.NS", "GAIL.NS", "GODREJCP.NS", "HAL.NS",
    "HAVELLS.NS", "HINDPETRO.NS", "ICICIGI.NS", "ICICIPRULI.NS", "INDIANB.NS",
    "INDIGO.NS", "INDUSTOWER.NS", "IOC.NS", "IRCTC.NS", "IRFC.NS",
    "JINDALSTEL.NS", "JIOFIN.NS", "LICI.NS", "LODHA.NS",
    # LTIM.NS removed -- Yahoo intermittently 404s after L&T-Mindtree merger
    "MARICO.NS", "NAUKRI.NS", "NHPC.NS", "PFC.NS", "PIDILITIND.NS",
    "PNB.NS", "RECLTD.NS", "SHREECEM.NS", "SIEMENS.NS", "SRF.NS",
    "TATAPOWER.NS", "TVSMOTOR.NS", "UNITDSPR.NS", "VBL.NS", "VEDL.NS",
]

NIFTY_100 = NIFTY_50 + NIFTY_NEXT_50

# --------------------------------------------------------------------------- #
# Nifty Midcap 100 (opt-in; expands universe to Nifty 200)
#
# Starter list of well-known mid-caps. NSE's official Nifty Midcap 100
# constituents change every six months -- verify against
# https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-midcap-100
# and run `python -m backend.check_universe` after editing.
# --------------------------------------------------------------------------- #
NIFTY_MIDCAP_100 = [
    "ABFRL.NS", "ALKEM.NS", "APOLLOTYRE.NS", "ASHOKLEY.NS", "ASTRAL.NS",
    "AUBANK.NS", "AUROPHARMA.NS", "BALKRISIND.NS", "BANDHANBNK.NS", "BHARATFORG.NS",
    "BHEL.NS", "BIOCON.NS", "COFORGE.NS", "CONCOR.NS", "COROMANDEL.NS",
    "CUMMINSIND.NS", "DALBHARAT.NS", "DEEPAKNTR.NS", "DIXON.NS", "ESCORTS.NS",
    "EXIDEIND.NS", "FEDERALBNK.NS", "FLUOROCHEM.NS", "GLAND.NS", "GLENMARK.NS",
    "GMRAIRPORT.NS", "GODREJPROP.NS", "GUJGASLTD.NS", "HDFCAMC.NS", "HINDZINC.NS",
    "IDFCFIRSTB.NS", "IGL.NS", "INDHOTEL.NS", "INDUSINDBK.NS", "IPCALAB.NS",
    "JKCEMENT.NS", "JUBLFOOD.NS", "KPITTECH.NS", "L&TFH.NS", "LICHSGFIN.NS",
    "LINDEINDIA.NS", "LUPIN.NS", "M&MFIN.NS", "MAXHEALTH.NS", "MFSL.NS",
    "MOTHERSON.NS", "MPHASIS.NS", "MRF.NS", "NMDC.NS", "OBEROIRLTY.NS",
    "OFSS.NS", "OIL.NS", "PAGEIND.NS", "PERSISTENT.NS", "PETRONET.NS",
    "PHOENIXLTD.NS", "PIIND.NS", "POLYCAB.NS", "POONAWALLA.NS", "RAMCOCEM.NS",
    "RVNL.NS", "SAIL.NS", "SBICARD.NS", "SUNDARMFIN.NS", "SUNTV.NS",
    "SUPREMEIND.NS", "SUZLON.NS", "SYNGENE.NS", "TATACOMM.NS", "TATAELXSI.NS",
    "TIINDIA.NS", "TORNTPHARM.NS", "TORNTPOWER.NS", "TRIDENT.NS", "TVSMOTOR.NS",
    "UBL.NS", "UNIONBANK.NS", "UPL.NS", "VOLTAS.NS", "YESBANK.NS",
    "ZYDUSLIFE.NS",
]

NIFTY_200 = NIFTY_100 + [t for t in NIFTY_MIDCAP_100 if t not in NIFTY_100]


# --------------------------------------------------------------------------- #
# Universe selector — driven by env var STOCKYA_UNIVERSE
# --------------------------------------------------------------------------- #

UNIVERSES = {
    "nifty100": NIFTY_100,
    "nifty200": NIFTY_200,
    "nifty50": NIFTY_50,         # for fast smoke tests
}


def _selected_universe_name() -> str:
    name = os.environ.get("STOCKYA_UNIVERSE", "nifty100").lower().strip()
    if name not in UNIVERSES:
        # Fall back to nifty100 silently rather than crash on a typo
        return "nifty100"
    return name


UNIVERSE_NAME = _selected_universe_name()
UNIVERSE = UNIVERSES[UNIVERSE_NAME]
