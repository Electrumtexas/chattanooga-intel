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

Each per-source file adds that source's own extra columns flattened at the
top level (e.g. case_type, amount on court_records.json rows). Only
unified_leads.json nests full per-signal detail under `signals.<source>`
(one row per parcel there, vs. one row per case/violation in the per-source
files).

`score`/`tier` are always the parcel's *combined* score across every signal
it has — even on a single-source page a parcel that also shows up in
another source displays its true elevated score, not a single-source view
of it. A record that never resolved to a parcel_id gets a single-signal-only
score computed from just itself (no absentee/entity bonus, since those live
on the parcel) — it can still be sorted and reviewed in Verify Mode, but its
low completeness_score flags it as needing enrichment rather than being
scored as low-distress.

dashboard/meta.json (not in the original file list, added here) carries
generation timestamp, per-source record counts, and address-resolution
fallback rates — small, additive, and needed because the quality guardrail
and the "how fresh/complete is this data" question both need somewhere to
surface. Documented in CLAUDE.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from db import get_connection, now_iso
from parcel_utils import assessor_card_url
from scoring import (
    CASE_TYPE_TIER,
    DEFAULT_CASE_TYPE_TIER,
    DEFAULT_VIOLATION_TYPE_TIER,
    VIOLATION_TYPE_TIER,
    code_enforcement_raw,
    combine_source_scores,
    completeness,
    compute_score,
    court_record_raw,
    label_tier,
    tax_delinquent_raw,
)

# Overridable so integration testing against a scratch CHATTANOOGA_DB never
# writes into the committed dashboard/ directory — mirrors db.py's
# CHATTANOOGA_DB override for the same reason.
DASHBOARD_DIR = Path(os.environ.get("DASHBOARD_DIR") or Path(__file__).resolve().parent.parent / "dashboard")

SOURCE_TABLES = ["court_records", "tax_delinquent", "code_enforcement"]


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
    "first_seen_at", "last_seen_at", "resolution_method",
}


def _trim_signal(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in _SIGNAL_BOOKKEEPING_KEYS and v is not None}


def _is_entity(owner_type: str | None) -> bool:
    return owner_type in ("llc", "corp", "trust")


def _court_top_signal(r: dict, extra_count: int = 0) -> str:
    label = (r.get("case_type") or "case").replace("_", " ").title()
    amt = f" — ${r['amount']:,.0f}" if r.get("amount") else ""
    date = f" filed {r['filing_date']}" if r.get("filing_date") else ""
    more = f" (+{extra_count} more case{'s' if extra_count > 1 else ''})" if extra_count else ""
    return f"{label}{date}{amt}{more}"


def _tax_top_signal(total_due: float, years: int) -> str:
    amt = f" — ${total_due:,.0f}" if total_due else ""
    yrs = f" across {years} yr" if years and years > 1 else ""
    return f"Tax delinquent{yrs}{amt}"


def _code_top_signal(r: dict, extra_count: int = 0) -> str:
    label = (r.get("violation_type") or "violation").replace("_", " ").title()
    status = f" — {r['status']}" if r.get("status") else ""
    more = f" (+{extra_count} more)" if extra_count else ""
    return f"{label} violation{status}{more}"


