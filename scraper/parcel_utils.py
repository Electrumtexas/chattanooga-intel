"""
Shared helpers used by every scraper that touches an owner name, a raw
address, or a Map/Group/Parcel triplet — kept in one place so the
classification/formatting logic (and its regexes) isn't duplicated per
source.
"""

from __future__ import annotations

import re

ENTITY_PATTERNS = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|LP|L\.P|LLP|PLLC|LTD|LIMITED|COMPANY|PARTNERSHIP|HOLDINGS|"
    r"PROPERTIES|INVESTMENTS?|ENTERPRISES|PARTNERS|ASSOCIATES|REALTY)\b",
    re.IGNORECASE,
)
TRUST_PATTERN = re.compile(r"\bTRUST\b", re.IGNORECASE)

# --- Owner classes that are not motivated private sellers ------------------
# Every pattern below was audited 2026-10-06 against all 16,547 distinct
# parcel owner names in the production DB (scoring recalibration, see
# scoring.py). Owner strings in Live_Parcels are "LAST FIRST" for people, so
# the anchors are phrasings a personal name never takes.

# Public bodies — 104 parcels / 41 names on that snapshot, no private owner
# among them: "HAMILTON COUNTY & <CITY> CITY OF" (the county's tax-sale
# strike-off holder, 6 spellings), the city under several spellings
# ("CHATT CITY OF", "CITY OF CHATTANOOGA TREASURERS OFFICE", "... C/O
# ELECTRIC POWER BOARD", "... A MUNICIPAL CORP"), housing / airport / land-bank
# authorities, the city's Health Educational & Housing Facility Board, school
# board and public schools, HUD, the State, Red Bank / East Ridge.
GOVERNMENT_PATTERN = re.compile(
    r"\bCITY OF\b|\bTOWN OF\b|\bCOUNTY OF\b|\bSTATE OF\b|\bUNITED STATES\b|"
    r"^(CHATT|CHATTANOOGA|EAST RIDGE|RED BANK|SODDY[ -]DAISY|COLLEGEDALE|SIGNAL MOUNTAIN|LOOKOUT MOUNTAIN|LAKESITE|WALDEN|RIDGESIDE) (CITY|TOWN) OF\b|"
    r"^HAMILTON COUNTY\b(?!.*\b(LLC|INC|PROPERTIES|HOLDINGS|PARTNERS|INVESTMENTS?)\b)|"
    r"\bHOUSING AUTHORITY\b|\bLAND BANK\b|\bAIRPORT AUTHORITY\b|\bPORT AUTHORITY\b|"
    r"\bELECTRIC POWER BOARD\b|\bEPB\b|\bTENNESSEE VALLEY AUTHORITY\b|\bMUNICIPAL CORP\b|"
    r"\bBOARD OF EDUCATION\b|\bDEPT OF\b|\bDEPARTMENT OF\b|\bSECRETARY OF\b|\bPARKS (& |AND )RECREATION\b|"
    r"\bHOUSING (& |AND )URBAN DEVELOPMENT\b|\bVETERANS AFFAIRS\b|\bHOUSING FACILITY BOARD\b|"
    r"\bWATER (& |AND )?WASTEWATER\b|\bUTILITY DISTRICT\b|\b(ELEMENTARY|MIDDLE|HIGH) SCHOOL\b|"
    r"\bREGIONAL PLANNING\b|\bINDUSTRIAL DEVELOPMENT BOARD\b|\bPUBLIC BUILDING AUTHORITY\b",
    re.IGNORECASE,
)

