"""Known structural / sector-level headwinds the picker must reason about.

This is **knowledge context** fed to the LLM on every call so picks are not made
in a vacuum on backward-looking fundamentals alone. Update this file when the
macro picture shifts.

The Buffett principle: "Risk comes from not knowing what you're doing." Pretending
sector-level risks don't exist while picking stocks is exactly that.
"""

# Each entry: (matcher used to identify the sector, headwind description shown to LLM).
# The matcher is checked against either yfinance "sector"/"industry" strings or the
# Nifty 50 ticker bucket name in universe.py.

SECTOR_HEADWINDS: dict[str, str] = {
    "IT / Technology Services": (
        "Indian IT services (TCS, INFY, WIPRO, HCLTECH, TECHM) is in a multi-quarter "
        "earnings derate. AI coding agents (Claude Code, Cursor, GitHub Copilot, "
        "Cognition Devin) are compressing software-engineering deal sizes, lengthening "
        "sales cycles, and shrinking headcount-led revenue growth. US/EU tech and BFSI "
        "discretionary spend has been weak since 2024. Margins are protected via "
        "automation but growth visibility is poor. **Treat any IT services pick as "
        "structurally challenged unless the company has a credible AI-platform / "
        "transformation narrative with bookings to back it.**"
    ),
    "Banks": (
        "Private banks face NIM compression as deposit costs reset higher and unsecured "
        "personal-loan book growth slows under RBI risk-weight tightening. PSU banks "
        "have already had their re-rating; further upside needs ROA expansion, not just "
        "credit-cost normalisation. Watch for any RBI enforcement action on individual "
        "names (e.g. IndusInd-style accounting issues)."
    ),
    "NBFC / Credit": (
        "RBI risk-weight increases on consumer/personal-loan exposures lifted capital "
        "costs in 2024. Funding-mix risk if wholesale spreads widen. Bajaj Finance "
        "specifically has decelerating AUM growth from a high base."
    ),
    "Auto": (
        "EV transition risk: ICE-only OEMs face medium-term volume risk. Tata Motors "
        "JLR demand is China-sensitive. Two-wheeler demand is rural-monsoon dependent. "
        "Maruti is gaining share as Hyundai+Kia stumble — that's a tailwind, not a "
        "headwind, but the small-car total addressable market is shrinking."
    ),
    "FMCG / Consumer Defensive": (
        "Rural demand has been recovering but premium urban demand is sluggish. Input "
        "costs (palm oil, crude derivatives) are volatile. ITC's FMCG/hotel demerger "
        "narrative is largely priced in. HUL faces premiumisation slowdown."
    ),
    "Pharma": (
        "US generic pricing pressure persists, partially offset by complex generics & "
        "specialty pipelines. Sun Pharma's specialty franchise is the differentiator. "
        "API/CDMO names benefit from China+1, but execution risk is high."
    ),
    "Metals & Mining": (
        "China property-stimulus uncertainty drives steel & base-metal demand. Coal "
        "India faces long-term thermal-coal demand decline as renewables scale. Tata "
        "Steel UK restructuring overhang."
    ),
    "Oil & Gas / Energy": (
        "Reliance: Jio ARPU hikes are the near-term catalyst; refining margins are "
        "cyclical; new-energy capex is a long-dated optionality. ONGC: gas-pricing "
        "regime favourable but crude-price exposure cuts both ways. OMCs (BPCL) face "
        "marketing-margin politics around fuel-price freezes."
    ),
    "Power / Utilities": (
        "Earnings have been driven by capacity additions and capex cycle. NTPC has a "
        "renewable-transition story but execution-paced. Power Grid is bond-proxy and "
        "rate-sensitive."
    ),
    "Telecom": (
        "Bharti is the cleanest 5G monetisation play; ARPU hikes are the catalyst. "
        "Vi-Idea solvency drives consolidation upside. Capex intensity peaks have likely "
        "passed."
    ),
    "Cement / Construction": (
        "Demand is rural-housing + infrastructure capex driven; pricing has been weak "
        "after 2024's price war. Consolidation (Adani-Ambuja, UltraTech-India Cements) "
        "is supportive medium-term but margins are still recovering."
    ),
    "Infrastructure / Capital Goods": (
        "L&T benefits from infrastructure capex cycle and Middle East order wins. "
        "Adani group names carry governance overhang and group-leverage risk."
    ),
    "Consumer Cyclical / Discretionary": (
        "Titan: jewellery is gold-price volatility plus wedding-season demand; mass "
        "discretionary is weak. Asian Paints: lost share to Birla Opus — competitive "
        "intensity has stepped up. Trent: high-multiple compounder, valuation-sensitive."
    ),
    "Defence": (
        "BEL: order-book visibility is strong on Make-in-India tailwind, but execution "
        "and order-conversion timing drives quarterly noise. HAL benefits from large "
        "domestic platforms (Tejas, helicopters) but earnings can be lumpy on order phasing."
    ),
    "Insurance": (
        "Indian life insurance (LICI, HDFCLIFE, SBILIFE, ICICIPRULI) is in a multi-year "
        "premium-growth phase but margins face pressure from regulator-driven product changes "
        "(surrender-value rules, commission caps) and rising competitive intensity. LIC is a "
        "rate-sensitive bond proxy plus protection franchise; private players face product-mix "
        "and persistency-ratio headwinds. General insurance (ICICIGI) tracks motor TP pricing "
        "and any IRDAI move on health-insurance pricing is a cycle risk."
    ),
    "Real Estate": (
        "Indian residential real estate is in a multi-year up-cycle on inventory absorption "
        "and price recovery, but the cycle is mature in NCR/MMR and price growth slowed "
        "through 2025. Listed names (DLF, LODHA) are heavily NCR/MMR exposed. Watch mortgage-"
        "rate sensitivity, RBI commentary on real-estate financing, and any regulatory action "
        "on under-construction project funding. Cash collections > pre-sales is a quality flag."
    ),
}


