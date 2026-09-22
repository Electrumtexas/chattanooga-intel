"""
Hamilton County Clerk & Master delinquent tax SALE list (Chancery Court) —
properties that have progressed past routine Trustee delinquency all the way
to a judicial tax-sale docket. Distinct from tax_delinquent.py (the Trustee's
ongoing currently-owed ledger); this is the list of parcels a Chancery Court
auction will actually sell if the debt isn't cleared first.

Live-verified 2026-09-22 against a real downloaded file (not assumed from
the county's boilerplate "Notice of Sale" form, which turned out to carry a
stale date — see below).

The PDF is real, has a text layer, and the year-folder URL pattern persists
--------------------------------------------------------------------------
https://www.hamiltontn.gov/Clerkmasterforms/taxsale/2026TaxSale/DELINQUENT%20TAX%20SALE%20LIST%202026.pdf
returned HTTP 200, `application/pdf`, body starting `%PDF-1.7`, 5 pages,
185 data rows. A web search turned up the same naming convention
(`DELINQUENT TAX SALE LIST <year>.pdf` inside a `<year>TaxSale/` folder)
for at least 2016-2026, so the folder-per-year layout is a long-standing
county convention, not a one-off. Both the current year (2026) and the
prior year (2025) resolve to real PDFs right now. A year with no list yet
(tried 2027) returns HTTP 200 with `Content-Type: text/html` and an HTML
error-page body instead of `%PDF...` — a **soft 404** — so fetch() checks
the actual bytes, not just the status code, exactly as the task's ground
rules warned.

Sale date: the list's own "REVISED 6/4/2026" header line IS the sale date
-------------------------------------------------------------------------
A web search independently confirmed "the 2026 tax sale auction is on
Thursday, June 4, 2026" — which matches the PDF's own top-of-page
"REVISED 6/4/2026" stamp exactly. The county's separate "TAX SALE
INFORMATION <year>.pdf" boilerplate form (FORM 123T) was also fetched to
check for a second source of the date, but its body text still says "tax
sale auction is on Thursday, June 05, 2025" even in the 2026-dated copy —
stale boilerplate, not authoritative. The per-list "REVISED <M/D/YYYY>"
line is used as filing_date for every row in that list instead.

Column layout, confirmed against the real extracted text
----------------------------------------------------------
One row per line: [STATUS] DOCKET ITEM# ADDRESS MAP GROUP PARCEL [SUFFIX] MINIMUM_BID
  - STATUS is blank (still active, headed to auction), "PAID" (resolved
    before sale), or "REMOVED" (pulled from the docket) — exactly the three
    values the task spec predicted. Parsed generically as "any run of
    all-caps word(s) before the docket number" so an unseen status label in
    a future year degrades to "resolved" rather than crashing.
  - DOCKET is 11255 or 11256 in the 2026 list — a web search of a prior
    year's list showed the same two-docket pattern with different numbers
    ("Hamilton County 11247 and City of Chattanooga 11246"), so DOCKET
    distinguishes the county tax batch from the City of Chattanooga batch,
    not the row's position.
  - ITEM# ranges from ~1700 to ~174,000 and is NOT sequential/contiguous
    row-to-row (it's the county's own bill/account id, not a 1..N ordinal)
    — so "gaps" between item numbers are expected and not a parse-failure
    signal. The real validation signal used here is 0 tail-parse failures
    across all 185 rows, checked with a token-position parser applied to
    the actual extracted text (not assumed from the header labels alone —
    the header row's own wording, "DOCKET ITEM # PROPERTY # PROPERTY
    ADDRESS...", implies a 4th numeric column that never actually appears
    in a data row; every row has exactly one number between DOCKET and the
    address, so PROPERTY # and ITEM # are the same rendered column, most
    likely a two-line sub-header flattened by text extraction).
  - MAP is 2-3 digits with an optional trailing letter (e.g. "145L", "026").
    GROUP is a single letter, or blank for rural/unincorporated parcels
    (seen on 19/185 rows) — this blank-group case is exactly what
    parcel_utils.format_parcel_id() already special-cases (join, omitting
    empty parts), so no format_parcel_id change was needed.
  - PARCEL can carry a ".01"/".20"-style two-decimal split-parcel suffix
    baked directly into the number (e.g. "013.01") — this is normal Map/
    Group/Parcel notation, not a separate field.
  - A distinct condo/sub-unit SUFFIX token (e.g. "C158", "S002") appears on
    3/185 rows, offset from PARCEL by extra whitespace in the extracted
    text (almost certainly its own PDF table column). CONFIRMED by
    cross-referencing the real parcels table (loaded from the unrelated
    Trustee CSV): concatenating suffix directly onto the parcel with NO
    separator reproduces existing parcel_ids exactly —
    "117O-A-011" + "C158" -> "117O-A-011C158", which is a real row already
    in `parcels` (one of ~40 sibling condo units "117O-A-011C022" ...
    "117O-A-011C470" at the same address). Same confirmed for
    "141-026" + "S002" -> "141-026S002". So suffix is concatenated onto
    parcel (no dash, no space) before calling format_parcel_id(), matching
    the Trustee file's own convention (spot-checked there too, e.g. an
    existing parcel_id "005-004M001").
  - No owner-name column exists anywhere in this PDF — party_names and
    raw_owner_name are always None here, not a parsing gap.
  - Multi-line address wrapping was NOT observed in the real 2026 file
    (every row prints on one physical line), but parse() still groups
    physical lines by "does this line start with a status/docket pattern"
    rather than assuming one-line-per-row, so a future year's long address
    wrapping onto a second line is absorbed instead of mis-parsed.

parcel_id overlap with the existing parcels/tax_delinquent tables
--------------------------------------------------------------------------
Checked against a copy of the real database (7,926 parcels / 12,333
tax_delinquent rows, from tax_delinquent.py's Trustee pull). Overlap is NOT
uniform across status and that split is informative, not a bug:
    PAID rows:    70/113 (62%) already exist in `parcels`
    REMOVED rows:  6/6  (100%) already exist in `parcels`
    ACTIVE rows:   2/66  (3%)  already exist in `parcels`
For the ACTIVE (still-heading-to-auction) rows, spot-checking several
"misses" showed the exact same MAP-GROUP prefix already present in
`parcels` under a *different* PARCEL suffix (e.g. "137I-E-016/022/024/025"
exist but "137I-E-018" doesn't) — i.e. the Map/Group/Parcel *format* is
right (same neighborhood block resolves), it's simply a different specific
parcel than what's currently sitting on the Trustee's active-balance roll.
The likely mechanism: once a parcel's debt is reduced to a Chancery Court
judgment/tax-sale docket, the Trustee's routine "current amount owed"
ledger (which tax_delinquent.py filters to nonzero-balance rows) may no
longer be the system of record for that specific bill — collection moves
to the court process — so low overlap for ACTIVE rows is an expected
consequence of the two sources tracking different stages of the same
debt, not a formatting mismatch. This is a judgment call, not a verified
fact from county documentation; noted as a limitation below.

No separate redemption or sale-results list found
--------------------------------------------------------------------------
Searched for a Clerk & Master "redemption list" or "sale results" page;
none exists as a public hamiltontn.gov PDF/page. The county's own "TAX SALE
INFORMATION" form says the post-sale "Order Confirming Sale" is filed
per-parcel in Chancery Court (not published as a list) and that the
for-sale-by-auction list itself is also mirrored on the third-party
www.civicsource.com (not verified live here — a different host, likely
needs its own reconnaissance, not added in this module).

Change detection
--------------------------------------------------------------------------
The PDF is tiny (~155KB, 5 pages, 185 rows) and pypdf text extraction over
it is milliseconds, so there's no real cost to re-parsing every run — unlike
tax_delinquent.py's 29MB zip, skipping the parse step here would save
nothing meaningful. fetch() still records a sha256 of the downloaded bytes
in source_state (key "tax_sale:<year>:sha256") purely for observability —
scrape_log's notes say whether the file actually changed since the last
run — not to skip work.

Scoring / quality-check / dashboard follow-ups this module needs (out of
scope for this file — see the build report) are proposed as exact diffs in
the task report rather than made here, per this build's file-scope limits.
"""

