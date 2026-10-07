"""
All score math lives here — build_unified.py calls into this module and
nowhere else computes a score.

Model (v2: signal families, time-aware, owner-class aware)
-----------------------------------------------------------
  1. Every record belongs to a signal FAMILY: foreclosure, tax_sale,
     tax_delinquency, code_violation, estate (probate + estate_owned), lien
     (municipal_lien + legacy lien types), eviction_collections (detainer +
     collections). Foreclosure + probate + an eviction on one parcel are
     three families, not one "court" signal as the old per-source-table
     model had it.
  2. Each family present on a parcel collapses to ONE raw value in
     [0, FAMILY_MAX_RAW=90] — never 100, so no single signal can saturate.
     Inside a family the strongest record counts in full and every further
     record adds a damped term (`combine_within_family`); tax years and code
     cases stack inside their family function, never as separate terms.
     Every raw value already includes TIME (sale dates, decay half-lives,
     stale bills) — see the per-family sections below.
  3. tax_sale and tax_delinquency are one PROCESS (unpaid property tax): the
     stronger counts, the other corroborates with at most +5
     (`process_raws`). Both stay visible as families in the exports, but
     they never stack as two independent noisy-OR terms or count as two tier
     families.
  4. Processes combine by noisy-OR: 1 - prod(1 - raw/100). Absentee and
     entity ownership each add ONE extra noisy-OR term proportional to the
     strongest process (`OWNER_FLAG_SHARE`) — they lift a lead, never create
     or saturate one. (The old model added +8/+6 to EVERY raw value, which is
     what pinned single-signal parcels at exactly 100.0.)
  5. Owners who are not motivated private sellers stay in every export but
     are demoted (`OWNER_CLASS_MULTIPLIER`): government x0.25, institutional
     x0.5, lender/REO x0.6; an exempt (land use EX) parcel with no current
     tax bill and no live tax-sale row x0.35 (a strike-off or charity parcel
     whose owner name was never updated). None of them get absentee/entity
     terms. `distress_score` in the export keeps the undemoted value.
  6. Tier = number of PROCESSES whose raw value is >= TIER_MIN_RAW (3+
     triple_threat, 2 multi_factor, else single_signal), so a stale closed
     overgrowth ticket or a $90 bill no longer makes a "multi-factor" lead.
  7. `score_parcel` also returns additive contributions (strongest first,
     owner flags, then the demotion as a negative entry) that sum to the
     score — exported as `score_breakdown`, the per-lead "why".

Every function is deterministic for a given as-of date (`as_of_date()`:
today UTC, or SCORING_AS_OF=YYYY-MM-DD for tests/backfills) and monotonic in
the obvious directions: an extra record, a larger amount, more delinquent
years, a higher debt-to-value ratio, a more severe category, open instead of
closed, litigation instead of in-progress, or a more recent date never lowers
a raw value or the final score. (Deliberate exceptions, all about an event
that has PASSED rather than more distress: a foreclosure/tax-sale date that
has gone by scores below an upcoming one.)

CALIBRATION STATUS — calibrated 2026-10-06 against production data
--------------------------------------------------------------------
Calibrated against the 2026-10-06 snapshot of data/chattanooga.db (two
weeks of daily production scrapes: 16,547 parcels with a signal; 12,333
tax_delinquent rows / 7,926 parcels; 29,082 code_enforcement rows = 21,296
cases / 9,491 parcels; 1,166 court_records rows), scored as of 2026-10-06.
Three independent proposals (distribution/stability, investor practice,
robustness) were prototyped on copies of that snapshot, judged, and merged;
`calibrate_from_data(conn)` prints the distributions below so the next
recalibration starts from numbers. Why each constant is what it is:

  Old model on the same data: 343 leads at exactly 100.0 (a 343-way tie at
  the top: 250 code-only parcels, 187 of them with zero open cases), deciles
  42.8..73.1, 15 of 16 "triple threats" built on a PAID tax-sale row, and
  all 100 government parcels labelled 'individual' with the absentee bonus.

  tax_delinquency (live parcels): total owed p5 $34 / p25 $277 / p50 $689 /
    p75 $1,552 / p90 $3,013 / p99 $16.6k -> amount scale $50..$15k (log).
    Years delinquent: 65% one, 19% two, 12% three, 4% four or more -> +5 per
    extra year, 6+ years = full 25. Debt / max(appraised, $25k): p50 0.4%,
    p90 1.1%, p99 2.7%, p99.9 8.4% -> ratio scale 1%..25%; the $25k floor
    keeps a $2k sliver lot owing $300 from reading as "15% of value". The
    task's example ($11k owed over 3 yr): raw 65 on a $20k parcel vs 50 on a
    $500k one. Bills go delinquent March 1 of the following year
    (`latest_delinquent_bill_year`); a newest delinquent bill more than one
    year behind that is STALE (bills stopped accruing — 296 of the 344
    parcels whose newest bill is 2022 or older are exempt land) and halves
    every 2 years, floor x0.2.
  code_violation: rows are per cited ordinance, not per case (one inspection
    produced 35 rows), so the unit is the case (case_number); status never
    differs within a case. Severity is ordered by how often the city takes a
    case to litigation: condemnation 15.4% LIT, unsafe structure 14.4%,
    property maintenance 9.9%, vacant building 7.9% (kept high: a vacancy
    signal), nuisance (overgrowth/litter) 0.8%. 95% of code parcels have no
    open case; open cases date mostly from 2025-26 (35 of 758 from 2021-23,
    likely never-closed records -> open cases fade after a 1-year grace).
    The scraper keeps closed rows for 730 days -> closed cases halve every
    365 days and closed history saturates on its own (CODE_CLOSED_CAP, at
    most raw ~35), below one open condemnation (~59; ~69 in litigation,
    about level with a post-auction tax-sale lead and below an upcoming
    foreclosure).
  foreclosure_notice (77 live notices, none with an amount): effective sale
    date = "postponed to" date > "Sale date" in description > filing_date.
    A sale within 30 days is urgent: 82 at 30 days rising to 86 on sale day
    (a gentle slope, so sales on different dates don't tie; it stays below
    two strong stacked families — a sale days out is often too late for a
    negotiated purchase, but it is the auction-day lead), 72 at 120+ days
    out. Once the date passes with no postponement posted the property has
    usually sold: step down to 60 for a 7-day grace (postponements are
    posted late), then halve every 21 days, floor 8 (kept as weak history,
    flagged "verify"). "Postponed, new date not given" holds at 60 for 60
    days, then halves every 30.
  tax_sale_filing (66 ACTIVE rows, all on the 2026-06-04 auction): the list
    PDF is frozen at sale day, so ACTIVE today means the parcel went to
    auction unpaid — sold or struck off, owner of record inside the
    statutory redemption window (Tenn. Code Ann. 67-5-2701, generally up to
    one year from the order confirming the sale; ASSUMED, not verified
    against a Hamilton County order), or redeemed since without the list
    saying so. 64 of the 66 are no longer on the Trustee's delinquent file.
    So: 64 for 365 days after the sale (below upcoming foreclosures), spread
    x0.9..x1.1 by minimum bid (p10 $1.2k, p90 $12.6k) and bid / max(value,
    $25k) (p10 3.7%, p90 15%), then halving every 90 days. A future list
    before its auction: 80, or 85 within 60 days.
  estate: estate_owned (estate_parcels.py; ~254 parcels titled to heirs /
    an estate / an executor, no date) 60, executor/administrator 64, estate
    62, a deceased co-owner's share 50, +4 when the row names a matching
    probate case; current title state, no decay. probate: tier by motion
    type, matched on the motion's own text (before " | ", "Atty:" or a
    trailing docket number): a motion to sell / list real property 60,
    approve a sale (buyer likely found) 45, administration 40, routine or
    closing 25, distributing sale proceeds (already sold) 20; halves every
    180 days.
  lien: municipal_lien (municipal_liens.py: city abatement lien, amount =
    outstanding lien balance) 50..60 by amount ($250..$10k log); current
    state, a paid lien flips to municipal_lien_resolved.
  eviction_collections: detainer 30 (the owner is the landlord PLAINTIFF —
    a "tired landlord" signal, not owner distress), collections 30..40 by
    amount; halve every 180 / 365 days. None resolve to a parcel yet (0 of
    841), so these only score their own unresolved rows today.
  _cancelled/_resolved types (foreclosure_notice_cancelled,
    tax_sale_resolved, estate_owned_resolved, municipal_lien_resolved): 0,
    excluded before any family is formed — audit trail only.
  Owner flags: absentee 12% / entity 8% of the strongest process — about
    +1..+3 points; at most ~+4 mid-range, 0 at the extremes.
  Owner classes (parcel_utils.classify_owner_type, audited against every
    owner name): 106 government, ~197 institutional, 7 lender, 9 placeholder
    ('unknown'). Exempt land use: 429 parcels; 302 privately-named ones have
    only stale or no bills (strike-offs). Multipliers are below.
  TIER_MIN_RAW 15: excludes a lone closed overgrowth ticket (~10) and a
    single-year bill under ~$300, keeps a single open nuisance case, any
    multi-year delinquency, and every live court signal.

  SCORE_CEILING 99.9: only a 4-5 family stack gets near it.

Measured on that snapshot, as of 2026-10-06 (old -> new): leads >= 99
346 -> 0 (343 at exactly 100.0 -> 0, max 93.0); deciles p10..p90 42.8..73.1
-> 4.1..34.8 (p99 63.8); distinct scores in the top 100 1 -> 72;
government/institutional parcels in the top 500 17 -> 0 (best government
rank 8 -> ~4,900); tiers triple/multi/single 16/1,062/15,469 -> 0/207/16,340
(6/237/16,496 of 16,739 leads once estate_owned + municipal_lien rows exist,
measured on the 2026-10-06 integration e2e DB with non-acquirable tiers
capped at single_signal). Rank
stability: a tie-break-only change keeps 99% of the top 100 (was 32%);
random +/-15% on every constant keeps ~90% of the top 100 — the one fragile
constant is TAX_SALE_REDEMPTION (its 66 near-identical rows straddle the
top-100 cutoff). Re-run `calibrate_from_data()` after a month of the new
sources (estate_owned, municipal_lien, internetpostings.com foreclosures)
and re-check the constants against it.
"""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timezone

