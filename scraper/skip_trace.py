"""
Automatic skip tracing of top leads through the DealMachine API, into the
encrypted contact store (contacts_store.py, data/contacts.db.enc).

PRIVACY — non-negotiable while the repo is public: GitHub Actions logs are
public, so nothing here ever prints or logs a name, phone or email. stdout
and the scrape_log note carry counts and credit numbers only; API error
bodies are reduced to HTTP status + error code; unexpected exceptions are
reduced to their class name and line number (an exception message could
carry a value). Contact data only ever lands in the encrypted store.

API (verified 2026-10-06 against https://api.docs.dealmachine.com/ and its
OpenAPI spec at /api-reference/openapi.json)
--------------------------------------------------------------------------
Base https://api.v2.dealmachine.com, `Authorization: Bearer <key>`.
  POST /v1/enrichment/apn      {data:[{apn}], location?, fields?, contact_audience?}
  POST /v1/enrichment/address  {data:[{street, city, state, zip?} | {full_address}], fields?, contact_audience?}
  GET  /v1/usage               credits.remaining / total_available (live balance; free)
Both enrichment endpoints take up to 250 items, return every item in order
with `input` echoed and `matched` true/false (`match_failure.code`
not_found | invalid_input), plus `totals` and `credits {used, properties,
people, deduplicated}`. Only matched items cost credits: 1 property credit
each, plus 1 people credit per returned contact when contact_audience isn't
'none'; credits are deduplicated within the billing period (a retry or a
re-trace in the same month is free). Contacts carry dm_person_id, full_name,
is_likely_owner / is_in_owner_family / is_resident / is_likely_renter,
phones[{number, type (wireless|landline|voip|mobile|unknown), do_not_call,
carrier}] (one docs example spells it `is_dnc` — both are read) and
emails[{address}]. No litigator flag and no per-contact confidence are
documented; litigator is read defensively from any key containing "litig"
and otherwise stored NULL (= UNKNOWN), and confidence is derived here.
Errors: 401 bad key, 402 data credits exhausted (Retry-After = billing reset),
403 forbidden, 429 rate limit (60/min, 5,000/day; details.limit_type),
408/503/504 enrichment cancelled/unavailable/deadline.

Lookup strategy
---------------
1. APN, scoped to Hamilton County (location county FIPS 47065). The API
   strips dashes/spaces/dots itself, so the county TAX_MAP_NO ("137A T 016")
   and our parcel_id ("137A-T-016") normalize identically. Which raw form
   DealMachine's county roll uses is unverified (no key to test with), and
   Tennessee rolls often pad the parcel to two decimals ("016.00"), so each
   parcel whose parcel number is a bare integer is ALSO submitted padded
   (request_kind 'apn_00'). Unmatched items are free, so the extra variant
   costs nothing; the log prints how many hits each variant got so the first
   real run shows the format.
2. Address fallback for anything APN didn't resolve: situs street + city +
   TN + zip. The parcel layer has no situs city/zip; for an owner-occupied
   parcel (is_absentee = 0) the mailing city/zip IS the situs city/zip, so
   that's used; otherwise "Chattanooga" and no zip.
3. Every match is location-verified before its contacts are kept: the
   returned lat/long must be within SKIPTRACE_MAX_DISTANCE_M (200 m) of our
   parcel's (house-number equality when either side lacks coordinates). It
   FAILS CLOSED: when neither check is possible the match is 'unverified' and
   treated as a mismatch. A failed check stores no contacts (status
   'location_mismatch') — a wrong property means wrong people. (The docs list
   latitude/longitude/address as always-included; 'coordinates' is requested
   anyway so a `fields` restriction can never drop them.)
4. Owner sanity check: owner_1_full_name / owner_2_full_name (requested as
   fields) and the returned contacts' names are compared with
   parcels.owner_name by token overlap -> match_quality. A mismatch is
   recorded, not dropped (it often just means a sale newer than the county
   roll), and lowers each contact's confidence.

Candidate selection (credit-conscious)
--------------------------------------
Scores come from build_unified.build_parcel_aggregates() — imported, not
re-implemented, so a recalibrated scoring.py/build_unified.py is picked up
automatically (works with both the original and the v2 family model).
Eligible: a parcel_id and a situs address; at least one LIVE signal
(excludes parcels whose only records are _cancelled/_resolved types or, under
v2, rows no longer in the latest pull); not a government / institutional /
lender / unknown-placeholder owner (parcel_utils.classify_owner_type, new
vocabulary or old, plus a small local fallback pattern), not a large-institution
name (TITLE / FUNDING / REIT / MORTGAGE / BANK ...; individuals and small LLCs
stay eligible) and not v2's acquirable=False; not traced
(matched / no_match / location_mismatch) within SKIPTRACE_REFRESH_DAYS;
score >= SKIPTRACE_MIN_SCORE. Ranked: multi-process leads and strong single
signals (foreclosure_notice, tax_sale_filing, estate_owned, municipal_lien,
probate matched to a parcel) first, then by score.

Caps and stops (env)
--------------------
SKIPTRACE_MAX_PER_RUN (25)            parcels per run
SKIPTRACE_MAX_CREDITS_PER_RUN (100)   credit budget per run
SKIPTRACE_MIN_REMAINING_CREDITS (100) never spend the account below this
SKIPTRACE_EST_CREDITS_PER_PARCEL (4)  worst-case planning estimate (1 property + ~3 owner contacts)
SKIPTRACE_MIN_SCORE (0)               score floor
SKIPTRACE_REFRESH_DAYS (180)          re-trace interval
SKIPTRACE_PERSON_ONLY (1)              1 = trace only owners named as people; 0 also allows LLCs/trusts/estates
SKIPTRACE_AUDIENCE (owners)           contact_audience; owners_and_family costs more people credits
SKIPTRACE_LOOKUP (apn_then_address)   or 'apn' / 'address'
SKIPTRACE_MAX_CREDITS_PER_DAY (150)   rolling-24h cap, from skip_traces.credits_used in the store
SKIPTRACE_BATCH_SIZE (5)              parcels per request (API max 250) — small, so one paid batch can't overshoot much
SKIPTRACE_MAX_ERRORS (3)              consecutive failures of a request that is clearly not billed
SKIPTRACE_MAX_DISTANCE_M (200)        location-verification radius
SKIPTRACE_MARK_PARCELS (0)            1 = also write parcels.skip_traced_at/skip_trace_status (PUBLIC db — off by default)
SKIPTRACE_DRY_RUN (0)                 select and count candidates; no API calls, no store writes
Stops immediately on 401/402/403/429, on low remaining credits (/v1/usage is
re-read before EVERY batch), when the per-run or per-day budget can't cover
another parcel at the worst-case estimate, and after SKIPTRACE_MAX_ERRORS
consecutive not-billed failures. A paid POST is never blindly retried: a client
timeout or any transport error that may have happened after the request was
sent stops the run (and /v1/usage is re-read to show what was spent); only a
refused connection / DNS failure, or a 5xx carrying a JSON error body, is
retried. A response that carries data is ALWAYS parsed and stored before the
run stops for X-RateLimit-Day-Remaining: 0 (that header describes the NEXT
request) or a 429 body that carries results. A billed response whose items
can't be mapped back to anything aborts the run.

Missing DEALMACHINE_API_KEY or CONTACTS_KEY -> prints "skipped: no key",
logs a zero-count scrape_log row, exits 0.

Main DB: db.log_scrape('skip_trace', n_traced, notes=counts/credits). The
parcels.skip_traced_at / skip_trace_status breadcrumb (mark_parcels) is OPT-IN
via SKIPTRACE_MARK_PARCELS=1: that DB is committed to a public repo, and the
columns would reveal which owners were traced; the encrypted store already
records every trace.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import contacts_store
import db
import parcel_utils

SOURCE = "skip_trace"
PROVIDER = "dealmachine"
DEFAULT_BASE_URL = "https://api.v2.dealmachine.com"
HAMILTON_COUNTY_FIPS = "47065"
API_MAX_ITEMS = 250
REQUEST_TIMEOUT = 90
MIN_REQUEST_INTERVAL = 1.1  # 60 req/min ceiling
# `fields` "can include both property and people fields" (DealMachine docs): a
# list holding only property fields leaves the contacts out of the response even
# when contact_audience asks for them. First live test (2026-10-13): 3/3
# properties matched, 0 phones/emails came back. People fields added below.
# dm_person_id / contact_type / is_resident come back on contacts automatically;
# requesting them in `fields` is rejected (HTTP 400 field_not_found, 2026-10-13).
PEOPLE_FIELDS = ["full_name", "first_name", "last_name", "phones", "emails"]
PROPERTY_FIELDS = ["owner_1_full_name", "owner_2_full_name", "parcel_number_raw", "coordinates"] + PEOPLE_FIELDS
DAILY_WINDOW_HOURS = 24
_SAFE_CODE = re.compile(r"[a-z_]{1,40}")


def safe_code(code) -> str:
    """API-supplied codes are printed/stored: whitelist them (an error body is untrusted)."""
    c = str(code or "")
    return c if _SAFE_CODE.fullmatch(c) else "other"

STRONG_CASE_TYPES = {"foreclosure_notice", "tax_sale_filing", "estate_owned", "municipal_lien"}
NON_DISTRESS_FALLBACK = {
    "foreclosure_notice_cancelled", "tax_sale_resolved", "estate_owned_resolved", "municipal_lien_resolved",
}
EXCLUDED_OWNER_TYPES = {"government", "institutional", "lender", "unknown"}
# Large institutions / title & funding shells: skip tracing them returns corporate
# switchboards (or nobody) and burns credits. Applied to every owner, LLC or not.
_LARGE_INSTITUTION = re.compile(
    r"\b(TITLE|MASTER FUNDING|FUNDING|REIT|PROPERTIES INC|RETAIL PROPERTIES|MORTGAGE|BANK)\b")
# Used alongside classify_owner_type so the exclusion also works with the
# pre-v2 classifier, which had no 'government'/'institutional' classes.
_FALLBACK_NON_PRIVATE = re.compile(
    r"\b(CITY OF|COUNTY|STATE OF|UNITED STATES|USA|HOUSING AUTH|AUTHORITY|BOARD OF|DEPT|DEPARTMENT|"
    r"SCHOOL|UNIVERSITY|COLLEGE|CHURCH OF|MINISTRIES|BAPTIST|METHODIST|PRESBYTERIAN|CATHOLIC|DIOCESE|"
    r"CONGREGATION|SYNAGOGUE|MOSQUE|CEMETERY|HOMEOWNERS|ASSOCIATION|ASSN|TVA|"
    r"FEDERAL NATIONAL|FANNIE MAE|FREDDIE MAC|FEDERAL HOME LOAN|SECRETARY OF HOUSING|HUD|"
    r"BANK|MORTGAGE|LENDING|SAVINGS|CREDIT UNION)\b"
)
_LOCAL_FAMILY = {
    "foreclosure_notice": "foreclosure", "tax_sale_filing": "tax", "probate": "estate", "estate_owned": "estate",
    "municipal_lien": "lien", "lien": "lien", "lis_pendens": "lien", "judgment": "lien",
    "detainer": "eviction_collections", "collections": "eviction_collections",
}
_NAME_STOPWORDS = {
    "ETAL", "ET", "AL", "UX", "VIR", "ESTATE", "EST", "OF", "THE", "HEIRS", "HEIR", "TRUST", "TRUSTEE", "TRS",
    "TR", "REVOCABLE", "LIVING", "FAMILY", "LLC", "INC", "CORP", "CO", "AND", "JR", "SR", "II", "III", "IV",
    "MR", "MRS", "MS", "DR", "C", "O", "ATTN", "LIFE", "DECEASED", "EXECUTOR", "ADMINISTRATOR", "EXR", "ADM",
}


def out(msg: str) -> None:
    """The only print path. Callers pass counts/codes only — never contact data."""
    print(f"skip_trace: {msg}", flush=True)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Config:
    max_per_run: int = 25
    max_credits_per_run: int = 100
    min_remaining_credits: int = 100
    est_credits_per_parcel: int = 4
    min_score: float = 0.0
    refresh_days: int = 180
    audience: str = "owners"
    lookup: str = "apn_then_address"
    batch_size: int = 5
    max_credits_per_day: int = 150
    mark_parcels: bool = False
    max_errors: int = 3
    max_distance_m: float = 200.0
    dry_run: bool = False
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            max_per_run=max(0, _env_int("SKIPTRACE_MAX_PER_RUN", 25)),
            max_credits_per_run=max(0, _env_int("SKIPTRACE_MAX_CREDITS_PER_RUN", 100)),
            min_remaining_credits=max(0, _env_int("SKIPTRACE_MIN_REMAINING_CREDITS", 100)),
            est_credits_per_parcel=max(1, _env_int("SKIPTRACE_EST_CREDITS_PER_PARCEL", 4)),
            min_score=_env_float("SKIPTRACE_MIN_SCORE", 0.0),
            refresh_days=max(0, _env_int("SKIPTRACE_REFRESH_DAYS", 180)),
            audience=(os.environ.get("SKIPTRACE_AUDIENCE") or "owners").strip(),
            lookup=(os.environ.get("SKIPTRACE_LOOKUP") or "apn_then_address").strip(),
            batch_size=min(API_MAX_ITEMS, max(1, _env_int("SKIPTRACE_BATCH_SIZE", 5))),
            max_credits_per_day=max(0, _env_int("SKIPTRACE_MAX_CREDITS_PER_DAY", 150)),
            mark_parcels=(os.environ.get("SKIPTRACE_MARK_PARCELS") or "0").strip() in ("1", "true", "yes"),
            max_errors=max(1, _env_int("SKIPTRACE_MAX_ERRORS", 3)),
            max_distance_m=_env_float("SKIPTRACE_MAX_DISTANCE_M", 200.0),
            dry_run=(os.environ.get("SKIPTRACE_DRY_RUN") or "0").strip() in ("1", "true", "yes"),
            base_url=(os.environ.get("DEALMACHINE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        )


# --- candidate selection ------------------------------------------------------------

def _non_distress_types() -> set[str]:
    try:
        import scoring
        types = set(getattr(scoring, "NON_DISTRESS_CASE_TYPES", ()) or ())
    except Exception:
        types = set()
    return types | NON_DISTRESS_FALLBACK


def _is_live(source: str, r: dict, non_distress: set[str]) -> bool:
    if r.get("_unlisted"):
        return False
    if source == "court_records":
        ct = r.get("case_type") or ""
        return ct not in non_distress and not ct.endswith(("_resolved", "_cancelled"))
    return True


def _process_of(source: str, r: dict) -> str:
    if source == "tax_delinquent":
        return "tax"
    if source == "code_enforcement":
        return "code_violation"
    return _LOCAL_FAMILY.get(r.get("case_type") or "", "other_court")


def _classifier_knows_government() -> bool:
    try:
        return parcel_utils.classify_owner_type("HAMILTON COUNTY") == "government"
    except Exception:
        return False


# People only (default). DealMachine holds people, not companies: the first live
# test (2026-10-13) traced the 3 top leads and got 0 contacts back. Owners named
# as a company, trust/trustee, estate or heirs are skipped unless
# SKIPTRACE_PERSON_ONLY=0. A deceased owner's heirs/estate parcel has no living
# person to look up under that name (estate_parcels carries the C/O contact).
_NOT_A_PERSON = re.compile(
    r"\b(TR|TRS|TRUSTEE|TRUSTEES|TTEE|TRUST|REVOCABLE|IRREVOCABLE|LIVING TRUST|FAMILY TRUST|"
    r"HEIRS?|ESTATE|EST OF|LIFE EST(ATE)?|DECEASED|DEC'?D|"
    r"LLC|L L C|LLP|PLLC|LP|LTD|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|"
    r"PARTNERS|PARTNERSHIP|HOLDINGS?|INVESTMENTS?|INVESTORS|PROPERTIES|PROPERTY|ENTERPRISES?|REALTY|"
    r"HOMES|HOUSING|GROUP|VENTURES?|CAPITAL|MANAGEMENT|MGMT|RENTALS?|DEVELOPMENT|DEVELOPERS|"
    r"CONSTRUCTION|BUILDERS?|SERVICES|ASSOCIATES|FUND|FOUNDATION|ORGANIZATION|SOLUTIONS|"
    r"APARTMENTS?|PROPERTY OWNERS?|C/O)\b"
)


def _person_only() -> bool:
    return os.environ.get("SKIPTRACE_PERSON_ONLY", "1") != "0"


def _owner_excluded(owner_name: str | None, stored_type: str | None, new_vocab: bool | None = None) -> bool:
    otype = parcel_utils.classify_owner_type(owner_name) or stored_type
    if otype in EXCLUDED_OWNER_TYPES:
        return True
    if _person_only():
        name_up = " ".join((owner_name or "").upper().replace("&", " & ").split())
        if otype not in (None, "individual") or _NOT_A_PERSON.search(name_up):
            return True
    if _LARGE_INSTITUTION.search(" ".join((owner_name or "").upper().split())):
        return True
    if new_vocab is None:
        new_vocab = _classifier_knows_government()
    if new_vocab:
        return False
    # Pre-v2 classifier (no government/institutional/lender classes): local
    # fallback. "LLC" names are never treated as public bodies, as in v2.
    name = " ".join((owner_name or "").upper().split())
    return bool(name) and "LLC" not in name and bool(_FALLBACK_NON_PRIVATE.search(name))


def compute_aggregates(conn) -> tuple[dict, dict]:
    """(records, aggregates) via build_unified's own public functions."""
    import build_unified as bu
    records = bu.load_all_records(conn)
    if hasattr(bu, "mark_unlisted"):
        bu.mark_unlisted(records)
    return records, bu.build_parcel_aggregates(conn, records)


