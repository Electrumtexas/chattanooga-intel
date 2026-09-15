"""
Phase 2 — Hamilton County Trustee delinquent tax file.

Live-verified 2026-09-15 (Sonnet session, not a checkpoint item — the spec
flagged this as "check whether it's automatable," and it turned out to be
straightforward once actually fetched). Key findings, each confirmed
against a real downloaded file rather than assumed:

Direct download, no manual step needed
---------------------------------------
tpti.hamiltontn.gov's "Delinquent File Download" link actually points to
www.hamiltontn.gov (a different host), and resolves to a plain direct file
download — no ASP.NET postback, no form, no session. The spec's fallback
plan (Jarrod downloads monthly, renames, commits manually) is NOT needed.
Two variants exist; we use the CSV one:
  https://www.hamiltontn.gov/_downloadsTrusteeDelinquent/CTRUDELQCSV.zip
File was last modified 2026-09-01 when checked — matches the spec's
"updates monthly" expectation.

This is a ledger, not a "what's newly delinquent" list
--------------------------------------------------------
The file holds ~54,500 rows, one row per (Map, Group, Parcel, Bill Year) —
a parcel accumulates a new row per delinquent year, some going back to
1999 (max seen: 26 distinct years on one parcel). ~97.5% of rows carry a
nonzero "current owed" balance, meaning most of what's in this file is
still outstanding, not historical/paid-off. We only ingest rows where
county+muni+stormwater current-owed sums to more than zero; a parcel's
total delinquency (summed across its years) is computed later in
build_unified.py, not here — see scoring.py's tax_delinquent_raw().

Money field conventions (verified against population percentiles, not a
single-row guess — a wrong guess here would have made every dollar
figure in the dashboard wrong by 100x with no error to catch it)
--------------------------------------------------------------------------
  - "Current *Owed" and "*Amount" columns: cents, implied 2 decimals.
    e.g. "000046906" == $469.06. Confirmed because interpreting them as
    whole dollars gives a median "current owed" of $16,519 and a p90 of
    $84,700 — implausible for county+muni+stormwater on a single year's
    bill — while the /100 reading gives a median of $165 and p90 of $847,
    consistent with real tax-bill magnitudes.
  - "Assessment"/"Original Assessment": whole dollars, NOT cents.
    Confirmed the opposite way — /100 gives a median assessed value of
    $72 (impossible for real property), while treating it as whole
    dollars gives a median of $7,200, which lines up with Tennessee's 25%
    residential assessment ratio against a very low appraised value —
    exactly what you'd expect a delinquent-tax list to skew toward.

Personal-property accounts are mixed into this file — filtered out
--------------------------------------------------------------------------
~77% of currently-owed rows have Map='PER' (40,592) or 'OSAP' (219) rather
than a real Map/Group/Parcel — these are business PERSONAL PROPERTY tax
accounts (equipment/inventory), not real estate. Confirmed by their "Land
Use" field being blank on 100% of them, vs. a real classification code
(111, 910, 112, ...) on every row with a digit-leading Map. A personal-
property tax debt doesn't correspond to a piece of real estate Jarrod
could approach an owner about buying, so these are excluded entirely
rather than ingested as parcels with no real property behind them. Only
rows whose Map starts with a digit are kept.

Legacy-export messiness handled defensively, not by assuming clean input
--------------------------------------------------------------------------
  - ~2.4% of rows are ragged (fewer columns than the header, from
    whatever embedded-character issue produced the export) — skipped and
    counted, not crashed on.
  - Three header names carry stray trailing whitespace ("Mail Addr 3 ",
    "Legal Description 2 "/"3 ") and "Filler" appears 7 times — headers
    are stripped before use; the Filler columns are never read.
  - "Back Tax Indicator" is 'Y'/'N' on well-formed rows but contains
    stray numeric junk on some of the ragged ones; only an exact 'Y'/'N'
    is kept, anything else stored as None (and None safely fails the
    scoring bonus's `== "Y"` check rather than producing a wrong value).
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import requests

from db import get_connection, log_scrape, upsert_parcel, upsert_record
from parcel_utils import (
    classify_owner_type,
    format_parcel_id,
    is_absentee,
    parse_cents,
)

ZIP_URL = "https://www.hamiltontn.gov/_downloadsTrusteeDelinquent/CTRUDELQCSV.zip"
SOURCE_URL = "https://tpti.hamiltontn.gov/"
RAW_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "tax_delinquent"
REQUIRED_FIELDS = ["Map", "Group", "Parcel", "Bill Year", "Current County Owed", "Current Mun Owed", "Current Stw Owed"]


def fetch() -> str:
    """Download the zip, extract the CSV, cache it to data/raw/, return the
    raw CSV text. Caching here (not gitignored-out at the tooling level,
    just not committed per .gitignore) lets parse() be re-run and fixed
    against the same pull without re-downloading a ~29MB file each time.
    """
    resp = requests.get(ZIP_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        csv_bytes = zf.read(csv_name)
    text = csv_bytes.decode("latin-1")

    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_CACHE_DIR / "CTRUDELQCSV.csv").write_text(text, encoding="utf-8")
    return text


def parse(csv_text: str) -> tuple[list[dict], dict]:
    """Returns (records, stats). Each record is one currently-owed
    (parcel, tax_year) bill — aggregation across a parcel's years happens
    in build_unified.py, not here.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]

    records = []
    stats = {"total_rows": 0, "malformed": 0, "zero_balance": 0, "personal_property_excluded": 0, "kept": 0}

    for row in reader:
        stats["total_rows"] += 1
        if any(row.get(f) is None for f in REQUIRED_FIELDS):
            stats["malformed"] += 1
            continue

        map_field = (row.get("Map") or "").strip()
        if not map_field or not map_field[0].isdigit():
            stats["personal_property_excluded"] += 1
            continue

        county_owed = parse_cents(row["Current County Owed"])
        mun_owed = parse_cents(row["Current Mun Owed"])
        stw_owed = parse_cents(row["Current Stw Owed"])
        if county_owed is None or mun_owed is None or stw_owed is None:
            stats["malformed"] += 1
            continue

        total_owed = county_owed + mun_owed + stw_owed
        if total_owed <= 0:
            stats["zero_balance"] += 1
            continue

        try:
            tax_year = int(row["Bill Year"].strip())
        except (ValueError, AttributeError):
            stats["malformed"] += 1
            continue

        parcel_id = format_parcel_id(row.get("Map"), row.get("Group"), row.get("Parcel"))
        if parcel_id is None:
            stats["malformed"] += 1
            continue

        back_tax = (row.get("Back Tax Indicator") or "").strip().upper()
        mailing_parts = [row.get("Mail Addr 1"), row.get("Mail Addr 2"), row.get("Mail Addr 3")]
        mailing_address = " ".join(p.strip() for p in mailing_parts if p and p.strip()) or None

        records.append({
            "parcel_id": parcel_id,
            "tax_year": tax_year,
            "amount_due": round(total_owed, 2),
            "status": "delinquent",
            "back_tax_indicator": back_tax if back_tax in ("Y", "N") else None,
            "mortgage_company": (row.get("Mortgage Company Name") or "").strip() or None,
            "raw_address": (row.get("Property Address") or "").strip() or None,
            "raw_owner_name": (row.get("Owner Name 1") or "").strip() or None,
            "mailing_address": mailing_address,
        })
        stats["kept"] += 1

    return records, stats