def load_all_records(conn) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    for table in SOURCE_TABLES:
        records[table] = [_row_to_dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return records



# Case types that represent a closed/resolved chapter of a court record rather
# than live distress — e.g. a tax-sale docket entry that was PAID or REMOVED
# before auction. CASE_TYPE_TIER already scores these at 0, but a parcel whose
# *only* court records are of this type could still pick up a nonzero
# court_record_raw() from the multi-case stacking bonus — excluded from the
# scoring group entirely (still shown in the dashboard's per-signal detail;
# just not counted toward score/exposure/case-count).
_NON_DISTRESS_CASE_TYPES = {"tax_sale_resolved", "foreclosure_notice_cancelled"}


def aggregate_source_group(source: str, group: list[dict]) -> tuple[float, str, float]:
    """Collapse ALL of one parcel's records within a single source into one
    (raw_score, top_signal_text, exposure_dollars) triple, so a parcel with
    e.g. 6 delinquent tax years counts as ONE tax_delinquent signal in the
    cross-source combination (see compute_score) rather than 6 independent
    ones — the years/case-count still matter, but as a stacking bonus on a
    single source-level raw value, not as separate noisy-OR terms.
    """
    if source == "court_records":
        active = [r for r in group if r.get("case_type") not in _NON_DISTRESS_CASE_TYPES] or group
        best = max(active, key=lambda r: CASE_TYPE_TIER.get(r.get("case_type"), DEFAULT_CASE_TYPE_TIER))
        total_amount = sum(r.get("amount") or 0.0 for r in active)
        raw = court_record_raw(best.get("case_type"), total_amount, case_count=len(active))
        return raw, _court_top_signal(best, len(group) - 1), total_amount

    if source == "tax_delinquent":
        total_due = sum(r.get("amount_due") or 0.0 for r in group)
        years = len({r.get("tax_year") for r in group if r.get("tax_year") is not None}) or len(group)
        has_back_tax = any((r.get("back_tax_indicator") or "").strip().upper() == "Y" for r in group)
        raw = tax_delinquent_raw(total_due, years, has_active_back_tax=has_back_tax)
        return raw, _tax_top_signal(total_due, years), total_due

    best = max(group, key=lambda r: VIOLATION_TYPE_TIER.get(r.get("violation_type"), DEFAULT_VIOLATION_TYPE_TIER))
    raw = code_enforcement_raw(best.get("violation_type"), len(group))
    return raw, _code_top_signal(best, len(group) - 1), 0.0


def build_parcel_aggregates(conn, records: dict[str, list[dict]]) -> dict[str, dict]:
    """One aggregate per parcel_id: combined score/tier/top_signal/exposure/
    which sources it appears in, built from every signal attached to it.
    """
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
        parcel = parcels_by_id.get(pid, {"parcel_id": pid})
        per_source_raw: dict[str, float] = {}
        per_source_text: dict[str, str] = {}
        total_exposure = 0.0
        event_dates = []
        for source, group in by_source.items():
            raw, text, exposure = aggregate_source_group(source, group)
            per_source_raw[source] = raw
            per_source_text[source] = text
            total_exposure += exposure
            for r in group:
                d = r.get("filing_date") or r.get("violation_date")
                if d:
                    event_dates.append(d)

        sources_present = sorted(per_source_raw.keys())
        score = compute_score(
            list(per_source_raw.values()),
            is_absentee=bool(parcel.get("is_absentee")),
            owner_type=parcel.get("owner_type"),
        )
        top_source = max(per_source_raw, key=per_source_raw.get)
        comp_score, comp_missing = completeness(parcel)
        aggregates[pid] = {
            "parcel": parcel,
            "score": score,
            "tier": label_tier(sources_present),
            "sources": sources_present,
            "top_signal": per_source_text[top_source],
            "total_exposure": round(total_exposure, 2),
            "event_date": max(event_dates) if event_dates else None,
            "completeness_score": comp_score,
            "completeness_missing": comp_missing,
            "signals": {source: [_trim_signal(r) for r in group] for source, group in by_source.items()},
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
    for pid, agg in aggregates.items():
        row = common_fields(
            agg["parcel"], agg["score"], agg["tier"], agg["event_date"],
            agg["total_exposure"], agg["top_signal"], agg["completeness_score"], agg["completeness_missing"],
        )
        row["id"] = pid
        row["sources"] = agg["sources"]
        row["signals"] = agg["signals"]
        leads.append(row)
    leads.sort(key=lambda r: r["score"], reverse=True)
    return leads


def build_source_export(source: str, records: list[dict], aggregates: dict[str, dict]) -> list[dict]:
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
        else:
            # Unresolved record: no parcel to join to, so score using only this
            # one signal and flag it as incomplete rather than "low distress."
            raw, top_signal, exposure = aggregate_source_group(source, [r])
            score = combine_source_scores([raw])
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

        row = dict(base)
        row["id"] = f"{source}:{r['id']}"
        row.update({k: v for k, v in r.items() if k not in ("id", "parcel_id")})
        rows.append(row)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def build_meta(records: dict[str, list[dict]], leads: list[dict]) -> dict:
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
        }
    tier_counts: dict[str, int] = {}
    for lead in leads:
        tier_counts[lead["tier"]] = tier_counts.get(lead["tier"], 0) + 1
    return {
        "generated_at": now_iso(),
        "unified_lead_count": len(leads),
        "tier_counts": tier_counts,
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
    conn = get_connection()
    records = load_all_records(conn)
    aggregates = build_parcel_aggregates(conn, records)

    unified_leads = build_unified_leads(aggregates)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    write_json(DASHBOARD_DIR / "unified_leads.json", unified_leads)

    for source in SOURCE_TABLES:
        rows = build_source_export(source, records[source], aggregates)
        write_json(DASHBOARD_DIR / f"{source}.json", rows)

    write_json(DASHBOARD_DIR / "meta.json", build_meta(records, unified_leads), pretty=True)

    print(f"Wrote {len(unified_leads)} unified leads across {len(aggregates)} parcels.")
    for source in SOURCE_TABLES:
        print(f"  {source}: {len(records[source])} records")


if __name__ == "__main__":
    main()