from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

import pypdf
import requests

from db import get_connection, get_state, log_scrape, set_state, upsert_parcel, upsert_record
from parcel_utils import format_parcel_id

SOURCE_PORTAL = "clerk_master_tax_sale"
URL_TEMPLATE = "https://www.hamiltontn.gov/Clerkmasterforms/taxsale/{year}TaxSale/DELINQUENT%20TAX%20SALE%20LIST%20{year}.pdf"
REQUEST_TIMEOUT = 30

# A "row start" is an optional run of all-caps status word(s), then DOCKET,
# then ITEM#, then everything else on the line. Generic (not hardcoded to
# just PAID/REMOVED) so an unseen status label degrades gracefully instead
# of failing to match at all.
_ROW_START_RE = re.compile(r"^((?:[A-Z]{2,})(?:\s+[A-Z]{2,})*\s+)?(\d{4,6})\s+(\d+)\s+(.*)$")
_REVISED_RE = re.compile(r"REVISED\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"^\$[\d,]+\.\d{2}$")
_SUFFIX_RE = re.compile(r"^[A-Z]\d{2,4}$")
_PARCEL_RE = re.compile(r"^\d{1,4}(?:\.\d{1,2})?$")
_GROUP_RE = re.compile(r"^[A-Z]$")
_MAP_RE = re.compile(r"^\d{2,3}[A-Z]?$")


def _candidate_years() -> list[int]:
    """Current year first, then the prior year — the list for next year's
    sale isn't posted until sometime before it, and last year's stays live
    for a while after (both 2025 and 2026 resolved when this was checked).
    """
    year = datetime.now().year
    return [year, year - 1]


def fetch(conn) -> tuple[bytes, str, int, bool]:
    """Try the current year's URL, then the prior year's. Returns
    (pdf_bytes, url_used, year_used, changed_since_last_run). Raises
    RuntimeError with every URL tried if neither is a real PDF — a 200
    status with an HTML error-page body (confirmed happening for a
    not-yet-posted year) must not be mistaken for success.
    """
    attempts = []
    for year in _candidate_years():
        url = URL_TEMPLATE.format(year=year)
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            attempts.append(f"{url} -> request error: {e}")
            continue
        if resp.status_code == 200 and resp.content[:4] == b"%PDF":
            sha256 = hashlib.sha256(resp.content).hexdigest()
            state_key = f"tax_sale:{year}:sha256"
            changed = get_state(conn, state_key) != sha256
            set_state(conn, state_key, sha256)
            return resp.content, url, year, changed
        attempts.append(f"{url} -> status={resp.status_code} content_type={resp.headers.get('Content-Type')} starts_with={resp.content[:20]!r}")
    raise RuntimeError(
        "No valid tax sale PDF found for the current or prior year. Tried:\n" + "\n".join(attempts)
    )


def _group_lines(text: str) -> tuple[list[dict], str | None]:
    """Split extracted text into logical rows. A "row start" line resets the
    buffer; any other non-empty line is treated as a continuation of the
    current row's text (handles a wrapped address without assuming one
    physical line per row).
    """
    sale_date = None
    rows: list[dict] = []
    current: dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = _REVISED_RE.search(line)
        if m and sale_date is None:
            month, day, year = (int(g) for g in m.groups())
            sale_date = f"{year:04d}-{month:02d}-{day:02d}"
        if "REVISED" in line.upper() and len(line.split()) <= 3:
            continue  # standalone "REVISED M/D/YYYY" stamp line
        if line.upper().startswith("DOCKET") and "MINIMUM BID" in line.upper():
            continue  # column header row

        m = _ROW_START_RE.match(line)
        if m:
            if current is not None:
                rows.append(current)
            status_raw, docket, item, rest = m.groups()
            current = {"status": (status_raw or "").strip() or None, "docket": docket, "item": item, "text": rest}
        elif current is not None:
            current["text"] += " " + line
        # else: stray text before any row has started — ignore

    if current is not None:
        rows.append(current)
    return rows, sale_date


def _parse_row_tail(rest: str) -> dict | None:
    """Peel MINIMUM_BID, [SUFFIX], PARCEL, GROUP, MAP off the right-hand end
    of a row's text, in that order, leaving the address in the middle.
    Returns None if the tail doesn't match the expected shape.
    """
    tokens = rest.split()
    if len(tokens) < 4:
        return None
    if not _AMOUNT_RE.match(tokens[-1]):
        return None
    amount = float(tokens[-1].replace("$", "").replace(",", ""))

    p = len(tokens) - 2
    suffix = None
    if p >= 0 and _SUFFIX_RE.match(tokens[p]):
        suffix = tokens[p]
        p -= 1

    if p < 0 or not _PARCEL_RE.match(tokens[p]):
        return None
    parcel = tokens[p]
    p -= 1

    group = ""
    if p >= 0 and _GROUP_RE.match(tokens[p]):
        group = tokens[p]
        p -= 1

    if p < 0 or not _MAP_RE.match(tokens[p]):
        return None
    map_ = tokens[p]

    address = " ".join(tokens[:p]).strip()
    return {"address": address or None, "map": map_, "group": group, "parcel": parcel, "suffix": suffix, "amount": amount}


def parse(pdf_bytes: bytes, year: int) -> tuple[list[dict], dict, str | None]:
    """Returns (records, stats, sale_date). Each record is one tax-sale
    docket line item — active, paid, or removed; none are dropped, since
    the audit trail of what left the docket and why matters as much as
    what's still active.
    """
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    grouped_rows, sale_date = _group_lines(text)

    stats = {
        "total_rows": len(grouped_rows),
        "parsed": 0,
        "failed": 0,
        "active": 0,
        "paid": 0,
        "removed": 0,
        "other_status": 0,
        "duplicate_dedupe_keys": 0,
        "failed_rows": [],
    }
    records = []
    seen_keys: set[str] = set()

    for row in grouped_rows:
        tail = _parse_row_tail(row["text"])
        if tail is None:
            stats["failed"] += 1
            stats["failed_rows"].append(f"docket={row['docket']} item={row['item']} text={row['text']!r}")
            continue

        status = row["status"]
        if status is None:
            stats["active"] += 1
            case_type = "tax_sale_filing"
            status_label = "ACTIVE"
        elif status == "PAID":
            stats["paid"] += 1
            case_type = "tax_sale_resolved"
            status_label = "PAID"
        elif status == "REMOVED":
            stats["removed"] += 1
            case_type = "tax_sale_resolved"
            status_label = "REMOVED"
        else:
            stats["other_status"] += 1
            case_type = "tax_sale_resolved"
            status_label = status

        # Suffix concatenates directly onto parcel (no separator) — verified
        # against real parcels-table rows, see module docstring.
        parcel_field = tail["parcel"] + (tail["suffix"] or "")
        parcel_id = format_parcel_id(tail["map"], tail["group"], parcel_field)

        dedupe_key = f"{SOURCE_PORTAL}:{year}:{row['docket']}:{row['item']}"
        if dedupe_key in seen_keys:
            stats["duplicate_dedupe_keys"] += 1
        seen_keys.add(dedupe_key)

        description = (
            f"{year} tax sale, docket {row['docket']} item {row['item']}, "
            f"status {status_label}, min bid ${tail['amount']:,.2f}"
        )

        records.append({
            "dedupe_key": dedupe_key,
            "case_number": f"{row['docket']}-{row['item']}",
            "case_type": case_type,
            "parcel_id": parcel_id,
            "amount": round(tail["amount"], 2),
            "filing_date": sale_date,
            "raw_address": tail["address"],
            "description": description,
        })
        stats["parsed"] += 1

    return records, stats, sale_date


def upsert(conn, records: list[dict], source_url: str) -> int:
    for r in records:
        if r["parcel_id"]:
            # Only situs_address is known from this source — never pass
            # situs_city/zip as None-guesses; upsert_parcel already refuses
            # to clobber an existing non-null field with None.
            upsert_parcel(conn, r["parcel_id"], situs_address=r["raw_address"])

        upsert_record(
            conn, "court_records", r["dedupe_key"],
            parcel_id=r["parcel_id"],
            source_portal=SOURCE_PORTAL,
            case_number=r["case_number"],
            case_type=r["case_type"],
            filing_date=r["filing_date"],
            party_names=None,  # PDF has no owner-name column
            amount=r["amount"],
            raw_address=r["raw_address"],
            raw_owner_name=None,
            resolution_method="native_parcel_id",  # source gives Map/Group/Parcel directly
            source_url=source_url,
            description=r["description"],
        )
    return len(records)


def main() -> None:
    conn = get_connection()
    try:
        pdf_bytes, url, year, changed = fetch(conn)
        records, stats, sale_date = parse(pdf_bytes, year)
        count = upsert(conn, records, url)
        conn.commit()

        notes = (
            f"year={year}, active={stats['active']}, "
            f"resolved={stats['paid'] + stats['removed'] + stats['other_status']} "
            f"(paid={stats['paid']}, removed={stats['removed']}, other={stats['other_status']}), "
            f"sale_date={sale_date}, parse_failures={stats['failed']}, "
            f"duplicate_dedupe_keys={stats['duplicate_dedupe_keys']}, pdf_changed_since_last_run={changed}, "
            f"url={url}"
        )
        log_scrape(conn, "tax_sale", record_count=count, status="ok", notes=notes)
        conn.commit()

        print(f"tax_sale: upserted {count} rows from the {year} list. {notes}")
        if stats["failed"]:
            print(f"tax_sale: WARNING — {stats['failed']} row(s) failed to parse:")
            for line in stats["failed_rows"]:
                print(f"  {line}")
    except Exception as e:
        log_scrape(conn, "tax_sale", record_count=0, status="error", notes=str(e))
        conn.commit()
        raise


if __name__ == "__main__":
    main()
