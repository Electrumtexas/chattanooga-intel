"""
Hamilton County General Sessions Court civil dockets, via edockets.us.

This is a separate, independently-scheduled source from the still-
unimplemented court_records.py skeleton (that one targets four *different*
portals — TN Case Finder, Civitek OCRS, hamiltonclerk.com probate, and the
Clerk & Master tax-sale docket, the last of which now actually lives in
tax_sale.py). This module writes into the SAME shared `court_records`
table under its own scrape_log source name ("sessions_dockets", distinct
from the table name) — the pattern tax_sale.py already established for a
PDF-driven portal that happens to feed the shared table.

Verified against page.html + 5 real docket PDFs (26 pages, 86 case
entries total, spanning all three docket types) pulled from GitHub's
US-based runners on 2026-09-15 — this dev machine cannot reach
edockets.us at all (CLAUDE.md's own multi-agent recon logged the same
TCP-timeout behavior for this host from a non-US IP). Nothing below is
guessed from the site's own marketing copy.

The list endpoint, exactly as the page's own XHR calls it
--------------------------------------------------------------------------
POST https://www.edockets.us/cgi-bin/webshell.asp, form-encoded:
GATEWAY=GATEWAY, XGATEWAY=DocketGetInfo, CGISCRIPT=webshell.asp,
XEVENT=VERIFY, WEBIOHANDLE=<epoch ms + day-of-month, matching the page's
own `x.getTime()+x.getDate()`>, MYPARENT=px, APPID=ham,
WEBWORDSKEY=SAMPLE, DEVPATH=/INNOVISION/HAMILTON/HAMMAIN.DATA,
OPERCODE=dummy, PASSWD=dummy. OPERCODE/PASSWD are hardcoded into the
page's own <script> tag (view-source, not a login form) — not a
credential, and nothing here signs in to anything. The response body is a
JS array literal that is already valid JSON in the one real capture taken
(`docket_list_raw.txt` == `"[ ]"`, i.e. an empty list); `_parse_list_response()`
tries `json.loads()` first and falls back to a regex extraction of
`["...", ...]` chunks for a near-JSON variant. The response is NEVER
eval()'d — it's untrusted network content that the site's own (much less
careful) client-side JS happens to run through `eval()`.

The list is frequently, and normally, EMPTY — not an error
--------------------------------------------------------------------------
Only *upcoming* dockets are listed, and the one real capture (2026-09-15)
was `[ ]`. `fetch_list()` returning `[]` is a routine, successful,
zero-new-dockets run: `main()` still logs an `ok` scrape_log row
(record_count=0 is an expected value some days, not a failure) and never
raises for it. The only things that ARE treated as genuine errors are
failing to reach the endpoint at all, a non-2xx response, or a response
that isn't recognizable as a JSON/JS array — see `_run_live()`.

Docket PDFs outlive their listing (verified: 9/15 paths still served 9/22)
--------------------------------------------------------------------------
So every docket path this module has EVER seen is remembered in
`source_state` (key "sessions_dockets_seen", a JSON dict capped at the
most recent 300 paths/filenames — comfortably more than this low-traffic
list has ever shown at once) and a path already in it is never
re-fetched/re-parsed. This is what makes the "often empty" property
harmless in practice: once a case's docket PDF has been parsed, it's
never touched again, so a daily run's real cost is just whatever *new*
paths appeared since yesterday (usually zero, occasionally a handful).

Docket PDF layout, confirmed against all 5 real sample PDFs
--------------------------------------------------------------------------
Every page repeats a 5-line header (court date + page #, session start
time, "HAMILTON COUNTY GENERAL SESSIONS COURT", a docket-type line
["DETAINER DOCKET" / "TRIAL DOCKET" / "APPEARANCE DOCKET"], then the
column-header row) followed by a table with 4 real columns — Attorney |
Style of Case | Comments | Response Date (a 5th, Cont[inuance count], is
present but unused here). Plain `pypdf` `extract_text()` (no layout mode
needed) reproduces this as FIXED-WIDTH text columns — confirmed by
locating each header label's character offset on all 5 sample PDFs
independently: ATTORNEY at column 5, STYLE OF CASE at 35, COMMENTS at 69,
RESPONSE DATE at 108, identically in every sample. `parse_docket()`
still locates these offsets dynamically per page (from that page's own
header line) rather than hardcoding them, so a future template shift
degrades to a skipped page instead of silently-wrong columns.

Each numbered entry spans 3+ physical text lines sharing those column
slices. Entries are found by scanning for a bare integer in the leftmost
sub-field (columns 0-4, left of "ATTORNEY"); confirmed reliable because
entry numbers run CONTINUOUSLY across page breaks in every multi-page
sample (e.g. page 2 of the 17-page Detainer docket starts at entry "6",
not "1"), so all of a PDF's page bodies are concatenated before
splitting — an entry is never assumed to fit on one page. Within an
entry: Style-of-Case lines BEFORE the "VS.   <case#>" line are the
plaintiff(s) (can be 2+ lines, e.g. two pro se co-plaintiffs each on
their own row); lines AFTER it are the defendant(s) (also can be 2+, e.g.
a joint tenancy). Comments-column fragments (a session-time override,
"Division N", the case-type label, "File Date: MM/DD/YYYY") are
scattered across those same rows in no fixed sub-order — confirmed by one
real anomaly (Trial-docket sample, entry 3) where "Division 1 / Other /
File Date: 10/21/2025" prints TWICE inside a single entry (almost
certainly a merged sub-row from the source system, not a parse bug); the
parser takes the FIRST occurrence of each recognized fragment as
authoritative and ignores the duplicate rather than concatenating.

The case TYPE (from Comments), not the docket PDF's own header label,
decides plaintiff-vs-defendant handling — the single most important
finding here
--------------------------------------------------------------------------
The docket-type header ("TRIAL DOCKET" etc.) does NOT reliably predict
what kind of case a given row is: real "Detainer/Rental"-labeled rows
were found on BOTH the Trial-docket sample (entry 10, case 26GS2994) and
the Appearance-docket sample (entries 1 and 3) — an eviction that has
moved past its first hearing still shows up on the general trial/status
calendar under its original case type. So classification here keys on
each ENTRY'S OWN Comments-column case-type text against a closed
whitelist, never on which PDF/header it was printed under:
    "Detainer/Rental", "Detainer"        -> 'detainer'    (keep PLAINTIFF only)
    "Collections", "Contract", "Other",
    "Sue & Attach" / "Sue and Attach"     -> 'collections' (keep DEFENDANT only)
Anything else — "Personal Injury" (seen in 3 samples) and any future or
unrecognized label, INCLUDING free-text scheduling notes that sometimes
occupy the comments column's first line ("MEDIATION/PAYMENT REVIEW", "10AM
MEDIATION STATUS") — is UNCLASSIFIED and the whole entry is dropped, per
the task's instruction to default to storing nothing rather than guess.

No dollar amounts, no addresses, anywhere in this source
--------------------------------------------------------------------------
Grepped all 5 sample PDFs' extracted text for "$" / "AMOUNT" / "JUDGMENT":
zero hits. This is a scheduling docket (who's on today's calendar), not a
judgment roll — `amount` is always None here, not a parsing gap. Same for
addresses: the four real columns are Attorney/Style-of-Case/Comments/
Response-Date; there is no address column at all, so `raw_address` is
always None and every row is `resolution_method='unresolved'` on write
(see "Known limitations" for a gap this creates downstream).

PRIVACY — why a detainer's tenant/defendant can never reach the DB, and
what happens if the layout shifts
--------------------------------------------------------------------------
This is the owner's explicit, non-negotiable call for this public
repo/dashboard: an eviction's lead VALUE is the plaintiff (the landlord/
property manager — a possible "tired landlord"), not the tenant being
evicted, a private individual with no lead value here and no legitimate
reason to appear in a scraped, published dataset. For every entry
classified 'detainer', `_parse_entry()` computes `defendant_lines` only to
find where the plaintiff block ends — that value is never assigned to any
field the function returns, so it is structurally impossible for a
tenant name to reach `party_names`/`raw_owner_name`/`description`, not
merely "usually filtered out." Conversely, for 'collections' rows the
DEFENDANT may own distressed property, so only `defendant_lines` is
stored and the plaintiff/creditor's name is discarded — its absence is a
design choice from the task's own instruction (a debt collector's name is
not a property lead), not a limitation. Attorney names ARE stored
regardless of side (public professionals in open court, not the privacy
concern here); the Attorney and Style-of-Case column text come from
disjoint fixed x-position slices of the source PDF, so an attorney string
cannot structurally contain a party's name either.

Fail-closed behavior: classification runs entirely off a literal
whitelist match against the Comments-column case-type text — never
inferred from docket type, column position alone, or fuzzy matching. If
the case-type text isn't recognized, the column-header line can't be
located on a page (a real template change, not just ragged whitespace),
or the "VS." case-number marker can't be found in an entry, that whole
page/entry is skipped and counted in `stats` rather than guessed at. The
one scenario this can't self-check is edockets.us silently swapping which
physical side (above/below the "VS." line) holds plaintiff vs. defendant
text — no such swap was seen across 86 sampled entries in 3 docket types,
but it is the one layout assumption that isn't verified by the parser
itself; see "Known limitations."

Dev-only samples mode (kept in the module, not a throwaway script)
--------------------------------------------------------------------------
edockets.us answers only US-origin requests, so this module can only be
exercised live from the GitHub Actions runner. `main()` therefore accepts
`--samples-dir DIR` / a `SESSIONS_SAMPLE_DIR` env var to parse a folder of
already-downloaded docket PDFs instead of calling `fetch_list()` /
`fetch_pdf()` — this is how the module was tested end-to-end (twice, to
confirm upsert/skip idempotency) against a disposable copy of the DB on
this dev machine. It prints a "DEV MODE" banner, and `_run_live()` (the
code path GitHub Actions actually runs daily) is entirely separate code
never exercised by the samples path.

Schema mapping
--------------------------------------------------------------------------
`source_portal='edockets_general_sessions'`; `case_type` is 'detainer' or
'collections' per the whitelist above; `dedupe_key=f"edockets:{case_number}"`
— ONE row per case even though the same case can legitimately appear on
several different dockets over its life (e.g. a Trial-docket appearance,
then later an Appearance-docket status check). Re-upserting the same
case_number just refreshes `description`/`filing_date`/etc. to the latest
docket's info instead of creating a duplicate row — the "update the
hearing date, don't duplicate" behavior the spec asked for, which falls
out for free from `db.upsert_record()`'s existing UPDATE-on-conflict
branch. `parcel_id`/`resolution_method` use
`keep_existing=("parcel_id", "resolution_method")` so a later
`enrich_assessor.py` match is never clobbered back to unresolved by a
later re-scrape of the same still-listed case.

Name normalization judgment call — raw docket text is kept, NOT
reformatted to "LAST FIRST"
--------------------------------------------------------------------------
`parcels.owner_name` in the test DB is written "LAST FIRST[ MIDDLE][
SUFFIX]" for individuals (e.g. "PICKETT RANDALL MAURICE") and businesses
as-is; docket names instead print individuals as "FIRST [MI] LAST" (e.g.
"JERMESHIA N SPENCE"). A blind word-order flip to match the assessor
convention was considered and rejected here: (a) no other scraper in this
codebase reformats a raw source name before storing it —
tax_delinquent.py / tax_sale.py / code_enforcement.py all store the
source's own text verbatim in `raw_owner_name` and leave normalization to
the consuming step, and (b) a flip applied uniformly would corrupt every
business/entity string in the Style-of-Case column (e.g. "DOMINION
PROPERTIES LLC DBA RIVERVIEW...") since there is no generic, reliable way
to tell — from the text alone — which convention a given multi-word
string is already in. So `party_names`/`raw_owner_name` store the
docket's own text verbatim (only whitespace-collapsed), matching existing
convention. `classify_owner_type()` still runs on that raw text (to
report the business-vs-individual mix in the run summary — see main()),
and any LAST/FIRST reordering is left to enrich_assessor.py's future
owner-name-fallback matcher, which will need its own normalization pass
over BOTH sides (court names and assessor names) to compare them
meaningfully — doing it piecemeal here would just create a second,
inconsistent convention.

Known limitations
--------------------------------------------------------------------------
- No addresses anywhere in this source, so every row depends entirely on
  a future owner-name match in enrich_assessor.py. That module's
  `fetch_unresolved()` currently filters to `raw_address IS NOT NULL`,
  which means it will NEVER pick up any row this module writes today —
  flagged in this module's author's final report; not fixed here since
  enrich_assessor.py is out of scope for this file.
- Multi-plaintiff/defendant entries are joined with " / " into one
  string; the dashboard displays this as free text, not as separately
  matchable owner names.
- The one observed "duplicate comment block" anomaly (see above) is
  resolved by taking the first occurrence, which was correct for that
  sample; not proven to be the right resolution for every future
  variant of that artifact.
- Case types outside the whitelist (seen: "Personal Injury") are dropped
  entirely rather than stored with an approximate case_type — a
  deliberate precision-over-recall choice per the privacy ground rule,
  not a bug.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pypdf
import requests

from db import get_connection, get_state, log_scrape, now_iso, set_state, upsert_record
from parcel_utils import classify_owner_type

SOURCE_PORTAL = "edockets_general_sessions"
LOG_SOURCE = "sessions_dockets"

PAGE_URL = "https://www.edockets.us/hamiltontn/"
LIST_URL = "https://www.edockets.us/cgi-bin/webshell.asp"
PDF_BASE_URL = "https://www.edockets.us/hamiltontn/"

USER_AGENT = "Mozilla/5.0 (compatible; chattanooga-intel/1.0)"
REQUEST_TIMEOUT = 30
PDF_FETCH_DELAY_SECONDS = 1.5  # politeness delay between successive PDF fetches in a single run

STATE_KEY = "sessions_dockets_seen"
MAX_SEEN_TRACKED = 300  # bounds source_state's stored JSON; comfortably above this list's real volume

LIST_FORM_FIELDS = {
    "GATEWAY": "GATEWAY",
    "XGATEWAY": "DocketGetInfo",
    "CGISCRIPT": "webshell.asp",
    "XEVENT": "VERIFY",
    "MYPARENT": "px",
    "APPID": "ham",
    "WEBWORDSKEY": "SAMPLE",
    "DEVPATH": "/INNOVISION/HAMILTON/HAMMAIN.DATA",
    "OPERCODE": "dummy",
    "PASSWD": "dummy",
}

# Comments-column case-type text -> court_records.case_type. Whitelist-only:
# an unrecognized label (e.g. "Personal Injury") is deliberately dropped
# rather than guessed at — see module docstring's privacy section.
CASE_TYPE_WHITELIST = {
    "DETAINER/RENTAL": "detainer",
    "DETAINER": "detainer",
    "COLLECTIONS": "collections",
    "CONTRACT": "collections",
    "OTHER": "collections",
    "SUE & ATTACH": "collections",
    "SUE AND ATTACH": "collections",
}

COLUMN_LABELS = ("ATTORNEY", "STYLE OF CASE", "COMMENTS", "RESPONSE DATE")

CASE_NUMBER_RE = re.compile(r"(\d{2}GS\d+)")
FILE_DATE_RE = re.compile(r"File Date:\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
DIVISION_RE = re.compile(r"Division\s+(\d+)", re.IGNORECASE)
HEADER_DATE_RE = re.compile(r"^([A-Za-z]+\s+\d{1,2},\s+\d{4})\s+PAGE", re.IGNORECASE)
DOCKET_TYPE_LINE_RE = re.compile(r"^(DETAINER|TRIAL|APPEARANCE)\s+DOCKET$", re.IGNORECASE)
SAMPLE_PATH_RE = re.compile(r"(ses\.\d+\.\d+\.pdf)$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------

def _webiohandle() -> str:
    """Matches the page's own `x.getTime()+x.getDate()` — epoch ms plus the
    day-of-month, not a real session token, just what the site's JS sends.
    """
    now = time.localtime()
    return str(int(time.time() * 1000) + now.tm_mday)


def fetch_list(session: requests.Session | None = None) -> list[list]:
    """POST the same XHR the docket-list page makes. Returns a list of
    [date, time, office, division, docket_type, path] rows — commonly [],
    which is a normal, successful result (see module docstring), not
    treated specially here; the caller decides what an empty list means.
    Raises requests.RequestException on a genuine network/HTTP failure.
    """
    session = session or requests.Session()
    data = dict(LIST_FORM_FIELDS, WEBIOHANDLE=_webiohandle())
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": PAGE_URL,
        "Origin": "https://www.edockets.us",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = session.post(LIST_URL, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return _parse_list_response(resp.text)


def _parse_list_response(text: str) -> list[list]:
    """Parses the JS-array-literal response safely — json.loads first (the
    one real capture, "[ ]", is already valid JSON), with a regex-based
    fallback for a near-JSON variant. Never eval()s the response.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for chunk in re.findall(r'\[("[^\]]*")\]', text):
            try:
                rows.append(json.loads("[" + chunk + "]"))
            except json.JSONDecodeError:
                continue
    if not isinstance(rows, list):
        raise ValueError(f"docket list response was not a JSON array: {text[:200]!r}")
    return [
        r for r in rows
        if isinstance(r, list) and len(r) >= 6 and str(r[5]).lower().endswith(".pdf")
    ]


