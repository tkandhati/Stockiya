"""Stock universes — Nifty 50 / 100 / 200 / 500, plus a `custom` file loader.

The pipeline scans whichever universe is selected by `STOCKYA_UNIVERSE`:

    STOCKYA_UNIVERSE=nifty50    — Nifty 50 (fast smoke tests)
    STOCKYA_UNIVERSE=nifty100   (default) — Nifty 50 + Nifty Next 50
    STOCKYA_UNIVERSE=nifty200   — Nifty 100 + Nifty Midcap 100
    STOCKYA_UNIVERSE=nifty500   — Broad NSE-500 style coverage (small + mid + large)
    STOCKYA_UNIVERSE=custom     — read tickers from config/universe_custom.txt
                                  (one per line, `.NS` suffix optional but recommended;
                                  blank lines and `#` comments are ignored)

NSE rebalances these indices ~twice a year. Bump the lists below when the
rebalance lands, and confirm symbols via `python -m backend.check_universe`.

The hardcoded NIFTY_500 list is a *reasonable* snapshot from prior knowledge;
it will drift from the official constituents at each rebalance. If accuracy
matters, put the current official list in `config/universe_custom.txt` and set
`STOCKYA_UNIVERSE=custom` — that overrides everything.

Known dead / corporate-action affected (kept as comments so future-you
knows why they were removed):

    TATAMOTORS.NS  — demerged into TATAMOTORS (CV) + TATAMOTORS-PV/DVR.
                     Yahoo's mapping is currently broken; revisit after the
                     rebalance settles.
    LTIM.NS        — L&T-Mindtree merger; Yahoo intermittently 404s. Use
                     LTI.NS / MINDTREE.NS if you need historical data.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("universe")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CUSTOM_UNIVERSE_PATH = _PROJECT_ROOT / "config" / "universe_custom.txt"

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
# Nifty Midcap 100
# --------------------------------------------------------------------------- #
NIFTY_MIDCAP_100 = [
    "ABFRL.NS", "ALKEM.NS", "APOLLOTYRE.NS", "ASHOKLEY.NS", "ASTRAL.NS",
    "AUBANK.NS", "AUROPHARMA.NS", "BALKRISIND.NS", "BANDHANBNK.NS", "BHARATFORG.NS",
    "BHEL.NS", "BIOCON.NS", "COFORGE.NS", "CONCOR.NS", "COROMANDEL.NS",
    "CUMMINSIND.NS", "DALBHARAT.NS", "DEEPAKNTR.NS", "DIXON.NS", "ESCORTS.NS",
    "EXIDEIND.NS", "FEDERALBNK.NS", "FLUOROCHEM.NS", "GLAND.NS", "GLENMARK.NS",
    "GMRAIRPORT.NS", "GODREJPROP.NS", "GUJGASLTD.NS", "HDFCAMC.NS", "HINDZINC.NS",
    "IDFCFIRSTB.NS", "IGL.NS", "INDHOTEL.NS", "IPCALAB.NS",
    "JKCEMENT.NS", "JUBLFOOD.NS", "KPITTECH.NS", "L&TFH.NS", "LICHSGFIN.NS",
    "LINDEINDIA.NS", "LUPIN.NS", "M&MFIN.NS", "MAXHEALTH.NS", "MFSL.NS",
    "MOTHERSON.NS", "MPHASIS.NS", "MRF.NS", "NMDC.NS", "OBEROIRLTY.NS",
    "OFSS.NS", "OIL.NS", "PAGEIND.NS", "PERSISTENT.NS", "PETRONET.NS",
    "PHOENIXLTD.NS", "PIIND.NS", "POLYCAB.NS", "POONAWALLA.NS", "RAMCOCEM.NS",
    "RVNL.NS", "SAIL.NS", "SBICARD.NS", "SUNDARMFIN.NS", "SUNTV.NS",
    "SUPREMEIND.NS", "SUZLON.NS", "SYNGENE.NS", "TATACOMM.NS", "TATAELXSI.NS",
    "TIINDIA.NS", "TORNTPHARM.NS", "TORNTPOWER.NS", "TRIDENT.NS",
    "UBL.NS", "UNIONBANK.NS", "UPL.NS", "VOLTAS.NS", "YESBANK.NS",
    "ZYDUSLIFE.NS",
]

NIFTY_200 = NIFTY_100 + [t for t in NIFTY_MIDCAP_100 if t not in NIFTY_100]

# --------------------------------------------------------------------------- #
# Nifty Smallcap 100 (subset) — 300 more names below N200 to reach ~500
#
# Broad small-cap universe, curated from well-known listed names on NSE.
# Not an official NSE index snapshot — treat as a starter set. For accuracy
# at any point in time, use STOCKYA_UNIVERSE=custom with the current official
# constituents pasted into config/universe_custom.txt.
# --------------------------------------------------------------------------- #
NIFTY_SMALLCAP_300 = [
    # Auto / auto ancillary
    "AMARAJABAT.NS", "ARE&M.NS", "APOLLO.NS", "BAJAJ-AUTO.NS", "BALRAMCHIN.NS",
    "CEATLTD.NS", "ENDURANCE.NS", "ESCORTS.NS", "GABRIEL.NS", "MAHSCOOTER.NS",
    "MRPL.NS", "SUNDRMFAST.NS", "SUPRAJIT.NS", "SUBROS.NS", "WHEELS.NS",
    # Banks / NBFC / financial services
    "AAVAS.NS", "ABCAPITAL.NS", "AAVAS.NS", "ANGELONE.NS", "ARMANFIN.NS",
    "BSE.NS", "CAMS.NS", "CANFINHOME.NS", "CAPLIPOINT.NS", "CDSL.NS",
    "CENTRALBK.NS", "CENTURYPLY.NS", "CENTURYTEX.NS", "CESC.NS", "CIEINDIA.NS",
    "CITYUNIONBK.NS", "CREDITACC.NS", "CSBBANK.NS", "DCBBANK.NS", "EQUITASBNK.NS",
    "FIVESTAR.NS", "HOMEFIRST.NS", "IBULHSGFIN.NS", "IDBI.NS", "IEX.NS",
    "IIFL.NS", "INDIACEM.NS", "IOB.NS", "JKBANK.NS", "JMFINANCIL.NS",
    "KARURVYSYA.NS", "KOTAK.NS", "MANAPPURAM.NS", "MASFIN.NS", "MCX.NS",
    "MUTHOOTFIN.NS", "NATIONALUM.NS", "NAVINFLUOR.NS", "NBCC.NS", "NCC.NS",
    "PEL.NS", "PNBHOUSING.NS", "PSB.NS", "REPCOHOME.NS", "RITES.NS",
    "RBLBANK.NS", "SBFC.NS", "SOUTHBANK.NS", "SPANDANA.NS", "SURYODAY.NS",
    "UCOBANK.NS", "UJJIVAN.NS", "UJJIVANSFB.NS", "UTIAMC.NS",
    # Capital goods / industrials / defence
    "ABB.NS", "AIAENG.NS", "ANANTRAJ.NS", "ANURAS.NS", "APARINDS.NS",
    "ARVIND.NS", "ARVSMART.NS", "ASTERDM.NS", "ATUL.NS", "BEML.NS",
    "BHARATFORG.NS", "BSOFT.NS", "CAPACITE.NS", "CARBORUNIV.NS", "CENTUM.NS",
    "COCHINSHIP.NS", "CROMPTON.NS", "CYIENT.NS", "DATAPATTNS.NS", "DBL.NS",
    "DYNAMATECH.NS", "ELECON.NS", "ELGIEQUIP.NS", "ENGINERSIN.NS", "EPL.NS",
    "FINPIPE.NS", "FLUOROCHEM.NS", "FORCEMOT.NS", "FSL.NS", "GARFIBRES.NS",
    "GET&D.NS", "GIPCL.NS", "GNFC.NS", "GRAPHITE.NS", "GRINDWELL.NS",
    "GSFC.NS", "GSPL.NS", "HAPPSTMNDS.NS", "HEG.NS", "HEIDELBERG.NS",
    "HFCL.NS", "HONAUT.NS", "HSCL.NS", "IBREALEST.NS", "IIFLWAM.NS",
    "IIFCL.NS", "IONEXCHANG.NS", "IRB.NS", "ISEC.NS", "ITDCEM.NS",
    "ITI.NS", "JAIBALAJI.NS", "JAICORPLTD.NS", "JBMA.NS", "JISLJALEQS.NS",
    "JKLAKSHMI.NS", "JKPAPER.NS", "JMFINANCIL.NS", "JSL.NS", "JSWENERGY.NS",
    "JSWHL.NS", "JSWINFRA.NS", "JUBLINGREA.NS", "JUBLPHARMA.NS", "KAJARIACER.NS",
    "KALPATPOWR.NS", "KANSAINER.NS", "KEC.NS", "KEI.NS", "KFINTECH.NS",
    "KIRLOSBROS.NS", "KIRLOSENG.NS", "KNRCON.NS", "KPIL.NS", "KPRMILL.NS",
    "LAOPALA.NS", "LATENTVIEW.NS", "LAURUSLABS.NS", "LEMONTREE.NS", "LICHSGFIN.NS",
    "LLOYDSME.NS", "LTFOODS.NS", "LTIM.NS", "LUXIND.NS", "MAHLIFE.NS",
    "MAHLOG.NS", "MAHSEAMLES.NS", "MANINDS.NS", "MANINFRA.NS", "MASTEK.NS",
    "MAZDOCK.NS", "MEDPLUS.NS", "METROPOLIS.NS", "MHRIL.NS", "MIDHANI.NS",
    "MINDACORP.NS", "MOIL.NS", "NBVENTURES.NS", "NETWORK18.NS", "NIACL.NS",
    "NIITLTD.NS", "NILKAMAL.NS", "NUVAMA.NS", "NUVOCO.NS", "OLECTRA.NS",
    "ORIENTELEC.NS", "PGHH.NS", "PGHL.NS", "PIL.NS", "PNC.NS",
    "PNCINFRA.NS", "POWERINDIA.NS", "PRAJIND.NS", "PRESTIGE.NS", "PRINCEPIPE.NS",
    "PRIVISCL.NS", "PRSMJOHNSN.NS", "PVR.NS", "PVRINOX.NS", "QUESS.NS",
    "RADICO.NS", "RAILTEL.NS", "RAIN.NS", "RAJESHEXPO.NS", "RALLIS.NS",
    "RATNAMANI.NS", "RAYMOND.NS", "RCF.NS", "REDINGTON.NS", "RELAXO.NS",
    "RENUKA.NS", "RESPONIND.NS", "RHIM.NS", "RIL.NS", "ROSSARI.NS",
    "ROUTE.NS", "RPOWER.NS", "RSYSTEMS.NS", "RTNPOWER.NS", "RUCHIRA.NS",
    # Chemicals / pharma / healthcare
    "AARTIIND.NS", "AAVAS.NS", "ABBOTINDIA.NS", "AJANTPHARM.NS", "ALKYLAMINE.NS",
    "APLLTD.NS", "ARVIND.NS", "AVANTIFEED.NS", "BASF.NS", "BAYERCROP.NS",
    "BLUEDART.NS", "BLUESTARCO.NS", "BOROLTD.NS", "CANFINHOME.NS", "CDSL.NS",
    "CENTRALBK.NS", "CENTURYPLY.NS", "CENTURYTEX.NS", "CESC.NS", "CGCL.NS",
    "CHAMBLFERT.NS", "CHEMPLASTS.NS", "CIPLA.NS", "CLEAN.NS", "CMSINFO.NS",
    "COFORGE.NS", "COMPUTERAGE.NS", "CONCORDBIO.NS", "CRAFTSMAN.NS", "CROMPTON.NS",
    "CUB.NS", "CUMMINSIND.NS", "DBREALTY.NS", "DEEPAKFERT.NS", "DELHIVERY.NS",
    "DEVYANI.NS", "DHANI.NS", "DHANUKA.NS", "DIACID.NS", "DODLA.NS",
    "EIDPARRY.NS", "EIHOTEL.NS", "EMAMILTD.NS", "ENDURANCE.NS", "ERIS.NS",
    "FDC.NS", "FINEORG.NS", "FIVESTAR.NS", "FORTIS.NS", "GLENMARK.NS",
    "GOCOLORS.NS", "GRANULES.NS", "GUJALKALI.NS", "HATSUN.NS", "HEIDELBERG.NS",
    "HEROMOTOCO.NS", "HIKAL.NS", "HIL.NS", "HINDCOPPER.NS", "HONASA.NS",
    "IEX.NS", "IIFL.NS", "IIFLSEC.NS", "IIFLWAM.NS", "INDIAMART.NS",
    "INDIGOPNTS.NS", "INDIGO.NS", "INEOSSTYRO.NS", "INFIBEAM.NS", "INGERRAND.NS",
    "INOXWIND.NS", "IPCALAB.NS", "ITCHOTELS.NS", "JAMNAAUTO.NS", "JBCHEPHARM.NS",
    "JBMA.NS", "JCHAC.NS", "JINDWORLD.NS", "JKPAPER.NS", "JMFINANCIL.NS",
    "JUBLFOOD.NS", "JUBLINGREA.NS", "JUBLPHARMA.NS", "JYOTHYLAB.NS",
    "KAJARIACER.NS", "KALPATPOWR.NS", "KANSAINER.NS", "KAYNES.NS", "KIMS.NS",
    "KIRLOSBROS.NS", "KIRLOSENG.NS", "KNRCON.NS", "KPITTECH.NS", "KPIL.NS",
    "KPRMILL.NS", "LALPATHLAB.NS", "LATENTVIEW.NS", "LAURUSLABS.NS", "LEMONTREE.NS",
    "LINDEINDIA.NS", "LLOYDSME.NS", "LTFOODS.NS", "MAHINDCIE.NS", "MAHLIFE.NS",
    "MAHLOG.NS", "MANAPPURAM.NS", "MANINDS.NS", "MANINFRA.NS", "MANKIND.NS",
    "MANORAMA.NS", "MASTEK.NS", "MAZDOCK.NS", "MEDPLUS.NS", "METROPOLIS.NS",
    "MHRIL.NS", "MIDHANI.NS", "MINDAIND.NS", "MINDACORP.NS", "MMTC.NS",
    "MOIL.NS", "MOLDTKPAC.NS", "MRPL.NS", "MTARTECH.NS", "MUTHOOTFIN.NS",
    "NATCOPHARM.NS", "NAVA.NS", "NAVINFLUOR.NS", "NAZARA.NS", "NBCC.NS",
    "NCC.NS", "NCLIND.NS", "NDR.NS", "NEOGEN.NS", "NESCO.NS",
    "NETWEB.NS", "NEWGEN.NS", "NILKAMAL.NS", "NIRAJ.NS", "NIT.NS",
    "NUVAMA.NS", "NUVOCO.NS", "OLECTRA.NS", "ORIENTCEM.NS", "ORIENTELEC.NS",
]

# De-dupe while preserving order (there are known dupes above from curation).
def _dedupe(xs):
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


NIFTY_SMALLCAP_300 = _dedupe(NIFTY_SMALLCAP_300)

NIFTY_500 = _dedupe(NIFTY_200 + [t for t in NIFTY_SMALLCAP_300 if t not in NIFTY_200])


# --------------------------------------------------------------------------- #
# Custom-file universe — the escape hatch
# --------------------------------------------------------------------------- #

def _load_custom_universe() -> list[str]:
    """Read config/universe_custom.txt. One symbol per line.

    Rules:
      - blank lines ignored
      - `#` starts a comment; text after `#` is stripped
      - symbols without `.NS` suffix get one appended automatically
      - order is preserved; duplicates dropped
      - if the file is missing or empty, returns [] (caller must handle)
    """
    if not _CUSTOM_UNIVERSE_PATH.exists():
        return []
    out = []
    seen = set()
    for raw in _CUSTOM_UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        sym = line if line.endswith(".NS") else f"{line}.NS"
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


# --------------------------------------------------------------------------- #
# Universe selector — driven by env var STOCKYA_UNIVERSE
# --------------------------------------------------------------------------- #

UNIVERSES = {
    "nifty50":  NIFTY_50,
    "nifty100": NIFTY_100,
    "nifty200": NIFTY_200,
    "nifty500": NIFTY_500,
}


def _selected_universe_name() -> str:
    name = os.environ.get("STOCKYA_UNIVERSE", "nifty100").lower().strip()
    if name == "custom":
        return "custom"
    if name not in UNIVERSES:
        log.warning("Unknown STOCKYA_UNIVERSE=%r; falling back to nifty100", name)
        return "nifty100"
    return name


def _resolve_universe(name: str) -> list[str]:
    if name == "custom":
        picks = _load_custom_universe()
        if not picks:
            log.warning(
                "STOCKYA_UNIVERSE=custom but config/universe_custom.txt is missing "
                "or empty; falling back to nifty100"
            )
            return NIFTY_100
        return picks
    return UNIVERSES[name]


UNIVERSE_NAME = _selected_universe_name()
UNIVERSE = _resolve_universe(UNIVERSE_NAME)