def upsert(conn, records: list[dict]) -> int:
    for r in records:
        upsert_parcel(
            conn, r["parcel_id"],
            situs_address=r["raw_address"],
            owner_name=r["raw_owner_name"],
            mailing_address=r["mailing_address"],
            owner_type=classify_owner_type(r["raw_owner_name"]),
            is_absentee=is_absentee(r["raw_address"], r["mailing_address"]),
        )
        dedupe_key = f"trustee:{r['parcel_id']}:{r['tax_year']}"
        upsert_record(
            conn, "tax_delinquent", dedupe_key,
            parcel_id=r["parcel_id"],
            tax_year=r["tax_year"],
            amount_due=r["amount_due"],
            status=r["status"],
            back_tax_indicator=r["back_tax_indicator"],
            mortgage_company=r["mortgage_company"],
            raw_address=r["raw_address"],
            raw_owner_name=r["raw_owner_name"],
            resolution_method="native_parcel_id",  # source gives Map/Group/Parcel directly — stronger confidence than a geocoded address match
            source_url=SOURCE_URL,
        )
    return len(records)


def main() -> None:
    conn = get_connection()
    csv_text = fetch()
    records, stats = parse(csv_text)
    count = upsert(conn, records)
    conn.commit()
    log_scrape(conn, "tax_delinquent", record_count=count, status="ok", notes=str(stats))
    conn.commit()
    print(f"tax_delinquent: upserted {count} currently-delinquent bills. {stats}")


if __name__ == "__main__":
    main()