def fetch_pdf(path: str, session: requests.Session | None = None) -> bytes | None:
    """GETs one docket PDF. Returns None (logging why to stderr) rather than
    raising on a fetch/format problem — one bad PDF in a batch of new
    dockets shouldn't abort the whole run; the caller counts the failure.
    """
    session = session or requests.Session()
    url = PDF_BASE_URL + path.lstrip("/")
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"sessions_dockets: fetch_pdf failed for {url}: {e}", file=sys.stderr)
        return None
    if not resp.ok or resp.content[:4] != b"%PDF":
        print(
            f"sessions_dockets: {url} did not return a PDF "
            f"(status {resp.status_code}, content-type {resp.headers.get('Content-Type')!r})",
            file=sys.stderr,
        )
        return None
    return resp.content


# --------------------------------------------------------------------------
# PDF layout parsing
# --------------------------------------------------------------------------

def _find_header_columns(line: str) -> dict[str, int] | None:
    if "ATTORNEY" not in line or "STYLE OF CASE" not in line or "COMMENTS" not in line:
        return None
    try:
        idx = {label: line.index(label) for label in COLUMN_LABELS}
    except ValueError:
        return None
    if not (idx["ATTORNEY"] < idx["STYLE OF CASE"] < idx["COMMENTS"] < idx["RESPONSE DATE"]):
        return None
    return idx