SCORE_MODEL = "v2-families-2026-10-06"

# --- Families ----------------------------------------------------------------

FORECLOSURE = "foreclosure"
TAX_SALE = "tax_sale"
TAX_DELINQUENCY = "tax_delinquency"
CODE_VIOLATION = "code_violation"
ESTATE = "estate"
LIEN = "lien"
EVICTION_COLLECTIONS = "eviction_collections"
OTHER_COURT = "other_court"

SIGNAL_FAMILIES = (FORECLOSURE, TAX_SALE, TAX_DELINQUENCY, CODE_VIOLATION, ESTATE, LIEN, EVICTION_COLLECTIONS, OTHER_COURT)

FAMILY_OF_CASE_TYPE = {
    "foreclosure_notice": FORECLOSURE,
    "tax_sale_filing": TAX_SALE,
    "probate": ESTATE,
    "estate_owned": ESTATE,
    "municipal_lien": LIEN,
    "lien": LIEN,
    "lis_pendens": LIEN,
    "judgment": LIEN,
    "detainer": EVICTION_COLLECTIONS,
    "collections": EVICTION_COLLECTIONS,
}
DEFAULT_FAMILY = OTHER_COURT   # an unknown future case type stays visible as its own family

FAMILY_LABEL = {
    FORECLOSURE: "Foreclosure",
    TAX_SALE: "Tax sale",
    TAX_DELINQUENCY: "Tax delinquent",
    CODE_VIOLATION: "Code violations",
    ESTATE: "Estate/probate",
    LIEN: "Lien",
    EVICTION_COLLECTIONS: "Eviction/collections",
    OTHER_COURT: "Court record",
}

