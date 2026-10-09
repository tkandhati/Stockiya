"""Stock universes — Nifty Total Market (volume strategy) + Nifty 50 / 100 /
200 / 300 / 500 and a `custom` file loader (discovery tools).

The volume strategy's ``VOLUME_UNIVERSE`` is sourced, in priority order:
  1. The owner-supplied file ``config/universe_custom.txt`` (the "top 500 by
     market cap" list — owner ask 2026-10-08). One symbol per line, ``.NS``
     optional, ``#`` comments / blank lines ignored. Override the path with
     ``STOCKYA_VOLUME_UNIVERSE_FILE``.
  2. If that file is absent/empty, the cleaned in-code ``NIFTY_300`` list below
     (~300 NSE names) — so the app is never left blank.
Dead / renamed / wrong-form tickers are stripped from every list via
``_DEAD_OR_RENAMED`` + ``_clean``. To change the live universe, edit the file;
to change the fallback, edit the curated lists below.

``STOCKYA_UNIVERSE`` selects the configurable universe used by independent
discovery tools such as the price-trend scanner (it is independent of the volume
strategy's ``VOLUME_UNIVERSE`` above):

    STOCKYA_UNIVERSE=nifty50     — Nifty 50 (fast smoke tests)
    STOCKYA_UNIVERSE=nifty100    — Nifty 50 + Nifty Next 50
    STOCKYA_UNIVERSE=nifty200    — Nifty 100 + Nifty Midcap 100
    STOCKYA_UNIVERSE=nifty300    — top-300 slice of the curated 500 set
    STOCKYA_UNIVERSE=nifty500    — Broad NSE-500 style coverage (small + mid + large)
    STOCKYA_UNIVERSE=niftytotal  (default) — the same in-code NIFTY_300 list the
                                  volume strategy uses (~300 names)
    STOCKYA_UNIVERSE=custom      — read tickers from config/universe_custom.txt
                                  (one per line, `.NS` suffix optional but recommended;
                                  blank lines and `#` comments are ignored)

NOTE: "Nifty 300" is NOT an official NSE index (the official broad indices are
Nifty 200 and Nifty 500; the top-300-by-cap set is closest to "Nifty
LargeMidcap 250" + 50). `NIFTY_300` here is a pragmatic top-300 slice of the
curated `NIFTY_500` list, used by both the volume strategy (VOLUME_UNIVERSE)
and the discovery selector.

NSE rebalances these indices ~twice a year. Bump the hardcoded lists below when
the rebalance lands, and confirm symbols via `python -m backend.check_universe`.

The hardcoded NIFTY_500 list is a *reasonable* snapshot from prior knowledge;
it will drift from official constituents at each rebalance.

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
# Known dead / renamed / wrong-form tickers — stripped from every curated list.
#
# These were confirmed delisted or renamed on Yahoo (the "possibly delisted"
# fetch loop, 2026-10-08) or are a wrong ticker form whose correct symbol is
# already present in the lists. The curated in-code lists below are now only the
# FALLBACK (the live universe is the owner-supplied config/universe_custom.txt),
# but keeping the fallback clean means a missing file never resurrects a corpse.
# Reversible: drop a symbol from this set to let it back in.
# --------------------------------------------------------------------------- #
_DEAD_OR_RENAMED = frozenset({
    "AMARAJABAT.NS",   # renamed -> ARE&M.NS (present)
    "L&TFH.NS",        # renamed -> LTF.NS
    "GET&D.NS",        # renamed -> GETD.NS
    "CENTURYTEX.NS",   # no data on Yahoo (corporate action)
    "IBREALEST.NS",    # Indiabulls Real Estate — no data on Yahoo
    "IIFCL.NS",        # not an EQ listing on Yahoo
    "IIFLWAM.NS",      # renamed -> 360ONE.NS
    "IBULHSGFIN.NS",   # renamed -> SAMMAANCAP.NS
    "ISEC.NS",         # ICICI Securities delisted (merged into ICICIBANK)
    "CITYUNIONBK.NS",  # wrong form -> CUB.NS (present)
    "UJJIVAN.NS",      # merged -> UJJIVANSFB.NS (present)
    "KOTAK.NS",        # wrong form -> KOTAKBANK.NS (present)
    "RIL.NS",          # wrong form -> RELIANCE.NS (present)
    "PVR.NS",          # merged -> PVRINOX.NS (present)
    "LTIM.NS",         # Yahoo 404s after the L&T-Mindtree merger
    "MAHINDCIE.NS",    # renamed -> CIEINDIA.NS (present)
    "COMPUTERAGE.NS",  # old name -> CAMS.NS (present)
})


def _clean(xs):
    """De-dupe (order-preserving) AND drop known dead/renamed tickers."""
    seen = set()
    out = []
    for x in xs:
        if x in seen or x in _DEAD_OR_RENAMED:
            continue
        seen.add(x)
        out.append(x)
    return out


def _read_symbol_file(path: Path) -> list[str]:
    """Read a one-symbol-per-line universe file. ``[]`` if missing/empty.

    Rules (shared by the volume universe and the discovery ``custom`` universe):
      - blank lines ignored; ``#`` starts a comment (text after it is stripped)
      - a symbol without a ``.NS`` suffix gets one appended automatically
      - order preserved, duplicates dropped, known dead/renamed names stripped
    """
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(line if line.upper().endswith(".NS") else f"{line}.NS")
    return _clean(out)

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

NIFTY_100 = _clean(NIFTY_50 + NIFTY_NEXT_50)

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

NIFTY_200 = _clean(NIFTY_100 + NIFTY_MIDCAP_100)

# --------------------------------------------------------------------------- #
# Nifty Smallcap 100 (subset) — 300 more names below N200 to reach ~500
#
# Broad small-cap universe, curated from well-known listed names on NSE.
# Not an official NSE index snapshot — treat as a starter set and refresh the
# tracked lists when the volume strategy's exact membership needs to change.
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

NIFTY_SMALLCAP_300 = _clean(NIFTY_SMALLCAP_300)

NIFTY_500 = _clean(NIFTY_200 + NIFTY_SMALLCAP_300)

# --------------------------------------------------------------------------- #
# Nifty 300 — pragmatic broad universe (NOT an official index).
#
# Top-300 slice of the curated 500 set: Nifty 200 (large + upper-mid) followed
# by the most-liquid small/mid names. Deterministic and offline — no fabricated
# tickers beyond what's already curated above. For the exact official
# constituents of any index, use STOCKYA_UNIVERSE=custom.
# --------------------------------------------------------------------------- #
NIFTY_300 = NIFTY_500[:300]


# --------------------------------------------------------------------------- #
# Nifty Total Market — the volume strategy's universe.
#
# PRIMARY SOURCE (owner ask, 2026-10-08: "I just need top 500 with market cap"):
# an owner-supplied file — the top-500-by-market-cap list — at
# ``config/universe_custom.txt`` (override path with STOCKYA_VOLUME_UNIVERSE_FILE).
# One symbol per line, ``.NS`` optional, ``#`` comments / blank lines ignored.
# When that file is present and non-empty it IS the volume universe; otherwise
# the strategy falls back to the cleaned in-code NIFTY_300 below so a missing
# file never leaves the app blank. (This re-enables file-sourcing that was
# dropped on 2026-09-06 — now at the owner's explicit request, and reversible:
# delete/empty the file to go back to the in-code list.)
# --------------------------------------------------------------------------- #

NIFTY_TOTAL_MARKET = list(NIFTY_300)


def _volume_universe_file() -> Path:
    raw = os.environ.get("STOCKYA_VOLUME_UNIVERSE_FILE")
    return Path(raw).expanduser() if raw else _CUSTOM_UNIVERSE_PATH


def _load_volume_universe() -> tuple[list[str], str, str]:
    """(symbols, name, label) — owner file if present, else the in-code fallback."""
    path = _volume_universe_file()
    names = _read_symbol_file(path)
    if names:
        if len(names) < 100:
            # Respect the file, but make a suspiciously short "top 500" loud.
            log.warning(
                "Volume universe from %s has only %d symbol(s) — expected ~500. "
                "Using it as-is; check the file if that's unintended.",
                path.name, len(names),
            )
        log.info("Volume universe: %d symbols from %s.", len(names), path.name)
        return names, "custom-file", f"Custom ({path.name})"
    log.info("Volume universe: %s file absent/empty — using in-code NIFTY_300 "
             "fallback (%d names).", _volume_universe_file().name, len(NIFTY_TOTAL_MARKET))
    return list(NIFTY_TOTAL_MARKET), "niftytotal", "Nifty Total Market"


# The volume strategy's eligibility boundary. Keep this separate from the
# configurable discovery ``UNIVERSE`` below: changing STOCKYA_UNIVERSE may widen
# or narrow independent scanners, but the volume strategy always scans exactly
# the membership loaded above.
_VOL_NAMES, VOLUME_UNIVERSE_NAME, VOLUME_UNIVERSE_LABEL = _load_volume_universe()
VOLUME_UNIVERSE = tuple(_VOL_NAMES)
VOLUME_UNIVERSE_SET = frozenset(VOLUME_UNIVERSE)
# Sanity guard: _read_symbol_file / _clean already de-dupe, so a duplicate here
# would be a real bug — fail loudly rather than silently scan a partial universe.
if len(VOLUME_UNIVERSE) != len(VOLUME_UNIVERSE_SET):
    raise RuntimeError("VOLUME_UNIVERSE contains duplicate symbols")
if not VOLUME_UNIVERSE:
    raise RuntimeError(
        "VOLUME_UNIVERSE is empty — both the owner file "
        f"({_volume_universe_file()}) and the in-code NIFTY_300 fallback are empty."
    )


# --------------------------------------------------------------------------- #
# Custom-file universe — the escape hatch
# --------------------------------------------------------------------------- #

def _load_custom_universe() -> list[str]:
    """Read config/universe_custom.txt for the discovery ``custom`` universe.

    Same file + rules as the volume universe (see ``_read_symbol_file``);
    returns ``[]`` if the file is missing or has no active symbols.
    """
    return _read_symbol_file(_CUSTOM_UNIVERSE_PATH)


# --------------------------------------------------------------------------- #
# Discovery-universe selector — driven by env var STOCKYA_UNIVERSE
# --------------------------------------------------------------------------- #

UNIVERSES = {
    "nifty50":    NIFTY_50,
    "nifty100":   NIFTY_100,
    "nifty200":   NIFTY_200,
    "nifty300":   NIFTY_300,
    "nifty500":   NIFTY_500,
    "niftytotal": NIFTY_TOTAL_MARKET,
}

# Human-readable labels for logs / UI / trace. Keeps user-facing text in sync
# with the selected universe instead of hardcoding "Nifty 100" in a dozen files.
_UNIVERSE_LABELS = {
    "nifty50": "Nifty 50", "nifty100": "Nifty 100", "nifty200": "Nifty 200",
    "nifty300": "Nifty 300", "nifty500": "Nifty 500",
    "niftytotal": "Nifty Total Market", "custom": "custom universe",
}

# Default discovery universe (2026-09-02): niftytotal (the full Nifty Total
# Market list, same CSV the volume strategy uses). Was nifty300 (2026-07-27),
# nifty100 before that.
_DEFAULT_UNIVERSE = "niftytotal"


def _selected_universe_name() -> str:
    name = os.environ.get("STOCKYA_UNIVERSE", _DEFAULT_UNIVERSE).lower().strip()
    if name == "custom":
        return "custom"
    if name not in UNIVERSES:
        log.warning("Unknown STOCKYA_UNIVERSE=%r; falling back to %s",
                    name, _DEFAULT_UNIVERSE)
        return _DEFAULT_UNIVERSE
    return name


def _resolve_universe(name: str) -> list[str]:
    if name == "custom":
        picks = _load_custom_universe()
        if not picks:
            log.warning(
                "STOCKYA_UNIVERSE=custom but config/universe_custom.txt is missing "
                "or empty; falling back to %s", _DEFAULT_UNIVERSE
            )
            return UNIVERSES[_DEFAULT_UNIVERSE]
        return picks
    return UNIVERSES[name]


UNIVERSE_NAME = _selected_universe_name()
UNIVERSE = _resolve_universe(UNIVERSE_NAME)
# Pretty label for the selected universe — import this instead of hardcoding.
UNIVERSE_LABEL = _UNIVERSE_LABELS.get(UNIVERSE_NAME, UNIVERSE_NAME)