# Religious, civic, educational, HOA/common-area, cemetery, non-profit and
# utility owners — ~180 parcels on that snapshot (churches, HOAs, schools,
# cemeteries, museums, Habitat, community housing non-profits, lodges, cell
# towers, gas/telephone/rail). CHURCH, TEMPLE, CHAPEL, SCHOOL, ACADEMY and
# FOUNDATION are also surnames ("TEMPLE JOHN W TR", "CHURCH PAUL D"), so they
# only count when they are NOT the first token and not right after "&" (a
# co-owner's surname); BIBLE, CHRISTIAN, FAITH and LODGE likewise only count
# next to an institutional companion word.
INSTITUTIONAL_PATTERN = re.compile(
    r"\bCHURCH (OF|INC|TRS|TR|TRUSTEES)\b|^CHURCH OF\b|\bMINISTRIES\b|\bMINISTRY\b|\bDIOCESE\b|\bCONGREGATION\b|"
    r"\b(BAPTIST|METHODIST|PRESBYTERIAN|PRESBITERIANA|LUTHERAN|EPISCOPAL|CATHOLIC|PENTECOSTAL|PENTECOSTES|APOSTOLIC|"
    r"ADVENTISTS?|NAZARENE|IGLESIA)\b|\bTABERNACLE\b|\bSYNAGOGUE\b|\bMOSQUE\b|\bISLAMIC\b|\bHOUSE OF (PRAYER|GOD)\b|"
    r"\bKINGDOM HALL\b|\bJEHOVAH|\bFELLOWSHIP\b|\bHABITAT FOR HUMANITY\b|\bMASONIC\b|\bGRAND LODGE\b|\bLODGE (NO|#|INC)|"
    r"\bKNIGHTS OF\b|\bIBPOE\b|\bAMERICAN LEGION\b|\bVFW\b|\bCEMET(?:ERY|ARY|EARY)\b|\bMUSEUM\b|\bYMCA\b|\bYWCA\b|"
    r"\bBOYS (& |AND )GIRLS CLUB\b|\bHUMANE\b|\bUNIVERSITY\b|\bHOSPITAL\b|\bERLANGER\b|\bGOODWILL\b|\bCONSERVANCY\b|"
    r"\bCHILDREN'?`?S HOME\b|\bCOMMUNITY HOUSING DEV|\bNEIGHBORHOOD ENTERPRISE\b|\bCOMMUNITY KITCHEN\b|"
    r"\b(HOME ?OWNER'?`?S?|OWNERS|COMMUNITY|CONDOMINIUM|PROPERTY OWNERS|NEIGHBORHOOD|SUBDIVISION) ASSOC(IATION)?\b|"
    r"\bHOMEOWNERS\b|\bHOA\b|\bRAILROAD\b|\bRAILWAY\b|\bNORFOLK SOUTHERN\b|\bCSX\b|\bTENNESSEE AMERICAN WATER\b|"
    r"\bCHATTANOOGA GAS\b|\bBELLSOUTH\b|\bTELEPHONE\b|\bELECTRIC (COOPERATIVE|MEMBERSHIP)\b",
    re.IGNORECASE,
)
INSTITUTIONAL_NOT_FIRST = re.compile(
    r"^\S+.*(?<!& )\b(CHURCH|TEMPLE|CHAPEL|SCHOOL|ACADEMY|FOUNDATION)\b", re.IGNORECASE
)
# Infrastructure site holders that are LLC-shaped but never sellers.
INSTITUTIONAL_LLC_PATTERN = re.compile(r"\b(CROWN CASTLE|SBA TOWERS|AMERICAN TOWER)\b", re.IGNORECASE)

# Lenders, securitization trustees and the GSEs holding bank-owned (REO)
# property: buyable (through a listing agent), but not a direct-to-seller
# motivated owner. Exact phrases only — FANNIE and FREDDIE are given names in
# the DB, so the bare words never match.
LENDER_PATTERN = re.compile(
    r"\bFEDERAL NATIONAL MORTGAGE\b|\bFEDERAL HOME LOAN MORTGAGE\b|\bFANNIE MAE\b|\bFREDDIE MAC\b|"
    r"\bDEUTSCHE BANK\b|\bWELLS FARGO\b|\bBANK OF AMERICA\b|\bWACHOVIA\b|\bU ?S BANK\b|\bBANK NATIONAL ASSOC|"
    r"\bNATIONAL TRUST CO|\bMORTGAGE ELECTRONIC REGISTRATION\b|\bDLJ MORTGAGE\b|\bMORTGAGE CAPITAL\b",
    re.IGNORECASE,
)

# Assessor placeholders where the real owner is withheld or mid-update
# ("Private Owner" = a confidential-owner record, 9 parcels on the snapshot;
# "Update in Progress" is already filtered upstream by enrich_assessor). The
# name says nothing, so no entity/absentee inference is made from it.
PLACEHOLDER_OWNER_PATTERN = re.compile(
    r"^\s*(PRIVATE OWNER|UPDATE IN PROGRESS|UNKNOWN( OWNER)?|OWNER UNKNOWN|CURRENT OWNER|CONFIDENTIAL)\b",
    re.IGNORECASE,
)

# owner_type values scoring.py treats as "not a motivated private seller"
# (no absentee/entity terms; OWNER_CLASS_MULTIPLIER). They stay in every
# export, filterable by owner_type.
NON_PRIVATE_OWNER_TYPES = {"government", "institutional", "lender", "unknown"}