def select_candidates(conn, last_traced: dict[str, str], cfg: Config, now: datetime | None = None,
                      aggregates_bundle: tuple[dict, dict] | None = None) -> tuple[list[dict], dict]:
    now = now or datetime.now(timezone.utc)
    records, aggs = aggregates_bundle or compute_aggregates(conn)
    non_distress = _non_distress_types()
    cutoff = (now - timedelta(days=cfg.refresh_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    by_pid: dict[str, list[tuple[str, dict]]] = {}
    for source, rows in records.items():
        for r in rows:
            if r.get("parcel_id"):
                by_pid.setdefault(r["parcel_id"], []).append((source, r))

    stats = {"parcels_scored": len(aggs), "no_address": 0, "non_private_owner": 0, "no_live_signal": 0,
             "recently_traced": 0, "below_min_score": 0, "eligible": 0, "priority": 0}
    cands = []
    new_vocab = _classifier_knows_government()
    for pid, agg in aggs.items():
        parcel = agg.get("parcel") or {}
        situs = (parcel.get("situs_address") or "").strip()
        if not pid or not situs:
            stats["no_address"] += 1
            continue
        if _owner_excluded(parcel.get("owner_name"), parcel.get("owner_type"), new_vocab) or agg.get("acquirable") is False:
            stats["non_private_owner"] += 1
            continue
        live = [(s, r) for s, r in by_pid.get(pid, []) if _is_live(s, r, non_distress)]
        if not live or ("family_scores" in agg and not agg["family_scores"]):
            stats["no_live_signal"] += 1
            continue
        if last_traced.get(pid) and last_traced[pid] >= cutoff:
            stats["recently_traced"] += 1
            continue
        score = float(agg.get("score") or 0.0)
        if score < cfg.min_score:
            stats["below_min_score"] += 1
            continue
        processes = {_process_of(s, r) for s, r in live}
        strong = any(
            s == "court_records" and (
                r.get("case_type") in STRONG_CASE_TYPES or r.get("case_type") == "probate"
                or (r.get("case_type") == "estate_owned" and "probate case" in (r.get("description") or ""))
            )
            for s, r in live
        )
        priority = 0 if (len(processes) >= 2 or strong) else 1
        stats["eligible"] += 1
        stats["priority"] += priority == 0
        cands.append({
            "parcel_id": pid,
            "situs_address": situs,
            "tax_map_no": parcel.get("tax_map_no"),
            "owner_name": parcel.get("owner_name"),
            "is_absentee": parcel.get("is_absentee"),
            "situs_city": parcel.get("situs_city"),
            "situs_zip": parcel.get("situs_zip"),
            "mailing_city": parcel.get("mailing_city"),
            "mailing_zip": parcel.get("mailing_zip"),
            "latitude": parcel.get("latitude"),
            "longitude": parcel.get("longitude"),
            "score": score,
            "priority": priority,
            "n_processes": len(processes),
        })
    cands.sort(key=lambda c: (c["priority"], -c["score"], -c["n_processes"], c["parcel_id"]))
    return cands, stats


# --- request building / response checks ------------------------------------------

def apn_variants(cand: dict) -> list[tuple[str, str]]:
    """[(request_kind, apn)] — county TAX_MAP_NO (or parcel_id), plus a
    two-decimal padded parcel number when the parcel part is a bare integer."""
    base = (cand.get("tax_map_no") or cand["parcel_id"].replace("-", " ")).strip()
    variants = [("apn", base)]
    parts = base.split()
    if len(parts) >= 2 and re.fullmatch(r"\d{3}", parts[-1]):
        variants.append(("apn_00", " ".join(parts[:-1] + [parts[-1] + ".00"])))
    return variants


def address_item(cand: dict) -> dict:
    city, zip_ = cand.get("situs_city"), cand.get("situs_zip")
    if not city and cand.get("is_absentee") == 0:
        city, zip_ = cand.get("mailing_city"), cand.get("mailing_zip")
    item = {"street": cand["situs_address"], "city": (city or "Chattanooga").title(), "state": "TN"}
    z = re.match(r"\d{5}", str(zip_ or ""))
    if z:
        item["zip"] = z.group(0)
    return item


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _house_number(addr: str | None) -> str | None:
    m = re.match(r"\s*(\d+)", addr or "")
    if not m:
        return None
    return m.group(1).lstrip("0") or "0"


def verify_location(cand: dict, item: dict, max_m: float) -> tuple[bool, str, float | None]:
    try:
        lat1, lon1 = float(cand["latitude"]), float(cand["longitude"])
        lat2, lon2 = float(item["latitude"]), float(item["longitude"])
    except (TypeError, ValueError, KeyError):
        lat1 = None
    if lat1 is not None:
        d = _haversine_m(lat1, lon1, lat2, lon2)
        return d <= max_m, "coords", round(d, 1)
    ours, theirs = _house_number(cand.get("situs_address")), _house_number(item.get("address") or item.get("full_address"))
    if ours and theirs:
        return ours == theirs, "house_number", None
    # Fail CLOSED: with nothing to compare, an APN/address hit is not trustworthy.
    return False, "unverified", None


def name_tokens(name: str | None) -> set[str]:
    toks = re.findall(r"[A-Z]+", (name or "").upper())
    return {t for t in toks if len(t) >= 2 and t not in _NAME_STOPWORDS}


def overlap_level(a: set[str], b: set[str]) -> str:
    if not a or not b:
        return "unknown"
    shared = a & b
    if a == b:
        return "exact"
    if len(shared) >= 2:
        return "strong"
    if len(shared) == 1:
        return "weak"
    return "none"


_LEVEL_RANK = {"exact": 4, "strong": 3, "weak": 2, "none": 1, "unknown": 0}


def owner_match_quality(cand: dict, item: dict) -> str:
    ours = name_tokens(cand.get("owner_name"))
    levels = [overlap_level(ours, name_tokens(item.get(f))) for f in ("owner_1_full_name", "owner_2_full_name")]
    levels += [overlap_level(ours, name_tokens(c.get("full_name"))) for c in item.get("contacts") or []]
    return max(levels, key=lambda lv: _LEVEL_RANK[lv]) if levels else "unknown"


def contact_role(c: dict) -> str:
    if c.get("is_likely_owner"):
        return "owner"
    if c.get("is_in_owner_family"):
        return "owner_family"
    if c.get("is_likely_renter"):
        return "renter"
    if c.get("is_resident"):
        return "resident"
    return "associated"


def contact_confidence(cand: dict, c: dict, match_quality: str) -> str:
    lv = overlap_level(name_tokens(cand.get("owner_name")), name_tokens(c.get("full_name")))
    if c.get("is_likely_owner") and lv in ("exact", "strong"):
        return "high"
    if (c.get("is_likely_owner") and lv == "weak") or (c.get("is_in_owner_family") and lv in ("exact", "strong", "weak")):
        return "medium"
    if c.get("is_likely_owner") and match_quality in ("exact", "strong"):
        return "medium"
    return "low"


def _flag(obj: dict, *keys: str) -> int | None:
    for k in keys:
        if k in obj and obj[k] is not None:
            return 1 if obj[k] else 0
    return None


def _litigator_flag(*objs: dict) -> int | None:
    found = None
    for o in objs:
        for k, v in (o or {}).items():
            if "litig" in k.lower() and v is not None:
                found = max(found or 0, 1 if v else 0)
    return found


def _digits(phone) -> str | None:
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d or None


# --- API client -----------------------------------------------------------------------

class StopRun(Exception):
    def __init__(self, reason: str, is_error: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.is_error = is_error


@dataclass
class RunStats:
    selected: int = 0
    requests: int = 0
    apn_submitted: int = 0
    apn_matched: int = 0
    apn_hits_base: int = 0
    apn_hits_pad00: int = 0
    address_submitted: int = 0
    address_matched: int = 0
    location_mismatch: int = 0
    no_match: int = 0
    missing: int = 0
    traced: int = 0
    matched_parcels: int = 0
    contacts_new: int = 0
    contacts_refreshed: int = 0
    phones: int = 0
    emails: int = 0
    dnc_phones: int = 0
    owner_match: dict = field(default_factory=dict)
    credits_used: float = 0.0
    credits_remaining_before: float | None = None
    credits_remaining_after: float | None = None
    stop_reason: str = "done"
    is_error: bool = False
    outcomes: dict = field(default_factory=dict)  # {parcel_id: final status} — never printed

    def notes(self) -> str:
        om = ",".join(f"{k}={v}" for k, v in sorted(self.owner_match.items())) or "none"
        rb = "na" if self.credits_remaining_before is None else f"{self.credits_remaining_before:g}"
        ra = "na" if self.credits_remaining_after is None else f"{self.credits_remaining_after:g}"
        return (
            f"selected={self.selected} traced={self.traced} matched={self.matched_parcels} "
            f"apn={self.apn_matched}/{self.apn_submitted}(base={self.apn_hits_base},pad00={self.apn_hits_pad00}) "
            f"address={self.address_matched}/{self.address_submitted} loc_mismatch={self.location_mismatch} "
            f"no_match={self.no_match} missing={self.missing} contacts_new={self.contacts_new} contacts_refreshed={self.contacts_refreshed} "
            f"phones={self.phones} emails={self.emails} dnc_phones={self.dnc_phones} owner_match[{om}] "
            f"requests={self.requests} credits_used={self.credits_used:g} "
            f"credits_remaining={rb}->{ra} stop={self.stop_reason}"
        )


_NOT_SENT_MARKERS = ("connection refused", "failed to establish a new connection", "name or service not known",
                     "getaddrinfo failed", "nodename nor servname", "temporary failure in name resolution",
                     "max retries exceeded")


def _surely_not_sent(exc: BaseException) -> bool:
    """True only when the failure happened BEFORE the request reached the server
    (refused connection / DNS / connect timeout) — the only transport errors a
    paid POST may be retried after. Read timeouts and dropped connections may
    have been billed. The text is inspected, never printed."""
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in ("ConnectTimeout", "ConnectionRefusedError", "NewConnectionError",
                                  "NameResolutionError", "gaierror"):
            return True
        msg = str(cur).lower()
        if any(m in msg for m in _NOT_SENT_MARKERS) and "read timed out" not in msg:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _shape(obj, depth: int = 0) -> str:
    """Structure only: key names, list lengths and value TYPES — never a value
    (a response holds names/phones/emails and this goes to a public log).
    Debug aid behind SKIPTRACE_DEBUG_SHAPE=1."""
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return "{" + ",".join(f"{k}:{_shape(v, depth + 1)}" for k, v in list(obj.items())[:40]) + "}"
    if isinstance(obj, list):
        return f"[{len(obj)}x " + (_shape(obj[0], depth + 1) if obj else "empty") + "]"
    return type(obj).__name__


class DealMachineClient:
    def __init__(self, api_key: str, cfg: Config, session=None, sleep=time.sleep):
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.cfg = cfg
        self.sleep = sleep
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                        "Accept": "application/json", "User-Agent": "chattanooga-intel-skiptrace/1.0"}
        self._last_request = 0.0
        self.requests = 0
        # Set when a response says the NEXT request would be refused (daily
        # quota spent, or a 429 that still carried data). The caller stores the
        # body it just got, commits, THEN stops — never discards a paid response.
        self.stop_after: str | None = None
        self.transport_failures = 0

    @staticmethod
    def _error_code(resp) -> str:
        try:
            return safe_code((resp.json().get("error") or {}).get("code") or "unknown")
        except Exception:
            return "unparseable"

    @staticmethod
    def _has_error_body(resp) -> bool:
        try:
            return isinstance((resp.json() or {}).get("error"), dict)
        except Exception:
            return False

    def _throttle(self) -> None:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0 and self._last_request:
            self.sleep(wait)

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.cfg.base_url}{path}"
        failures = 0
        paid = method != "GET"
        while True:
            self._throttle()
            self._last_request = time.monotonic()
            self.requests += 1
            try:
                if method == "GET":
                    resp = self.session.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)
                else:
                    resp = self.session.post(url, headers=self.headers, json=payload, timeout=REQUEST_TIMEOUT)
            except Exception as exc:  # transport error: class name only
                self.transport_failures += 1
                failures += 1
                out(f"request failed: {type(exc).__name__} (attempt {failures})")
                if paid and not _surely_not_sent(exc):
                    # Timeout / dropped connection on a POST: it may have been
                    # processed and billed. No blind retry — stop; the caller
                    # re-reads /v1/usage to show what (if anything) was spent.
                    raise StopRun(f"post_transport_failure:{safe_code(type(exc).__name__.lower())}", is_error=True)
                if failures >= self.cfg.max_errors:
                    raise StopRun(f"repeated_errors:{type(exc).__name__}", is_error=True)
                self.sleep(min(2 ** failures, 30) + random.uniform(0, 0.5))
                continue
            status = resp.status_code
            if 200 <= status < 300:
                body = resp.json()  # parse FIRST — a paid body is never discarded over a header
                self._check_rate_headers(resp)
                if os.environ.get("SKIPTRACE_DEBUG_SHAPE") == "1" and method == "POST":
                    out("shape: " + _shape(body))
                return body
            code = self._error_code(resp)
            out(f"HTTP {status} {code}")
            if status == 401:
                raise StopRun(f"auth_failed:{code}", is_error=True)
            if status == 402:
                raise StopRun(f"credits_exhausted:{code}")
            if status == 403:
                raise StopRun(f"forbidden:{code}", is_error=True)
            if status == 429:
                try:
                    body = resp.json()
                except Exception:
                    body = None
                if paid and isinstance(body, dict) and isinstance(body.get("data"), list) and body["data"]:
                    self.stop_after = f"rate_limited:{code}"  # data came back: store it, then stop
                    return body
                raise StopRun(f"rate_limited:{code}")
            if status in (408, 500, 502, 503, 504):
                failures += 1
                if paid and not self._has_error_body(resp):
                    # A bare 5xx/408 on a POST gives no assurance nothing was billed.
                    raise StopRun(f"post_unconfirmed:http_{status}", is_error=True)
                if failures >= self.cfg.max_errors:
                    raise StopRun(f"repeated_errors:http_{status}", is_error=True)
                # A 5xx with an error body is a refusal; already-billed entities are deduplicated anyway.
                self.sleep(min(2 ** failures, 30) + random.uniform(0, 0.5))
                continue
            raise StopRun(f"http_{status}:{code}", is_error=True)

    def _check_rate_headers(self, resp) -> None:
        h = getattr(resp, "headers", {}) or {}
        day = h.get("X-RateLimit-Day-Remaining")
        if day is not None and str(day).strip() == "0":
            # "0 left" describes the NEXT request; this response is good and paid for.
            self.stop_after = self.stop_after or "daily_request_limit_reached"
        minute = h.get("X-RateLimit-Remaining")
        if minute is not None and str(minute).strip() == "0":
            try:
                reset = float(h.get("X-RateLimit-Reset"))
                self.sleep(max(0.0, min(60.0, reset - time.time())) + 0.5)
            except (TypeError, ValueError):
                self.sleep(60.0)

    def remaining_credits(self) -> float | None:
        body = self.request("GET", "/v1/usage")
        credits = body.get("credits") or {}
        for k in ("total_available", "remaining"):
            if isinstance(credits.get(k), (int, float)):
                return float(credits[k])
        return None


