"""
Hamilton County trustee foreclosure-sale notices — ingested from SIX
independent third-party posting sites, additively, into court_records as
case_type='foreclosure_notice'.

Why six sites at all
--------------------
Tennessee foreclosures are non-judicial: state law requires 3 consecutive
weeks of newspaper publication before a sale, not a county court filing.
Each law firm/trustee picks ONE third-party site to mirror its newspaper ad
digitally, so the six sites carry almost entirely disjoint sets of Hamilton
notices (a prior cross-check of 40 unique Hamilton notices over one month
found the first four sites below covered all 40 with zero overlap between
them). This module is therefore an ADDITIVE multi-source ingest, not a
"pick the best site" scraper — every site's function runs independently
(a failure in one never stops the others; main() only raises if all six
fail), and a cross-source merge pass collapses the SAME real-world sale
when it happens to appear on more than one site (see "Merge-key design"
below).

Live-verified 2026-09-22 against real responses from all six sites (not
assumed from the task brief) — one specific finding per site:

1. betterchoicenotices.com — anonymous JSON API, no terms found on the
   site itself (class A). `/api/counties/?stateId=44` confirmed Hamilton
   is still countyId=33 (hardcoded below with this comment as the
   verification record). `/api/notices/?...countyId=33...` returned 44
   Hamilton rows for a -60/+90 day window; the response's Content-Type
   header lies (`text/html`) even though the body is real JSON — parsed
   with `json.loads(resp.text)` regardless, as the brief warned.
   `/api/document-contents/?noticeId=<id>` (the numeric `id` field, NOT
   `bcns_id` — confirmed `bcns_id` 400s) returns `{"preSignedURL": ...}`,
   an S3 URL that resolved to a real `%PDF-1.x` when fetched immediately.
   The API list itself carries no book/page or grantor name — only the
   notice PDF does — so this is the one site where a per-notice PDF fetch
   is unavoidable if book/page (our primary dedupe key) is wanted.

2. nwpostingservices.com — anonymous JSON API (form POST), no terms found
   (class A). `POST /Public/getAuctions` with `SaleDateFrom/SaleDateTo`
   (ISO)/`State=TN`/`County=Hamilton`/`ZipCode=` returned 5 real Hamilton
   rows, one already postponed (`NewSaledate` wrapped in
   `<span class="postponed">11/17/26</span>` — HTML-in-JSON, stripped
   before parsing). The row's own `View` field is an `<a href=...>` whose
   href already tells you which of the two PDF path shapes applies
   (`/documents/posting/<id>` or `/documents/ad/<id>/pdf`) — both
   resolved to real `%PDF` bytes when tried live, so parse() reads the id
   straight out of that href rather than guessing/trying both blindly.

3. foreclosuretennessee.com — iMIS + Telerik RadGrid ASP.NET postback,
   class B/C terms ("You may view, search, and download foreclosure
   notices for informational purposes only. You may not republish or
   reuse content without written permission" — Terms of Use page, quoted
   verbatim). The task owner has approved ingesting this site for
   private, internal lead-research use only — never republished or
   redistributed — consistent with that clause. Confirmed live: one GET
   of `/` yields `__VIEWSTATE`/`__VIEWSTATEGENERATOR` plus a Hamilton
   `<option>` inside a multi-select listbox
   (`...ResultsGrid$Sheet0$Input0$ctl00$ListBox`); POSTing every hidden
   field plus that listbox set to "Hamilton" and the `SubmitButton` field
   set to "Find", in the SAME session, returned a RadGrid results table
   with 5 Hamilton rows and `submissionID=903/911/912/913/914` detail
   links. One detail page (submissionID=913, Soddy Daisy) was fetched:
   its `data:application/pdf;base64,...` inline PDF decoded to a real,
   text-layer PDF mentioning "HAMILTON COUNTY" and printing "Tax Map
   Identification No.: 057L-A-009" — a rare case clean enough to trust as
   a native parcel_id (see parcel resolution below). A session that
   expired would 200 with a "Sign In" page and no `rgMasterTable`/
   `RadGrid` marker in the body — fetch() checks for that marker and
   raises rather than silently treating a login wall as "zero results."

4. tnlegalpub.com — one anonymous WP REST call, no terms found (class A).
   `/wp-json/wp/v2/county?search=Hamilton` confirmed the `county` taxonomy
   id is still 132 (hardcoded below with this comment as the verification
   record — no separate `legal_notice_county` endpoint exists, a 404 was
   confirmed). `/wp-json/wp/v2/legal_notice?county=132&per_page=100&after=...`
   returned real Hamilton notices (5 within 200 days, 8 within 270 days at
   verification time) with full notice text inline in
   `content.rendered` — no PDF fetch needed for this source at all, the
   cheapest of the six. `.json()` is wrapped in a try/except per the
   brief's warning that this WP REST endpoint occasionally returns
   malformed JSON; a bad response is skipped (this whole site's run
   fails, but the exception is caught at the main()-level per-site try/
   except so the other five sites are unaffected either way). Keyed on
   the REST `id` (not slug — slugs seen ending in `__trashed`).

5. capitalcitypostings.com — static HTML, class C terms (the task brief's
   pre-supplied classification: "personal use only", bans building
   mailing/marketing lists). The task owner has explicitly approved
   ingesting this site anyway, "to prove we can build a complete list",
   with the standing instruction to prefer a cleaner (class A/B) source
   for the same record whenever one exists — see "Merge-key design",
   which implements exactly that preference. One GET of
   `/tennessee-postings` returned one big all-states `<table>` (headers:
   NOS/County/Sale Date/Property Address/Postponed/Client); the County
   column reads "Hamilton County" (not bare "Hamilton" — confirmed by
   inspection, filtering must check `startswith`, not equality), 15 rows
   matched at verification time, all client "PLG" (Padgett Law Group).
   One NOS PDF was fetched (334 Pine Ridge Road) and confirmed to be a
   real text-layer PDF containing "recorded in Book GI 14058, Page 120"
   and, on a different sampled PDF, an explicit
   "Map/Parcel Number: 135C J 002" line — clean enough to trust as a
   native parcel_id when the shape validates (see below).

6. tennesseepostings.com — static HTML (wpDataTables), class C terms
   (same personal-use/no-marketing-list restriction as #5, same owner
   approval, same "prefer a cleaner source" rule). One GET of `/`
   server-rendered `<table id="table_1">` (headers: Document/TS#/Address/
   City/County/State/Zip Code/Sale date/Status) with ALL TN rows; 4
   matched County=="Hamilton" at verification time. One PDF
   (3054 Igou Crossing Drive) was fetched and confirmed to be a real
   text-layer PDF ("recorded in Deed Book GI 12041, Page (s) 413" —
   note the "Page (s)" spacing variant, handled by BOOK_PAGE_RE below).
   `/wp-json/wp/v2/media` exists and could support incremental detection
   but isn't used here — see "Change detection" below.

internetpostings.com was explicitly NOT built here — it sits behind a
Terms-of-Service click-through checkbox that only the account holder (the
task owner) can accept; ground rules for this build forbid completing any
consent gate. Flagged as a manual-decision item in the build report, not
attempted.

Merge-key design (the single most important decision in this module)
--------------------------------------------------------------------
Every normalized record — regardless of which site it came from — gets a
`dedupe_key`, computed the SAME way for all six sites, in this priority
order:

  1. `foreclosure_notice:bp:<book>-<page>` when a Register of Deeds book
     and page number could be extracted from the notice's own text (via
     BOOK_PAGE_RE, which tolerates the "Book GI N", "GI N", "GI Book N ...
     at Page N", and "Page (s) N" phrasings actually seen live across all
     four PDF-based sites plus tnlegalpub's inline text). Book/page is the
     PRIMARY key because it identifies the underlying recorded Deed of
     Trust instrument itself — the one fact that's identical no matter
     which site re-published the same newspaper ad. Both book and page
     numbers are stored with any "GI"/"Book" prefix and leading zeros
     stripped (`str(int(...))`) so "GI 013067" and "13067" collapse to the
     same key.
  2. `foreclosure_notice:addr:<normalized address>:<sale_date>` when no
     book/page could be extracted but a property address and a sale date
     are both known. `parcel_utils.normalize_address()` (uppercase, strip
     punctuation, collapse whitespace) is reused here exactly as it's
     used elsewhere in this codebase for the same reason: two sites
     rendering "123 Main St." vs "123 MAIN STREET" should still collapse
     when the sale date matches.
  3. `foreclosure_notice:site:<site>:<notice id>` as a last resort — this
     effectively never merges across sources (each site's own notice id
     is site-specific), but it still gives every record a stable key so
     it's upserted (not dropped) even when neither of the above can be
     computed.

After every site's records are collected for the run, `merge_records()`
groups all of them by this key. For a key seen on more than one site, the
group's records are ranked by (a) how many of a fixed set of "value
fields" are populated (more complete wins), then (b) a fixed SITE_PRIORITY
order that puts the three class-A sites first, then foreclosuretennessee
(owner-approved, internal-use-only), then the two class-C sites last —
this is exactly the task owner's standing instruction to "prefer a cleaner
source for the same record when one exists." The winning record's blank
fields are then backfilled from the other group members (so a class-A
site's thin address-only row can still pick up a class-C site's grantor
name, for example), `cancelled` is OR'd across the whole group (a
cancellation reported by ANY site is treated as real), `source_portal` is
set to that single site's short name unless the group's records span
MORE THAN ONE DISTINCT SITE, in which case it becomes `'multi'`;
`source_url` stays the winning record's own URL, and every OTHER
contributing site's short name is appended to `description` as
"— also posted: <site>, <site>". This is deliberately a per-RUN merge
(each daily run re-collects all six sites' current small feeds and
re-merges from scratch) rather than a persisted merge state — see
"Change detection" below for why that's safe here.

A key's records can collapse to a single group without ever being
cross-site: the FIRST live run (2026-09-22) found 6 such keys, and every
one of them turned out to be one site posting the SAME Book/Page twice
(betterchoicenotices re-submits a notice as a new API row each time a
sale is postponed; tnlegalpub occasionally carries a revised post under a
second numeric id for the same notice) rather than genuine cross-source
corroboration. `merge_records()` tracks the two cases separately
(`multi_source_keys` for >1 distinct site vs. `same_site_duplicate_keys`
for >1 raw record but only one site) so `source_portal` and
scrape_log's notes never overstate cross-site coverage on a day when no
real overlap happens to occur — consistent with the task brief's own
finding that these sites carry "almost entirely disjoint" notices.

Parcel resolution — deliberately conservative
-----------------------------------------------
Almost none of these notices print a clean, machine-parseable Map/Group/
Parcel triplet; most only give a legal description and a street address.
`_extract_native_parcel()` looks for an explicit label ("Tax Map
Identification No.:", "Map/Parcel Number:", "Map and Parcel Numbers:") in
the notice's own text, splits the captured value into up to 3 whitespace-
or-hyphen-separated tokens (dropping a stray "and" when a notice lists two
split-parcel numbers — only the first pair is kept, a known limitation),
and validates each token's shape (map ~ 2-3 digits + optional letter,
group ~ 1-2 letters, parcel ~ digits with an optional 2-decimal split
suffix) before calling `parcel_utils.format_parcel_id()`. A confident hit
sets `resolution_method='native_parcel_id'`; everything else is left
`parcel_id=None, resolution_method='unresolved'` for `enrich_assessor.py`
to resolve later by address, exactly as the task specified. Both
`parcel_id` and `resolution_method` are passed through
`db.upsert_record(..., keep_existing=("parcel_id", "resolution_method"))`
on every upsert, so a later enrichment (or a native match this module
itself makes on a subsequent run) is never clobbered back to NULL/
'unresolved' by an ordinary re-scrape.

Cancelled / withdrawn notices
------------------------------
court_records has no status column (checked — it doesn't; the schema was
read directly from db.py before writing this). A cancelled notice
(betterchoicenotices' `cancelled` field, or a site's own "Postponed"/
"Cancelled" status text) gets case_type='foreclosure_notice_cancelled'
instead of 'foreclosure_notice' — mirroring tax_sale.py's
'tax_sale_resolved' pattern exactly, with a matching 0-tier entry in
scoring.py's CASE_TYPE_TIER (added during integration) so a cancelled sale
never scores as live distress. The cancellation is also folded into
`description` as a `[CANCELLED/WITHDRAWN]` tag for a human-readable audit
trail. The row is kept, never deleted.

Change detection — per-notice document caching, not a full-refresh skip
--------------------------------------------------------------------------
Unlike tax_sale.py/probate_dockets.py (one small county PDF, cheap to
re-parse every run), four of these six sites require a SEPARATE per-notice
PDF fetch to get book/page and grantor name (betterchoicenotices,
nwpostingservices, capitalcitypostings, tennesseepostings all need one PDF
GET per row; foreclosuretennessee needs one detail-page GET per
submissionID). Re-fetching every notice's PDF from a small third-party
site EVERY day — most of which are notices already seen yesterday — would
not be "a small daily delta," it would be a full daily bulk pull of
someone else's PDF hosting, which the ground rules for this build
explicitly want avoided ("small daily deltas, not bulk historical
pulls... don't hammer any site"). So each of those five sites keeps a
small, permanent cache in `source_state` (`foreclosure_notices:<site>:
doc_cache`, a JSON dict keyed by notice id or PDF URL) of the
already-extracted book/page/grantor/trustee/native-parcel fields; a
notice already in the cache is never re-fetched, only genuinely new
notice ids/PDF URLs trigger a new GET (with the same 1.5s-per-host delay
as every other request in this module). The cheap, no-extra-fetch fields
(address, sale date, postponed date, cancelled flag) still come straight
from each site's own listing/API response on every run, so those stay
fully current; only the PDF-derived fields are cached. tnlegalpub needs no
cache at all (its one API call already returns full notice text).
Each cache is bounded to the ~3000 most-recently-added entries so
source_state can't grow unbounded.

Amount is always None
----------------------
None of the six sites' notices state a dollar figure that means what
`court_records.amount` means elsewhere in this schema (a debt or judgment
amount). A "minimum bid" dollar figure was not observed on any of the six
sites' text/tables at verification time; if a future notice does print
one, it is NOT written to `amount` without saying explicitly which site
and field it came from, per the task's ground rules.

Known limitations (see also the build report)
-----------------------------------------------
- Grantor-name and trustee-name extraction from PDF/notice free text is a
  best-effort regex heuristic (several phrasing variants observed live
  are handled, in priority order), not a guaranteed-correct parse — names
  are kept "as printed" rather than normalized to the county's LAST FIRST
  convention, since this module can rarely confirm a parcel match to know
  that convention applies (see task spec).
- A notice with an explicit two-split-parcel "Map and Parcel Numbers: X
  and Y" line only has its FIRST pair captured as a native-parcel
  candidate.
- foreclosuretennessee's own documented defect (a listing occasionally
  carries a different county's PDF) is guarded by checking the extracted
  PDF text for "HAMILTON COUNTY" before trusting book/page/grantor from
  it; a near-zero-text scanned PDF is guarded by a text-length floor
  (both fall back to the listing page's own labeled fields, never crash).
- betterchoicenotices' S3 `preSignedURL` expires in ~60 seconds — fetched
  immediately after the document-contents call, with no sleep in between
  (the per-host rate limit sleep happens before the NEXT notice's API
  call instead). Its `court_records.source_url` is set to the stable
  `/api/document-contents/?noticeId=<id>` endpoint (re-fetchable any
  time to mint a fresh presigned URL), never the presigned URL itself.
- No pagination limit was hit on any site at verification time (largest
  single response was betterchoicenotices' 44 rows); page_size=500 and
  per_page=100 were used specifically to make a second page unlikely for
  a single-county daily delta, but no second-page-fetch loop is
  implemented — if a site's Hamilton volume ever exceeds one page, this
  silently truncates rather than erroring, another report-flagged item.
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone

import pypdf
import requests
from bs4 import BeautifulSoup

from db import get_connection, get_state, log_scrape, set_state, upsert_parcel, upsert_record
from parcel_utils import format_parcel_id, normalize_address

REQUEST_HEADERS = {
    "User-Agent": "chattanooga-intel/1.0 (distressed-property lead research; contact via github.com/Electrumtexas/chattanooga-intel)"
}
REQUEST_TIMEOUT = 30
REQUEST_DELAY_SECONDS = 1.5  # minimum delay between requests to the same host
MAX_DOC_CACHE_ENTRIES = 3000

# --- Regex helpers shared across sites -------------------------------------

# Tolerates: "Book GI 13067, Page 176" / "GI Book 13440 at Page 675" /
# "GI 10189, Page 746" / "Book 8925, Page 350" / "Deed Book GI 12041 ,
# Page (s) 413" / "Deed Book GI 14408, Page(s) 687-689" — all confirmed
# live across the four/five PDF-or-inline-text sources. See module
# docstring's "Merge-key design" for why this is the PRIMARY dedupe field.
BOOK_PAGE_RE = re.compile(
    r"(?:GI\s*Book|Book\s*GI|GI|Book)\s+(\d+)[\s\S]{0,20}?Page\s*(?:\(s\))?\s*[:,]?\s*(\d+)",
    re.IGNORECASE,
)

# Best-effort grantor/borrower (the distressed owner) extraction, tried in
# this priority order against the notice's own text. "Owner of Property:"
# (tnlegalpub's own label) is unambiguous where present, so it's tried
# first; the rest are heuristics for the "executed by NAME to TRUSTEE" /
# "WHEREAS, NAME, a married woman, by Deed of Trust" / "wherein NAME
# conveyed to" phrasings actually observed live across the other sites.
GRANTOR_RES = [
    re.compile(r"Owner of Property:\s*(.+?)(?:\s{2,}|Other Interested|Map and Parcel|$)", re.IGNORECASE),
    re.compile(r"WHEREAS,\s+(.+?),\s+a\s+(?:married|single)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"WHEREAS,\s+(.+?)\s+by Deed of Trust", re.IGNORECASE | re.DOTALL),
    re.compile(
        r"\bby\s+(.+?)(?:,?\s+to\s+[A-Z]|\.\s+The Deed of Trust|,\s+(?:a\s+)?(?:married|unmarried|single)\b)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"wherein\s+(.+?)\s+conveyed to", re.IGNORECASE | re.DOTALL),
]

# Best-effort trustee/law-firm extraction — secondary field, not the lead
# identity, so a lower coverage bar is acceptable here (see docstring).
TRUSTEE_RES = [
    re.compile(r"\bto\s+([A-Z][A-Za-z0-9.,&'\-\s]{2,80}?),?\s+(?:as\s+)?Trustee\b", re.IGNORECASE),
    re.compile(r"([A-Z][A-Za-z0-9.,&'\-\s]{2,80}?)\s+is\s+the\s+Trustee\b", re.IGNORECASE),
    re.compile(r"conducted by\s+([A-Z][A-Za-z0-9.,&'\-\s]{2,80}?),\s+having been appointed", re.IGNORECASE),
]

# Native Map/Group/Parcel label variants actually seen live (foreclosure-
# tennessee.com and capitalcitypostings.com PDFs, tnlegalpub inline text).
MAP_LABEL_RE = re.compile(
    r"(?:Tax\s+Map\s+(?:Identification\s+No\.?|ID)|Map\s*/\s*Parcel\s*(?:Number|No\.?)s?"
    r"|Map\s*(?:and|&)\s*Parcel\s*Numbers?|Map\s*Parcel\s*(?:Number|No\.?))\s*:?\s*"
    r"([A-Z0-9][A-Z0-9.\-]*(?:\s+[A-Z0-9][A-Z0-9.\-]*){0,2})",
    re.IGNORECASE,
)
_MAP_TOKEN_RE = re.compile(r"^\d{2,3}[A-Z]{0,2}$", re.IGNORECASE)
_GROUP_TOKEN_RE = re.compile(r"^[A-Z]{1,2}$", re.IGNORECASE)
_PARCEL_TOKEN_RE = re.compile(r"^\d{1,4}(?:\.\d{1,2})?[A-Z0-9]{0,6}$", re.IGNORECASE)

# tnlegalpub's inline notice text has no separate address field — pulled
# from the text itself via whichever phrasing the trustee's boilerplate
# uses (all three confirmed live against real Hamilton notices).
ADDR_RES = [
    re.compile(r"street address[^.]{0,60}?is believed to be\s+(.+?),\s*([A-Za-z .]+?),\s*TN\s*(\d{5})", re.IGNORECASE),
    re.compile(r"Street Address:\s*(.+?)\s+City:\s*(.+?)\s+Zip:\s*(\d{5})", re.IGNORECASE),
    re.compile(r"commonly known as[:\s]+(.+?),\s*([A-Za-z .]+?),?\s*(?:Hamilton County,?\s*)?TN\s*(\d{5})", re.IGNORECASE),
]

_DOW = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
SALE_DATE_RES = [
    re.compile(rf"will be (?:sold|on)\s+([A-Za-z]+\s+\d{{1,2}},?\s+\d{{4}})", re.IGNORECASE),
    re.compile(rf"on\s+{_DOW},\s+([A-Za-z]+\s+\d{{1,2}},?\s+\d{{4}})", re.IGNORECASE),
    re.compile(r"sale (?:date|will occur on|to be held on)\s*[:]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE),
    re.compile(r"on\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4}),?\s+(?:at|between)", re.IGNORECASE),
]

_ISO_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _to_iso_date(raw) -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    m = _ISO_PREFIX_RE.match(raw)
    if m:
        return m.group(1)
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", " ", s)


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _extract_book_page(text: str) -> tuple[str | None, str | None]:
    m = BOOK_PAGE_RE.search(re.sub(r"\s+", " ", text))
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _extract_grantor(text: str) -> str | None:
    window = re.sub(r"\s+", " ", text)[:2500]
    for rx in GRANTOR_RES:
        m = rx.search(window)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
            if name and 3 < len(name) < 150 and "WHEREAS" not in name.upper() and "deed of trust" not in name.lower():
                return name
    return None


# Role-filler words that show up standing in for a real trustee name in
# boilerplate like "...known to the Substitute Trustee may include..." —
# TRUSTEE_RES[0]'s "to NAME, Trustee" shape can match this "to the
# Substitute Trustee" phrase just as validly as a real "to CHARLES E.
# TONKIN, Trustee" one, so a candidate made up ENTIRELY of these filler
# words (not a real corporate name that happens to start with "The", e.g.
# "The Bank of New York Mellon") is rejected. Confirmed live: a real
# betterchoicenotices PDF produced exactly this false match before this
# guard was added.
_TRUSTEE_FILLER_WORDS = {
    "the", "a", "an", "said", "such", "any", "known", "claim", "claims",
    "substitute", "successor", "current", "above", "named", "trustee",
}


def _extract_trustee(text: str) -> str | None:
    """Tries every match of each pattern (not just the first) before moving
    to the next pattern — needed because TRUSTEE_RES[0]'s "to NAME, Trustee"
    shape can also match an unrelated earlier "pursuant to Deed of Trust
    executed by GRANTOR, to TRUSTEE, Trustee" sentence, lazily swallowing
    the grantor clause too (confirmed live on a real betterchoicenotices
    PDF). A candidate containing "deed of trust"/"executed by", or made up
    entirely of role-filler words, is rejected the same way a bad
    grantor-name candidate is rejected in _extract_grantor(), and the
    search moves on to the next match/pattern.
    """
    window = re.sub(r"\s+", " ", text)[:3000]
    for rx in TRUSTEE_RES:
        for m in rx.finditer(window):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
            if not name or not (3 < len(name) < 100):
                continue
            if "WHEREAS" in name.upper() or "deed of trust" in name.lower() or "executed by" in name.lower():
                continue
            tokens = [t.strip(",.").lower() for t in name.split()]
            if tokens and all(t in _TRUSTEE_FILLER_WORDS for t in tokens):
                continue
            return name
    return None


def _extract_native_parcel(text: str) -> list[str] | None:
    window = re.sub(r"\s+", " ", text)
    m = MAP_LABEL_RE.search(window)
    if not m:
        return None
    tokens = [t for t in re.split(r"\s+", m.group(1).strip()) if t.lower() not in ("and", "or")]
    tokens = tokens[:3]
    return tokens or None


def _tokens_to_parcel_id(tokens: list[str] | None) -> str | None:
    if not tokens:
        return None
    if len(tokens) == 2:
        map_, group, parcel = tokens[0], "", tokens[1]
    elif len(tokens) == 3:
        map_, group, parcel = tokens
    else:
        return None
    if not _MAP_TOKEN_RE.match(map_):
        return None
    if group and not _GROUP_TOKEN_RE.match(group):
        return None
    if not _PARCEL_TOKEN_RE.match(parcel):
        return None
    return format_parcel_id(map_, group, parcel)


def _extract_address_from_text(text: str) -> tuple[str | None, str | None, str | None]:
    window = re.sub(r"\s+", " ", text)
    for rx in ADDR_RES:
        m = rx.search(window)
        if m:
            return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return None, None, None


def _extract_sale_date_from_text(text: str) -> str | None:
    window = re.sub(r"\s+", " ", text)
    for rx in SALE_DATE_RES:
        m = rx.search(window)
        if m:
            iso = _to_iso_date(m.group(1))
            if iso:
                return iso
    return None


def _split_address_csz(full: str | None) -> tuple[str | None, str | None, str | None]:
    """"123 Main St, Chattanooga, TN 37402" -> (street, city, zip)."""
    if not full:
        return None, None, None
    m = re.search(r"^(.*?),\s*([^,]+?),\s*TN\s*(\d{5})", full.strip(), re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    return full.strip(), None, None


def _fetch_pdf_fields(pdf_url: str, headers: dict | None = None) -> dict | None:
    """Shared PDF fetch + extraction used by every site whose notice text
    lives in a downloadable PDF (all but tnlegalpub). Returns None on any
    fetch/parse problem so the caller can count it as a failure and move
    on rather than crash the whole site's run.
    """
    try:
        resp = requests.get(pdf_url, headers=headers or REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200 or resp.content[:4] != b"%PDF":
        return None
    text = _pdf_text(resp.content)
    book, page = _extract_book_page(text)
    return {
        "book": book,
        "page": page,
        "grantor": _extract_grantor(text),
        "trustee": _extract_trustee(text),
        "native_parcel": _extract_native_parcel(text),
        "has_hamilton": "HAMILTON COUNTY" in text.upper(),
        "text_len": len(text),
    }


# --- Per-notice document cache (see docstring's "Change detection") --------


def _load_doc_cache(conn, site_key: str) -> dict:
    raw = get_state(conn, f"foreclosure_notices:{site_key}:doc_cache")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _save_doc_cache(conn, site_key: str, cache: dict) -> None:
    if len(cache) > MAX_DOC_CACHE_ENTRIES:
        cache = dict(list(cache.items())[-MAX_DOC_CACHE_ENTRIES:])
    set_state(conn, f"foreclosure_notices:{site_key}:doc_cache", json.dumps(cache))


# --- Common record shape ----------------------------------------------------


def _normalize_book_page(book, page) -> tuple[str | None, str | None]:
    b = str(int(book)) if book and str(book).isdigit() else None
    p = str(int(page)) if page and str(page).isdigit() else None
    return b, p


def _base_record(
    *,
    source_site: str,
    source_notice_id: str,
    source_url: str | None = None,
    pdf_url: str | None = None,
    raw_address: str | None = None,
    city: str | None = None,
    zip_code: str | None = None,
    sale_date: str | None = None,
    postponed_sale_date: str | None = None,
    filing_date: str | None = None,
    book=None,
    page=None,
    grantor_name: str | None = None,
    trustee_name: str | None = None,
    native_parcel_tokens: list[str] | None = None,
    cancelled: bool = False,
    case_ref: str | None = None,
    needs_ocr: bool = False,
    wrong_county_pdf: bool = False,
    postponed_flag_only: bool = False,
) -> dict:
    book_norm, page_norm = _normalize_book_page(book, page)
    parcel_id = _tokens_to_parcel_id(native_parcel_tokens)
    return {
        "source_site": source_site,
        "source_notice_id": source_notice_id,
        "source_url": source_url,
        "pdf_url": pdf_url,
        "raw_address": (raw_address or "").strip() or None,
        "city": (city or "").strip() or None,
        "zip": (zip_code or "").strip() or None,
        "sale_date": sale_date,
        "postponed_sale_date": postponed_sale_date,
        "filing_date": filing_date,
        "book_norm": book_norm,
        "page_norm": page_norm,
        "grantor_name": (grantor_name or "").strip() or None,
        "trustee_name": (trustee_name or "").strip() or None,
        "parcel_id": parcel_id,
        "resolution_method": "native_parcel_id" if parcel_id else "unresolved",
        "cancelled": bool(cancelled),
        "case_ref": case_ref,
        "needs_ocr": bool(needs_ocr),
        "wrong_county_pdf": bool(wrong_county_pdf),
        "postponed_flag_only": bool(postponed_flag_only),
    }


def compute_dedupe_key(rec: dict) -> str:
    """See module docstring's "Merge-key design" for the full rationale."""
    if rec.get("book_norm") and rec.get("page_norm"):
        return f"foreclosure_notice:bp:{rec['book_norm']}-{rec['page_norm']}"
    addr_norm = normalize_address(rec.get("raw_address"))
    date_for_key = rec.get("sale_date") or rec.get("filing_date")
    if addr_norm and date_for_key:
        return f"foreclosure_notice:addr:{addr_norm}:{date_for_key}"
    return f"foreclosure_notice:site:{rec['source_site']}:{rec.get('source_notice_id') or 'unknown'}"