# Families that are stages of ONE process combine before the cross-process
# noisy-OR (see process_raws): a tax sale is where unpaid tax delinquency ends.
PROCESS_OF_FAMILY = {TAX_SALE: "tax", TAX_DELINQUENCY: "tax"}
PROCESS_CORROBORATION_BONUS = 5.0

# _cancelled/_resolved variants: audit trail only — never scored, never in a
# family, never counted toward exposure or tier.
NON_DISTRESS_CASE_TYPES = frozenset({
    "foreclosure_notice_cancelled",
    "tax_sale_resolved",
    "estate_owned_resolved",
    "municipal_lien_resolved",
})

# Peak raw value per case type before timing / amount factors (see each
# family's section for the curve). 0 = never scored.
CASE_TYPE_TIER = {
    "foreclosure_notice": 85,
    "foreclosure_notice_cancelled": 0,
    "tax_sale_filing": 85,
    "tax_sale_resolved": 0,
    "estate_owned": 60,
    "estate_owned_resolved": 0,
    "municipal_lien": 60,
    "municipal_lien_resolved": 0,
    "probate": 60,
    "collections": 40,
    "detainer": 30,
    # not produced by any live module; kept so a stray row scores sanely
    "lis_pendens": 60,
    "lien": 50,
    "judgment": 45,
}
DEFAULT_CASE_TYPE_TIER = 40

FAMILY_MAX_RAW = 90.0             # no family reaches 100 -> no single-signal 100.0 ties
SAME_FAMILY_EXTRA_WEIGHT = 0.25   # 2nd+ record in a family: damped noisy-OR term
TIER_MIN_RAW = 15.0
# Only a 4-5 family stack can get this close (foreclosure 86 + code 85 + tax
# 82 + estate 68 + lien 60 = 99.95); the cap keeps "100.0" from ever
# reappearing as a tie value.
SCORE_CEILING = 99.9


# --- Time ---------------------------------------------------------------------

def as_of_date() -> date:
    """Scoring date: SCORING_AS_OF=YYYY-MM-DD pins it (tests, backfills,
    reproducible comparisons); otherwise today's UTC date, matching the daily
    GitHub Actions run. Scores move day to day as dates approach and history
    decays, even with no new data."""
    pinned = os.environ.get("SCORING_AS_OF")
    if pinned:
        return date.fromisoformat(pinned.strip()[:10])
    return datetime.now(timezone.utc).date()