def _row_cells(line: str, cols: dict[str, int]) -> tuple[str, str, str, str]:
    line = line.ljust(cols["RESPONSE DATE"] + 20)
    return (
        line[0:cols["ATTORNEY"]].strip(),
        line[cols["ATTORNEY"]:cols["STYLE OF CASE"]].strip(),
        line[cols["STYLE OF CASE"]:cols["COMMENTS"]].strip(),
        line[cols["COMMENTS"]:cols["RESPONSE DATE"]].strip(),
    )


def _parse_page_header(lines: list[str]) -> tuple[str | None, str | None]:
    date_raw, docket_type = None, None
    for line in lines[:6]:
        stripped = line.strip()
        if date_raw is None:
            m = HEADER_DATE_RE.match(stripped)
            if m:
                date_raw = m.group(1)
        if docket_type is None:
            m2 = DOCKET_TYPE_LINE_RE.match(stripped)
            if m2:
                docket_type = m2.group(1).lower()
    return date_raw, docket_type


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _split_entries(body_rows: list[tuple[str, str, str, str]]) -> list[list[tuple[str, str, str, str]]]:
    """A new entry starts at a row whose leftmost sub-field is a bare
    integer; everything after it (until the next such row) belongs to the
    same entry. A stray continuation row before any numbered row is
    dropped — not seen in any real sample, so treated as noise rather than
    engineered around.
    """
    groups: list[list[tuple[str, str, str, str]]] = []
    for row in body_rows:
        if row[0].isdigit():
            groups.append([row])
        elif groups:
            groups[-1].append(row)
    return groups