# --- the run ----------------------------------------------------------------------------

class Tracer:
    def __init__(self, cfg: Config, client: DealMachineClient, store: contacts_store.ContactsStore,
                 used_24h: float = 0.0):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.stats = RunStats()
        self.budget = 0.0
        self.used_24h = used_24h
        self.apn_mismatched: set[str] = set()
        self.apn_missing: set[str] = set()
        self._fresh = False  # /v1/usage already read since the last paid POST

    def _affordable(self) -> int:
        return int((self.budget - self.stats.credits_used) // self.cfg.est_credits_per_parcel)

    def _store_match(self, cand: dict, item: dict, kind: str, credits_share: float) -> None:
        mq = owner_match_quality(cand, item)
        self.stats.owner_match[mq] = self.stats.owner_match.get(mq, 0) + 1
        seen = contacts_store.now_iso()
        ok, method, dist = verify_location(cand, item, self.cfg.max_distance_m)
        if not ok:
            self.stats.location_mismatch += 1
            self.store.record_trace(cand["parcel_id"], PROVIDER, kind, "location_mismatch", mq, credits_share,
                                    item.get("dm_property_id"), dist, method, traced_at=seen)
            self.stats.outcomes.setdefault(cand["parcel_id"], "location_mismatch")
            return
        self.store.record_trace(cand["parcel_id"], PROVIDER, kind, "matched", mq, credits_share,
                                item.get("dm_property_id"), dist, method, traced_at=seen)
        self.stats.matched_parcels += 1
        self.stats.outcomes[cand["parcel_id"]] = "matched"
        for c in item.get("contacts") or []:
            name = (c.get("full_name") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or None)
            role = contact_role(c)
            conf = contact_confidence(cand, c, mq)
            pid_ref = c.get("dm_person_id")
            lit_c = _litigator_flag(c)
            rows = []
            for ph in c.get("phones") or []:
                num = _digits(ph.get("number"))
                if not num:
                    continue
                dnc = _flag(ph, "do_not_call", "is_dnc", "dnc")
                rows.append(dict(phone=num, phone_type=ph.get("type") or ph.get("phone_type"), dnc_flag=dnc,
                                 litigator_flag=_litigator_flag(ph) if _litigator_flag(ph) is not None else lit_c,
                                 carrier=ph.get("carrier")))
                self.stats.phones += 1
                self.stats.dnc_phones += dnc == 1
            for em in c.get("emails") or []:
                addr = (em.get("address") if isinstance(em, dict) else em) or ""
                if addr.strip():
                    rows.append(dict(email=addr.strip(), litigator_flag=lit_c))
                    self.stats.emails += 1
            if not rows:
                rows.append(dict(litigator_flag=lit_c))
            for r in rows:
                new = self.store.upsert_contact(cand["parcel_id"], PROVIDER, name, role, confidence=conf,
                                                dm_person_id=pid_ref, seen_at=seen, **r)
                if new:
                    self.stats.contacts_new += 1
                else:
                    self.stats.contacts_refreshed += 1

    def _post_batch(self, path: str, items: list[dict], extra: dict) -> dict:
        payload = {"data": items, "fields": PROPERTY_FIELDS, "contact_audience": self.cfg.audience, **extra}
        self._fresh = False
        body = self.client.request("POST", path, payload)
        self.stats.requests = self.client.requests
        return body

    def _refresh_budget(self) -> float | None:
        """Re-read /v1/usage (free) and re-derive the budget — called before EVERY
        paid batch, so a balance that fell (another run, the web app) is noticed."""
        if self._fresh:
            return None
        remaining = self.client.remaining_credits()
        self._fresh = True
        if self.client.stop_after:  # the usage reply itself says the next request would be refused
            raise StopRun(self.client.stop_after)
        if remaining is not None:
            spendable = remaining - self.cfg.min_remaining_credits
            if spendable < self.cfg.est_credits_per_parcel:
                raise StopRun("low_account_credits")
            self.budget = min(self.budget, self.stats.credits_used + spendable)
        return remaining

    @staticmethod
    def _norm_input(inp) -> str | None:
        """Echo-comparison key tolerant of the API's normalization (APNs: dashes,
        spaces and dots stripped; addresses: case/whitespace)."""
        if not isinstance(inp, dict):
            return None
        if inp.get("apn") is not None:
            return "apn:" + re.sub(r"[\s.\-]", "", str(inp["apn"])).upper()
        parts = [str(inp.get(k) or "") for k in ("full_address", "street", "city", "state", "zip")]
        return "addr:" + "|".join(" ".join(p.upper().split()) for p in parts)

    @classmethod
    def _results_in_order(cls, body: dict, submitted: list[dict]) -> list[dict | None]:
        """Map response items back to submitted ones. The API returns every
        item, in order — so equal lengths are matched BY POSITION (the echoed
        `input` is normalized and can't be compared byte-for-byte). Only a
        length mismatch falls back to a normalized-echo match; an item that
        can't be placed is None ('missing'), never a 'no match'."""
        raw = body.get("data") or []
        data = [d for d in raw if isinstance(d, dict)]
        if len(data) == len(submitted) and len(data) == len(raw):
            return list(data)
        by_key: dict[str, dict] = {}
        dup: set[str] = set()
        for it in data:
            k = cls._norm_input(it.get("input"))
            if k is None:
                continue
            if k in by_key:
                dup.add(k)
            by_key[k] = it
        outl: list[dict | None] = []
        for sub in submitted:
            k = cls._norm_input(sub)
            outl.append(None if (k is None or k in dup) else by_key.get(k))
        return outl

    def _unmapped_check(self, body: dict, used: float, m_items: list[dict]) -> bool:
        """True (after recording the paid credits) when the response was billed
        but its items couldn't be mapped to what we sent."""
        tm = (body.get("totals") or {}).get("matched")
        short = isinstance(tm, int) and len(m_items) < tm
        if used > 0 and (not m_items or short):
            self.store.record_trace("_run", PROVIDER, None, "error", None, 0.0 if m_items else used,
                                    notes="billed_but_unmapped")
            self.store.commit()
            return True
        return False

    def _after_batch(self, warned: bool, unmapped: bool) -> None:
        if unmapped:
            raise StopRun("billed_but_unmapped", is_error=True)
        if warned:
            raise StopRun("partial_result_low_credits")
        if self.client.stop_after:  # data stored + committed above; now honour the stop
            raise StopRun(self.client.stop_after)

    def _credits_of(self, body: dict, matched_items: list[dict]) -> tuple[float, bool]:
        """(credits charged, partial-result warning present). The docs say a
        request the balance can't fully cover may come back partial "with a
        warning" (shape undocumented for enrichment) — any warning key stops
        the run after this batch is stored."""
        credits = body.get("credits") or {}
        if isinstance(credits.get("used"), (int, float)):
            used = float(credits["used"])
        else:  # undocumented shape: assume worst case
            used = float(sum(1 + len(it.get("contacts") or []) for it in matched_items))
        self.stats.credits_used += used
        return used, bool(body.get("warning") or body.get("warnings"))

    def _shares(self, used: float, matched_items: list[dict]) -> dict[int, float]:
        weights = {id(it): 1 + len(it.get("contacts") or []) for it in matched_items}
        total = sum(weights.values()) or 1
        return {k: round(used * w / total, 2) for k, w in weights.items()}

    def run_apn(self, cands: list[dict]) -> list[dict]:
        unresolved = []
        step = max(1, min(self.cfg.batch_size, API_MAX_ITEMS // 2))  # <=2 APN variants per parcel: one POST per chunk
        for i in range(0, len(cands), step):
            chunk = cands[i:i + step]
            self._refresh_budget()
            n = min(len(chunk), self._affordable())
            if n <= 0:
                raise StopRun("run_credit_budget_reached")
            chunk = chunk[:n]
            items, owners = [], []
            for c in chunk:
                for kind, apn in apn_variants(c):
                    items.append({"apn": apn})
                    owners.append((c, kind))
            body = self._post_batch("/v1/enrichment/apn", items,
                                    {"location": {"type": "county", "code": HAMILTON_COUNTY_FIPS}})
            results = self._results_in_order(body, items)
            m_items = [it for it in results if it and it.get("matched")]
            used, warn = self._credits_of(body, m_items)
            shares = self._shares(used, m_items)
            self.stats.apn_submitted += len(chunk)
            best: dict[str, tuple[dict, str]] = {}
            seen_any: set[str] = set()
            for (c, kind), it in zip(owners, results):
                if it is not None:
                    seen_any.add(c["parcel_id"])
                if it and it.get("matched") and c["parcel_id"] not in best:
                    best[c["parcel_id"]] = (it, kind)
            for c in chunk:
                hit = best.get(c["parcel_id"])
                if not hit:
                    if c["parcel_id"] not in seen_any:
                        self.apn_missing.add(c["parcel_id"])
                    unresolved.append(c)
                    continue
                it, kind = hit
                self.stats.apn_matched += 1
                if kind == "apn":
                    self.stats.apn_hits_base += 1
                else:
                    self.stats.apn_hits_pad00 += 1
                before = self.stats.location_mismatch
                self._store_match(c, it, kind, shares.get(id(it), 0.0))
                if self.stats.location_mismatch > before:
                    self.apn_mismatched.add(c["parcel_id"])
                    unresolved.append(c)  # wrong property by APN: let the address lookup try
                else:
                    self.stats.traced += 1
            self.store.commit()
            self._after_batch(warn, self._unmapped_check(body, used, m_items))
        return unresolved

    def run_address(self, cands: list[dict], final: bool) -> None:
        for i in range(0, len(cands), self.cfg.batch_size):
            chunk = cands[i:i + self.cfg.batch_size]
            self._refresh_budget()
            n = min(len(chunk), self._affordable())
            if n <= 0:
                raise StopRun("run_credit_budget_reached")
            chunk = chunk[:n]
            items = [address_item(c) for c in chunk]
            body = self._post_batch("/v1/enrichment/address", items, {})
            results = self._results_in_order(body, items)
            m_items = [it for it in results if it and it.get("matched")]
            used, warned = self._credits_of(body, m_items)
            shares = self._shares(used, m_items)
            self.stats.address_submitted += len(chunk)
            for c, it in zip(chunk, results):
                if it and it.get("matched"):
                    self.stats.address_matched += 1
                    self._store_match(c, it, "address", shares.get(id(it), 0.0))
                    self.stats.traced += 1
                elif it is None:
                    # No response item for this input: an uncounted, retryable
                    # outcome ('error' is not a completed status) - never a no_match.
                    self.stats.missing += 1
                    self.store.record_trace(c["parcel_id"], PROVIDER, "address", "error", None, 0.0,
                                            notes="match_failure=missing")
                elif final:
                    self.stats.no_match += 1
                    self.stats.traced += 1
                    code = safe_code((it.get("match_failure") or {}).get("code") or "unknown")
                    self.store.record_trace(c["parcel_id"], PROVIDER, "address", "no_match", None, 0.0,
                                            notes=f"match_failure={code}")
                    self.stats.outcomes.setdefault(c["parcel_id"], "no_match")
            self.store.commit()
            self._after_batch(warned, self._unmapped_check(body, used, m_items))

    def run(self, cands: list[dict]) -> RunStats:
        st = self.stats
        try:
            self.budget = float(self.cfg.max_credits_per_run)
            day_left = self.cfg.max_credits_per_day - self.used_24h
            if day_left < self.cfg.est_credits_per_parcel:
                raise StopRun("daily_credit_cap_reached")
            self.budget = min(self.budget, day_left)
            remaining = self._refresh_budget()
            st.credits_remaining_before = remaining
            todo = cands[: min(self.cfg.max_per_run, max(0, self._affordable()))]
            st.selected = len(todo)
            if not todo:
                raise StopRun("nothing_affordable" if cands else "no_candidates")
            lookup = self.cfg.lookup
            if lookup in ("apn", "apn_then_address"):
                unresolved = self.run_apn(todo)
                if lookup == "apn":
                    self._record_apn_only_no_match(unresolved)
                elif unresolved:
                    self.run_address(unresolved, final=True)
            else:
                self.run_address(todo, final=True)
        except StopRun as stop:
            st.stop_reason, st.is_error = stop.reason, stop.is_error
        finally:
            st.requests = self.client.requests
        if not st.stop_reason.startswith(("auth_failed", "forbidden", "repeated_errors")):
            try:  # also the post-transport-failure re-read of what was actually spent
                st.credits_remaining_after = self.client.remaining_credits()
            except StopRun:
                pass
        st.requests = self.client.requests
        return st

    def _record_apn_only_no_match(self, unresolved: list[dict]) -> None:
        """APN-only mode: anything unresolved is a no-match, except parcels
        whose APN hit failed location verification (already recorded)."""
        for c in unresolved:
            if c["parcel_id"] in self.apn_mismatched:
                self.stats.traced += 1
                continue
            if c["parcel_id"] in self.apn_missing:  # no response item: retryable, not a no_match
                self.stats.missing += 1
                self.store.record_trace(c["parcel_id"], PROVIDER, "apn", "error", None, 0.0,
                                        notes="match_failure=missing")
                continue
            self.stats.no_match += 1
            self.stats.traced += 1
            self.store.record_trace(c["parcel_id"], PROVIDER, "apn", "no_match", None, 0.0)
            self.stats.outcomes[c["parcel_id"]] = "no_match"
        self.store.commit()


def mark_parcels(conn, outcomes: dict[str, str], traced_at: str | None = None) -> int:
    """OPT-IN (SKIPTRACE_MARK_PARCELS=1) main-DB breadcrumb: parcels.skip_traced_at / skip_trace_status
    (timestamp + 'matched'/'no_match'/'location_mismatch' — no contact data),
    so the dashboard can show "traced" without the key. Inert until db.py's
    ADDED_COLUMNS gains those columns; returns rows updated."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)")}
    if "skip_traced_at" not in cols or not outcomes:
        return 0
    traced_at = traced_at or db.now_iso()
    has_status = "skip_trace_status" in cols
    for pid, status in outcomes.items():
        if has_status:
            conn.execute("UPDATE parcels SET skip_traced_at = ?, skip_trace_status = ? WHERE parcel_id = ?",
                         (traced_at, status, pid))
        else:
            conn.execute("UPDATE parcels SET skip_traced_at = ? WHERE parcel_id = ?", (traced_at, pid))
    return len(outcomes)


def _selection_line(sel: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in sel.items())


def main(argv: list[str] | None = None, session=None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description="Skip-trace top leads via DealMachine into the encrypted contact store.")
    ap.add_argument("--max", type=int, help="override SKIPTRACE_MAX_PER_RUN")
    ap.add_argument("--dry-run", action="store_true", help="select and count candidates only")
    args = ap.parse_args(argv)
    cfg = Config.from_env()
    if args.max is not None:
        cfg.max_per_run = max(0, args.max)
    cfg.dry_run = cfg.dry_run or args.dry_run

    conn = db.get_connection()
    try:
        api_key = (os.environ.get("DEALMACHINE_API_KEY") or "").strip()
        try:
            key = contacts_store.load_key()
        except contacts_store.StoreKeyError:
            out("error: CONTACTS_KEY is not a valid Fernet key")
            db.log_scrape(conn, SOURCE, 0, status="error", notes="invalid contacts key")
            conn.commit()
            return 1

        if cfg.dry_run:
            # Read-only, in memory: a dry run never writes the store or a plaintext file.
            last = {}
            if key and contacts_store.enc_path().exists():
                try:
                    ro = contacts_store.open_readonly(key)
                except contacts_store.StoreKeyError:
                    out("error: contacts key does not decrypt the store")
                    return 1
                try:
                    q = ("SELECT parcel_id, MAX(traced_at) FROM skip_traces WHERE status IN "
                         f"({','.join('?' for _ in contacts_store.COMPLETED_STATUSES)}) GROUP BY parcel_id")
                    last = {r[0]: r[1] for r in ro.execute(q, contacts_store.COMPLETED_STATUSES)}
                finally:
                    ro.close()
            cands, sel = select_candidates(conn, last, cfg)
            take = cands[: cfg.max_per_run]
            out(f"dry run: {_selection_line(sel)} would_trace={len(take)} "
                f"priority_in_batch={sum(1 for c in take if c['priority'] == 0)}")
            return 0

        if not api_key or not key:
            out("skipped: no key")
            db.log_scrape(conn, SOURCE, 0, status="ok", notes="skipped: no key")
            conn.commit()
            return 0

        client = DealMachineClient(api_key, cfg, session=session, sleep=sleep)
        try:
            with contacts_store.ContactsStore(key) as store:
                cands, sel = select_candidates(conn, store.last_traced(), cfg)
                out(f"candidates: {_selection_line(sel)}")
                used_24h = store.credits_since(
                    (datetime.now(timezone.utc) - timedelta(hours=DAILY_WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                out(f"credits used by skip_trace in the last {DAILY_WINDOW_HOURS}h: {used_24h:g} "
                    f"(cap {cfg.max_credits_per_day})")
                stats = Tracer(cfg, client, store, used_24h=used_24h).run(cands)
                totals = store.counts()
            out(stats.notes())
            out(f"store totals: parcels_traced={totals['parcels_traced']} contact_rows={totals['contact_rows']} "
                f"phones={totals['phones']} emails={totals['emails']} dnc_phones={totals['dnc_phones']} "
                f"store_changed={int(store.changed)}")
        except contacts_store.StoreKeyError:
            out("error: contacts key does not decrypt the store (store left untouched)")
            db.log_scrape(conn, SOURCE, 0, status="error", notes="contacts key does not decrypt the store")
            conn.commit()
            return 1
        if cfg.mark_parcels:  # opt-in: the main DB is public (see module docstring)
            mark_parcels(conn, stats.outcomes)
        db.log_scrape(conn, SOURCE, stats.traced, status="error" if stats.is_error else "ok", notes=stats.notes())
        conn.commit()
        return 1 if stats.is_error else 0
    except Exception as exc:
        tb = exc.__traceback__
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        where = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}" if tb else "?"
        out(f"error: {type(exc).__name__} at {where}")  # class + location only; messages may carry data
        try:
            db.log_scrape(conn, SOURCE, 0, status="error", notes=f"{type(exc).__name__} at {where}")
            conn.commit()
        except Exception:
            pass
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