def parse_date(value) -> date | None:
    """First 10 chars as an ISO date; anything unparseable -> None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _half_life(age_days: float, half_life_days: float) -> float:
    """1.0 at age <= 0, halving every half_life_days."""
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def _log_scale(value: float | None, low: float, high: float, missing: float = 0.5) -> float:
    """0..1 on a log scale clipped to [low, high]; unknown/<=0 -> `missing`
    (0.5 = neutral, so a record just missing one field isn't scored as
    trivial)."""
    if value is None or value <= 0:
        return missing
    lo, hi = math.log10(low), math.log10(high)
    x = max(lo, min(hi, math.log10(value)))
    return (x - lo) / (hi - lo)


def latest_delinquent_bill_year(as_of: date) -> int:
    """Newest tax year that can be delinquent: Hamilton County bills become
    delinquent March 1 of the following year."""
    return as_of.year - 1 if as_of.month >= 3 else as_of.year - 2


# --- Foreclosure ----------------------------------------------------------------

FORECLOSURE_SALE_DAY = 86.0        # sale today (rising linearly from FORECLOSURE_URGENT 30 days out)
FORECLOSURE_URGENT = 82.0          # sale FORECLOSURE_URGENT_DAYS out
FORECLOSURE_URGENT_DAYS = 30
FORECLOSURE_FAR = 72.0             # sale FORECLOSURE_FAR_DAYS+ out, or no usable date
FORECLOSURE_FAR_DAYS = 120
FORECLOSURE_PASSED = 60.0          # date passed, no postponement posted
FORECLOSURE_PASSED_GRACE_DAYS = 7
FORECLOSURE_PASSED_HALF_LIFE = 21.0
FORECLOSURE_HELD_OVER = 60.0       # "postponed (new date not given by source)"
FORECLOSURE_HELD_OVER_DAYS = 60
FORECLOSURE_HELD_OVER_HALF_LIFE = 30.0
FORECLOSURE_FLOOR = 8.0            # long-past sale: probably sold/REO, kept as weak history

_SALE_DATE_RE = re.compile(r"Sale date (\d{4}-\d{2}-\d{2})")
_POSTPONED_TO_RE = re.compile(r"postponed to (\d{4}-\d{2}-\d{2})")
_HELD_OVER_RE = re.compile(r"postponed \(new date not given")


def effective_sale_date(record: dict) -> tuple[date | None, bool]:
    """(sale date, held_over). foreclosure_notices.py writes "Sale date X;
    postponed to Y" / "postponed (new date not given by source)" into
    description; filing_date is a posting OR sale date depending on the site,
    so it is only the fallback."""
    desc = record.get("description") or ""
    m = _POSTPONED_TO_RE.search(desc)
    if m:
        return parse_date(m.group(1)), False
    held_over = bool(_HELD_OVER_RE.search(desc))
    m = _SALE_DATE_RE.search(desc)
    if m:
        return parse_date(m.group(1)), held_over
    return parse_date(record.get("filing_date")), held_over


def foreclosure_raw(sale: date | None, held_over: bool, as_of: date) -> float:
    if sale is None:
        return FORECLOSURE_FAR
    d = (sale - as_of).days
    if d >= FORECLOSURE_FAR_DAYS:
        return FORECLOSURE_FAR
    if d > FORECLOSURE_URGENT_DAYS:
        span = FORECLOSURE_FAR_DAYS - FORECLOSURE_URGENT_DAYS
        return FORECLOSURE_URGENT - (FORECLOSURE_URGENT - FORECLOSURE_FAR) * (d - FORECLOSURE_URGENT_DAYS) / span
    if d >= 0:
        return FORECLOSURE_SALE_DAY - (FORECLOSURE_SALE_DAY - FORECLOSURE_URGENT) * d / FORECLOSURE_URGENT_DAYS
    past = -d
    if held_over:
        if past <= FORECLOSURE_HELD_OVER_DAYS:
            return FORECLOSURE_HELD_OVER
        return max(FORECLOSURE_FLOOR, FORECLOSURE_HELD_OVER * _half_life(past - FORECLOSURE_HELD_OVER_DAYS, FORECLOSURE_HELD_OVER_HALF_LIFE))
    if past <= FORECLOSURE_PASSED_GRACE_DAYS:
        return FORECLOSURE_PASSED
    return max(FORECLOSURE_FLOOR, FORECLOSURE_PASSED * _half_life(past - FORECLOSURE_PASSED_GRACE_DAYS, FORECLOSURE_PASSED_HALF_LIFE))


# --- Tax sale -------------------------------------------------------------------

TAX_SALE_PRESALE = 80.0            # on a published list, auction > TAX_SALE_URGENT_DAYS away
TAX_SALE_URGENT = 85.0             # auction within TAX_SALE_URGENT_DAYS
TAX_SALE_URGENT_DAYS = 60
TAX_SALE_REDEMPTION = 64.0         # auction held, row still ACTIVE: redemption window (see docstring)
TAX_SALE_REDEMPTION_DAYS = 365
TAX_SALE_POST_HALF_LIFE = 90.0
TAX_SALE_FLOOR = 10.0
TAX_SALE_BID_LOW, TAX_SALE_BID_HIGH = 1_000, 25_000
TAX_SALE_RATIO_LOW, TAX_SALE_RATIO_HIGH = 0.02, 0.50   # min bid / max(appraised, TAX_RATIO_VALUE_FLOOR)


def tax_sale_raw(sale: date | None, min_bid: float | None, appraised_value: float | None, as_of: date) -> float:
    """Timing curve x evidence factor 0.9..1.1 (half dollar size of the
    minimum bid, half bid-to-value: more owed relative to the property =
    harder to redeem)."""
    ratio = None
    if min_bid and min_bid > 0 and appraised_value and appraised_value > 0:
        ratio = min_bid / max(appraised_value, TAX_RATIO_VALUE_FLOOR)
    evidence = 0.5 * _log_scale(min_bid, TAX_SALE_BID_LOW, TAX_SALE_BID_HIGH) + 0.5 * _log_scale(ratio, TAX_SALE_RATIO_LOW, TAX_SALE_RATIO_HIGH)
    factor = 0.9 + 0.2 * evidence
    if sale is None:
        base = TAX_SALE_PRESALE
    else:
        d = (sale - as_of).days
        if d > TAX_SALE_URGENT_DAYS:
            base = TAX_SALE_PRESALE
        elif d >= 0:
            base = TAX_SALE_URGENT
        elif -d <= TAX_SALE_REDEMPTION_DAYS:
            base = TAX_SALE_REDEMPTION
        else:
            base = max(TAX_SALE_FLOOR, TAX_SALE_REDEMPTION * _half_life(-d - TAX_SALE_REDEMPTION_DAYS, TAX_SALE_POST_HALF_LIFE))
    return min(base * factor, FAMILY_MAX_RAW)


# --- Estate / probate -------------------------------------------------------------

ESTATE_OWNED_RAW = {"executor_admin": 64.0, "estate": 62.0, "heirs": 60.0, "heirs_coowner": 50.0}
ESTATE_OWNED_DEFAULT = 60.0
ESTATE_PROBATE_LINK_BONUS = 4.0    # estate_parcels.py found exactly one matching probate case
# Up to +4 for a recently recorded transfer (estate_parcels.py: "last recorded
# transfer <date>"; 207 of 219 heirs parcels show a $0 instrument since 2020):
# an estate whose paper is moving now vs a decades-old settled family holding.
# Also breaks the 60.0 plateau (93 identical heirs-only leads otherwise).
ESTATE_RECENT_TRANSFER_POINTS = 4.0
ESTATE_RECENT_TRANSFER_HALF_LIFE = 730.0
_ESTATE_MATCH_RE = re.compile(r"^\s*(\w+):")
_ESTATE_TRANSFER_RE = re.compile(r"last recorded transfer (\d{4}-\d{2}-\d{2})")

PROBATE_HALF_LIFE = 180.0
PROBATE_DEFAULT_TIER = 35.0
# First match wins, most specific first. Matched only on the motion's own
# text: probate_dockets.py appends the attorney/firm after "Atty:" and some
# descriptions carry a neighbouring docket entry after " | " or a docket
# number ("21-0234 ..."), none of which may trigger a keyword.
PROBATE_MOTION_TIERS = (
    (re.compile(r"\b(DISBURSE\w*|DISTRIBUT\w*)\b.*\b(SALE|PROCEEDS)\b"), 20.0, "distribute sale proceeds (already sold)"),
    (re.compile(r"\bAPPROV\w*\b.*\bSALES?\b|\bSALES? (AGREEMENT|PRICE)\b"), 45.0, "approve a sale (buyer likely found)"),
    (re.compile(r"\bSELL\b.*\b(REAL|PROPERTY|ESTATE|HOME|HOUSE|RESIDENCE|LAND|LOTS?)\b|\bLIST\b.*\bSALE\b|"
                r"\bINTO (THE )?ESTATE\b|\b(REAL PROPERTY|REAL ESTATE) SALES?\b"), 60.0, "motion to sell/list real property"),
    (re.compile(r"\bADDITIONAL TIME\b|\bEXTEN(D|SION)\b|\bINSTRUCTIONS?\b|\bSTATUS CONFERENCE\b|\bADMINISTRATION\b|"
                r"\bAPPOINT\w*|\bLETTERS\b|\bINVENTORY\b|\bCLAIMS?\b|\bINSOLVEN\w*|\bANTI-DUST\b|\bHEIR-?SHIP\b|\bENCROACH\b"), 40.0, "estate administration motion"),
    (re.compile(r"\bWITHDRAW\w*|\bFEES?\b|\bACCOUNTING\b|\bDISTRIBUT\w*|\bCLOSE\b|\bDISBURSE\w*|\bATTORNEY|\bREGISTRY\b|\bPAYMENT\b"), 25.0, "routine/closing motion"),
)
_PROBATE_CUT_RE = re.compile(r"Atty:|\s\|\s|\b\d{2}-\d{4}\b")


def probate_motion(description: str | None) -> tuple[float, str]:
    """(tier, plain-English motion kind) from the motion's own text."""
    text = _PROBATE_CUT_RE.split((description or "").upper(), maxsplit=1)[0]
    for pattern, tier, kind in PROBATE_MOTION_TIERS:
        if pattern.search(text):
            return tier, kind
    return PROBATE_DEFAULT_TIER, "probate motion"