def _empty_entry_stats() -> dict:
    return {
        "pages": 0,
        "entries_seen": 0,
        "kept_detainer": 0,
        "kept_collections": 0,
        "skipped_unclassified": 0,
        "skipped_no_case_number": 0,
        "skipped_no_party_name": 0,
        "parse_errors": 0,
        "docket_header_type": None,
        "case_type_seen": {},
        "owner_type_seen": {},
    }


def _bump(counter: dict, key) -> None:
    key = key or "(none)"
    counter[key] = counter.get(key, 0) + 1


def parse_docket(pdf_bytes: bytes, source_url: str | None = None) -> tuple[list[dict], dict]:
    """Returns (records, stats) for one docket PDF. `records` are ready for
    `_upsert_records()` — case_type already classified, and only the
    privacy-safe side's party name populated (see module docstring).
    """
    stats = _empty_entry_stats()
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        stats["parse_errors"] += 1
        print(f"sessions_dockets: could not open PDF: {e}", file=sys.stderr)
        return [], stats

    court_date_raw, docket_type_header = None, None
    body_rows: list[tuple[str, str, str, str]] = []

    for page in reader.pages:
        stats["pages"] += 1
        try:
            text = page.extract_text() or ""
        except Exception as e:
            stats["parse_errors"] += 1
            print(f"sessions_dockets: page extract_text() failed: {e}", file=sys.stderr)
            continue

        lines = text.splitlines()
        d, t = _parse_page_header(lines)
        court_date_raw = court_date_raw or d
        docket_type_header = docket_type_header or t

        cols, header_idx = None, None
        for i, line in enumerate(lines):
            found = _find_header_columns(line)
            if found:
                cols, header_idx = found, i
                break
        if cols is None:
            # Couldn't locate the column-header row on this page at all —
            # a real template change, not ragged whitespace. Fail closed:
            # skip this page's rows rather than guess at column positions.
            stats["parse_errors"] += 1
            continue

        for line in lines[header_idx + 1:]:
            if line.strip():
                body_rows.append(_row_cells(line, cols))

    stats["docket_header_type"] = docket_type_header

    court_date_mdy = None
    if court_date_raw:
        try:
            court_date_mdy = datetime.strptime(court_date_raw, "%B %d, %Y").strftime("%m/%d/%Y")
        except ValueError:
            court_date_mdy = None

    records = []
    for group in _split_entries(body_rows):
        stats["entries_seen"] += 1
        record = _parse_entry(group, court_date_mdy, source_url, stats)
        if record:
            records.append(record)

    return records, stats


