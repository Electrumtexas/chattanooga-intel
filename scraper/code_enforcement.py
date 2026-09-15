"""
Phase 3 — ChattaData open-data portal (Socrata SODA API), "Code Enforcement
- Violations" dataset.

Dataset ID qcrz-rvw7 confirmed CURRENT as of 2026-09-15 (matches the spec's
"as of Sept 2026" note) via web search — the dataset page
https://www.chattadata.org/Public-Safety/Code-Enforcement-Violations/qcrz-rvw7
resolves and search results describe it as violations data from the city's
Cityview code-enforcement system.

IMPORTANT — exact field names NOT live-verified, confirm on first real run
--------------------------------------------------------------------------
Every attempt to actually fetch JSON from chattadata.org during this
session failed: the fetch tool returned "socket closed" on
/resource/qcrz-rvw7.json (tried 3x, across two different paths on that
host) and a local `curl` on this Windows dev machine failed at the TLS
handshake stage (schannel) hitting the same host — before any HTTP
request was even sent. Both point to a client/network-specific issue
(this dev sandbox / this Windows curl+schannel build) rather than the
endpoint being down — search results confirm the dataset is live. A
GitHub Actions ubuntu-latest runner uses Python's `requests` (OpenSSL),
a completely different TLS stack and network egress, and should not hit
the same wall.

Because the exact column names for this specific dataset were never
actually seen, `FIELD_CANDIDATES` below is a best-guess set of common
Socrata naming conventions rather than a verified schema. parse() prints
the real key names from the first fetched row (see stderr) and tries each
candidate per logical field, leaving a field None rather than raising if
none match — so a wrong guess degrades to "field missing" (visible in the
printed sample and in a lower completeness_score downstream) instead of
crashing the whole scrape. FIRST PRIORITY on the first real GitHub Actions
run: check that log output and correct FIELD_CANDIDATES if needed. See
CLAUDE.md pending-work list.

No auth, no rate limiting mentioned anywhere for this endpoint — it's a
public Socrata REST API, paginated via $limit/$offset.
"""

from __future__ import annotations

import sys

import requests

from db import get_connection, log_scrape, upsert_record

DATASET_ID = "qcrz-rvw7"
BASE_URL = f"https://www.chattadata.org/resource/{DATASET_ID}.json"
SOURCE_URL = "https://www.chattadata.org/Public-Safety/Code-Enforcement-Violations/qcrz-rvw7"
PAGE_SIZE = 1000
MAX_PAGES = 100  # hard safety cap (100k rows) so a pagination bug can't loop forever

FIELD_CANDIDATES = {
    "case_number": ["case_number", "casenumber", "violation_number", "case_no", "caseno", "id"],
    "violation_type": ["violation_type", "violationtype", "violation_description", "code_section", "type", "description"],
    "violation_date": ["violation_date", "date_reported", "created_date", "open_date", "date", "inspection_date"],
    "status": ["status", "case_status", "violation_status"],
    "description": ["description", "comments", "violation_description", "narrative", "details"],
    "address": ["address", "location", "violation_address", "property_address", "full_address", "site_address"],
}


def _first_present(row: dict, candidates: list[str]):
    for key in candidates:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def fetch() -> list[dict]:
    all_rows = []
    offset = 0
    for _ in range(MAX_PAGES):
        resp = requests.get(BASE_URL, params={"$limit": PAGE_SIZE, "$offset": offset}, timeout=30)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        all_rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


def parse(raw_rows: list[dict]) -> list[dict]:
    if raw_rows:
        print(f"[code_enforcement] field names in first row: {sorted(raw_rows[0].keys())}", file=sys.stderr)

    records = []
    for i, row in enumerate(raw_rows):
        case_number = _first_present(row, FIELD_CANDIDATES["case_number"])
        records.append({
            "case_number": str(case_number) if case_number is not None else None,
            "violation_type": _first_present(row, FIELD_CANDIDATES["violation_type"]),
            "violation_date": _first_present(row, FIELD_CANDIDATES["violation_date"]),
            "status": _first_present(row, FIELD_CANDIDATES["status"]),
            "description": _first_present(row, FIELD_CANDIDATES["description"]),
            "raw_address": _first_present(row, FIELD_CANDIDATES["address"]),
            "_fallback_key": i,
        })
    return records


def upsert(conn, records: list[dict]) -> int:
    for r in records:
        dedupe_key = f"chattadata:{r['case_number'] or r['raw_address'] or r['_fallback_key']}"
        # Address -> parcel resolution happens in enrich_assessor.py once the
        # GIS endpoint is confirmed (checkpoint). Every record lands
        # unresolved until then.
        upsert_record(
            conn, "code_enforcement", dedupe_key,
            parcel_id=None,
            case_number=r["case_number"],
            violation_type=r["violation_type"],
            violation_date=r["violation_date"],
            status=r["status"],
            description=r["description"],
            raw_address=r["raw_address"],
            resolution_method="unresolved",
            source_url=SOURCE_URL,
        )
    return len(records)


def main() -> None:
    conn = get_connection()
    raw_rows = fetch()
    records = parse(raw_rows)
    count = upsert(conn, records)
    conn.commit()
    log_scrape(conn, "code_enforcement", record_count=count, status="ok")
    conn.commit()
    print(f"code_enforcement: upserted {count} records from {len(raw_rows)} fetched rows")


if __name__ == "__main__":
    main()