def estate_owned_match(description: str | None) -> str | None:
    m = _ESTATE_MATCH_RE.match(description or "")
    return m.group(1) if m else None


def estate_owned_transfer(description: str | None) -> date | None:
    m = _ESTATE_TRANSFER_RE.search(description or "")
    return parse_date(m.group(1)) if m else None


# --- Liens, eviction / collections --------------------------------------------------

LIEN_BASE, LIEN_AMOUNT_POINTS = 50.0, 10.0
LIEN_AMOUNT_LOW, LIEN_AMOUNT_HIGH = 250, 10_000
DETAINER_HALF_LIFE = 180.0
COLLECTIONS_BASE, COLLECTIONS_AMOUNT_POINTS = 30.0, 10.0
COLLECTIONS_HALF_LIFE = 365.0
COLLECTIONS_AMOUNT_LOW, COLLECTIONS_AMOUNT_HIGH = 500, 10_000


def court_record_raw(record: dict, as_of: date, appraised_value: float | None = None) -> float:
    """Raw value of ONE live court record, time included (0 for the
    _cancelled/_resolved audit types)."""
    ct = record.get("case_type")
    if ct in NON_DISTRESS_CASE_TYPES:
        return 0.0
    amount = record.get("amount")
    filed = parse_date(record.get("filing_date"))
    age = (as_of - filed).days if filed else 0
    if ct == "foreclosure_notice":
        sale, held_over = effective_sale_date(record)
        return foreclosure_raw(sale, held_over, as_of)
    if ct == "tax_sale_filing":
        return tax_sale_raw(filed, amount, appraised_value, as_of)
    if ct == "estate_owned":
        desc = record.get("description") or ""
        raw = ESTATE_OWNED_RAW.get(estate_owned_match(desc), ESTATE_OWNED_DEFAULT)
        transfer = estate_owned_transfer(desc)
        if transfer:
            raw += ESTATE_RECENT_TRANSFER_POINTS * _half_life((as_of - transfer).days, ESTATE_RECENT_TRANSFER_HALF_LIFE)
        return raw + (ESTATE_PROBATE_LINK_BONUS if "probate case" in desc else 0.0)
    if ct == "probate":
        tier, _ = probate_motion(record.get("description"))
        return tier * _half_life(age, PROBATE_HALF_LIFE)
    if ct == "municipal_lien":
        return LIEN_BASE + LIEN_AMOUNT_POINTS * _log_scale(amount, LIEN_AMOUNT_LOW, LIEN_AMOUNT_HIGH)
    if ct == "detainer":
        return CASE_TYPE_TIER["detainer"] * _half_life(age, DETAINER_HALF_LIFE)
    if ct == "collections":
        base = COLLECTIONS_BASE + COLLECTIONS_AMOUNT_POINTS * _log_scale(amount, COLLECTIONS_AMOUNT_LOW, COLLECTIONS_AMOUNT_HIGH)
        return base * _half_life(age, COLLECTIONS_HALF_LIFE)
    tier = CASE_TYPE_TIER.get(ct, DEFAULT_CASE_TYPE_TIER)
    return tier * (0.8 + 0.2 * _log_scale(amount, 1_000, 200_000))


def combine_within_family(raws: list[float]) -> float:
    """Strongest record in full; each further record in the same family adds
    a damped noisy-OR term (a re-posted notice is corroboration, not a new
    independent signal). Capped at FAMILY_MAX_RAW."""
    raws = sorted((r for r in raws if r > 0), reverse=True)
    if not raws:
        return 0.0
    survival = 1.0 - min(raws[0], 100.0) / 100.0
    for r in raws[1:]:
        survival *= 1.0 - SAME_FAMILY_EXTRA_WEIGHT * min(r, 100.0) / 100.0
    return min((1.0 - survival) * 100.0, FAMILY_MAX_RAW)


def court_family_raws(records: list[dict], as_of: date, appraised_value: float | None = None) -> dict[str, tuple[float, dict, list[dict]]]:
    """{family: (raw, strongest_record, member_records)} over a parcel's LIVE
    court_records rows (the caller drops _cancelled/_resolved rows; this
    skips them too)."""
    per_family: dict[str, list[tuple[float, dict]]] = {}
    for r in records:
        if r.get("case_type") in NON_DISTRESS_CASE_TYPES:
            continue
        fam = FAMILY_OF_CASE_TYPE.get(r.get("case_type"), DEFAULT_FAMILY)
        per_family.setdefault(fam, []).append((court_record_raw(r, as_of, appraised_value), r))
    out = {}
    for fam, items in per_family.items():
        items.sort(key=lambda t: t[0], reverse=True)
        out[fam] = (combine_within_family([t[0] for t in items]), items[0][1], [t[1] for t in items])
    return out


# --- Tax delinquency ------------------------------------------------------------------