# ============================================================================
# Site 1 — betterchoicenotices.com
# ============================================================================

BCN_BASE = "https://api.betterchoicenotices.com"
BCN_STATE_ID = 44  # Tennessee
BCN_COUNTY_ID = 33  # Hamilton — verified live 2026-09-22 via /api/counties/?stateId=44
BCN_WINDOW_PAST_DAYS = 60
BCN_WINDOW_FUTURE_DAYS = 90


def _fetch_bcn_pdf_fields(notice_id) -> dict | None:
    try:
        r = requests.get(
            f"{BCN_BASE}/api/document-contents/?noticeId={notice_id}",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        payload = json.loads(r.text)  # Content-Type lies here too
        pdf_url = payload.get("preSignedURL")
        if not pdf_url:
            return None
        # Presigned URL expires in ~60s — fetch immediately, no sleep here.
        pdf_resp = requests.get(pdf_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        if pdf_resp.status_code != 200 or pdf_resp.content[:4] != b"%PDF":
            return None
        text = _pdf_text(pdf_resp.content)
        book, page = _extract_book_page(text)
        return {
            "book": book,
            "page": page,
            "grantor": _extract_grantor(text),
            "trustee": _extract_trustee(text),
            "native_parcel": _extract_native_parcel(text),
        }
    except (requests.RequestException, ValueError):
        return None


def fetch_betterchoicenotices(conn) -> tuple[list, dict]:
    stats = {"api_items": 0, "non_foreclosure_skipped": 0, "pdf_fetched": 0, "pdf_cache_hits": 0, "pdf_fetch_failed": 0}
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=BCN_WINDOW_PAST_DAYS)).isoformat()
    to = (today + timedelta(days=BCN_WINDOW_FUTURE_DAYS)).isoformat()
    url = (
        f"{BCN_BASE}/api/notices/?stateId={BCN_STATE_ID}&countyId={BCN_COUNTY_ID}"
        f"&page=1&page_size=500&searchFromDate={frm}&searchToDate={to}"
    )
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    items = json.loads(resp.text)
    if not isinstance(items, list):
        items = items.get("results") or items.get("data") or []
    stats["api_items"] = len(items)

    cache = _load_doc_cache(conn, "betterchoicenotices")
    enriched = []
    for item in items:
        if "foreclosure" not in (item.get("notice_category_name") or "").lower():
            stats["non_foreclosure_skipped"] += 1
            continue
        notice_id = item.get("id")
        cache_key = str(notice_id)
        doc = cache.get(cache_key)
        if doc is None:
            time.sleep(REQUEST_DELAY_SECONDS)
            doc = _fetch_bcn_pdf_fields(notice_id)
            if doc is not None:
                cache[cache_key] = doc
                stats["pdf_fetched"] += 1
            else:
                stats["pdf_fetch_failed"] += 1
                doc = {}
        else:
            stats["pdf_cache_hits"] += 1
        enriched.append((item, doc))
    _save_doc_cache(conn, "betterchoicenotices", cache)
    return enriched, stats