# Map Nifty 50 tickers to a headwind bucket key.
TICKER_HEADWIND_KEY: dict[str, str] = {
    # IT
    "TCS.NS": "IT / Technology Services",
    "INFY.NS": "IT / Technology Services",
    "WIPRO.NS": "IT / Technology Services",
    "HCLTECH.NS": "IT / Technology Services",
    "TECHM.NS": "IT / Technology Services",
    # Banks
    "HDFCBANK.NS": "Banks",
    "ICICIBANK.NS": "Banks",
    "AXISBANK.NS": "Banks",
    "KOTAKBANK.NS": "Banks",
    "SBIN.NS": "Banks",
    "INDUSINDBK.NS": "Banks",
    # NBFC / Credit
    "BAJFINANCE.NS": "NBFC / Credit",
    "BAJAJFINSV.NS": "NBFC / Credit",
    "SHRIRAMFIN.NS": "NBFC / Credit",
    "HDFCLIFE.NS": "NBFC / Credit",
    "SBILIFE.NS": "NBFC / Credit",
    # Auto
    "MARUTI.NS": "Auto",
    "TATAMOTORS.NS": "Auto",
    "M&M.NS": "Auto",
    "BAJAJ-AUTO.NS": "Auto",
    "EICHERMOT.NS": "Auto",
    "HEROMOTOCO.NS": "Auto",
    # FMCG
    "HINDUNILVR.NS": "FMCG / Consumer Defensive",
    "ITC.NS": "FMCG / Consumer Defensive",
    "NESTLEIND.NS": "FMCG / Consumer Defensive",
    "BRITANNIA.NS": "FMCG / Consumer Defensive",
    "TATACONSUM.NS": "FMCG / Consumer Defensive",
    # Pharma / Healthcare
    "SUNPHARMA.NS": "Pharma",
    "DRREDDY.NS": "Pharma",
    "CIPLA.NS": "Pharma",
    "APOLLOHOSP.NS": "Pharma",
    # Metals
    "TATASTEEL.NS": "Metals & Mining",
    "JSWSTEEL.NS": "Metals & Mining",
    "HINDALCO.NS": "Metals & Mining",
    "COALINDIA.NS": "Metals & Mining",
    # Oil & Gas / Energy
    "RELIANCE.NS": "Oil & Gas / Energy",
    "ONGC.NS": "Oil & Gas / Energy",
    "BPCL.NS": "Oil & Gas / Energy",
    # Power
    "NTPC.NS": "Power / Utilities",
    "POWERGRID.NS": "Power / Utilities",
    # Telecom
    "BHARTIARTL.NS": "Telecom",
    # Cement
    "ULTRACEMCO.NS": "Cement / Construction",
    "GRASIM.NS": "Cement / Construction",
    # Infra
    "LT.NS": "Infrastructure / Capital Goods",
    "ADANIPORTS.NS": "Infrastructure / Capital Goods",
    "ADANIENT.NS": "Infrastructure / Capital Goods",
    # Consumer Discretionary
    "TITAN.NS": "Consumer Cyclical / Discretionary",
    "TRENT.NS": "Consumer Cyclical / Discretionary",
    "ASIANPAINT.NS": "Consumer Cyclical / Discretionary",
    # Defence
    "BEL.NS": "Defence",

    # ----- Nifty Next 50 additions -----

    # PSU Banks
    "BANKBARODA.NS": "Banks",
    "CANBK.NS": "Banks",
    "INDIANB.NS": "Banks",
    "PNB.NS": "Banks",
    # NBFCs / PSU finance
    "CHOLAFIN.NS": "NBFC / Credit",
    "JIOFIN.NS": "NBFC / Credit",
    "BAJAJHLDNG.NS": "NBFC / Credit",
    "IRFC.NS": "NBFC / Credit",
    "PFC.NS": "NBFC / Credit",
    "RECLTD.NS": "NBFC / Credit",
    # Insurance (new sector)
    "ICICIGI.NS": "Insurance",
    "ICICIPRULI.NS": "Insurance",
    "LICI.NS": "Insurance",
    # IT
    "LTIM.NS": "IT / Technology Services",
    # Auto
    "BOSCHLTD.NS": "Auto",
    "TVSMOTOR.NS": "Auto",
    # Pharma
    "DIVISLAB.NS": "Pharma",
    # FMCG / Consumer Defensive
    "DABUR.NS": "FMCG / Consumer Defensive",
    "GODREJCP.NS": "FMCG / Consumer Defensive",
    "MARICO.NS": "FMCG / Consumer Defensive",
    "COLPAL.NS": "FMCG / Consumer Defensive",
    "UNITDSPR.NS": "FMCG / Consumer Defensive",
    "VBL.NS": "FMCG / Consumer Defensive",
    # Metals
    "JINDALSTEL.NS": "Metals & Mining",
    "VEDL.NS": "Metals & Mining",
    # Oil & Gas
    "HINDPETRO.NS": "Oil & Gas / Energy",
    "IOC.NS": "Oil & Gas / Energy",
    "GAIL.NS": "Oil & Gas / Energy",
    # Power
    "TATAPOWER.NS": "Power / Utilities",
    "NHPC.NS": "Power / Utilities",
    "ADANIPOWER.NS": "Power / Utilities",
    "ADANIGREEN.NS": "Power / Utilities",
    # Telecom
    "INDUSTOWER.NS": "Telecom",
    # Cement
    "ACC.NS": "Cement / Construction",
    "AMBUJACEM.NS": "Cement / Construction",
    "SHREECEM.NS": "Cement / Construction",
    # Industrials / Capital Goods (folded into existing key)
    "ABB.NS": "Infrastructure / Capital Goods",
    "SIEMENS.NS": "Infrastructure / Capital Goods",
    "CGPOWER.NS": "Infrastructure / Capital Goods",
    # Defence
    "HAL.NS": "Defence",
    # Real Estate (new sector)
    "DLF.NS": "Real Estate",
    "LODHA.NS": "Real Estate",
    # Consumer Discretionary (paints / building / retail / travel / digital)
    "BERGEPAINT.NS": "Consumer Cyclical / Discretionary",
    "HAVELLS.NS": "Consumer Cyclical / Discretionary",
    "PIDILITIND.NS": "Consumer Cyclical / Discretionary",
    "SRF.NS": "Consumer Cyclical / Discretionary",
    "DMART.NS": "Consumer Cyclical / Discretionary",
    "INDIGO.NS": "Consumer Cyclical / Discretionary",
    "IRCTC.NS": "Consumer Cyclical / Discretionary",
    "NAUKRI.NS": "Consumer Cyclical / Discretionary",
}


