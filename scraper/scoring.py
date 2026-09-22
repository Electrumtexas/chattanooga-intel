"""
All score math lives here — build_unified.py calls into this module and
nowhere else computes a score.

Why not flat base+bonus capped at 100
--------------------------------------
A flat "+30 per signal, capped at 100" scheme saturates as soon as 2-3
signals stack: a parcel with a lien + one open code violation and a parcel
with a foreclosure + tax sale + three open violations both end up pinned at
100, even though the second is a dramatically stronger lead. That destroys
the dashboard's ability to rank within its own top tier.

The approach here instead:
  1. Each source produces a *raw* distress value (roughly 0-100, but not
     hard-capped pre-combination) from a category tier plus dollar-amount
     scaling (`_amount_scale`).
  2. Small additive flag bonuses (absentee owner, entity ownership) are
     applied to those raw values *before* combination — see `compute_score`.
  3. Multiple sources combine via a noisy-OR style diminishing-returns
     function (`combine_source_scores`): treat each raw value/100 as an
     independent "probability of distress," combine as
     1 - product(1 - p_i), rescale to 0-100. This pushes the combined score
     up with *diminishing* marginal effect per added signal, spreading
     multi-signal leads across the usable range instead of clumping them at
     the ceiling — without a hard cap discontinuity.

CALIBRATION STATUS — placeholder, not yet tuned on real data
--------------------------------------------------------------
CASE_TYPE_TIER, VIOLATION_TYPE_TIER, and the low/high breakpoints passed to
`_amount_scale` below are reasonable starting guesses, not calibrated
against Hamilton County's actual pulled data. Once Phase 1-3 scrapes have
real records, run `calibrate_from_data()` to print the actual 25th/50th/
75th/90th percentile dollar amounts per source and hand-tune the constants
below against them — see CLAUDE.md pending-work list. Do not treat
cross-lead score comparisons as trustworthy until that pass happens.
"""

from __future__ import annotations

import math

# --- Per-source category tiers (0-100 base distress value) -----------------

CASE_TYPE_TIER = {
    "foreclosure_notice": 90,
    "foreclosure_notice_cancelled": 0,  # cancelled/withdrawn sale — audit trail only, not live distress
    "tax_sale_filing": 90,
    "tax_sale_resolved": 0,  # PAID/REMOVED from the tax-sale docket — audit trail only, not live distress
    "lis_pendens": 70,
    "lien": 65,
    "collections": 55,
    "judgment": 60,
    "probate": 40,
    "detainer": 35,  # a landlord filing eviction is a moderate "tired landlord" signal, not owner financial distress
}
DEFAULT_CASE_TYPE_TIER = 50

VIOLATION_TYPE_TIER = {
    "condemnation": 85,
    "unsafe_structure": 80,
    "vacant_building": 70,
    "nuisance": 45,
    "property_maintenance": 40,
}
DEFAULT_VIOLATION_TYPE_TIER = 35

TAX_DELINQUENT_BASE = 50
TAX_DELINQUENT_PER_YEAR_BONUS = 8
TAX_DELINQUENT_MAX_YEAR_BONUS_YEARS = 5
TAX_DELINQUENT_BACK_TAX_BONUS = 10  # Trustee file's Back Tax Indicator='Y' — already past routine delinquency, in active collection

COURT_RECORD_STACKING_BONUS_PER_CASE = 5
COURT_RECORD_MAX_STACKING_CASES = 3

# Flag bonuses — small, additive, applied pre-combination (see compute_score)
ABSENTEE_OWNER_BONUS = 8
ENTITY_OWNER_BONUS = 6
ENTITY_OWNER_TYPES = {"llc", "corp", "trust"}

# Completeness tracking (kept separate from score — see completeness())
COMPLETENESS_FIELDS = ["owner_name", "situs_address", "mailing_address", "parcel_id", "latitude"]


def _amount_scale(amount: float | None, low: float, high: float) -> float:
    """Map a dollar amount to a 0-1 multiplier on a log scale, clipped to
    [low, high]. An unknown amount returns 0.5 (neutral) rather than 0, so a
    record just missing that one field isn't scored as if it were trivial.
    """
    if amount is None or amount <= 0:
        return 0.5
    lo, hi = math.log10(low), math.log10(high)
    x = max(lo, min(hi, math.log10(amount)))
    return (x - lo) / (hi - lo)


def court_record_raw(case_type: str | None, total_amount: float | None, case_count: int = 1) -> float:
    """`case_type` should be the most severe case type among this parcel's
    court records (highest CASE_TYPE_TIER); `total_amount` the sum of amounts
    across all of them; `case_count` how many distinct court records this
    parcel has — multiple filings against the same property is itself an
    escalation signal, so it adds a small stacking bonus on top of the tier.
    """
    tier = CASE_TYPE_TIER.get(case_type, DEFAULT_CASE_TYPE_TIER)
    scale = _amount_scale(total_amount, low=1_000, high=200_000)
    stacking_bonus = min(max(case_count - 1, 0), COURT_RECORD_MAX_STACKING_CASES) * COURT_RECORD_STACKING_BONUS_PER_CASE
    return min(tier * (0.6 + 0.4 * scale) + stacking_bonus, 100)