TAX_BASE = 5.0
TAX_AMOUNT_POINTS = 27.0
TAX_AMOUNT_LOW, TAX_AMOUNT_HIGH = 50, 15_000     # ~p6 .. ~p99 of per-parcel totals
TAX_YEAR_POINTS = 5.0                             # per delinquent year beyond the first
TAX_MAX_EXTRA_YEARS = 5                           # 6+ years = full 25
TAX_RATIO_POINTS = 20.0
TAX_RATIO_LOW, TAX_RATIO_HIGH = 0.01, 0.25
TAX_RATIO_VALUE_FLOOR = 25_000                    # denominator floor (slivers / common areas)
TAX_BACK_TAX_POINTS = 5.0                         # Trustee Back Tax Indicator = Y (54% of parcels: weak evidence)
TAX_STALE_GRACE_YEARS = 1
TAX_STALE_HALF_LIFE_YEARS = 2.0
TAX_STALE_FLOOR = 0.2


def tax_staleness_factor(newest_tax_year: int | None, as_of: date) -> float:
    if not newest_tax_year:
        return 1.0
    lag = latest_delinquent_bill_year(as_of) - int(newest_tax_year) - TAX_STALE_GRACE_YEARS
    if lag <= 0:
        return 1.0
    return max(TAX_STALE_FLOOR, 0.5 ** (lag / TAX_STALE_HALF_LIFE_YEARS))


def tax_delinquent_raw(
    total_amount_due: float | None,
    years_delinquent: int | None,
    has_active_back_tax: bool = False,
    appraised_value: float | None = None,
    newest_tax_year: int | None = None,
    as_of: date | None = None,
) -> float:
    """Aggregate over ALL of a parcel's live delinquent-year rows. Points:
    base 5 + amount 0-27 (log $50..$15k) + years 0-25 + debt-to-value 0-20
    (log 1%..25% of max(value, $25k)) + back-tax 5 = max 82, times the
    staleness factor."""
    as_of = as_of or as_of_date()
    years = max(int(years_delinquent or 1), 1)
    pts = TAX_BASE
    pts += TAX_AMOUNT_POINTS * _log_scale(total_amount_due, TAX_AMOUNT_LOW, TAX_AMOUNT_HIGH, missing=0.0)
    pts += TAX_YEAR_POINTS * min(years - 1, TAX_MAX_EXTRA_YEARS)
    if total_amount_due and total_amount_due > 0 and appraised_value and appraised_value > 0:
        ratio = total_amount_due / max(appraised_value, TAX_RATIO_VALUE_FLOOR)
        if ratio > TAX_RATIO_LOW:
            pts += TAX_RATIO_POINTS * _log_scale(ratio, TAX_RATIO_LOW, TAX_RATIO_HIGH)
    if has_active_back_tax:
        pts += TAX_BACK_TAX_POINTS
    return min(pts * tax_staleness_factor(newest_tax_year, as_of), FAMILY_MAX_RAW)


# --- Code violations -------------------------------------------------------------------

VIOLATION_SEVERITY = {
    "condemnation": 1.00,
    "unsafe_structure": 0.85,
    "vacant_building": 0.80,
    "property_maintenance": 0.45,
    "nuisance": 0.30,
}
DEFAULT_VIOLATION_SEVERITY = 0.40
# The same ordering on the old 0-100 scale (CLAUDE.md / code_enforcement.py refer to it by name).
VIOLATION_TYPE_TIER = {k: round(v * 100) for k, v in VIOLATION_SEVERITY.items()}
DEFAULT_VIOLATION_TYPE_TIER = round(DEFAULT_VIOLATION_SEVERITY * 100)

CODE_STATUS_WEIGHT = {"LIT": 1.00, "open": 0.70, "closed": 0.30}
CODE_EXTRA_ROW_WEIGHT = 0.05        # per extra cited ordinance inside one case (max 4 -> x1.2)
CODE_MAX_EXTRA_ROWS = 4
CODE_CLOSED_HALF_LIFE = 365.0
CODE_OPEN_GRACE_DAYS = 365          # an open case only starts to fade after a year
CODE_OPEN_HALF_LIFE = 730.0
CODE_SATURATION = 0.60              # raw = 85 * (1 - exp(-sum / 0.60))
CODE_CLOSED_CAP = 0.32              # closed history saturates at this case value (raw ~35)
CODE_MAX_RAW = 85.0

_RAW_STATUS_RE = re.compile(r"\[(\w+)\]\s*$")


def code_cases(records: list[dict]) -> list[dict]:
    """Collapse violation rows into inspection cases (case_number, falling
    back to record_id). Per case: worst severity, row count, status (LIT >
    open > closed, LIT read from code_enforcement.py's "[LIT]" suffix),
    newest violation_date."""
    cases: dict[str, dict] = {}
    for r in records:
        key = r.get("case_number") or r.get("record_id") or r.get("dedupe_key") or str(r.get("id"))
        c = cases.setdefault(key, {"case": key, "severity": 0.0, "rows": 0, "status": "closed", "date": None, "worst": None})
        vt = r.get("violation_type")
        sev = VIOLATION_SEVERITY.get(vt, DEFAULT_VIOLATION_SEVERITY)
        c["rows"] += 1
        if sev > c["severity"]:
            c["severity"], c["worst"] = sev, vt or "violation"
        if (r.get("status") or "").lower() != "closed":
            m = _RAW_STATUS_RE.search(r.get("description") or "")
            if m and m.group(1).upper() == "LIT":
                c["status"] = "LIT"
            elif c["status"] != "LIT":
                c["status"] = "open"
        d = parse_date(r.get("violation_date"))
        if d and (c["date"] is None or d > c["date"]):
            c["date"] = d
    return list(cases.values())


def code_case_value(case: dict, as_of: date) -> float:
    age = (as_of - case["date"]).days if case["date"] else 0
    if case["status"] == "closed":
        recency = _half_life(age, CODE_CLOSED_HALF_LIFE)
    else:
        recency = _half_life(age - CODE_OPEN_GRACE_DAYS, CODE_OPEN_HALF_LIFE)
    breadth = 1.0 + CODE_EXTRA_ROW_WEIGHT * min(max(case["rows"] - 1, 0), CODE_MAX_EXTRA_ROWS)
    return case["severity"] * breadth * CODE_STATUS_WEIGHT[case["status"]] * recency