def parse_betterchoicenotices(enriched: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "cancelled": 0}
    for item, doc in enriched:
        addr, city, zip_ = _split_address_csz(item.get("property_address"))
        cancelled = bool(item.get("cancelled"))
        if cancelled:
            stats["cancelled"] += 1
        notice_id = item.get("id")
        rec = _base_record(
            source_site="betterchoicenotices",
            source_notice_id=str(notice_id),
            source_url=f"{BCN_BASE}/api/document-contents/?noticeId={notice_id}",
            raw_address=addr,
            city=city,
            zip_code=zip_,
            sale_date=_to_iso_date(item.get("sale_date")),
            postponed_sale_date=_to_iso_date(item.get("postponed_sale_date")),
            filing_date=_to_iso_date(item.get("first_run_date")) or _to_iso_date(item.get("sale_date")),
            book=doc.get("book"),
            page=doc.get("page"),
            grantor_name=doc.get("grantor"),
            trustee_name=doc.get("trustee") or item.get("customer_name"),
            native_parcel_tokens=doc.get("native_parcel"),
            cancelled=cancelled,
            case_ref=item.get("law_firm_case_number"),
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats


# ============================================================================
# Site 2 — nwpostingservices.com
# ============================================================================

NW_BASE = "https://nwpostingservices.com"
NW_HREF_RE = re.compile(r"href=['\"]?(/documents/(?:ad/(\d+)/pdf|posting/(\d+)))['\"]?", re.IGNORECASE)


def fetch_nwpostingservices(conn) -> tuple[list, dict]:
    stats = {"api_rows": 0, "pdf_fetched": 0, "pdf_cache_hits": 0, "pdf_fetch_failed": 0, "no_id_extracted": 0}
    today = datetime.now().date()
    frm = (today - timedelta(days=60)).isoformat()
    to = (today + timedelta(days=90)).isoformat()
    resp = requests.post(
        f"{NW_BASE}/Public/getAuctions",
        data={"SaleDateFrom": frm, "SaleDateTo": to, "State": "TN", "County": "Hamilton", "ZipCode": ""},
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json()
    stats["api_rows"] = len(rows)

    cache = _load_doc_cache(conn, "nwpostingservices")
    enriched = []
    for row in rows:
        m = NW_HREF_RE.search(row.get("View") or "")
        if not m:
            stats["no_id_extracted"] += 1
            continue
        path = m.group(1)
        notice_id = m.group(2) or m.group(3)
        pdf_url = NW_BASE + path
        doc = cache.get(notice_id)
        if doc is None:
            time.sleep(REQUEST_DELAY_SECONDS)
            doc = _fetch_pdf_fields(pdf_url)
            if doc is not None:
                cache[notice_id] = doc
                stats["pdf_fetched"] += 1
            else:
                stats["pdf_fetch_failed"] += 1
                doc = {}
        else:
            stats["pdf_cache_hits"] += 1
        enriched.append((row, notice_id, pdf_url, doc))
    _save_doc_cache(conn, "nwpostingservices", cache)
    return enriched, stats


def parse_nwpostingservices(enriched: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "postponed": 0}
    for row, notice_id, pdf_url, doc in enriched:
        orig = _to_iso_date(row.get("OriginalSaledate"))
        new_raw = _strip_html(row.get("NewSaledate") or "").strip()
        postponed = _to_iso_date(new_raw) if new_raw else None
        if postponed:
            stats["postponed"] += 1
        rec = _base_record(
            source_site="nwpostingservices",
            source_notice_id=notice_id,
            source_url=pdf_url,
            pdf_url=pdf_url,
            raw_address=row.get("Street"),
            city=row.get("City"),
            zip_code=row.get("Zip"),
            # NewSaledate is authoritative for the currently-expected sale
            # date once postponed — OriginalSaledate anchors identity/dedupe
            # (see docstring: past sales vanish from this API entirely, so
            # every row seen is upserted, never deleted).
            sale_date=orig,
            postponed_sale_date=postponed,
            filing_date=orig,
            book=doc.get("book"),
            page=doc.get("page"),
            grantor_name=doc.get("grantor"),
            trustee_name=doc.get("trustee"),
            native_parcel_tokens=doc.get("native_parcel"),
            cancelled=False,
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats


# ============================================================================
# Site 3 — foreclosuretennessee.com
# ============================================================================

FT_BASE = "https://www.foreclosuretennessee.com"
FT_LISTBOX_FIELD = "ctl01$TemplateBody$WebPartManager1$gwpciNewQueryMenuCommon$ciNewQueryMenuCommon$ResultsGrid$Sheet0$Input0$ctl00$ListBox"
FT_SUBMIT_FIELD = "ctl01$TemplateBody$WebPartManager1$gwpciNewQueryMenuCommon$ciNewQueryMenuCommon$ResultsGrid$Sheet0$SubmitButton"
FT_LABEL_RE_TMPL = r"<strong>{}:</strong>\s*([^<]*)"


def _fetch_ft_detail(session: requests.Session, submission_id: int) -> dict | None:
    try:
        r = session.get(
            f"{FT_BASE}/Foreclosure/Foreclosure-Listing.aspx?submissionID={submission_id}",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None

    html_text = r.text
    fields = {}
    for label in ("Posted", "Sale Date", "Trustee Name", "Property Address", "City/State", "Zipcode"):
        m = re.search(FT_LABEL_RE_TMPL.format(re.escape(label)), html_text)
        fields[label] = m.group(1).strip() if m else None

    doc = {
        "posted": _to_iso_date(fields.get("Posted")),
        "sale_date": _to_iso_date(fields.get("Sale Date")),
        "trustee": fields.get("Trustee Name"),
        "raw_address": (fields.get("Property Address") or "").split("\n")[0].strip() or None,
        "city": (fields.get("City/State") or "").split(",")[0].strip() or None,
        "zip": fields.get("Zipcode"),
        "needs_ocr": False,
        "wrong_county": False,
    }

    m = re.search(r'data:application/pdf;base64,([A-Za-z0-9+/=\s]+)"', html_text)
    if not m:
        return doc  # no inline PDF at all — labeled fields still usable

    try:
        pdf_bytes = base64.b64decode(m.group(1))
    except Exception:
        pdf_bytes = b""
    text = _pdf_text(pdf_bytes) if pdf_bytes else ""

    if len(text.strip()) < 40:
        doc["needs_ocr"] = True  # scanned/near-zero text layer — don't crash, just fall back
        return doc

    if "HAMILTON COUNTY" not in text.upper():
        doc["wrong_county"] = True  # documented site defect — don't trust this PDF's body text
        return doc

    book, page = _extract_book_page(text)
    doc["book"], doc["page"] = book, page
    doc["grantor"] = _extract_grantor(text)
    doc["native_parcel"] = _extract_native_parcel(text)
    return doc


def fetch_foreclosuretennessee(conn) -> tuple[list, dict]:
    stats = {
        "submission_ids_found": 0,
        "detail_fetched": 0,
        "detail_cache_hits": 0,
        "detail_fetch_failed": 0,
        "wrong_county_pdf": 0,
        "needs_ocr": 0,
    }
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    r = session.get(FT_BASE + "/", timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    if form is None:
        raise RuntimeError("foreclosuretennessee: no <form> found on landing page — page structure changed")

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "submit":
            continue
        if itype in ("checkbox", "radio"):
            if inp.get("checked") is not None:
                data[name] = inp.get("value", "on")
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name or "ListBox" in name:
            continue
        opts = sel.find_all("option")
        chosen = [o.get("value", "") for o in opts if o.get("selected") is not None]
        data[name] = chosen[0] if chosen else (opts[0].get("value", "") if opts else "")
    data[FT_LISTBOX_FIELD] = "Hamilton"
    data[FT_SUBMIT_FIELD] = "Find"

    time.sleep(REQUEST_DELAY_SECONDS)
    r2 = session.post(FT_BASE + "/?", data=data, timeout=REQUEST_TIMEOUT)
    r2.raise_for_status()
    if "rgMasterTable" not in r2.text and "RadGrid" not in r2.text:
        raise RuntimeError(
            "foreclosuretennessee: results grid missing from response — session likely expired "
            "(a Sign In page was returned with HTTP 200 instead of search results)"
        )

    ids = sorted(set(int(x) for x in re.findall(r"submissionID=(\d+)", r2.text)))
    stats["submission_ids_found"] = len(ids)

    cache = _load_doc_cache(conn, "foreclosuretennessee")
    enriched = []
    for sid in ids:
        doc = cache.get(str(sid))
        if doc is None:
            time.sleep(REQUEST_DELAY_SECONDS)
            doc = _fetch_ft_detail(session, sid)
            if doc is not None:
                cache[str(sid)] = doc
                stats["detail_fetched"] += 1
                if doc.get("needs_ocr"):
                    stats["needs_ocr"] += 1
                if doc.get("wrong_county"):
                    stats["wrong_county_pdf"] += 1
            else:
                stats["detail_fetch_failed"] += 1
                doc = {}
        else:
            stats["detail_cache_hits"] += 1
        enriched.append((sid, doc))
    _save_doc_cache(conn, "foreclosuretennessee", cache)
    return enriched, stats


def parse_foreclosuretennessee(enriched: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "wrong_county_dropped_pdf_fields": 0}
    for sid, doc in enriched:
        if not doc:
            continue
        wrong_county = doc.get("wrong_county")
        rec = _base_record(
            source_site="foreclosuretennessee",
            source_notice_id=str(sid),
            source_url=f"{FT_BASE}/Foreclosure/Foreclosure-Listing.aspx?submissionID={sid}",
            raw_address=doc.get("raw_address"),
            city=doc.get("city"),
            zip_code=doc.get("zip"),
            sale_date=doc.get("sale_date"),
            filing_date=doc.get("posted") or doc.get("sale_date"),
            book=None if wrong_county else doc.get("book"),
            page=None if wrong_county else doc.get("page"),
            grantor_name=None if wrong_county else doc.get("grantor"),
            trustee_name=doc.get("trustee"),
            native_parcel_tokens=None if wrong_county else doc.get("native_parcel"),
            cancelled=False,
            needs_ocr=doc.get("needs_ocr", False),
            wrong_county_pdf=bool(wrong_county),
        )
        records.append(rec)
        stats["parsed"] += 1
        if wrong_county:
            stats["wrong_county_dropped_pdf_fields"] += 1
    return records, stats


# ============================================================================
# Site 4 — tnlegalpub.com
# ============================================================================

TLP_BASE = "https://tnlegalpub.com"
TLP_COUNTY_ID = 132  # Hamilton — verified live 2026-09-22 via /wp-json/wp/v2/county?search=Hamilton
TLP_WINDOW_DAYS = 270  # generous: "after" filters by POST date, notices stay relevant well past posting


def fetch_tnlegalpub(conn) -> tuple[list, dict]:
    stats = {"api_items": 0}
    after = (datetime.now(timezone.utc) - timedelta(days=TLP_WINDOW_DAYS)).strftime("%Y-%m-%dT00:00:00")
    url = f"{TLP_BASE}/wp-json/wp/v2/legal_notice?county={TLP_COUNTY_ID}&per_page=100&after={after}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    try:
        items = resp.json()
    except ValueError as e:
        # The brief flagged this WP REST endpoint as occasionally returning
        # invalid JSON — this whole site's run fails for today (caught at
        # the main()-level per-site try/except), the other five are unaffected.
        raise RuntimeError(f"tnlegalpub: response body was not valid JSON: {e}")
    if not isinstance(items, list):
        items = []
    stats["api_items"] = len(items)
    return items, stats


def parse_tnlegalpub(items: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "malformed_skipped": 0}
    for item in items:
        try:
            content_html = ((item.get("content") or {}).get("rendered")) or ""
            notice_id = item.get("id")
        except (AttributeError, TypeError):
            stats["malformed_skipped"] += 1
            continue
        if not notice_id or not content_html:
            stats["malformed_skipped"] += 1
            continue
        text = _strip_html(content_html)
        text = (
            text.replace("&#8217;", "'").replace("&#8216;", "'")
            .replace("&#8220;", '"').replace("&#8221;", '"')
            .replace("&amp;", "&").replace("&nbsp;", " ")
        )
        if not text.strip():
            stats["malformed_skipped"] += 1
            continue

        addr, city, zip_ = _extract_address_from_text(text)
        book, page = _extract_book_page(text)
        rec = _base_record(
            source_site="tnlegalpub",
            source_notice_id=str(notice_id),
            source_url=item.get("link"),
            raw_address=addr,
            city=city,
            zip_code=zip_,
            sale_date=_extract_sale_date_from_text(text),
            filing_date=_to_iso_date(item.get("date")),
            book=book,
            page=page,
            grantor_name=_extract_grantor(text),
            trustee_name=_extract_trustee(text),
            native_parcel_tokens=_extract_native_parcel(text),
            cancelled=False,
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats


# ============================================================================
# Sites 5 & 6 — capitalcitypostings.com / tennesseepostings.com
# (shared shape: one static all-states page, filter to Hamilton client-
# side, PDF detail per row — see module docstring)
# ============================================================================


def _extract_ccp_rows(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table")
    rows = []
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        county = tds[1].get_text(strip=True)
        if not county.startswith("Hamilton"):  # observed value is "Hamilton County", not bare "Hamilton"
            continue
        a = tds[0].find("a")
        pdf_url = a.get("href") if a else None
        if not pdf_url:
            continue
        rows.append(
            {
                "pdf_url": pdf_url,
                "sale_date_raw": tds[2].get_text(strip=True),
                "raw_address_full": tds[3].get_text(strip=True),
                "postponed_flag": tds[4].get_text(strip=True),
                "client": tds[5].get_text(strip=True),
            }
        )
    return rows


def _extract_tp_rows(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table", id="table_1") or soup.find("table")
    rows = []
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        county = tds[4].get_text(strip=True)
        if county != "Hamilton":
            continue
        a = tds[0].find("a")
        pdf_url = a.get("href") if a else None
        if not pdf_url:
            continue
        rows.append(
            {
                "pdf_url": pdf_url,
                "ts_number": tds[1].get_text(strip=True),
                "raw_address": tds[2].get_text(strip=True),
                "city": tds[3].get_text(strip=True),
                "zip": tds[6].get_text(strip=True),
                "sale_date_raw": tds[7].get_text(strip=True),
                "status_text": tds[8].get_text(strip=True),
            }
        )
    return rows


def _fetch_table_site(conn, site_key: str, list_url: str, row_extractor) -> tuple[list, dict]:
    stats = {"list_rows_hamilton": 0, "pdf_fetched": 0, "pdf_cache_hits": 0, "pdf_fetch_failed": 0}
    resp = requests.get(list_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    rows = row_extractor(soup)
    stats["list_rows_hamilton"] = len(rows)

    cache = _load_doc_cache(conn, site_key)
    enriched = []
    for row in rows:
        pdf_url = row["pdf_url"]
        doc = cache.get(pdf_url)
        if doc is None:
            time.sleep(REQUEST_DELAY_SECONDS)
            doc = _fetch_pdf_fields(pdf_url)
            if doc is not None:
                cache[pdf_url] = doc
                stats["pdf_fetched"] += 1
            else:
                stats["pdf_fetch_failed"] += 1
                doc = {}
        else:
            stats["pdf_cache_hits"] += 1
        enriched.append((row, doc))
    _save_doc_cache(conn, site_key, cache)
    return enriched, stats


def fetch_capitalcitypostings(conn) -> tuple[list, dict]:
    return _fetch_table_site(conn, "capitalcitypostings", "https://capitalcitypostings.com/tennessee-postings", _extract_ccp_rows)


def parse_capitalcitypostings(enriched: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "postponed": 0}
    for row, doc in enriched:
        addr_only, city, zip_ = _split_address_csz(row["raw_address_full"])
        postponed = bool(row.get("postponed_flag"))
        if postponed:
            stats["postponed"] += 1
        sale_date = _to_iso_date(row["sale_date_raw"])
        rec = _base_record(
            source_site="capitalcitypostings",
            source_notice_id=row["pdf_url"].rsplit("/", 1)[-1].split("?")[0],
            source_url=row["pdf_url"],
            pdf_url=row["pdf_url"],
            raw_address=addr_only or row["raw_address_full"],
            city=city,
            zip_code=zip_,
            sale_date=sale_date,
            filing_date=sale_date,
            book=doc.get("book"),
            page=doc.get("page"),
            grantor_name=doc.get("grantor"),
            trustee_name=doc.get("trustee") or row.get("client"),
            native_parcel_tokens=doc.get("native_parcel"),
            cancelled=False,
            postponed_flag_only=postponed,  # site gives a flag only, no explicit new date
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats


def fetch_tennesseepostings(conn) -> tuple[list, dict]:
    return _fetch_table_site(conn, "tennesseepostings", "https://tennesseepostings.com/", _extract_tp_rows)


def parse_tennesseepostings(enriched: list) -> tuple[list[dict], dict]:
    records = []
    stats = {"parsed": 0, "status_seen": {}}
    for row, doc in enriched:
        status = (row.get("status_text") or "").strip()
        stats["status_seen"][status or "(blank)"] = stats["status_seen"].get(status or "(blank)", 0) + 1
        cancelled = status.lower() in ("cancelled", "canceled", "withdrawn")
        sale_date = _to_iso_date(row.get("sale_date_raw"))
        rec = _base_record(
            source_site="tennesseepostings",
            source_notice_id=row.get("ts_number") or row["pdf_url"],
            source_url=row["pdf_url"],
            pdf_url=row["pdf_url"],
            raw_address=row.get("raw_address"),
            city=row.get("city"),
            zip_code=row.get("zip"),
            sale_date=sale_date,
            filing_date=sale_date,
            book=doc.get("book"),
            page=doc.get("page"),
            grantor_name=doc.get("grantor"),
            trustee_name=doc.get("trustee"),
            native_parcel_tokens=doc.get("native_parcel"),
            cancelled=cancelled,
            case_ref=row.get("ts_number"),
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats


# ============================================================================
# Cross-source merge (see module docstring's "Merge-key design")
# ============================================================================

# Preference order for which site "wins" a tie (and thus supplies
# source_portal/source_url/case_number when a key is only seen once):
# class-A sites first, then foreclosuretennessee (owner-approved,
# internal-use-only), then the two class-C sites last — matching the task
# owner's standing instruction to prefer a cleaner source when available.
SITE_PRIORITY = [
    "betterchoicenotices",
    "nwpostingservices",
    "tnlegalpub",
    "foreclosuretennessee",
    "capitalcitypostings",
    "tennesseepostings",
]

_VALUE_FIELDS = [
    "raw_address", "city", "zip", "sale_date", "postponed_sale_date", "filing_date",
    "book_norm", "page_norm", "grantor_name", "trustee_name", "parcel_id", "pdf_url",
]
_BACKFILL_FIELDS = _VALUE_FIELDS + ["case_ref", "source_url", "needs_ocr", "wrong_county_pdf"]


def _populated_count(rec: dict) -> int:
    return sum(1 for f in _VALUE_FIELDS if rec.get(f))


def _site_rank(site: str) -> int:
    return SITE_PRIORITY.index(site) if site in SITE_PRIORITY else len(SITE_PRIORITY)


def merge_records(all_records: list[dict]) -> tuple[list[dict], dict]:
    groups: dict[str, list[dict]] = {}
    for rec in all_records:
        key = compute_dedupe_key(rec)
        groups.setdefault(key, []).append(rec)

    merged = []
    multi_source_keys = 0  # keys backed by >1 DISTINCT site — genuine cross-source corroboration
    same_site_duplicate_keys = 0  # keys with >1 raw record but all from one site (re-post/revision/postponement)
    for key, recs in groups.items():
        recs_sorted = sorted(recs, key=lambda r: (-_populated_count(r), _site_rank(r["source_site"])))
        primary = dict(recs_sorted[0])
        contributing_sites = [primary["source_site"]]
        for other in recs_sorted[1:]:
            contributing_sites.append(other["source_site"])
            for f in _BACKFILL_FIELDS:
                if not primary.get(f) and other.get(f):
                    primary[f] = other[f]

        distinct_sites = set(contributing_sites)
        primary["cancelled"] = any(r.get("cancelled") for r in recs)
        primary["resolution_method"] = "native_parcel_id" if primary.get("parcel_id") else "unresolved"
        primary["dedupe_key"] = key
        # 'multi' means genuinely cross-site (>1 distinct site posted this same
        # sale). When every raw record in the group shares one site — e.g. the
        # same site re-submitting a notice after a postponement, or a WordPress
        # revision under a second post id — that's this SITE's own dedup, not
        # a cross-source merge, so source_portal stays that single site's name.
        primary["_source_portal"] = primary["source_site"] if len(distinct_sites) == 1 else "multi"
        primary["_other_sites"] = sorted(distinct_sites - {primary["source_site"]})
        if len(distinct_sites) > 1:
            multi_source_keys += 1
        elif len(recs) > 1:
            same_site_duplicate_keys += 1
        merged.append(primary)

    stats = {
        "unique_keys": len(groups),
        "multi_source_keys": multi_source_keys,
        "same_site_duplicate_keys": same_site_duplicate_keys,
        "total_raw_records": len(all_records),
    }
    return merged, stats


def _describe(rec: dict) -> str:
    parts = []
    if rec.get("sale_date"):
        parts.append(f"Sale date {rec['sale_date']}")
    if rec.get("postponed_sale_date") and rec["postponed_sale_date"] != rec.get("sale_date"):
        parts.append(f"postponed to {rec['postponed_sale_date']}")
    elif rec.get("postponed_flag_only"):
        parts.append("postponed (new date not given by source)")
    if rec.get("trustee_name"):
        parts.append(f"trustee {rec['trustee_name']}")
    if rec.get("book_norm") and rec.get("page_norm"):
        parts.append(f"Book GI {rec['book_norm']}/Page {rec['page_norm']}")
    if rec.get("needs_ocr"):
        parts.append("[needs_ocr: source PDF had no usable text layer]")
    if rec.get("wrong_county_pdf"):
        parts.append("[source PDF did not mention Hamilton County — book/page/grantor withheld]")
    if rec.get("cancelled"):
        parts.append("[CANCELLED/WITHDRAWN]")
    desc = "; ".join(parts) if parts else "Foreclosure sale notice"
    other = rec.get("_other_sites") or []
    if other:
        desc += f" — also posted: {', '.join(other)}"
    return desc[:600]


def _case_number(rec: dict) -> str:
    if rec.get("book_norm") and rec.get("page_norm"):
        return f"GI {rec['book_norm']}/{rec['page_norm']}"
    if rec.get("case_ref"):
        return str(rec["case_ref"])
    return f"{rec['source_site']}:{rec.get('source_notice_id')}"


def upsert(conn, merged: list[dict]) -> int:
    for rec in merged:
        if rec.get("parcel_id"):
            upsert_parcel(
                conn, rec["parcel_id"],
                situs_address=rec.get("raw_address"),
                situs_city=rec.get("city"),
                situs_zip=rec.get("zip"),
            )
        upsert_record(
            conn, "court_records", rec["dedupe_key"],
            keep_existing=("parcel_id", "resolution_method"),
            parcel_id=rec.get("parcel_id"),
            source_portal=rec["_source_portal"],
            case_number=_case_number(rec),
            case_type="foreclosure_notice_cancelled" if rec.get("cancelled") else "foreclosure_notice",
            filing_date=rec.get("filing_date") or rec.get("sale_date"),
            party_names=rec.get("grantor_name"),
            amount=None,
            raw_address=rec.get("raw_address"),
            raw_owner_name=rec.get("grantor_name"),
            resolution_method=rec["resolution_method"],
            source_url=rec.get("source_url"),
            description=_describe(rec),
        )
    return len(merged)


# ============================================================================
# main()
# ============================================================================

SITES = [
    ("betterchoicenotices", fetch_betterchoicenotices, parse_betterchoicenotices),
    ("nwpostingservices", fetch_nwpostingservices, parse_nwpostingservices),
    ("foreclosuretennessee", fetch_foreclosuretennessee, parse_foreclosuretennessee),
    ("tnlegalpub", fetch_tnlegalpub, parse_tnlegalpub),
    ("capitalcitypostings", fetch_capitalcitypostings, parse_capitalcitypostings),
    ("tennesseepostings", fetch_tennesseepostings, parse_tennesseepostings),
]


def main() -> None:
    conn = get_connection()
    all_records: list[dict] = []
    site_results: dict[str, dict] = {}
    failed_sites: list[str] = []

    for name, fetch_fn, parse_fn in SITES:
        try:
            raw, fetch_stats = fetch_fn(conn)
            records, parse_stats = parse_fn(raw)
            all_records.extend(records)
            site_results[name] = {"count": len(records), "fetch_stats": fetch_stats, "parse_stats": parse_stats}
            print(f"foreclosure_notices: {name} ok — {len(records)} record(s). fetch={fetch_stats} parse={parse_stats}")
        except Exception as e:
            failed_sites.append(name)
            site_results[name] = {"error": str(e)}
            print(f"foreclosure_notices: {name} FAILED — {e}")
        # Persist any doc-cache writes this site made even if parsing (or a
        # later site) fails — cached PDF lookups shouldn't be re-fetched
        # tomorrow just because a different site's step raised today.
        conn.commit()

    if len(failed_sites) == len(SITES):
        log_scrape(conn, "foreclosure_notices", record_count=0, status="error", notes=f"all sites failed: {site_results}")
        conn.commit()
        raise RuntimeError(f"foreclosure_notices: every source site failed: {failed_sites}")

    merged, merge_stats = merge_records(all_records)
    count = upsert(conn, merged)
    conn.commit()

    cancelled_count = sum(1 for r in merged if r.get("cancelled"))
    per_site_counts = ", ".join(f"{n}={site_results[n].get('count', 'FAILED')}" for n, _, _ in SITES)
    notes = (
        f"{per_site_counts}, merged_unique={merge_stats['unique_keys']}, "
        f"multi_source_keys={merge_stats['multi_source_keys']}, "
        f"same_site_duplicate_keys={merge_stats['same_site_duplicate_keys']}, "
        f"total_raw_records={merge_stats['total_raw_records']}, "
        f"cancelled={cancelled_count}, failed_sites={failed_sites}"
    )
    log_scrape(conn, "foreclosure_notices", record_count=count, status="ok", notes=notes)
    conn.commit()
    print(f"foreclosure_notices: upserted {count} unique notices. {notes}")


if __name__ == "__main__":
    main()