def tax_delinquent_raw(total_amount_due: float | None, years_delinquent: int | None, has_active_back_tax: bool = False) -> float:
    """`total_amount_due` and `years_delinquent` are aggregates across ALL of
    a parcel's delinquent-year rows (see build_unified.py), not a single
    row — most delinquent parcels carry several years on file at once, and
    treating each year as an independent signal would double-count.
    """
    years = min(years_delinquent or 1, TAX_DELINQUENT_MAX_YEAR_BONUS_YEARS)
    base = TAX_DELINQUENT_BASE + years * TAX_DELINQUENT_PER_YEAR_BONUS
    if has_active_back_tax:
        base += TAX_DELINQUENT_BACK_TAX_BONUS
    scale = _amount_scale(total_amount_due, low=500, high=50_000)
    return min(base * (0.6 + 0.4 * scale), 100)


def code_enforcement_raw(violation_type: str | None, violation_count: int | None) -> float:
    """`violation_type` should be the most severe among this parcel's
    violations; `violation_count` how many total records it has. Ideally
    this would filter to currently-open violations only, but real
    code_enforcement data (Phase 3) needs to confirm the status field is
    reliable enough for that before switching — see CLAUDE.md.
    """
    base = VIOLATION_TYPE_TIER.get(violation_type, DEFAULT_VIOLATION_TYPE_TIER)
    stacking_bonus = min(max((violation_count or 1) - 1, 0), 4) * 6
    return min(base + stacking_bonus, 100)


def combine_source_scores(raw_values: list[float]) -> float:
    """Noisy-OR combination: each raw value/100 is treated as an independent
    'probability of distress'; combined = 1 - product(1 - p_i). Stacking
    signals raises the score with diminishing marginal effect per addition,
    instead of a flat sum that either undercounts or saturates.
    """
    if not raw_values:
        return 0.0
    combined_survival = 1.0
    for raw in raw_values:
        p = min(max(raw / 100.0, 0.0), 1.0)
        combined_survival *= 1.0 - p
    return round((1.0 - combined_survival) * 100, 1)


def compute_score(
    source_raw_values: list[float],
    is_absentee: bool = False,
    owner_type: str | None = None,
) -> float:
    """Apply flag bonuses to each raw value (pre-combination), then combine."""
    raw_values = list(source_raw_values)
    if is_absentee:
        raw_values = [v + ABSENTEE_OWNER_BONUS for v in raw_values]
    if owner_type in ENTITY_OWNER_TYPES:
        raw_values = [v + ENTITY_OWNER_BONUS for v in raw_values]
    raw_values = [min(v, 100.0) for v in raw_values]
    return combine_source_scores(raw_values)


def label_tier(sources_present: list[str]) -> str:
    n = len(set(sources_present))
    if n >= 3:
        return "triple_threat"
    if n == 2:
        return "multi_factor"
    return "single_signal"


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
    """Diagnostic only — run manually once Phase 1-3 have pulled real data.
    Prints percentile breakpoints per source so CASE_TYPE_TIER,
    VIOLATION_TYPE_TIER, and the low/high args to _amount_scale above can be
    hand-tuned against reality. Does not modify anything itself.
    """
    import statistics

    def _print_percentiles(label: str, values: list[float]) -> None:
        if not values:
            print(f"{label}: no data yet")
            return
        values = sorted(values)
        pcts = {p: values[min(int(len(values) * p / 100), len(values) - 1)] for p in (25, 50, 75, 90)}
        print(
            f"{label}: n={len(values)} "
            f"p25=${pcts[25]:,.0f} p50=${pcts[50]:,.0f} p75=${pcts[75]:,.0f} p90=${pcts[90]:,.0f} "
            f"mean=${statistics.mean(values):,.0f}"
        )

    court_amounts = [r[0] for r in conn.execute(
        "SELECT amount FROM court_records WHERE amount IS NOT NULL AND amount > 0"
    ).fetchall()]
    tax_amounts = [r[0] for r in conn.execute(
        "SELECT amount_due FROM tax_delinquent WHERE amount_due IS NOT NULL AND amount_due > 0"
    ).fetchall()]
    _print_percentiles("court_records.amount", court_amounts)
    _print_percentiles("tax_delinquent.amount_due", tax_amounts)

    for table, col in (("court_records", "case_type"), ("code_enforcement", "violation_type")):
        rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {table} GROUP BY {col}").fetchall()
        print(f"{table}.{col} distribution: {dict(rows)}")