def code_enforcement_raw(records: list[dict], as_of: date) -> tuple[float, list[dict]]:
    """(raw, cases). Saturating sum of case values; closed history saturates
    on its own at CODE_CLOSED_CAP, so no number of closed tickets outranks
    one open dangerous-structure case."""
    cases = code_cases(records)
    open_sum = sum(code_case_value(c, as_of) for c in cases if c["status"] != "closed")
    closed_sum = sum(code_case_value(c, as_of) for c in cases if c["status"] == "closed")
    closed_eff = CODE_CLOSED_CAP * (1.0 - math.exp(-closed_sum / CODE_CLOSED_CAP))
    return CODE_MAX_RAW * (1.0 - math.exp(-(open_sum + closed_eff) / CODE_SATURATION)), cases


# --- Owner classes ------------------------------------------------------------------------

OWNER_FLAG_SHARE = {"absentee": 0.12, "entity": 0.08}
ENTITY_OWNER_TYPES = {"llc", "corp", "trust"}
# No absentee/entity terms for these (parcel_utils.NON_PRIVATE_OWNER_TYPES).
NON_PRIVATE_OWNER_TYPES = {"government", "institutional", "lender", "unknown"}
OWNER_CLASS_MULTIPLIER = {"government": 0.25, "institutional": 0.5, "lender": 0.6}
# Not acquirable from a private seller: excluded by the dashboard's "Private
# owners" filter (lender REO is buyable through an agent, so it stays).
NON_ACQUIRABLE_OWNER_TYPES = {"government", "institutional"}
EXEMPT_LAND_USES = {"EX"}
EXEMPT_UNTAXED_MULTIPLIER = 0.35
EXEMPT_CURRENT_BILL_YEARS = 2       # a delinquent bill this recent proves the parcel is taxable now


def is_exempt_untaxed(land_use: str | None, newest_live_tax_year: int | None, has_live_tax_sale: bool, as_of: date) -> bool:
    """Assessor land use EX with no current delinquent bill and no live
    tax-sale row: a public/religious/charity holder (often a county
    strike-off whose owner name was never updated). An EX parcel that is
    still accruing bills, or sits on a tax-sale list, is evidently taxable
    and is NOT demoted."""
    if (land_use or "").strip().upper() not in EXEMPT_LAND_USES or has_live_tax_sale:
        return False
    recent = latest_delinquent_bill_year(as_of) - (EXEMPT_CURRENT_BILL_YEARS - 1)
    return not (newest_live_tax_year and newest_live_tax_year >= recent)


def is_acquirable(owner_type: str | None, exempt_untaxed: bool = False) -> bool:
    return owner_type not in NON_ACQUIRABLE_OWNER_TYPES and not exempt_untaxed


# --- Combination --------------------------------------------------------------------------

def combine_source_scores(raw_values: list[float]) -> float:
    """Noisy-OR of raw values, rounded to 0.1 (unresolved records)."""
    if not raw_values:
        return 0.0
    survival = 1.0
    for raw in raw_values:
        survival *= 1.0 - min(max(raw / 100.0, 0.0), 1.0)
    return round((1.0 - survival) * 100.0, 1)


def process_raws(family_raws: dict[str, float]) -> dict[str, tuple[float, list[str]]]:
    """{process: (raw, member families strongest first)}. Families of one
    process (tax_sale + tax_delinquency) collapse to the stronger raw plus a
    corroboration bonus scaled by how material the weaker one is."""
    groups: dict[str, list[tuple[str, float]]] = {}
    for fam, raw in family_raws.items():
        if raw > 0:
            groups.setdefault(PROCESS_OF_FAMILY.get(fam, fam), []).append((fam, raw))
    out = {}
    for proc, members in groups.items():
        members.sort(key=lambda t: (-t[1], t[0]))
        raw = members[0][1]
        if len(members) > 1:
            raw += PROCESS_CORROBORATION_BONUS * min(1.0, members[1][1] / TIER_MIN_RAW)
        out[proc] = (min(raw, FAMILY_MAX_RAW), [f for f, _ in members])
    return out


def score_parcel(
    family_raws: dict[str, float],
    is_absentee: bool = False,
    owner_type: str | None = None,
    exempt_untaxed: bool = False,
) -> tuple[float, float, list[tuple[str, float]]]:
    """(score, distress_score, contributions).

    distress_score = noisy-OR over processes + owner-flag terms (private
    owners only); score = distress_score x the owner-class / exempt
    multiplier (the strongest one, never compounded). Contributions are the
    sequential marginal gains — processes strongest first (a two-family
    process split into its lead family and the corroborating one), then
    'absentee' / 'entity', then 'owner_class' or 'exempt_untaxed' as a
    negative entry — and sum to the score (to rounding)."""
    procs = sorted(process_raws(family_raws).items(), key=lambda kv: (-kv[1][0], kv[0]))
    if not procs:
        return 0.0, 0.0, []
    contributions: list[tuple[str, float]] = []
    survival, prev = 1.0, 0.0
    for _, (raw, fams) in procs:
        before = survival
        survival *= 1.0 - raw / 100.0
        now = (1.0 - survival) * 100.0
        if len(fams) == 1:
            contributions.append((fams[0], now - prev))
        else:
            lead_now = (1.0 - before * (1.0 - family_raws[fams[0]] / 100.0)) * 100.0
            contributions.append((fams[0], lead_now - prev))
            contributions.append((fams[1], now - lead_now))
        prev = now
    private = owner_type not in NON_PRIVATE_OWNER_TYPES and not exempt_untaxed
    if private:
        p_max = procs[0][1][0] / 100.0
        flags = (["absentee"] if is_absentee else []) + (["entity"] if owner_type in ENTITY_OWNER_TYPES else [])
        for flag in flags:
            survival *= 1.0 - OWNER_FLAG_SHARE[flag] * p_max
            now = (1.0 - survival) * 100.0
            contributions.append((flag, now - prev))
            prev = now
    distress = min(prev, SCORE_CEILING)
    if prev > SCORE_CEILING:
        contributions.append(("ceiling", SCORE_CEILING - prev))
        prev = SCORE_CEILING
    owner_mult = OWNER_CLASS_MULTIPLIER.get(owner_type, 1.0)
    exempt_mult = EXEMPT_UNTAXED_MULTIPLIER if exempt_untaxed else 1.0
    mult = min(owner_mult, exempt_mult)
    if mult < 1.0:
        contributions.append(("owner_class" if owner_mult <= exempt_mult else "exempt_untaxed", prev * mult - prev))
        prev *= mult
    return round(prev, 1), round(distress, 1), contributions


