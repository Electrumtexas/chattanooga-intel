"""
Estate-owned parcels — a standing inventory of Hamilton County parcels whose
county owner record says title sits with a decedent's HEIRS, ESTATE, or an
EXECUTOR/ADMINISTRATOR. The owner of record is dead and title hasn't moved
to a living buyer or heir yet: one of the strongest motivated-seller signals,
and unlike the probate dockets it comes with a parcel AND a mailing address.
Stored in court_records as case_type='estate_owned' (one row per parcel).

Everything below was verified live on 2026-10-06 against
mapsdev.hamiltontn.gov's Live_Parcels/MapServer/0 (the layer
enrich_assessor.py already uses), not taken from the 2026-09-22 notes.

The layer and what a query costs
---------------------------------
  - ArcGIS 10.61, maxRecordCount 1000, pagination/orderBy supported,
    OBJECTID dense 1..169,662, returnCountOnly count 169,662. No auth.
  - LIKE is case-sensitive. Owner names are upper case except 316
    "Private Owner" (confidential owners) and 7 "Update in Progress"
    mid-reassessment placeholders; both are rejected outright.
  - A leading-wildcard LIKE is a full scan: ~1.3 s per clause server-side
    (1 clause 2.6 s, 2 clauses 4.2 s, 6 clauses 9.0 s). A single 69-clause
    OR timed out at 60 s three times. So the pre-filter is 4 queries of
    2-8 clauses (~10 s each) rather than one big OR.
  - For measuring precision/recall, every row's owner/mailing attributes were
    pulled once (170 requests, 328 s, 1.6 s apart) and the classifier was run
    over all 169,662 parcels. That full pull is NOT what this module does
    daily; it's how the pre-filter's recall was proven (below).

What the owner records actually contain (full-table counts)
-----------------------------------------------------------
  - HEIRS as a word: 220 in OWNERNAME1, 30 in OWNERNAME2. Never a surname.
    "HIERS" (1) IS a surname, so misspellings are not matched.
  - Decedent estates: 11 ("X EST", "X ESTATE", one "EXE OF ESTATE OF X").
    The word ESTATE appears 332 times, overwhelmingly "REAL ESTATE" companies
    and corporate "ATTN REAL ESTATE DEPT" lines; "ESTATES" (plural) is
    subdivisions/HOAs/LLCs (59).
  - Executor/administrator wording: 8 parcels. The earlier sweep's "~22 in
    OWNERNAME2" were mostly corporate "ATTN LEASE ADMIN"/"C/O REALTY
    ADMINISTRATION" lines. Three true hits live ONLY in the mailing street
    field — MASTNAME="C/O <name> EXECUTOR" with the street pushed to MALINE2
    — invisible to an owner-name-only search, so MASTNAME C/O/ATTN lines
    are swept too (1,465 rows).
  - Life estate: exactly ONE parcel says "LIFE ESTATE"; no L/E, REMAINDER,
    TOD, DECEASED/DEC'D anywhere. Bare "LE" (82 hits) is always a surname
    (LE, LE BLANC, LE MAY ...). Decision: life_estate is recognised but NOT
    stored — a life tenant is by definition alive, so it isn't "owner of
    record deceased", and the only pattern wide enough to find more is pure
    surname noise. Counted in notes as life_estate_excluded.
  - C/O lines: OWNERNAME2 'C/O%' is on 3,374 parcels and is not an estate
    signal by itself; it is only used as the contact person on a parcel that
    matched for another reason (on an estate parcel the C/O is usually the
    executor/heir handling it). 24 stored rows carry one.
  - POA / CONSERVATOR / GUARDIAN lines mean a LIVING owner with a fiduciary
    — rejected (living_owner_fiduciary), not an estate.

Classifier (classify(), pure) — server LIKE for recall, client rules for precision
---------------------------------------------------------------------------------
  Pre-filter (OWNERNAME1/OWNERNAME2): %HEIR% %ESTATE% '% EST' '% EST %' |
  %EXE% %EXR% %EXTR% %ADM% | %REP% %P/R% %DEC%, plus MASTNAME 'C/O%'/'ATTN%'.
  Then word-boundary rules per owner field (C/O tails cut off first):
    heirs           \\bHEIRS?\\b on OWNERNAME1 (or in a C/O line).
    heirs_coowner   HEIRS only on OWNERNAME2 while OWNERNAME1 is a different
                    living owner — a deceased co-owner's share (typically a
                    spouse: "SMITH JOHN / SMITH MARY HEIRS").
    estate          "ESTATE OF"/"EST OF", or EST/ESTATE closing a person's
                    name (optionally before C/O / ETAL / &), or DECEASED
                    wording — after deleting REAL ESTATE, LIFE ESTATE and
                    ESTATE TRUST/PLANNING/HOLDINGS phrases. "ESTATES" can't
                    match (\\b). Also "ESTATE OF" in a C/O/ATTN line.
    executor_admin  EXECUTOR/EXECUTRIX/EXEC/EXE/EXR/EXTR/ADMINISTRATOR(TRIX)/
                    ADMR/ADMX/CO-ADM/PERSONAL REP/PERS REP/P/R/ADMIN CTA, in
                    a name field or a C/O line. Bare ADMIN never counts.
  Vetoes: HEIRS/ESTATE on an organisation or trust name (LLC, INC, CHURCH,
  CITY, CEMETERY, TRUST, TR, TESTAMENTARY, ...) — title already moved into
  that vehicle; fiduciary words when OWNERNAME1 is an organisation; C/O lines
  addressed to a department (LEASE, TAX, DEPT, DIRECTOR, PUBLIC WORKS, ...);
  heirs holding a leftover share beside an LLC owner (2 parcels, investor
  buy-outs — the mailing address reaches the LLC, not heirs). Priority when
  several fire: executor_admin > estate > heirs > heirs_coowner.

Measured (live, 2026-10-06): 254 stored = heirs 219, heirs_coowner 16,
estate 11, executor_admin 8; 2,361 pre-filter candidates.
  - Precision, random sample of 60 accepted (seeded): 59 clear true
    positives, 1 uncertain (heirs_coowner beside unrelated buyers after a paid
    2024 transfer — maybe a partial buy-out, maybe a stale name). Every
    estate (11), executor_admin (8) and heirs_coowner (16) row was also
    reviewed by hand: all true except that one uncertain row; among heirs,
    3 of 219 still read HEIRS despite a paid 2023-2025 SALE1 (uncertain —
    possibly a partial buy-out — kept).
    Estimate ~98% certain / ~100% plausible, per type: heirs ~99%,
    heirs_coowner ~94-100%, estate 100%, executor_admin 100%.
  - Recall, random sample of 40 near-misses (wording hits the classifier
    rejected): 0 were decedent estates (REAL ESTATE companies, ESTATES
    LLCs/HOAs, ATTN-ADMIN corporate lines, churches, POA). The whole
    564-row near-miss pool, reviewed by category, holds only the deliberate
    exclusions above (1 life estate, 1 testamentary trust, 1 trustee's
    heirs, 1 heirs' cemetery, 2 heirs-beside-LLC, living-owner fiduciaries).
  - Pre-filter recall: 0 of the 254 parcels the classifier accepts on the
    full 169,662-row pull fall outside the LIKE pre-filter, and the live
    pre-filter returned the same 2,361 candidates the offline simulation
    predicted. What NO wording rule can see: a decedent's parcel still titled
    in the decedent's own name — that's what the probate cross-reference
    (below) and probate_dockets.py are for.

Storage (court_records, one row per parcel)
-------------------------------------------
dedupe_key "estate_owned:<parcel_id>"; source_portal 'county_gis_owner_records';
parcel_id from MAP/GROUP_/PARCEL with the PARCEL padding collapsed (see
canonical_parcel_id — 3 of today's parcels are condo units whose raw value
would otherwise give "117O-A-011   C038"), resolution_method
'native_parcel_id'; case_number = TAX_MAP_NO exactly as the county stores it
(padding intact, so it can be queried back); raw_owner_name/party_names =
"OWNERNAME1 / OWNERNAME2" (party_names also gets "/ C/O <name>" when the
contact sits in the mailing field); raw_address = situs ADDRESS; amount NULL;
source_url = the Assessor card; description = "<match_type>: owner record
reads '<wording>' — contact person (C/O): ... — mail: ... — land use ... —
last recorded transfer ... [— probate case YY-P-NNN]". The parcel itself is
upserted through enrich_assessor.upsert_parcel_from_gis() (owner, mailing,
lat/long centroid, land use, values).

filing_date stays NULL — investigated, not invented
---------------------------------------------------
The layer's only dates are SALE1DATE..SALE4DATE (+ consideration, book/page).
SALE1 is the latest recorded instrument: it matched the Assessor card's
"Current Property Sales Information" date, price and book/page exactly on 6
sampled heirs parcels. 207/219 heirs parcels have a $0 SALE1 since 2020 (vs
20.3% county-wide), so the HEIRS label clearly tracks a recent $0
instrument — but on 3 of those 6 cards the grantor of that instrument
already read "... HEIRS", i.e. it was a later heir-to-heir paper, not the
moment the estate took title; and the 11 "X EST" parcels' SALE1 dates are
1904-1971 (the decedent's own purchase, or 1900-01-01 placeholders). No field
genuinely means "when the estate came to hold title", so filing_date is NULL
and the description carries "last recorded transfer <date> $<amount>" as
context instead.

Lifecycle
---------
Each full sweep is the complete current inventory. A previously active row
the sweep didn't return is re-queried by TAX_MAP_NO and re-classified on fresh
attributes FIRST (so a pre-filter gap can't masquerade as a settled estate —
counted as rescued_by_requery); only if it truly no longer matches is it
flipped to case_type='estate_owned_resolved' with "RESOLVED <date>: ..."
appended (or "parcel no longer in county GIS" if it vanished). Rows are never
deleted, and the parcel's fresh owner data is upserted. A resolved parcel that
matches again flips back to 'estate_owned' ("re-flagged <date>"). scoring.py
should give estate_owned_resolved 0 and build_unified.py should exclude it,
exactly like tax_sale_resolved (proposed in the build report — out of this
file's scope). Verified on a scratch DB: excluding one real parcel (a condo
unit, found again through its padded TAX_MAP_NO) + injecting a non-matching
parcel and a non-existent one resolved all 3 (1 parcel_gone);
the next normal run flipped the real one back; dropping a parcel from the
sweep result alone was rescued by the re-query.

Safety: nothing is written unless the health check passes (count between
MIN_HEALTHY_PARCEL_COUNT and 300k), every pre-filter page arrived, the page
cap wasn't hit, and the sweep found at least max(50, 50% of the rows
currently tracked). Each of those aborts logs status='error' and exits 1.

Probate cross-reference
-----------------------
probate_dockets.py stores the decedent as "LAST FIRST MIDDLE [SUFFIX]". For
each estate parcel the estate wording is stripped from the owner field that
carried it ("DOE JOHN A & MARY B HEIRS" -> DOE JOHN A, DOE MARY B;
"ESTATE OF JOHN A SMITH" -> SMITH JOHN A; a C/O "ESTATE OF ..." too) and
compared on same last + same first name with a compatible middle initial and
suffix (Mc/Mac/De/Van... prefixes joined). Exactly one matching probate case
-> "probate case <docket>" goes into the estate row's description; and if
that case also matches no other estate parcel and its parcel_id IS NULL, it
gets this parcel_id with resolution_method='owner_name_fallback' via
upsert_record(keep_existing=("parcel_id",)). Measured on today's 60 probate
rows: 1 link (an exact decedent match the enrich_assessor owner fallback
missed because the GIS owner reads "<decedent> HEIRS"), 0 ambiguous. Like
enrich_assessor's own fallback, that upsert passes touch_last_seen=False, so
the backfill does not move the probate row's last_seen_at.

Runtime and cadence
-------------------
A full run is 9 requests (health count, 3 single-page owner queries, 2 pages
of MASTNAME C/O lines, 3 geometry batches) in ~42-46 s, so it sweeps every
run (daily). ESTATE_SWEEP_INTERVAL_DAYS (default 0) can throttle it to e.g.
weekly via source_state; a skip day still logs the tracked active count so
quality_check's trailing baseline stays meaningful. ESTATE_MAX_PAGES (default
40) caps total requests per run. Requests are >=1.5 s apart with a descriptive
User-Agent, 3 retries with backoff. enrich_assessor's _post_query isn't reused
(no User-Agent, 0.05 s pause — fine for its indexed IN lookups, not for these
full scans); its field list, escaping, TAX_MAP_NO conversion and
upsert_parcel_from_gis() are. HC_GIS_BASE (read by enrich_assessor) moves both.

stdout prints counts only — owner names and addresses go to the DB, never the
public Actions log.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from db import get_connection, get_state, log_scrape, now_iso, set_state, upsert_record
from enrich_assessor import (
    MIN_HEALTHY_PARCEL_COUNT,
    PARCEL_LAYER,
    QUERY_FIELDS,
    _PLACEHOLDER_OWNER_RE,
    _esc,
    parcel_id_to_tax_map_no,
    upsert_parcel_from_gis,
)
from parcel_utils import assessor_card_url, format_parcel_id

SOURCE = "estate_parcels"
SOURCE_PORTAL = "county_gis_owner_records"
CASE_TYPE_ACTIVE = "estate_owned"
CASE_TYPE_RESOLVED = "estate_owned_resolved"
DEDUPE_PREFIX = "estate_owned:"
STATE_KEY_LAST_SWEEP = "estate_parcels:last_full_sweep"

REQUEST_HEADERS = {
    "User-Agent": "chattanooga-intel/1.0 (distressed-property lead research; contact via github.com/Electrumtexas/chattanooga-intel)"
}
REQUEST_TIMEOUT = 90            # an 8-clause leading-wildcard LIKE query takes ~10 s server-side; leave headroom
REQUEST_DELAY_SECONDS = 1.5     # minimum gap between any two requests to the county host
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5.0
PAGE_SIZE = 1000                # the layer's maxRecordCount
ID_BATCH_SIZE = 100

MAX_PAGES = int(os.environ.get("ESTATE_MAX_PAGES", "40"))                    # hard cap on requests per run (a full run uses 9-10)
SWEEP_INTERVAL_DAYS = float(os.environ.get("ESTATE_SWEEP_INTERVAL_DAYS", "0"))  # 0 = full sweep every run (it's cheap — see docstring)
MAX_HEALTHY_PARCEL_COUNT = 300_000   # live count is 169,662; far above this means the layer/service changed shape
MIN_EXPECTED_MATCHES = 50            # live inventory is ~250; fewer than this means owner names were blanked/broken
MIN_RETAINED_RATIO = 0.5             # a sweep that finds < 50% of the currently-tracked inventory is treated as broken

EXTRA_FIELDS = "OBJECTID,SALE1DATE,SALE1CONSD"
SWEEP_FIELDS = f"{EXTRA_FIELDS},{QUERY_FIELDS}"

# Server-side pre-filter: recall only (precision comes from classify()).
# Each leading-wildcard LIKE is a full scan of ~170k rows costing ~1.3 s on
# the county server, so clauses are grouped into a few modest queries
# instead of one giant OR (a 69-clause OR timed out at 60 s, three times).
_OWNER_PATTERN_GROUPS = (
    ("%HEIR%", "%ESTATE%", "% EST", "% EST %"),
    ("%EXE%", "%EXR%", "%EXTR%", "%ADM%"),
    ("%REP%", "%P/R%", "%DEC%"),
)
PREFILTER_WHERES = tuple(
    " OR ".join(f"{field} LIKE '{pat}'" for pat in group for field in ("OWNERNAME1", "OWNERNAME2"))
    for group in _OWNER_PATTERN_GROUPS
) + ("MASTNAME LIKE 'C/O%' OR MASTNAME LIKE 'ATTN%'",)


# --------------------------------------------------------------------------
# Classifier — pure functions, no I/O (see module docstring for the rules
# and the measured precision behind each one).
# --------------------------------------------------------------------------

MATCH_PRIORITY = ("executor_admin", "estate", "heirs", "heirs_coowner")
STORED_MATCH_TYPES = frozenset(MATCH_PRIORITY)

_HEIRS_RE = re.compile(r"\bHEIRS?\b")
_ESTATE_OF_RE = re.compile(r"\bEST(?:ATE)? OF\b")
# A standalone EST/ESTATE closing an owner's name, optionally followed by a
# C/O line or an ET AL/& continuation — "SMITH JOHN A EST", "SMITH JOHN
# ESTATE C/O ...". "ESTATES" (plural) can never match: \b stops at the S.
_TRAILING_ESTATE_RE = re.compile(r"\b(EST|ESTATE)\b(?=\s*(?:$|C/O\b|ET ?AL\b|&))")
_DECEASED_RE = re.compile(r"\b(DECEASED|DEC'D|DECD)\b")
_EXECUTOR_RE = re.compile(
    r"\b(EXECUTORS?|EXECUTRIX|EXECUTRICES|CO-EXECUTOR|CO-EXECUTRIX|EXEC|EXECS|EXE|EXRS?|EXRX|EXTRX?"
    r"|ADMINISTRATORS?|ADMINISTRATRIX|ADMRS?|ADMRX|ADMX|CO-ADMR?|CO-ADMINISTRATOR|ADMIN CTA|ADMINISTRATOR CTA"
    r"|PERSONAL REPRESENTATIVES?|PERS REP|PER REP|P/R)\b"
)
_LIFE_ESTATE_RE = re.compile(r"\bLIFE (ESTATE|EST|INTEREST|INT|TENANT)\b|\bL/E\b")
# Phrases where EST/ESTATE is part of a business, trust or land-use term,
# never a decedent's estate: "REAL ESTATE", "LIFE ESTATE", "ESTATE TRUST" ...
_ESTATE_NOT_DECEDENT_RE = re.compile(
    r"\b(REAL|LIFE) EST(ATE)?\b|\bEST(ATE)? (TRUST|TR|PLANNING|HOLDINGS?|SERVICES|ASSETS|AGENT)\b"
)
# A fiduciary for a LIVING owner (power of attorney, conservator, guardian) —
# the opposite of an estate. Counted as a near-miss, never stored.
_LIVING_FIDUCIARY_RE = re.compile(r"\b(POA|CONSERVATOR|GUARDIAN|ATTY IN FACT|ATTORNEY IN FACT)\b")
# Owner-name tokens that mark an organisation rather than a natural person,
# and (separately) a trust. Checked per name field, so "SMITH JOHN HEIRS"
# passes but "SMITH HEIRS PROPERTY LLC" or "DOE ESTATE TRUST" doesn't.
# Bare "CO" is deliberately absent: it would veto "CO-ADM"/"CO TRS".
_ORG_RE = re.compile(
    r"\b(LLC|L L C|LCC|INC|CORP|CORPORATION|COMPANY|LP|LLP|LTD|GP|PARTNERS|PARTNERSHIP|HOLDINGS?|PROPERTIES"
    r"|INVESTMENTS?|INVESTORS|REALTY|ASSOC|ASSOCIATION|ASSN|HOA|HOMEOWNERS|CHURCH|MINISTRIES|TABERNACLE|FELLOWSHIP"
    r"|CONGREGATION|BAPTIST|METHODIST|CITY|COUNTY|STATE|UNITED STATES|AUTHORITY|BOARD|BANK|FOUNDATION|UNIVERSITY"
    r"|COLLEGE|SCHOOL|HOSPITAL|CEMETERY|DEPT|DEPARTMENT|INSURANCE|FUND|CLUB|LODGE)\b"
)
_TRUST_RE = re.compile(r"\b(TRUST|TR|TRS|TRUSTEES?|TESTAMENTARY)\b")
# C/O or ATTN lines that address a company department ("ATTN LEASE ADMIN",
# "C/O ADMINISTRATOR PUBLIC WORKS") rather than a person handling an estate.
_CORPORATE_CO_RE = re.compile(
    r"\b(LEASE|PROP|PROPERTY|REALTY|TAX|DEPT|DEPARTMENT|OFFICE|DIRECTOR|PUBLIC WORKS|LOAN|CHIEF|SERVICES|CITY|COUNTY)\b"
)
_CO_PREFIX_RE = re.compile(r"^(C/O|ATTN:?)\s*")


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def canonical_parcel_id(attrs: dict) -> str | None:
    """parcel_id from GIS attributes in the canonical (Trustee/tax_sale)
    form. Condo/unit parcels carry internal padding in PARCEL on this layer
    ("011   C038", 5,397 parcels county-wide); format_parcel_id() only trims
    the ends, which would give "117O-A-011   C038" instead of the
    "117O-A-011C038" every other source uses — so the padding is collapsed
    first (verified: 3 of today's estate parcels are such units).
    """
    return format_parcel_id(attrs.get("MAP"), attrs.get("GROUP_"), re.sub(r"\s+", "", attrs.get("PARCEL") or ""))


def _raw_tax_map_no(attrs: dict) -> str | None:
    """TAX_MAP_NO exactly as the county stores it (only the ends trimmed):
    its internal spacing is significant — a blank group is a double space and
    unit suffixes are padded — so it must not be whitespace-collapsed if it's
    going to be queried back with TAX_MAP_NO IN (...).
    """
    value = str(attrs.get("TAX_MAP_NO") or "").strip()
    return value or None


def _co_line(attrs: dict) -> tuple[str | None, str | None]:
    """(where, text) of the parcel's care-of / attention line, if any. On
    this layer a C/O shows up in three places (all seen live): OWNERNAME2
    ("C/O JANE DOE"), mid-OWNERNAME1 ("SMITH JOHN EST C/O ..."), or the
    mailing street field MASTNAME ("C/O JANE DOE EXECUTOR", with the real
    street pushed down into MALINE2 and MASTNUM blank).
    """
    n1, n2, mast = _norm(attrs.get("OWNERNAME1")), _norm(attrs.get("OWNERNAME2")), _norm(attrs.get("MASTNAME"))
    if _CO_PREFIX_RE.match(n2):
        return "OWNERNAME2", _CO_PREFIX_RE.sub("", n2) or None
    if " C/O " in f" {n1} ":
        return "OWNERNAME1", n1.split("C/O", 1)[1].strip() or None
    if _CO_PREFIX_RE.match(mast) and not _norm(attrs.get("MASTNUM")):
        return "MASTNAME", _CO_PREFIX_RE.sub("", mast) or None
    return None, None


def classify(attrs: dict) -> dict:
    """Returns {"match_type", "wording", "reject", "contact", "contact_source",
    "decedent_field"}. match_type is one of MATCH_PRIORITY (stored),
    "life_estate" (recognised but deliberately not stored — see docstring),
    or None. `reject` names the rule that vetoed a wording hit, so near-misses
    can be counted and reviewed. `decedent_field` says which owner field the
    estate wording sat on (used by the probate cross-reference).
    """
    n1, n2 = _norm(attrs.get("OWNERNAME1")), _norm(attrs.get("OWNERNAME2"))
    co_where, co_text = _co_line(attrs)
    result = {
        "match_type": None, "wording": [], "reject": None,
        "contact": None, "contact_source": None, "decedent_field": None,
    }

    if _PLACEHOLDER_OWNER_RE.search(n1) or _PLACEHOLDER_OWNER_RE.search(n2) or n1 == "PRIVATE OWNER":
        result["reject"] = "placeholder_owner"
        return result

    # Owner-name fields with any C/O tail cut off, so a contact's own name
    # can't make the OWNER look like an entity or an estate.
    own1 = n1.split(" C/O ", 1)[0].strip() if " C/O " in f" {n1} " else n1
    own2 = "" if _CO_PREFIX_RE.match(n2) else n2
    owner1_is_org = bool(_ORG_RE.search(own1))

    found: dict[str, list[str]] = {}
    decedent_field: dict[str, str] = {}
    rejects: list[str] = []

    for fname, text in (("OWNERNAME1", own1), ("OWNERNAME2", own2)):
        if not text:
            continue
        # HEIRS/ESTATE wording on an organisation or trust name means title
        # has already moved into that vehicle — not an open estate.
        entity = bool(_ORG_RE.search(text) or _TRUST_RE.search(text))
        m = _LIFE_ESTATE_RE.search(text)
        if m:
            found.setdefault("life_estate", []).append(m.group(0))
        m = _HEIRS_RE.search(text)
        if m:
            if entity:
                rejects.append("heirs_on_entity_or_trust")
            elif fname == "OWNERNAME1" or not own1:
                found.setdefault("heirs", []).append(m.group(0))
                decedent_field.setdefault("heirs", fname)
            elif owner1_is_org:
                # An LLC owns the parcel and a decedent's heirs hold a
                # leftover fractional share (seen live after investor
                # buy-outs) — the mailing address reaches the LLC, not heirs.
                rejects.append("heirs_share_with_org_owner")
            else:
                found.setdefault("heirs_coowner", []).append(m.group(0))
                decedent_field.setdefault("heirs_coowner", fname)
        cleaned = _ESTATE_NOT_DECEDENT_RE.sub(" ", text)
        m = _ESTATE_OF_RE.search(cleaned) or _TRAILING_ESTATE_RE.search(cleaned) or _DECEASED_RE.search(cleaned)
        if m:
            if entity:
                rejects.append("estate_on_entity_or_trust")
            else:
                found.setdefault("estate", []).append(m.group(0))
                decedent_field.setdefault("estate", fname)
        elif _ESTATE_NOT_DECEDENT_RE.search(text) and not _LIFE_ESTATE_RE.search(text):
            rejects.append("real_estate_or_estate_trust")
        m = _EXECUTOR_RE.search(text)
        if m:
            # An executor who is also a trustee ("... EXE & TRUSTEE") is still
            # an estate fiduciary, so only an ORGANISATION owner vetoes this.
            if owner1_is_org:
                rejects.append("fiduciary_word_on_org")
            else:
                found.setdefault("executor_admin", []).append(m.group(0))

    if co_text:
        if _LIVING_FIDUCIARY_RE.search(co_text):
            rejects.append("living_owner_fiduciary")
        else:
            m_exec = _EXECUTOR_RE.search(co_text)
            m_est = _ESTATE_OF_RE.search(co_text) or _DECEASED_RE.search(co_text)
            m_heirs = _HEIRS_RE.search(co_text)
            if (m_exec or m_est or m_heirs) and (owner1_is_org or _CORPORATE_CO_RE.search(co_text)):
                rejects.append("corporate_co_line")
            elif m_exec:
                found.setdefault("executor_admin", []).append(f"{m_exec.group(0)} (C/O line)")
                decedent_field.setdefault("executor_admin", "OWNERNAME1")
            elif m_est:
                found.setdefault("estate", []).append(f"{m_est.group(0)} (C/O line)")
            elif m_heirs:
                found.setdefault("heirs", []).append(f"{m_heirs.group(0)} (C/O line)")
                decedent_field.setdefault("heirs", "OWNERNAME1")
        result["contact"] = co_text
        result["contact_source"] = co_where

    for mt in MATCH_PRIORITY:
        if mt in found:
            result["match_type"] = mt
            break
    else:
        if "life_estate" in found:
            result["match_type"] = "life_estate"
    result["wording"] = list(dict.fromkeys(w for mt in (*MATCH_PRIORITY, "life_estate") for w in found.get(mt, [])))
    if result["match_type"]:
        result["decedent_field"] = decedent_field.get(result["match_type"])
    elif rejects:
        result["reject"] = rejects[0]
    return result


# --------------------------------------------------------------------------
# GIS access — own polite client (descriptive UA, >=1.5 s spacing, retries
# with backoff, a per-run page cap). enrich_assessor's _post_query sends no
# User-Agent and pauses only 0.05 s, so it isn't reused for these heavier
# full-scan LIKE queries; its field list, escaping, TAX_MAP_NO conversion and
# parcel upsert are.
# --------------------------------------------------------------------------

class PageCapReached(RuntimeError):
    pass


class GisClient:
    def __init__(self, max_pages: int = MAX_PAGES):
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)
        self.max_pages = max_pages
        self.requests = 0
        self._last = 0.0

    def query(self, params: dict, count_against_cap: bool = True) -> dict:
        if count_against_cap and self.requests >= self.max_pages:
            raise PageCapReached(f"ESTATE_MAX_PAGES={self.max_pages} reached")
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            wait = REQUEST_DELAY_SECONDS - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.post(f"{PARCEL_LAYER}/query", data={"f": "json", **params}, timeout=REQUEST_TIMEOUT)
                self._last = time.monotonic()
                self.requests += 1
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                self._last = time.monotonic()
                last_exc = exc
            else:
                if isinstance(data, dict) and "error" in data:
                    last_exc = RuntimeError(f"ArcGIS error: {data['error']}")
                else:
                    return data
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def count(self, where: str) -> int:
        return int(self.query({"where": where, "returnCountOnly": "true"}, count_against_cap=False).get("count", 0))

    def fetch_where(self, where: str, out_fields: str) -> list[dict]:
        """All attribute rows for `where`, paged by resultOffset (ordered by
        OBJECTID so pages are stable). No geometry — fetched later, only for
        the parcels the classifier accepts.
        """
        rows: list[dict] = []
        offset = 0
        while True:
            data = self.query({
                "where": where, "outFields": out_fields, "returnGeometry": "false",
                "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
            })
            feats = data.get("features", [])
            rows.extend(f["attributes"] for f in feats)
            if not data.get("exceededTransferLimit") and len(feats) < PAGE_SIZE:
                return rows
            if not feats:
                return rows
            offset += len(feats)

    def fetch_geometry(self, object_ids: list[int]) -> dict[int, dict]:
        geoms: dict[int, dict] = {}
        for i in range(0, len(object_ids), ID_BATCH_SIZE):
            batch = object_ids[i:i + ID_BATCH_SIZE]
            data = self.query({
                "where": f"OBJECTID IN ({','.join(str(int(o)) for o in batch)})",
                "outFields": "OBJECTID", "returnGeometry": "true", "outSR": "4326",
            })
            for f in data.get("features", []):
                geoms[f["attributes"]["OBJECTID"]] = f.get("geometry")
        return geoms

    def fetch_by_tax_map_no(self, tax_map_nos: list[str]) -> list[dict]:
        feats: list[dict] = []
        for i in range(0, len(tax_map_nos), ID_BATCH_SIZE):
            batch = tax_map_nos[i:i + ID_BATCH_SIZE]
            quoted = ",".join(f"'{_esc(t)}'" for t in batch)
            data = self.query({
                "where": f"TAX_MAP_NO IN ({quoted})", "outFields": SWEEP_FIELDS,
                "returnGeometry": "true", "outSR": "4326",
            })
            feats.extend(data.get("features", []))
        return feats


# --------------------------------------------------------------------------
# Row building
# --------------------------------------------------------------------------

def _epoch_ms_to_date(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=float(value))).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _mail_line(attrs: dict, contact_source: str | None) -> str | None:
    """Mailing address for the description. When MASTNAME holds a C/O line
    (contact_source == 'MASTNAME') it's the contact, not part of the street,
    so the street comes from MALINE2 alone.
    """
    if contact_source == "MASTNAME":
        street_parts = [attrs.get("MALINE2")]
    else:
        street_parts = [attrs.get(f) for f in ("MASTNUM", "MADIRPFX", "MASTNAME", "MATYPESFX", "MALINE2")]
    street = " ".join(_norm(p) for p in street_parts if _norm(p))
    city_state = " ".join(_norm(attrs.get(f)) for f in ("MACITY", "MASTATE") if _norm(attrs.get(f)))
    tail = " ".join(p for p in (city_state, _norm(attrs.get("MAZIP"))) if p)
    line = ", ".join(p for p in (street, tail) if p)
    return line or None


def build_record(pid: str, attrs: dict, cls: dict) -> dict:
    n1, n2 = _norm(attrs.get("OWNERNAME1")), _norm(attrs.get("OWNERNAME2"))
    raw_owner = f"{n1} / {n2}" if n2 else n1
    party_names = raw_owner
    if cls["contact"] and cls["contact_source"] == "MASTNAME":
        party_names = f"{raw_owner} / C/O {cls['contact']}"

    parts = [f"{cls['match_type']}: owner record reads " + ", ".join(f"'{w}'" for w in cls["wording"])]
    if cls["contact"]:
        parts.append(f"contact person (C/O): {cls['contact']}")
    mail = _mail_line(attrs, cls["contact_source"])
    if mail:
        parts.append(f"mail: {mail}")
    if _norm(attrs.get("CURRENTUSE")):
        parts.append(f"land use {_norm(attrs.get('CURRENTUSE'))}")
    sale_date = _epoch_ms_to_date(attrs.get("SALE1DATE"))
    if sale_date and sale_date > "1900-01-01":
        consd = attrs.get("SALE1CONSD")
        amount = f" ${consd:,.0f}" if isinstance(consd, (int, float)) else ""
        parts.append(f"last recorded transfer {sale_date}{amount}")

    return {
        "dedupe_key": f"{DEDUPE_PREFIX}{pid}",
        "parcel_id": pid,
        "case_number": _raw_tax_map_no(attrs),
        "raw_owner_name": raw_owner,
        "party_names": party_names,
        "raw_address": _norm(attrs.get("ADDRESS")) or None,
        "description_parts": parts,
        "match_type": cls["match_type"],
    }


def _upsert_estate_row(conn, rec: dict, description: str) -> None:
    upsert_record(
        conn, "court_records", rec["dedupe_key"],
        keep_existing=("parcel_id", "resolution_method"),
        parcel_id=rec["parcel_id"],
        source_portal=SOURCE_PORTAL,
        case_number=rec["case_number"],
        case_type=CASE_TYPE_ACTIVE,
        filing_date=None,   # no field on the layer means "when the estate took title" — see docstring
        party_names=rec["party_names"],
        amount=None,
        raw_address=rec["raw_address"],
        raw_owner_name=rec["raw_owner_name"],
        resolution_method="native_parcel_id",
        source_url=assessor_card_url(rec["parcel_id"]),
        description=description,
    )


# --------------------------------------------------------------------------
# Probate cross-reference
# --------------------------------------------------------------------------

_SUFFIXES = {"JR", "SR", "II", "III", "IV"}   # not "V": on this layer a trailing V is a middle initial ("DOE MARY V")
_SURNAME_PREFIXES = {"MC", "MAC", "DE", "DEL", "DELA", "LA", "VAN", "VON", "DI", "DU", "ST", "O"}
_STRIP_WORDS_RE = re.compile(
    r"\b(HEIRS?|OF|ESTATE|EST|ETAL|ET AL|ET UX|UNKNOWN|THE|DECEASED|DEC'D|DECD|AND)\b|" + _EXECUTOR_RE.pattern
)


def _parse_person(tokens: list[str], natural_order: bool = False) -> tuple[str, str, str | None, str | None] | None:
    """(last, first, middle_initial, suffix) from name tokens. Assessor and
    probate_dockets.normalize_decedent_name() both use "LAST FIRST MIDDLE
    [SUFFIX]"; an "ESTATE OF JOHN A SMITH" phrase is natural order instead.
    """
    tokens = [t for t in tokens if t]
    suffix = None
    if tokens and tokens[-1] in _SUFFIXES:
        suffix = tokens.pop()
    if len(tokens) < 2:
        return None
    if natural_order:
        tokens = [tokens[-1], *tokens[:-1]]
    last = tokens[0]
    rest = tokens[1:]
    if last in _SURNAME_PREFIXES and len(rest) >= 2:
        last, rest = last + rest[0], rest[1:]
    first = rest[0]
    middle = rest[1][0] if len(rest) > 1 else None
    return last, first, middle, suffix


def _decedent_candidates(attrs: dict, cls: dict) -> list[tuple]:
    """Possible decedent names on an estate parcel: the person(s) on the
    owner field that carried the estate wording, with that wording removed
    ("DOE JOHN A & MARY B HEIRS" -> DOE JOHN A, DOE MARY B), plus
    the natural-order name after any "ESTATE OF".
    """
    n1, n2 = _norm(attrs.get("OWNERNAME1")), _norm(attrs.get("OWNERNAME2"))
    n1 = n1.split(" C/O ", 1)[0]
    texts = {"OWNERNAME1": n1, "OWNERNAME2": "" if _CO_PREFIX_RE.match(n2) else n2}
    out: list[tuple] = []
    for text in (*texts.values(), cls.get("contact") or ""):
        m = re.search(r"\bEST(?:ATE)? OF (.+)$", text)
        if m:
            p = _parse_person(re.sub(r"[^A-Z' ]", " ", m.group(1)).split(), natural_order=True)
            if p:
                out.append(p)
    field = cls.get("decedent_field")
    if field and texts.get(field) and not re.search(r"\bEST(?:ATE)? OF\b", texts[field]):
        text = re.sub(r"[^A-Z'&/ -]", " ", texts[field])
        text = _STRIP_WORDS_RE.sub(" ", text)
        people = [p.split() for p in re.split(r"\s*&\s*|\s*/\s*", text) if p.strip()]
        if people:
            first = _parse_person(people[0])
            if first:
                out.append(first)
                for other in people[1:]:
                    # "& MARY B" inherits the first person's surname; a
                    # 3+-token "& ROE JANE K" is read as its own full name.
                    p = _parse_person(other) if len(other) >= 3 else _parse_person([first[0], *other])
                    if p:
                        out.append(p)
    return list(dict.fromkeys(out))


def _names_compatible(a: tuple, b: tuple) -> bool:
    if a[0] != b[0] or a[1] != b[1]:
        return False
    if a[2] and b[2] and a[2] != b[2]:
        return False
    if a[3] and b[3] and a[3] != b[3]:
        return False
    return True


def link_probate(conn, accepted: dict[str, dict]) -> tuple[dict[str, str], dict]:
    """Returns ({parcel_id: probate docket}, stats). See module docstring."""
    stats = {"probate_rows": 0, "estate_parcels_with_one_match": 0, "ambiguous": 0, "parcel_ids_filled": 0}
    probate = []
    for r in conn.execute(
        "SELECT dedupe_key, case_number, raw_owner_name, parcel_id FROM court_records "
        "WHERE case_type = 'probate' AND raw_owner_name IS NOT NULL"
    ):
        p = _parse_person(_norm(r["raw_owner_name"]).replace(".", "").replace(",", "").split())
        if p:
            probate.append((p, r))
    stats["probate_rows"] = len(probate)

    links: dict = {}   # parcel_id -> matching probate court_records row
    by_case: dict[str, list[str]] = {}
    for pid, item in accepted.items():
        cands = _decedent_candidates(item["attrs"], item["cls"])
        hits = {r["dedupe_key"]: r for p, r in probate for c in cands if _names_compatible(c, p)}
        if len(hits) == 1:
            row = next(iter(hits.values()))
            links[pid] = row
            by_case.setdefault(row["dedupe_key"], []).append(pid)
        elif len(hits) > 1:
            stats["ambiguous"] += 1
    stats["estate_parcels_with_one_match"] = len(links)

    for case_key, pids in by_case.items():
        if len(pids) != 1:
            continue  # decedent owns several flagged parcels: mention on each, but don't pick one for the probate row
        current =conn.execute("SELECT parcel_id FROM court_records WHERE dedupe_key = ?", (case_key,)).fetchone()
        if current and current["parcel_id"] is None:
            upsert_record(
                conn, "court_records", case_key,
                keep_existing=("parcel_id",),
                touch_last_seen=False,  # a backfill, not a sighting of the docket row
                parcel_id=pids[0],
                resolution_method="owner_name_fallback",
            )
            stats["parcel_ids_filled"] += 1
    return {pid: row["case_number"] for pid, row in links.items()}, stats


# --------------------------------------------------------------------------
# Sweep + lifecycle
# --------------------------------------------------------------------------

def sweep(client: GisClient) -> tuple[dict[str, dict], dict]:
    """Run the pre-filter queries, classify every candidate, and return
    ({parcel_id: {"attrs", "cls", "object_id"}}, stats).
    """
    stats = {"candidates": 0, "by_match_type": {}, "rejects": {}, "life_estate_excluded": 0, "duplicate_parcel_ids": 0}
    candidates: dict[int, dict] = {}
    for where in PREFILTER_WHERES:
        for attrs in client.fetch_where(where, SWEEP_FIELDS):
            candidates[attrs["OBJECTID"]] = attrs
    stats["candidates"] = len(candidates)

    accepted: dict[str, dict] = {}
    for oid in sorted(candidates):
        attrs = candidates[oid]
        cls = classify(attrs)
        mt = cls["match_type"]
        if mt in STORED_MATCH_TYPES:
            pid = canonical_parcel_id(attrs)
            if not pid:
                continue
            if pid in accepted:
                stats["duplicate_parcel_ids"] += 1
                continue
            accepted[pid] = {"attrs": attrs, "cls": cls, "object_id": oid}
            stats["by_match_type"][mt] = stats["by_match_type"].get(mt, 0) + 1
        elif mt == "life_estate":
            stats["life_estate_excluded"] += 1
        elif cls["reject"]:
            stats["rejects"][cls["reject"]] = stats["rejects"].get(cls["reject"], 0) + 1
    return accepted, stats


def _active_rows(conn) -> dict[str, dict]:
    return {
        r["parcel_id"]: dict(r)
        for r in conn.execute(
            "SELECT dedupe_key, parcel_id, case_number, case_type, description FROM court_records "
            "WHERE source_portal = ? AND case_type = ?",
            (SOURCE_PORTAL, CASE_TYPE_ACTIVE),
        )
    }


def _count_rows(conn, case_type: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM court_records WHERE source_portal = ? AND case_type = ?", (SOURCE_PORTAL, case_type)
    ).fetchone()[0]


def resolve_missing(conn, client: GisClient, accepted: dict[str, dict], today: str) -> dict:
    """Previously-active rows the sweep didn't return. Each is re-queried by
    TAX_MAP_NO and re-classified on fresh attributes first, so a pre-filter
    gap can never masquerade as a settled estate; only a parcel that truly no
    longer matches (or no longer exists) is flipped to estate_owned_resolved.
    """
    stats = {"checked": 0, "resolved": 0, "rescued_by_requery": 0, "parcel_gone": 0}
    missing = {pid: row for pid, row in _active_rows(conn).items() if pid not in accepted}
    stats["checked"] = len(missing)
    if not missing:
        return stats
    # case_number holds the county's own TAX_MAP_NO (padding intact); the
    # parcel_id conversion is only a fallback for a row that lacks one.
    tmn = {pid: (row.get("case_number") or parcel_id_to_tax_map_no(pid)) for pid, row in missing.items()}
    feats = client.fetch_by_tax_map_no(sorted({t for t in tmn.values() if t}))
    fresh: dict[str, dict] = {}
    for f in feats:
        pid = canonical_parcel_id(f["attributes"])
        if pid:
            fresh[pid] = f

    for pid, row in missing.items():
        feat = fresh.get(pid)
        if feat:
            upsert_parcel_from_gis(conn, pid, feat["attributes"], feat.get("geometry"))
            cls = classify(feat["attributes"])
            if cls["match_type"] in STORED_MATCH_TYPES:
                accepted[pid] = {"attrs": feat["attributes"], "cls": cls, "object_id": feat["attributes"].get("OBJECTID"), "geometry": feat.get("geometry")}
                stats["rescued_by_requery"] += 1
                continue
            note = f"RESOLVED {today}: county owner record no longer shows estate/heirs wording (settled, transferred or sold)"
        else:
            note = f"RESOLVED {today}: parcel no longer in county GIS (split, merged or retired)"
            stats["parcel_gone"] += 1
        conn.execute(
            "UPDATE court_records SET case_type = ?, description = ? WHERE dedupe_key = ?",
            (CASE_TYPE_RESOLVED, f"{row['description'] or ''} — {note}".strip(" —"), row["dedupe_key"]),
        )
        stats["resolved"] += 1
    return stats


def upsert_accepted(conn, client: GisClient, accepted: dict[str, dict], probate_links: dict[str, str], today: str) -> dict:
    stats = {"new": 0, "refreshed": 0, "flipped_back": 0}
    need_geom = [item["object_id"] for item in accepted.values() if "geometry" not in item]
    geoms = client.fetch_geometry(need_geom) if need_geom else {}
    existing = {
        r["parcel_id"]: r["case_type"]
        for r in conn.execute(
            "SELECT parcel_id, case_type FROM court_records WHERE source_portal = ?", (SOURCE_PORTAL,)
        )
    }
    for pid, item in accepted.items():
        geometry = item.get("geometry") or geoms.get(item["object_id"])
        upsert_parcel_from_gis(conn, pid, item["attrs"], geometry)
        rec = build_record(pid, item["attrs"], item["cls"])
        parts = list(rec["description_parts"])
        if pid in probate_links:
            parts.append(f"probate case {probate_links[pid]}")
        prior = existing.get(pid)
        if prior == CASE_TYPE_RESOLVED:
            parts.append(f"re-flagged {today} after an earlier resolution")
            stats["flipped_back"] += 1
        elif prior is None:
            stats["new"] += 1
        else:
            stats["refreshed"] += 1
        _upsert_estate_row(conn, rec, " — ".join(parts))
    return stats


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _abort(conn, notes: str) -> None:
    log_scrape(conn, SOURCE, 0, status="error", notes=notes)
    conn.commit()
    print(f"{SOURCE}: {notes} — aborting without touching data.")
    sys.exit(1)


def main() -> None:
    conn = get_connection()
    client = GisClient()
    started = time.time()
    today = now_iso()[:10]

    try:
        total = client.count("1=1")
    except Exception as exc:  # noqa: BLE001 — any failure here must not touch data
        _abort(conn, f"GIS health check request failed: {exc}")
    if not (MIN_HEALTHY_PARCEL_COUNT <= total <= MAX_HEALTHY_PARCEL_COUNT):
        _abort(conn, f"GIS health check returned count={total}, expected {MIN_HEALTHY_PARCEL_COUNT}-{MAX_HEALTHY_PARCEL_COUNT}")

    tracked_before = _count_rows(conn, CASE_TYPE_ACTIVE)
    last_sweep = get_state(conn, STATE_KEY_LAST_SWEEP)
    if SWEEP_INTERVAL_DAYS > 0 and last_sweep:
        age_days = (datetime.now(timezone.utc) - datetime.strptime(last_sweep, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 86400
        if age_days < SWEEP_INTERVAL_DAYS:
            notes = (f"skipped sweep (last full sweep {last_sweep}, interval {SWEEP_INTERVAL_DAYS:g} d); "
                     f"active={tracked_before}, requests={client.requests}")
            log_scrape(conn, SOURCE, tracked_before, status="ok", notes=notes)
            conn.commit()
            print(f"{SOURCE}: {notes}")
            return

    try:
        accepted, sweep_stats = sweep(client)
    except PageCapReached as exc:
        # A partial pre-filter result can't be told apart from settled
        # estates, so nothing is written at all on a capped run.
        _abort(conn, f"sweep incomplete: {exc} after {client.requests} requests")
    except Exception as exc:  # noqa: BLE001
        _abort(conn, f"sweep query failed: {exc}")

    floor = max(MIN_EXPECTED_MATCHES, int(tracked_before * MIN_RETAINED_RATIO))
    if len(accepted) < floor:
        _abort(conn, f"sweep found {len(accepted)} estate parcels, below the sanity floor of {floor} (tracked {tracked_before})")

    try:
        lifecycle = resolve_missing(conn, client, accepted, today)
        probate_links, probate_stats = link_probate(conn, accepted)
        upsert_stats = upsert_accepted(conn, client, accepted, probate_links, today)
    except PageCapReached as exc:
        conn.rollback()
        _abort(conn, f"page cap hit after the sweep: {exc}; nothing written")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _abort(conn, f"post-sweep step failed: {exc}; nothing written")
    set_state(conn, STATE_KEY_LAST_SWEEP, now_iso())
    conn.commit()

    active = _count_rows(conn, CASE_TYPE_ACTIVE)
    resolved_total = _count_rows(conn, CASE_TYPE_RESOLVED)
    by_type: dict[str, int] = {}
    for item in accepted.values():
        by_type[item["cls"]["match_type"]] = by_type.get(item["cls"]["match_type"], 0) + 1
    elapsed = round(time.time() - started, 1)
    notes = (
        f"by_match_type={dict(sorted(by_type.items()))}, active={active}, new={upsert_stats['new']}, "
        f"refreshed={upsert_stats['refreshed']}, "
        f"resolved_this_run={lifecycle['resolved']} (parcel_gone={lifecycle['parcel_gone']}), resolved_total={resolved_total}, "
        f"flipped_back={upsert_stats['flipped_back']}, rescued_by_requery={lifecycle['rescued_by_requery']}, "
        f"probate_links={len(probate_links)}, probate_parcel_ids_filled={probate_stats['parcel_ids_filled']}, "
        f"probate_ambiguous={probate_stats['ambiguous']}, candidates={sweep_stats['candidates']}, "
        f"rejects={dict(sorted(sweep_stats['rejects'].items()))}, life_estate_excluded={sweep_stats['life_estate_excluded']}, "
        f"duplicate_parcel_ids={sweep_stats['duplicate_parcel_ids']}, gis_count={total}, "
        f"requests={client.requests}, seconds={elapsed}"
    )
    log_scrape(conn, SOURCE, active, status="ok", notes=notes)
    conn.commit()
    print(f"{SOURCE}: {notes}")


if __name__ == "__main__":
    main()