def _parse_entry(
    rows: list[tuple[str, str, str, str]],
    court_date_mdy: str | None,
    source_url: str | None,
    stats: dict,
) -> dict | None:
    vs_idx, case_number = None, None
    for i, row in enumerate(rows):
        m = CASE_NUMBER_RE.search(row[2])
        if m:
            vs_idx, case_number = i, m.group(1)
            break
    if case_number is None:
        stats["skipped_no_case_number"] += 1
        return None

    # Style-of-Case lines before the "VS. <case#>" row are the plaintiff(s);
    # after it are the defendant(s). Only ONE side ever reaches the return
    # value below — see module docstring's privacy section.
    plaintiff_lines = [_clean(r[2]) for r in rows[:vs_idx] if r[2].strip()]
    defendant_lines = [_clean(r[2]) for r in rows[vs_idx + 1:] if r[2].strip()]

    comments = [c for c in (r[3].strip() for r in rows) if c]

    case_type_raw, category = None, None
    for c in comments:
        mapped = CASE_TYPE_WHITELIST.get(c.upper())
        if mapped:
            case_type_raw, category = c, mapped
            break
    _bump(stats["case_type_seen"], case_type_raw if case_type_raw else "(unrecognized)")

    if category is None:
        stats["skipped_unclassified"] += 1
        return None

    file_date_iso = None
    for c in comments:
        m = FILE_DATE_RE.search(c)
        if m:
            try:
                file_date_iso = datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat()
            except ValueError:
                pass
            break

    division = None
    for c in comments:
        m = DIVISION_RE.search(c)
        if m:
            division = m.group(1)
            break

    attorney = next((_clean(r[1]) for r in rows if r[1].strip()), None)

    if category == "detainer":
        party_name = " / ".join(dict.fromkeys(plaintiff_lines)) or None
    else:
        party_name = " / ".join(dict.fromkeys(defendant_lines)) or None

    if not party_name:
        # Can't build a usable lead without a name — fail closed rather
        # than storing a case with nothing to eventually match to a parcel.
        stats["skipped_no_party_name"] += 1
        return None

    _bump(stats["owner_type_seen"], classify_owner_type(party_name))

    hearing_bits = [b for b in (court_date_mdy, f"Div {division}" if division else None) if b]
    desc_parts = [case_type_raw]
    if hearing_bits:
        desc_parts.append("hearing " + " ".join(hearing_bits))
    if attorney:
        desc_parts.append(f"atty {attorney}")
    description = ", ".join(desc_parts)

    if category == "detainer":
        stats["kept_detainer"] += 1
    else:
        stats["kept_collections"] += 1

    return {
        "dedupe_key": f"edockets:{case_number}",
        "case_number": case_number,
        "case_type": category,
        "filing_date": file_date_iso,
        "party_names": party_name,
        "raw_owner_name": party_name,
        "amount": None,
        "raw_address": None,
        "description": description,
        "source_url": source_url,
    }


