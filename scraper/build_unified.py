"""
Reads chattanooga.db, joins every source to its parcel, scores each parcel,
and writes the exact JSON files the dashboard reads. This is the only place
that writes to dashboard/*.json — those files are always fully regenerated
here, never hand-edited or partially patched.

Output contract (dashboard/*.json)
-----------------------------------
Every record in every export file — court_records.json, tax_delinquent.json,
code_enforcement.json, and unified_leads.json — shares this common field
set, so the dashboard's single generic rendering engine (map, table, filter
bar, KPI strip) can treat all four uniformly:

    id, parcel_id, situs_address, city, zip, owner_name, mailing_address,
    owner_type, is_entity_owner, is_absentee, latitude, longitude,
    land_use, appraised_value, assessor_card_url,
    score, tier, sources, event_date, total_exposure, top_signal,
    completeness_score, completeness_missing, last_updated

unified_leads.json rows additionally carry (added 2026-10-06, score model v2
— kept off the per-source files, whose rows the dashboard joins to their
parcel's unified lead by parcel_id):

    family_scores    {family: raw 0-90}, strongest first — every signal
                     family on the parcel (scoring.SIGNAL_FAMILIES); those
                     >= meta.tier_min_raw count toward the tier
    score_breakdown  list of plain-English lines whose signed numbers sum to
                     `score`: each family's contribution and why, owner
                     flags, and any owner-class demotion
    distress_score   the score before the owner-class / exempt demotion
                     (equals `score` for private owners)
    acquirable       False for government / institutional owners and for an
                     exempt (land use EX) parcel with no current tax bill
    next_sale_date   earliest upcoming foreclosure or tax-sale date, or null

Meaning notes (v2): `sources` is unchanged — the source TABLES a parcel has
any record in (it drives the dashboard's Court/Tax/Code filter). `tier` now
counts signal processes (foreclosure, tax, code, estate, lien,
eviction/collections — tax sale + tax delinquency count once) strong enough
to matter, not source tables; a non-acquirable parcel (acquirable=False)
is capped at single_signal, so the Triple Threat / Multi-Factor filters only
list parcels a private buyer can approach. `owner_type` gains 'government',
'institutional', 'lender' and 'unknown' (placeholder name) and is re-derived
from owner_name at build time (parcel_utils.classify_owner_type), so a
classifier change applies to every parcel at once. `top_signal` is the
strongest family's headline. `total_exposure` counts only live records.

Each per-source file adds that source's own extra columns flattened at the
top level (e.g. case_type, amount on court_records.json rows). Only
unified_leads.json nests full per-signal detail under `signals.<source>`
(one row per parcel there, vs. one row per case/violation in the per-source
files).

`score`/`tier` are always the parcel's *combined* score across every signal
it has — even on a single-source page a parcel that also shows up in
another source displays its true elevated score, not a single-source view
of it. A record that never resolved to a parcel_id gets a single-signal-only
score computed from just itself (no owner flags or demotion, since those live
on the parcel) — it can still be sorted and reviewed in Verify Mode, but its
low completeness_score flags it as needing enrichment rather than being
scored as low-distress.

Live vs audit-only records. Never scored, never counted toward exposure or
tier, but still shown in `signals` / the per-source files and in `sources`:
  - court_records rows of a _cancelled/_resolved case type
    (scoring.NON_DISTRESS_CASE_TYPES);
  - tax_delinquent / code_enforcement rows the latest pull no longer
    contains. Both scrapers re-upsert every row of a full file on each
    ingest (tax_delinquent daily; code_enforcement whenever the city's CSV
    changes), so a row whose last_seen date is more than
    SNAPSHOT_STALE_TOLERANCE_DAYS before the table's latest FULL-ingest date
    has left the source: a paid bill, or a closed case that aged out of the
    730-day window. The reference is the newest date carried by >= 10% of the
    table's rows (latest_ingest_date), NOT MAX(last_seen_at): parcel_id
    backfills used to bump single rows' last_seen_at, and with MAX one bumped
    row unscored the whole code table (release review, 2026-10-06). Backfills
    now pass upsert_record(touch_last_seen=False). Guard: when the rows that
    dropped out AT THE LATEST INGEST exceed 25% of (live + that batch) — a
    partial ingest — that batch stays scored and meta.json says
    stale_rule_skipped; rows dropped at earlier ingests stay unlisted, so the
    ever-growing history of paid bills / aged-out cases never trips it (see
    mark_unlisted). Measured on the 2026-10-06 snapshot:
    515 tax rows (446 parcels, 378 of them fully paid) and 312 code rows
    (311 closed 2024 rows + 1 open 2024 case; none in the 2026-10-02 city
    CSV). Relative to each table's own ingest,
    so a source outage marks nothing stale.

dashboard/meta.json carries generation timestamp, per-source record counts
(plus `not_in_latest_pull`), address-resolution fallback rates, tier counts
and score-model monitoring fields (score_model, scoring_as_of, tier_min_raw,
family_counts, owner_type_counts, score_deciles, score_ge_99,
non_acquirable_leads) — small, additive, and needed because the quality
guardrail and the "how fresh/complete is this data" question both need
somewhere to surface. Documented in CLAUDE.md.

stdout prints counts only (GitHub Actions logs on this repo are public).
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from db import get_connection, now_iso
from parcel_utils import assessor_card_url, classify_owner_type
from scoring import (
    CODE_VIOLATION,
    EXEMPT_UNTAXED_MULTIPLIER,
    FAMILY_LABEL,
    NON_DISTRESS_CASE_TYPES,
    OWNER_CLASS_MULTIPLIER,
    SCORE_MODEL,
    TAX_DELINQUENCY,
    TAX_RATIO_VALUE_FLOOR,
    TAX_SALE,
    TAX_SALE_REDEMPTION_DAYS,
    TIER_MIN_RAW,
    as_of_date,
    code_enforcement_raw,
    combine_source_scores,
    completeness,
    court_family_raws,
    effective_sale_date,
    estate_owned_match,
    estate_owned_transfer,
    is_acquirable,
    is_exempt_untaxed,
    label_tier,
    parse_date,
    probate_motion,
    score_parcel,
    tax_delinquent_raw,
    tax_staleness_factor,
)

# Overridable so integration testing against a scratch CHATTANOOGA_DB never
# writes into the committed dashboard/ directory — mirrors db.py's
# CHATTANOOGA_DB override for the same reason.
DASHBOARD_DIR = Path(os.environ.get("DASHBOARD_DIR") or Path(__file__).resolve().parent.parent / "dashboard")

SOURCE_TABLES = ["court_records", "tax_delinquent", "code_enforcement"]

# Full-snapshot tables (see the module docstring, "Live vs audit-only").
SNAPSHOT_TABLES = ("tax_delinquent", "code_enforcement")
# Rows whose last_seen date is more than this many days before the table's
# latest full-ingest date are unlisted. 1 (not 0) only so an ingest that
# straddles midnight UTC can't split itself; the daily cron runs at 09:00 UTC.
SNAPSHOT_STALE_TOLERANCE_DAYS = 1
# A date counts as a full ingest when at least this share of the table's rows
# carry it (a real ingest touches ~95-100%; a backfill bump touched 0.4%).
SNAPSHOT_INGEST_MIN_SHARE = 0.10
# Partial-ingest guard: if the rows that dropped out AT THE LATEST INGEST are
# more than this share of (live + that batch), the batch stays scored. Older
# drops are never part of the test (see mark_unlisted).
SNAPSHOT_MAX_UNLISTED_SHARE = 0.25
# ...and only while that batch is recent: when the previous full ingest is more
# than this many days before the latest one, a large drop is treated as a real
# shrink of the source (e.g. a narrower date window), not a partial download,
# so the guard can't hold it scored forever. Ingests run daily (tax) or every
# few days (code: whenever the city's CSV changes), and a truncated file of the
# kind the guard exists for also fails quality_check's 30% blocking floor.
SNAPSHOT_GUARD_MAX_DAYS = 14


def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()}


# Bookkeeping columns every source table carries that add nothing for the
# dashboard's detail view once a signal is nested under a unified lead —
# parcel_id/raw_address/raw_owner_name are already on the parent lead
# object, and dedupe_key/first_seen_at/last_seen_at/resolution_method are
# pipeline internals. Dropping these cut unified_leads.json roughly in
# half on the first real data pull (7,926 leads, ~14MB -> much smaller) —
# worth trimming since the whole file has to be fetched and parsed
# client-side with no backend to paginate it.
_SIGNAL_BOOKKEEPING_KEYS = {
    "id", "dedupe_key", "parcel_id", "raw_address", "raw_owner_name",
    "first_seen_at", "last_seen_at", "resolution_method", "_unlisted",
}


def _trim_signal(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in _SIGNAL_BOOKKEEPING_KEYS and v is not None}


def _is_entity(owner_type: str | None) -> bool:
    return owner_type in ("llc", "corp", "trust")


# case_types whose `filing_date` actually holds an auction/sale date, not a
# filing date — several foreclosure posting sites expose only a sale date
# (no separate "posted" date), and tax_sale.py's PDF only has the auction
# date, so `filing_date` is reused for it (see those modules' docstrings).
# Rendering it with the word "filed" is actively misleading: a future sale
# date next to "filed" reads as a contradiction (caught 2026-10-06 — a real
# notice's "filed 2026-11-19" was a sale date three weeks out, not a filing).
_SALE_DATED_CASE_TYPES = {"foreclosure_notice", "foreclosure_notice_cancelled", "tax_sale_filing", "tax_sale_resolved"}


def _court_top_signal(r: dict, extra_count: int = 0) -> str:
    label = (r.get("case_type") or "case").replace("_", " ").title()
    amt = f" — ${r['amount']:,.0f}" if r.get("amount") else ""
    if r.get("filing_date"):
        verb = "sale" if r.get("case_type") in _SALE_DATED_CASE_TYPES else "filed"
        date_txt = f" {verb} {r['filing_date']}"
    else:
        date_txt = ""
    more = f" (+{extra_count} more case{'s' if extra_count > 1 else ''})" if extra_count else ""
    return f"{label}{date_txt}{amt}{more}"


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _days(d: date, as_of: date) -> str:
    n = (d - as_of).days
    if n == 0:
        return "today"
    return f"in {n} d" if n > 0 else f"{-n} d ago"


def load_all_records(conn) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    for table in SOURCE_TABLES:
        records[table] = [_row_to_dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return records


def latest_ingest_date(seen: list[date | None]) -> date | None:
    """The date of the table's latest FULL ingest: the newest last_seen date
    that at least SNAPSHOT_INGEST_MIN_SHARE of the rows share.

    Not MAX(last_seen_at): a handful of rows can carry a newer date than the
    last full ingest without the source having been re-read (before
    db.upsert_record grew touch_last_seen, enrich_assessor's parcel_id
    backfills bumped last_seen_at; 156 code rows in the 2026-10-06 snapshot
    carry such dates). Using MAX made ONE bumped row put every other code row
    "behind the newest pull" — 29,081 of 29,082 rows went unscored in the
    release review's reproduction. A full ingest touches ~all rows on one
    date, so the newest date with a real share of the table is that ingest.
    """
    counts: dict[date, int] = {}
    for d in seen:
        if d:
            counts[d] = counts.get(d, 0) + 1
    total = sum(counts.values())
    if not total:
        return None
    for d in sorted(counts, reverse=True):
        if counts[d] >= SNAPSHOT_INGEST_MIN_SHARE * total:
            return d
    return max(counts)  # no date dominates (shouldn't happen): fall back to newest


def mark_unlisted(records: dict[str, list[dict]]) -> tuple[dict[str, int], dict[str, bool]]:
    """Flags (r['_unlisted'] = True) snapshot-table rows the latest full pull
    no longer contains. Returns (per-table flagged counts, per-table
    "rule skipped" flags) for meta.json.

    A row is unlisted when its last_seen date is more than
    SNAPSHOT_STALE_TOLERANCE_DAYS before the table's latest full-ingest date
    (latest_ingest_date).

    Partial-ingest guard, judged on the LATEST drop only: the batch of rows
    last seen on/near the previous full ingest (latest_ingest_date over the
    stale rows' dates) is the set that dropped out at the latest ingest. If
    that batch is more than SNAPSHOT_MAX_UNLISTED_SHARE of (live rows + the
    batch), and the previous ingest is within SNAPSHOT_GUARD_MAX_DAYS of the
    latest one, the batch stays scored (stale_rule_skipped=True); rows that
    dropped out at earlier ingests stay unlisted. Accumulated history never
    enters the test — both tables keep every row forever, so a
    share-of-whole-table test would switch the rule off permanently after
    months of normal churn. The day limit stops a genuine source shrink from
    being held scored forever.
    """
    tol = timedelta(days=SNAPSHOT_STALE_TOLERANCE_DAYS)
    counts: dict[str, int] = {}
    skipped: dict[str, bool] = {}
    for table in SNAPSHOT_TABLES:
        rows = records.get(table) or []
        seen = [parse_date(r.get("last_seen_at")) for r in rows]
        ref = latest_ingest_date(seen)
        stale = [i for i, d in enumerate(seen) if ref and d and d < ref - tol]
        live = len(rows) - len(stale)
        # The guard judges only the rows that dropped out at the latest ingest
        # (last seen on/near the previous full ingest), never the accumulated
        # history: neither table deletes rows, so earlier drops pile up and a
        # share-of-whole-table test would trip permanently after months of
        # normal churn (release review round 2: 8 monthly batches of ~500 paid
        # bills reach 25% of tax_delinquent).
        prev_ref = latest_ingest_date([seen[i] for i in stale])
        newly: set[int] = set()
        if prev_ref and (ref - prev_ref).days <= SNAPSHOT_GUARD_MAX_DAYS:
            newly = {i for i in stale if seen[i] >= prev_ref - tol}
        if newly and len(newly) > SNAPSHOT_MAX_UNLISTED_SHARE * (live + len(newly)):
            # Suspected partial ingest: keep this batch scored; rows that were
            # already unlisted before it stay unlisted.
            skipped[table] = True
            stale = [i for i in stale if i not in newly]
        else:
            skipped[table] = False
        for i in stale:
            rows[i]["_unlisted"] = True
        counts[table] = len(stale)
    return counts, skipped


def _is_live(source: str, r: dict) -> bool:
    if r.get("_unlisted"):
        return False
    return not (source == "court_records" and r.get("case_type") in NON_DISTRESS_CASE_TYPES)


# --- Per-family text (name-free) ----------------------------------------------------
# Each family gets a short `headline` (top_signal — repeated on every per-source
# row, so kept compact) and a fuller `why` (the score_breakdown line).

_ESTATE_KIND = {
    "heirs": ("heirs", "title with heirs"),
    "heirs_coowner": ("deceased co-owner", "a deceased co-owner's share with heirs"),
    "estate": ("decedent's estate", "title with a decedent's estate"),
    "executor_admin": ("executor/administrator", "title with an executor/administrator"),
}


def _court_text(fam: str, best: dict, members: list[dict], appraised_value, as_of: date) -> tuple[str, str]:
    ct = best.get("case_type")
    more = len(members) - 1
    more_txt = f" (+{more} more)" if more else ""
    if ct == "foreclosure_notice":
        sale, held_over = effective_sale_date(best)
        if sale is None:
            return f"Foreclosure notice{more_txt}", f"foreclosure notice, sale date unknown{more_txt}"
        if sale >= as_of:
            return (f"Foreclosure sale {sale} ({_days(sale, as_of)}){more_txt}",
                    f"foreclosure sale {sale} ({_days(sale, as_of)}){more_txt}")
        if held_over:
            return (f"Foreclosure held over at {sale} sale{more_txt}",
                    f"held over at the {sale} sale, new date not posted ({_days(sale, as_of)}){more_txt}")
        return (f"Foreclosure sale date {sale} passed{more_txt}",
                f"sale date {sale} passed ({_days(sale, as_of)}), no postponement posted; verify{more_txt}")
    if ct == "tax_sale_filing":
        sale = parse_date(best.get("filing_date"))
        bid = f", min bid {_money(best['amount'])}" if best.get("amount") else ""
        ratio = ""
        if best.get("amount") and appraised_value:
            ratio = f" ({100 * best['amount'] / max(appraised_value, TAX_RATIO_VALUE_FLOOR):.0f}% of value)"
        if sale and sale < as_of:
            end = date.fromordinal(sale.toordinal() + TAX_SALE_REDEMPTION_DAYS)
            window = f"redemption window to ~{end}" if as_of <= end else "redemption window likely closed"
            short = "redemption window" if as_of <= end else "redemption window likely closed"
            return (f"Tax sale {sale} held, still listed ({short}){bid}",
                    f"still ACTIVE after the {sale} auction: {window}{bid}{ratio}")
        when = f"{sale} ({_days(sale, as_of)})" if sale else "date unknown"
        return f"Tax sale {when}{bid}", f"tax-sale list, auction {when}{bid}{ratio}"
    if fam == "estate":
        estate_rows = [r for r in members if r.get("case_type") == "estate_owned"]
        if estate_rows:
            short, long = _ESTATE_KIND.get(estate_owned_match(estate_rows[0].get("description")), ("estate", "estate-owned title"))
            link = ", matching probate case" if "probate case" in (estate_rows[0].get("description") or "") else ""
            transfer = estate_owned_transfer(estate_rows[0].get("description"))
            since = f", last transfer recorded {transfer}" if transfer else ""
            return f"Estate-owned ({short}){more_txt}", f"{long} (county owner record{since}{link}){more_txt}"
        _, kind = probate_motion(best.get("description"))
        return (f"Probate: {kind}{more_txt}",
                f"probate {kind}, docket {best.get('filing_date') or 'undated'}{more_txt}")
    if ct == "municipal_lien":
        amt = f" {_money(best['amount'])}" if best.get("amount") else ""
        return (f"Municipal lien{amt}{more_txt}",
                f"city municipal lien{amt} (abatement cost billed to the owner){more_txt}")
    if fam == "eviction_collections":
        kinds: dict[str, int] = {}
        for r in members:
            kinds[r.get("case_type") or "case"] = kinds.get(r.get("case_type") or "case", 0) + 1
        latest = max((r.get("filing_date") for r in members if r.get("filing_date")), default=None)
        what = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        text = f"{what}{f' (latest {latest})' if latest else ''}"
        return text[:1].upper() + text[1:], text
    return _court_top_signal(best, more), _court_top_signal(best, more)


def _tax_text(info: dict, as_of: date) -> tuple[str, str]:
    yrs = f" across {info['years']} yr" if info["years"] > 1 else ""
    headline = f"Tax delinquent{yrs} — {_money(info['total_due'])}"
    parts = [f"{_money(info['total_due'])} over {info['years']} yr"]
    if info.get("ratio") is not None:
        parts.append(f"{info['ratio'] * 100:.1f}% of value")
    if info.get("back_tax"):
        parts.append("back-tax flag")
    if info.get("newest_year") and tax_staleness_factor(info["newest_year"], as_of) < 1:
        parts.append(f"newest bill {info['newest_year']} (stale)")
        headline += f" (newest bill {info['newest_year']})"
    return headline, ", ".join(parts)


_CODE_RANK = {"LIT": 2, "open": 1, "closed": 0}


def _code_text(cases: list[dict]) -> tuple[str, str]:
    best = max(cases, key=lambda c: (_CODE_RANK[c["status"]], c["severity"], c["date"] or date.min))
    label = (best["worst"] or "violation").replace("_", " ").title()
    status = {"LIT": "open, in litigation", "open": "open", "closed": "closed"}[best["status"]]
    more = len(cases) - 1
    headline = f"{label} violation — {status}" + (f" (+{more} more case{'s' if more > 1 else ''})" if more else "")
    n_open = sum(1 for c in cases if c["status"] != "closed")
    n_lit = sum(1 for c in cases if c["status"] == "LIT")
    worst = max(cases, key=lambda c: c["severity"])["worst"] or "violation"
    newest = max((c["date"] for c in cases if c["date"]), default=None)
    lit = f" ({n_lit} in litigation)" if n_lit else ""
    why = (f"{n_open} open{lit} of {len(cases)} case{'s' if len(cases) != 1 else ''}, "
           f"worst {worst.replace('_', ' ')}, newest {newest or 'undated'}")
    return headline, why


# --- Families ------------------------------------------------------------------------

def family_signals(source: str, group: list[dict], appraised_value=None, as_of: date | None = None) -> dict[str, dict]:
    """Collapse one parcel's LIVE records within ONE source table into
    per-FAMILY entries {family: {"raw", "headline", "why", "exposure",
    "next_sale", ...}}. court_records can yield several families;
    tax_delinquent yields `tax_delinquency`; code_enforcement yields
    `code_violation`. Multiple records within a family collapse to ONE raw
    value."""
    as_of = as_of or as_of_date()
    live = [r for r in group if _is_live(source, r)]
    if not live:
        return {}
    if source == "court_records":
        out = {}
        for fam, (raw, best, members) in court_family_raws(live, as_of, appraised_value).items():
            upcoming = []
            for r in members:
                if r.get("case_type") == "foreclosure_notice":
                    sale = effective_sale_date(r)[0]
                elif r.get("case_type") == "tax_sale_filing":
                    sale = parse_date(r.get("filing_date"))
                else:
                    sale = None
                if sale and sale >= as_of:
                    upcoming.append(sale)
            headline, why = _court_text(fam, best, members, appraised_value, as_of)
            out[fam] = {
                "raw": raw, "headline": headline, "why": why,
                "exposure": sum(r.get("amount") or 0.0 for r in members),
                "next_sale": min(upcoming) if upcoming else None,
            }
        return out

    if source == "tax_delinquent":
        total_due = sum(r.get("amount_due") or 0.0 for r in live)
        years = len({r.get("tax_year") for r in live if r.get("tax_year") is not None}) or len(live)
        newest = max((r.get("tax_year") for r in live if r.get("tax_year") is not None), default=None)
        back_tax = any((r.get("back_tax_indicator") or "").strip().upper() == "Y" for r in live)
        info = {
            "raw": tax_delinquent_raw(total_due, years, back_tax, appraised_value, newest, as_of),
            "exposure": total_due, "next_sale": None, "total_due": total_due, "years": years,
            "newest_year": newest, "back_tax": back_tax,
            "ratio": total_due / appraised_value if appraised_value else None,
        }
        info["headline"], info["why"] = _tax_text(info, as_of)
        return {TAX_DELINQUENCY: info}

    raw, cases = code_enforcement_raw(live, as_of)
    headline, why = _code_text(cases)
    return {CODE_VIOLATION: {"raw": raw, "headline": headline, "why": why, "exposure": 0.0, "next_sale": None}}


def _breakdown(contributions, fam_info: dict, owner_type, as_of: date) -> list[str]:
    out = []
    for key, delta in contributions:
        if key in fam_info:
            raw = fam_info[key]["raw"]
            weak = "; below tier cutoff" if raw < TIER_MIN_RAW else ""
            out.append(f"{FAMILY_LABEL.get(key, key)} {delta:+.1f} (raw {raw:.0f}): {fam_info[key]['why']}{weak}")
        elif key == "absentee":
            out.append(f"Absentee owner {delta:+.1f}")
        elif key == "entity":
            out.append(f"Entity owner ({owner_type}) {delta:+.1f}")
        elif key == "owner_class":
            out.append(f"{(owner_type or 'non-private').title()} owner x{OWNER_CLASS_MULTIPLIER[owner_type]:g} {delta:+.1f} (not a motivated private seller)")
        elif key == "ceiling":
            out.append(f"Score ceiling {delta:+.1f}")
        elif key == "exempt_untaxed":
            out.append(f"Exempt land use, no current tax bill x{EXEMPT_UNTAXED_MULTIPLIER:g} {delta:+.1f} (likely public/charity-held)")
    return out


def build_parcel_aggregates(conn, records: dict[str, list[dict]], as_of: date | None = None) -> dict[str, dict]:
    """One aggregate per parcel_id: combined score/tier/top_signal/exposure,
    which source TABLES it appears in (`sources`, the dashboard filter's
    contract) and which signal FAMILIES drive the score (`family_scores`,
    `score_breakdown`).
    """
    as_of = as_of or as_of_date()
    parcels_by_id = {
        r["parcel_id"]: _row_to_dict(r)
        for r in conn.execute("SELECT * FROM parcels").fetchall()
    }

    grouped: dict[str, dict[str, list[dict]]] = {}
    for source in SOURCE_TABLES:
        for r in records[source]:
            pid = r.get("parcel_id")
            if not pid:
                continue
            grouped.setdefault(pid, {}).setdefault(source, []).append(r)

    aggregates: dict[str, dict] = {}
    for pid, by_source in grouped.items():
        parcel = dict(parcels_by_id.get(pid, {"parcel_id": pid}))
        # Re-derive owner_type at build time so a classifier change applies to
        # every parcel immediately, not only to rows a scraper re-touches.
        parcel["owner_type"] = classify_owner_type(parcel.get("owner_name")) or parcel.get("owner_type")
        owner_type = parcel["owner_type"]
        fam_info: dict[str, dict] = {}
        event_dates, live_event_dates = [], []
        for source, group in by_source.items():
            fam_info.update(family_signals(source, group, parcel.get("appraised_value"), as_of))
            for r in group:
                d = r.get("filing_date") or r.get("violation_date")
                if d:
                    event_dates.append(d)
                    if _is_live(source, r):
                        live_event_dates.append(d)
        # event_date (dashboard date filter + 4th sort key) comes from live
        # records only, so an audit-only resolved/cancelled or no-longer-listed
        # row can't move a parcel within a tie; all rows only as a fallback.
        event_dates = live_event_dates or event_dates

        family_raws = {fam: info["raw"] for fam, info in fam_info.items() if info["raw"] > 0}
        newest_tax = fam_info.get(TAX_DELINQUENCY, {}).get("newest_year")
        exempt_untaxed = is_exempt_untaxed(parcel.get("land_use"), newest_tax, TAX_SALE in fam_info, as_of)
        score, distress, contributions = score_parcel(
            family_raws,
            is_absentee=bool(parcel.get("is_absentee")),
            owner_type=owner_type,
            exempt_untaxed=exempt_untaxed,
        )
        ordered = sorted(family_raws, key=lambda f: (-family_raws[f], f))
        if ordered:
            top_signal = fam_info[ordered[0]]["headline"]
        else:
            top_signal = "No live signal — only resolved/cancelled or no-longer-listed records"
        upcoming = [info["next_sale"] for info in fam_info.values() if info.get("next_sale")]
        next_sale = min(upcoming).isoformat() if upcoming else None
        latest_event = max(event_dates) if event_dates else None
        latest_day = parse_date(latest_event)
        comp_score, comp_missing = completeness(parcel)
        acquirable = is_acquirable(owner_type, exempt_untaxed)
        aggregates[pid] = {
            "parcel": parcel,
            "score": score,
            "distress_score": distress,
            # Tier is a lead-quality label, so a non-acquirable parcel
            # (government / institutional owner, exempt strike-off) is capped
            # at single_signal; its families stay visible in family_scores.
            "tier": label_tier(family_raws) if acquirable else "single_signal",
            "sources": sorted(by_source.keys()),
            "family_scores": {fam: round(family_raws[fam], 1) for fam in ordered},
            "score_breakdown": _breakdown(contributions, fam_info, owner_type, as_of),
            "acquirable": acquirable,
            "next_sale_date": next_sale,
            "top_signal": top_signal,
            "total_exposure": round(sum(info["exposure"] for info in fam_info.values()), 2),
            "event_date": latest_event,
            "completeness_score": comp_score,
            "completeness_missing": comp_missing,
            "signals": {source: [_trim_signal(r) for r in group] for source, group in by_source.items()},
            # Deterministic order: score, then total family evidence, then the
            # sooner upcoming sale, then the most recent event, then parcel id.
            "_sort_key": (
                -score,
                -round(sum(family_raws.values()), 6),
                next_sale or "9999-12-31",
                -(latest_day.toordinal() if latest_day else 0),
                pid,
            ),
        }

    return aggregates


def common_fields(parcel: dict, score: float, tier: str, event_date, total_exposure, top_signal, comp_score, comp_missing) -> dict:
    owner_type = parcel.get("owner_type")
    return {
        "parcel_id": parcel.get("parcel_id"),
        "situs_address": parcel.get("situs_address"),
        "city": parcel.get("situs_city"),
        "zip": parcel.get("situs_zip"),
        "owner_name": parcel.get("owner_name"),
        "mailing_address": parcel.get("mailing_address"),
        "owner_type": owner_type,
        "is_entity_owner": _is_entity(owner_type),
        "is_absentee": bool(parcel.get("is_absentee")),
        "latitude": parcel.get("latitude"),
        "longitude": parcel.get("longitude"),
        "land_use": parcel.get("land_use"),
        "appraised_value": parcel.get("appraised_value"),
        "assessor_card_url": assessor_card_url(parcel.get("parcel_id")),
        "score": score,
        "tier": tier,
        "event_date": event_date,
        "total_exposure": total_exposure,
        "top_signal": top_signal,
        "completeness_score": comp_score,
        "completeness_missing": comp_missing,
        "last_updated": now_iso(),
    }


def build_unified_leads(aggregates: dict[str, dict]) -> list[dict]:
    leads = []
    for pid in sorted(aggregates, key=lambda p: aggregates[p]["_sort_key"]):
        agg = aggregates[pid]
        row = common_fields(
            agg["parcel"], agg["score"], agg["tier"], agg["event_date"],
            agg["total_exposure"], agg["top_signal"], agg["completeness_score"], agg["completeness_missing"],
        )
        row["id"] = pid
        row["sources"] = agg["sources"]
        row["family_scores"] = agg["family_scores"]
        row["score_breakdown"] = agg["score_breakdown"]
        row["distress_score"] = agg["distress_score"]
        row["acquirable"] = agg["acquirable"]
        row["next_sale_date"] = agg["next_sale_date"]
        row["signals"] = agg["signals"]
        leads.append(row)
    # Already in deterministic order; the dashboard's stable sort by score
    # preserves it among equal scores.
    return leads


def build_source_export(source: str, records: list[dict], aggregates: dict[str, dict], as_of: date | None = None) -> list[dict]:
    as_of = as_of or as_of_date()
    rows = []
    empty_parcel_shell = {"parcel_id": None}
    for r in records:
        pid = r.get("parcel_id")
        if pid and pid in aggregates:
            agg = aggregates[pid]
            base = common_fields(
                agg["parcel"], agg["score"], agg["tier"], agg["event_date"],
                agg["total_exposure"], agg["top_signal"], agg["completeness_score"], agg["completeness_missing"],
            )
            base["sources"] = agg["sources"]
            sort_key = agg["_sort_key"]
        else:
            # Unresolved record: no parcel to join to, so score using only this
            # one signal and flag it as incomplete rather than "low distress."
            fams = family_signals(source, [r], None, as_of)
            score = combine_source_scores([f["raw"] for f in fams.values()])
            exposure = sum(f["exposure"] for f in fams.values())
            if fams:
                fam = max(fams, key=lambda k: fams[k]["raw"])
                top_signal = fams[fam]["headline"]
            elif source == "court_records":
                top_signal = _court_top_signal(r)
            else:
                top_signal = "No longer in the latest pull (paid / aged out)"
            comp_score, comp_missing = completeness({
                "owner_name": r.get("raw_owner_name"),
                "situs_address": r.get("raw_address"),
                "mailing_address": None,
                "parcel_id": pid,
                "latitude": r.get("latitude"),
            })
            base = common_fields(
                empty_parcel_shell, score, "single_signal", None, round(exposure, 2), top_signal, comp_score, comp_missing,
            )
            base["situs_address"] = r.get("raw_address")
            base["owner_name"] = r.get("raw_owner_name")
            # Some sources (code_enforcement) carry their own lat/long even before
            # parcel resolution — surface it so the map has something to plot.
            if r.get("latitude") is not None:
                base["latitude"] = r.get("latitude")
                base["longitude"] = r.get("longitude")
            base["sources"] = [source]
            sort_key = (-score, 0.0, "9999-12-31", 0, "")

        row = dict(base)
        row["id"] = f"{source}:{r['id']}"
        row.update({k: v for k, v in r.items() if k not in ("id", "parcel_id", "_unlisted")})
        rows.append((sort_key, row["id"], row))
    rows.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in rows]


def build_meta(records: dict[str, list[dict]], leads: list[dict], unlisted: dict[str, int] | None = None, as_of: date | None = None,
               stale_rule_skipped: dict[str, bool] | None = None) -> dict:
    per_source = {}
    for source, rows in records.items():
        total = len(rows)
        native = sum(1 for r in rows if r.get("resolution_method") == "native_parcel_id")
        resolved = sum(1 for r in rows if r.get("resolution_method") == "address_match")
        point = sum(1 for r in rows if r.get("resolution_method") == "point_in_parcel")
        fallback = sum(1 for r in rows if r.get("resolution_method") == "owner_name_fallback")
        unresolved = total - native - resolved - point - fallback
        per_source[source] = {
            "total_records": total,
            "resolved_by_native_parcel_id": native,
            "resolved_by_address": resolved,
            "resolved_by_point_in_parcel": point,
            "resolved_by_owner_name_fallback": fallback,
            "unresolved": unresolved,
            "fallback_rate": round(fallback / total, 3) if total else 0.0,
            "not_in_latest_pull": (unlisted or {}).get(source, 0),
        }
        if source in (stale_rule_skipped or {}):
            per_source[source]["stale_rule_skipped"] = stale_rule_skipped[source]
    tier_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    owner_type_counts: dict[str, int] = {}
    for lead in leads:
        tier_counts[lead["tier"]] = tier_counts.get(lead["tier"], 0) + 1
        for fam, raw in (lead.get("family_scores") or {}).items():
            if raw >= TIER_MIN_RAW:
                family_counts[fam] = family_counts.get(fam, 0) + 1
        ot = lead.get("owner_type") or "none"
        owner_type_counts[ot] = owner_type_counts.get(ot, 0) + 1
    scores = sorted(lead["score"] for lead in leads)
    deciles = [scores[min(int(len(scores) * p / 10), len(scores) - 1)] for p in range(1, 10)] if scores else []
    return {
        "generated_at": now_iso(),
        "score_model": SCORE_MODEL,
        "scoring_as_of": (as_of or as_of_date()).isoformat(),
        "tier_min_raw": TIER_MIN_RAW,
        "unified_lead_count": len(leads),
        "tier_counts": tier_counts,
        "family_counts": family_counts,
        "owner_type_counts": owner_type_counts,
        "non_acquirable_leads": sum(1 for lead in leads if lead.get("acquirable") is False),
        "score_deciles": deciles,
        "score_ge_99": sum(1 for s in scores if s >= 99),
        "sources": per_source,
    }


def write_json(path: Path, data, pretty: bool = False) -> None:
    """Compact by default — these files are fetched and parsed client-side
    with no backend to paginate them, so every byte counts. Pretty-printed
    only for meta.json, which is small and meant to be human-glanceable.
    """
    kwargs = {"indent": 2} if pretty else {"separators": (",", ":")}
    path.write_text(json.dumps(data, default=str, **kwargs), encoding="utf-8")


def main() -> None:
    as_of = as_of_date()
    conn = get_connection()
    records = load_all_records(conn)
    unlisted, stale_skipped = mark_unlisted(records)
    aggregates = build_parcel_aggregates(conn, records, as_of)

    unified_leads = build_unified_leads(aggregates)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    write_json(DASHBOARD_DIR / "unified_leads.json", unified_leads)

    for source in SOURCE_TABLES:
        rows = build_source_export(source, records[source], aggregates, as_of)
        write_json(DASHBOARD_DIR / f"{source}.json", rows)

    write_json(DASHBOARD_DIR / "meta.json", build_meta(records, unified_leads, unlisted, as_of, stale_skipped), pretty=True)

    # Counts only — GitHub Actions logs on this repo are public.
    print(f"Wrote {len(unified_leads)} unified leads across {len(aggregates)} parcels (score model {SCORE_MODEL}, as of {as_of}).")
    for source in SOURCE_TABLES:
        extra = f", {unlisted[source]} no longer in the latest pull (unscored)" if source in unlisted else ""
        if stale_skipped.get(source):
            extra += (" — partial-ingest guard tripped: the latest ingest dropped > 25% of rows, "
                      "that batch is kept scored; check the last ingest")
        print(f"  {source}: {len(records[source])} records{extra}")


if __name__ == "__main__":
    main()