def format_parcel_id(map_: str | None, group: str | None, parcel: str | None) -> str | None:
    """Canonical parcel_id from Hamilton County's Map/Group/Parcel triplet —
    every source needs to agree on this exact format to join correctly.
    Blank Group (common) is simply omitted rather than leaving a stray
    separator.
    """
    parts = [p.strip() for p in (map_, group, parcel) if p and p.strip()]
    return "-".join(parts) if parts else None


def assessor_card_url(parcel_id: str | None) -> str | None:
    """The county's server-prerendered per-parcel page. Verified byte-for-
    byte against Live_Parcels' own RecordsOnl field, including the
    blank-group case (e.g. "005-001" -> .../card/005_001, group omitted
    rather than double-underscored) — see enrich_assessor.py.
    """
    if not parcel_id:
        return None
    return f"https://assessor.hamiltontn.gov/card/{parcel_id.replace('-', '_')}"


def classify_owner_type(owner_name: str | None) -> str | None:
    """Best-effort classification from the raw owner name string alone.

    Returns 'unknown' | 'government' | 'institutional' | 'lender' | 'trust' |
    'llc' | 'corp' | 'individual' (the default), or None for no name.

    Order is deliberate:
      1. 'unknown' — a placeholder ("Private Owner") can't be anything else;
      2. 'government' — before corp, so "CHATT CITY OF A MUNICIPAL CORP" and
         "HAMILTON COUNTY & CHATT CITY OF" (which used to fall through to
         'individual' and collect the absentee bonus) are public bodies;
      3. 'institutional' — churches/HOAs often carry INC or TRS;
      4. 'lender' — bank/GSE REO holders;
      5. trust / llc / corp / individual exactly as before (corp now also
         catches PROPERTIES / PARTNERS / INVESTMENTS / REALTY / LLP names,
         77 parcels on the snapshot that were mislabelled 'individual').
    A name containing "LLC" is never government/institutional ("HAMILTON
    COUNTY HOMES LLC", "EVREN FOUNDATION LLC"), except the cell-tower site
    holders. Heirs/estate wording is deliberately NOT a class here:
    estate_parcels.py's classifier (with vetoes) is the single source of the
    estate signal, and enrich_assessor's probate owner-name fallback needs
    heirs-titled parcels to stay 'individual'. Not a substitute for real
    business-registry data.
    """
    if not owner_name or not owner_name.strip():
        return None
    name = " ".join(owner_name.upper().split())
    if PLACEHOLDER_OWNER_PATTERN.search(name):
        return "unknown"
    has_llc = "LLC" in name
    if not has_llc:
        if GOVERNMENT_PATTERN.search(name):
            return "government"
        if INSTITUTIONAL_PATTERN.search(name) or INSTITUTIONAL_NOT_FIRST.search(name):
            return "institutional"
    if INSTITUTIONAL_LLC_PATTERN.search(name):
        return "institutional"
    if LENDER_PATTERN.search(name):
        return "lender"
    if TRUST_PATTERN.search(name):
        return "trust"
    if has_llc:
        return "llc"
    if ENTITY_PATTERNS.search(name):
        return "corp"
    return "individual"


def normalize_address(address: str | None) -> str | None:
    """Loose normalization for absentee-owner comparison: uppercase,
    strip punctuation, collapse whitespace. Not a USPS-grade normalizer —
    won't catch every abbreviation variant (St vs Street), but catches the
    common case cheaply without an external service.
    """
    if not address:
        return None
    address = re.sub(r"[^\w\s]", "", address.upper())
    address = re.sub(r"\s+", " ", address).strip()
    return address or None


def is_absentee(situs_address: str | None, mailing_address: str | None) -> bool | None:
    """None means "can't tell" (one side missing) — the caller should treat
    that as unknown, not as False, so a thin record doesn't get scored as
    if it were confirmed owner-occupied.
    """
    situs_n = normalize_address(situs_address)
    mail_n = normalize_address(mailing_address)
    if situs_n is None or mail_n is None:
        return None
    return situs_n not in mail_n and mail_n not in situs_n


def parse_cents(raw: str | None) -> float | None:
    """Parse a zero-padded fixed-width integer field that represents a
    dollar amount with an implied 2 decimal places (this source's
    convention for money AMOUNTS — but NOT for its Assessment fields,
    which are whole dollars; see tax_delinquent.py for why that
    distinction matters and how it was verified).
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or not re.fullmatch(r"-?\d+", raw):
        return None
    return int(raw) / 100


def parse_whole_dollars(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or not re.fullmatch(r"-?\d+", raw):
        return None
    return float(int(raw))