# --------------------------------------------------------------------------
# Upsert
# --------------------------------------------------------------------------

def _upsert_records(conn, records: list[dict]) -> None:
    for r in records:
        upsert_record(
            conn, "court_records", r["dedupe_key"],
            keep_existing=("parcel_id", "resolution_method"),
            parcel_id=None,
            source_portal=SOURCE_PORTAL,
            case_number=r["case_number"],
            case_type=r["case_type"],
            filing_date=r["filing_date"],
            party_names=r["party_names"],
            amount=r["amount"],
            raw_address=r["raw_address"],
            raw_owner_name=r["raw_owner_name"],
            resolution_method="unresolved",
            source_url=r["source_url"],
            description=r["description"],
        )


# --------------------------------------------------------------------------
# source_state bookkeeping ("seen" docket paths)
# --------------------------------------------------------------------------

def _load_seen(conn) -> dict:
    raw = get_state(conn, STATE_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_seen(conn, seen: dict) -> None:
    if len(seen) > MAX_SEEN_TRACKED:
        # Dicts preserve insertion order (3.7+); keep the most recently
        # inserted entries so the bound never grows unbounded over time.
        seen = dict(list(seen.items())[-MAX_SEEN_TRACKED:])
    set_state(conn, STATE_KEY, json.dumps(seen))


# --------------------------------------------------------------------------
# Run totals across every docket processed in one invocation
# --------------------------------------------------------------------------

def _new_run_totals() -> dict:
    totals = _empty_entry_stats()
    totals["docket_header_types"] = {}
    totals["dockets_in_list"] = 0
    totals["dockets_new"] = 0
    totals["dockets_processed"] = 0
    totals["fetch_failures"] = 0
    totals["list_empty"] = False
    return totals


def _merge_stats(totals: dict, add: dict) -> None:
    for key in (
        "pages", "entries_seen", "kept_detainer", "kept_collections",
        "skipped_unclassified", "skipped_no_case_number",
        "skipped_no_party_name", "parse_errors",
    ):
        totals[key] = totals.get(key, 0) + add.get(key, 0)
    for k, v in add.get("case_type_seen", {}).items():
        totals["case_type_seen"][k] = totals["case_type_seen"].get(k, 0) + v
    for k, v in add.get("owner_type_seen", {}).items():
        totals["owner_type_seen"][k] = totals["owner_type_seen"].get(k, 0) + v
    header_type = add.get("docket_header_type")
    if header_type:
        totals["docket_header_types"][header_type] = totals["docket_header_types"].get(header_type, 0) + 1


# --------------------------------------------------------------------------
# Live run (what GitHub Actions executes daily)
# --------------------------------------------------------------------------

def _run_live(conn) -> dict:
    session = requests.Session()
    try:
        entries = fetch_list(session)
    except (requests.RequestException, ValueError) as e:
        # Genuine failure to reach/parse the endpoint — the only case that
        # should make main() exit non-zero for this source. An empty list
        # (the normal case) never reaches this branch.
        raise RuntimeError(f"could not reach/parse the edockets.us docket list: {e}") from e

    seen = _load_seen(conn)
    new_entries = [e for e in entries if str(e[5]) not in seen]

    totals = _new_run_totals()
    totals["dockets_in_list"] = len(entries)
    totals["dockets_new"] = len(new_entries)
    totals["list_empty"] = len(entries) == 0

    for i, entry in enumerate(new_entries):
        date_str, _court_time, _office, _division, docket_type, path = entry[:6]
        if i > 0:
            time.sleep(PDF_FETCH_DELAY_SECONDS)
        pdf_bytes = fetch_pdf(path, session)
        if pdf_bytes is None:
            totals["fetch_failures"] += 1
            continue
        records, stats = parse_docket(pdf_bytes, source_url=PDF_BASE_URL + path)
        _upsert_records(conn, records)
        _merge_stats(totals, stats)
        totals["dockets_processed"] += 1
        seen[path] = {"date": date_str, "docket_type": docket_type, "parsed_at": now_iso()}

    _save_seen(conn, seen)
    return totals


# --------------------------------------------------------------------------
# Dev-only samples run — parses local PDFs instead of hitting edockets.us.
# This is how the module is tested end-to-end on a machine that can't
# reach the live host (see module docstring). NOT used by the daily
# workflow, which calls _run_live() via main()'s default (no --samples-dir).
# --------------------------------------------------------------------------

def _reconstruct_sample_source_url(filename: str) -> str:
    """Sample files are named "<Type>_known_<MMDD>_<original-basename>.pdf"
    (see .github/workflows/dev_samples.yml) — recover the real edockets.us
    path from the basename when possible, purely so source_url looks like
    what a live run would have stored; falls back to a clearly-marked
    "dev-sample:" pseudo-URL when the filename doesn't match that pattern
    (e.g. a hand-added test fixture).
    """
    m = SAMPLE_PATH_RE.search(filename)
    if m:
        return PDF_BASE_URL + "dockets/" + m.group(1)
    return f"dev-sample:{filename}"


def _run_samples(conn, samples_dir: Path) -> dict:
    seen = _load_seen(conn)
    pdf_paths = sorted(samples_dir.glob("*.pdf"))
    new_paths = [p for p in pdf_paths if f"sample:{p.name}" not in seen]

    totals = _new_run_totals()
    totals["dockets_in_list"] = len(pdf_paths)
    totals["dockets_new"] = len(new_paths)
    totals["list_empty"] = len(pdf_paths) == 0

    for p in new_paths:
        pdf_bytes = p.read_bytes()
        records, stats = parse_docket(pdf_bytes, source_url=_reconstruct_sample_source_url(p.name))
        _upsert_records(conn, records)
        _merge_stats(totals, stats)
        totals["dockets_processed"] += 1
        seen[f"sample:{p.name}"] = {
            "date": None,
            "docket_type": stats.get("docket_header_type"),
            "parsed_at": now_iso(),
        }

    _save_seen(conn, seen)
    return totals


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest Hamilton County General Sessions Court civil dockets from edockets.us."
    )
    ap.add_argument(
        "--samples-dir",
        help=(
            "DEV ONLY: parse local docket PDFs from this directory instead of "
            "hitting edockets.us live (also settable via SESSIONS_SAMPLE_DIR). "
            "edockets.us answers only US-origin requests, so this is the only "
            "way to exercise the parser end-to-end from a non-US dev machine."
        ),
    )
    args = ap.parse_args()
    samples_dir_str = args.samples_dir or os.environ.get("SESSIONS_SAMPLE_DIR")

    conn = get_connection()

    if samples_dir_str:
        print(f"sessions_dockets: DEV MODE — parsing local samples from {samples_dir_str} instead of edockets.us")
        totals = _run_samples(conn, Path(samples_dir_str))
    else:
        try:
            totals = _run_live(conn)
        except RuntimeError as e:
            log_scrape(conn, LOG_SOURCE, record_count=0, status="error", notes=str(e))
            conn.commit()
            print(f"sessions_dockets: ERROR — {e}", file=sys.stderr)
            return 1

    conn.commit()

    kept = totals["kept_detainer"] + totals["kept_collections"]
    notes = (
        f"dockets_in_list={totals['dockets_in_list']}, dockets_new={totals['dockets_new']}, "
        f"dockets_processed={totals['dockets_processed']}, list_empty={totals['list_empty']}, "
        f"fetch_failures={totals['fetch_failures']}, kept_detainer={totals['kept_detainer']}, "
        f"kept_collections={totals['kept_collections']}, "
        f"skipped_unclassified={totals['skipped_unclassified']}, "
        f"skipped_no_case_number={totals['skipped_no_case_number']}, "
        f"skipped_no_party_name={totals['skipped_no_party_name']}, "
        f"parse_errors={totals['parse_errors']}"
    )
    log_scrape(conn, LOG_SOURCE, record_count=kept, status="ok", notes=notes)
    conn.commit()

    print(
        f"sessions_dockets: upserted {kept} cases "
        f"({totals['kept_detainer']} detainer, {totals['kept_collections']} collections). {notes}"
    )
    print(f"  case_type_seen={totals['case_type_seen']}")
    print(f"  owner_type_seen (of kept party names)={totals['owner_type_seen']}")
    print(f"  docket_header_types={totals['docket_header_types']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