def tier_processes(family_raws: dict[str, float]) -> list[str]:
    """Processes strong enough to count toward the tier."""
    return [p for p, (raw, _) in process_raws(family_raws).items() if raw >= TIER_MIN_RAW]


def label_tier(family_raws: dict[str, float]) -> str:
    """Number of PROCESSES with raw >= TIER_MIN_RAW (not source tables). A
    lead always has at least one record, so 0 or 1 -> single_signal."""
    n = len(tier_processes(family_raws))
    if n >= 3:
        return "triple_threat"
    if n == 2:
        return "multi_factor"
    return "single_signal"


# Completeness tracking (kept separate from score — see completeness())
COMPLETENESS_FIELDS = ["owner_name", "situs_address", "mailing_address", "parcel_id", "latitude"]


def completeness(parcel_row: dict) -> tuple[float, list[str]]:
    """Returns (completeness_score in [0,1], list of missing field names).
    Tracked separately from the distress score so an incomplete record
    doesn't get defaulted to "low distress" just because it's thin — it
    should instead be flagged for enrichment.
    """
    missing = [f for f in COMPLETENESS_FIELDS if not parcel_row.get(f)]
    score = (len(COMPLETENESS_FIELDS) - len(missing)) / len(COMPLETENESS_FIELDS)
    return round(score, 2), missing


def calibrate_from_data(conn) -> None:
    """Diagnostic only — prints the distributions the constants above were
    set from (see CALIBRATION STATUS). Counts and percentiles only, never a
    name or address. Does not modify anything."""
    import statistics

    as_of = as_of_date()

    def _pct(label: str, values: list[float], money: bool = True) -> None:
        if not values:
            print(f"{label}: no data yet")
            return
        values = sorted(values)
        pick = lambda p: values[min(int(len(values) * p / 100), len(values) - 1)]  # noqa: E731
        fmt = (lambda v: f"${v:,.0f}") if money else (lambda v: f"{v:.4g}")
        print(f"{label}: n={len(values)} " + " ".join(f"p{p}={fmt(pick(p))}" for p in (5, 25, 50, 75, 90, 99))
              + f" mean={fmt(statistics.mean(values))}")

    print(f"as of {as_of}, model {SCORE_MODEL}")
    _pct("court_records.amount", [r[0] for r in conn.execute("SELECT amount FROM court_records WHERE amount > 0")])
    _pct("tax_sale min bid", [r[0] for r in conn.execute(
        "SELECT amount FROM court_records WHERE case_type = 'tax_sale_filing' AND amount > 0")])
    # Live rows = exactly the rows build_unified scores: its stale-row rule
    # (latest full-ingest reference + partial-ingest guard), not MAX(last_seen_at),
    # which one bumped row would skew. Imported lazily — build_unified imports
    # this module at load time.
    from build_unified import mark_unlisted
    tax_rows = [
        {"parcel_id": r[0], "tax_year": r[1], "amount_due": r[2], "last_seen_at": r[3]}
        for r in conn.execute("SELECT parcel_id, tax_year, amount_due, last_seen_at FROM tax_delinquent")
    ]
    mark_unlisted({"tax_delinquent": tax_rows, "code_enforcement": []})
    values = dict(conn.execute("SELECT parcel_id, appraised_value FROM parcels").fetchall())
    agg: dict[str, list] = {}
    for r in tax_rows:
        if r["parcel_id"] is None or r.get("_unlisted"):
            continue
        a = agg.setdefault(r["parcel_id"], [0.0, set()])
        a[0] += r["amount_due"] or 0.0
        if r["tax_year"] is not None:
            a[1].add(r["tax_year"])
    per_parcel = [
        (total, len(years), max(years) if years else None, values.get(pid))
        for pid, (total, years) in agg.items()
    ]
    _pct("tax per-parcel total (live rows)", [r[0] for r in per_parcel if r[0]])
    _pct("tax per-parcel years", [r[1] for r in per_parcel], money=False)
    _pct("tax debt / max(value, $25k)", [r[0] / max(r[3], TAX_RATIO_VALUE_FLOOR) for r in per_parcel if r[0] and r[3]], money=False)
    stale = sum(1 for r in per_parcel if r[2] and tax_staleness_factor(r[2], as_of) < 1)
    print(f"tax parcels whose newest delinquent bill is stale: {stale} of {len(per_parcel)}")
    for table, col in (("court_records", "case_type"), ("code_enforcement", "violation_type"), ("code_enforcement", "status")):
        rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {table} GROUP BY {col}").fetchall()
        print(f"{table}.{col}: {dict(rows)}")
    cases = conn.execute(
        "SELECT parcel_id, COUNT(DISTINCT case_number), COUNT(DISTINCT CASE WHEN status = 'open' THEN case_number END) "
        "FROM code_enforcement WHERE parcel_id IS NOT NULL GROUP BY parcel_id"
    ).fetchall()
    _pct("code cases per parcel", [r[1] for r in cases], money=False)
    print(f"code parcels with >=1 open case: {sum(1 for r in cases if r[2])} of {len(cases)}")
    lit = conn.execute(
        "SELECT violation_type, COUNT(DISTINCT case_number), "
        "COUNT(DISTINCT CASE WHEN description LIKE '%[LIT]' THEN case_number END) FROM code_enforcement GROUP BY 1"
    ).fetchall()
    print("code cases referred to litigation by type: " + ", ".join(f"{t}: {l}/{n}" for t, n, l in lit))
    print("land use EX parcels:", conn.execute("SELECT COUNT(*) FROM parcels WHERE land_use = 'EX'").fetchone()[0])