# Tight one-line risk tags per sector — used by the card's `risk_headline`.
# Long-form text in SECTOR_HEADWINDS is shown on the detail page; this is the
# scannable version.
SECTOR_RISK_TAG: dict[str, str] = {
    "IT / Technology Services":
        "AI-coding-agent disruption compressing deal sizes",
    "Banks":
        "NIM compression + RBI risk-weight tightening",
    "NBFC / Credit":
        "Funding-cost reset + AUM growth deceleration",
    "Insurance":
        "IRDAI surrender-value rules + persistency pressure",
    "Auto":
        "EV transition risk + rural / China demand cycle",
    "FMCG / Consumer Defensive":
        "Premium urban demand sluggish + input-cost volatility",
    "Pharma":
        "US generic pricing pressure",
    "Metals & Mining":
        "China demand uncertainty + commodity cycle",
    "Oil & Gas / Energy":
        "Refining-margin cyclicality + fuel-price politics",
    "Power / Utilities":
        "Capex-paced earnings + rate sensitivity",
    "Telecom":
        "ARPU-hike cadence + capex intensity",
    "Cement / Construction":
        "Pricing weakness + margin recovery still in progress",
    "Infrastructure / Capital Goods":
        "Order-conversion timing + execution risk",
    "Consumer Cyclical / Discretionary":
        "Discretionary demand sluggish + competitive intensity",
    "Defence":
        "Order-phasing lumpiness",
    "Real Estate":
        "Cycle mature in NCR/MMR + mortgage-rate sensitivity",
}


def headwind_for(symbol: str) -> str | None:
    """Return the long-form sector headwind text (for the detail page)."""
    key = TICKER_HEADWIND_KEY.get(symbol)
    if key:
        return SECTOR_HEADWINDS.get(key)
    return None


def risk_tag_for(symbol: str) -> str | None:
    """Return the short one-line risk tag (for the card's risk_headline)."""
    key = TICKER_HEADWIND_KEY.get(symbol)
    if key:
        return SECTOR_RISK_TAG.get(key)
    return None


def headwinds_block() -> str:
    """Render the full headwinds dictionary as text for the LLM system context."""
    lines = ["# Known sector / structural headwinds (as of latest review)"]
    for key, body in SECTOR_HEADWINDS.items():
        lines.append(f"\n## {key}\n{body}")
    return "\n".join(lines)
