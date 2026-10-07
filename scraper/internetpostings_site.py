"""
internetpostings.com — the 7th Hamilton County foreclosure posting site for
foreclosure_notices.py. Exposes fetch_internetpostings(conn) and
parse_internetpostings(enriched) with exactly the same interface and return
shapes as the six site functions in foreclosure_notices.py, and reuses that
module's helpers (_base_record, _pdf_text, _extract_book_page,
_extract_grantor, _extract_trustee, _extract_native_parcel, _to_iso_date,
_load_doc_cache/_save_doc_cache, REQUEST_HEADERS/TIMEOUT/DELAY) instead of
duplicating them. Records come out of _base_record(source_site=
"internetpostings", ...), so compute_dedupe_key()/merge_records() collapse a
sale seen here and on another site into one court_records row.

Operator: Attorney's Title Group, LLC (footer: "Attorney's Realty Group"). It
posts the notices of Foundation Legal Group, LLP fka Wilson & Associates,
P.L.L.C. — every Hamilton notice sampled live was an "FLG No." Foundation
Legal notice. Use https://internetpostings.com/ (the www host had no DNS
during research). robots.txt is a 404 (nothing disallowed).

Owner authorization — read before changing the terms handling
---------------------------------------------------------------
On 2026-10-06 Jarrod (the owner) accepted internetpostings.com's Terms of
Service himself, in his own browser. He then authorized, in chat, the daily
scraper to submit that same Terms-of-Service acceptance on his behalf on
every run. He was told beforehand that research had flagged a clause telling
prospective bidders not to contact borrowers. This authorization covers ONLY
submitting the site's own terms-acceptance checkbox and its "View Property
Listings" postback. It does NOT cover creating accounts, logging in, solving
CAPTCHAs, or getting around any other control. fetch_internetpostings()
raises (this site fails for the day and the other six are unaffected) if the
gate ever shows a password field or a CAPTCHA/Turnstile marker.

Pinned to the terms he accepted: the gate's terms text, with whitespace
collapsed, is hashed on every run and compared with AUTHORIZED_TERMS_SHA256
(the text live on 2026-10-06). If the operator changes the terms, the scraper
does NOT accept the new version. It raises a "terms changed" error until the
owner re-reads the new terms and the constant is updated. The owner agreed
to those exact terms, not to whatever replaces them.

What the terms say (captured live 2026-10-06, before acceptance; summarized
here, the full verbatim capture is kept outside the repo with the build
report):
- Section 7 ("Disclaimer Regarding Property Information") says Prospective
  Bidders "shall not trespass on the Properties, disturb the occupants, or
  contact the borrowers" (Attorney's Title Group ToS, sec. 7). The clause
  frames that as getting information about the Properties, and warns of
  possible criminal and/or civil liability for doing so. This is the
  borrower-contact clause the owner was told about. Anyone using these
  leads must read it before contacting a borrower or occupant on a property
  sourced from this site.
- The terms say the information given to prospective bidders, and the
  posted documents and pictures, are for informational purposes only (class
  B, like foreclosuretennessee.com). The terms also disclaim accuracy and
  completeness, say sales may be postponed, rescinded or adjourned at any
  time, and ask readers not to contact the operator about sale times.
- There is NO clause about automated access, robots, scraping, copying,
  republishing or reuse anywhere in the terms (checked against the full
  text). The only no-marketing wording concerns the operator's own use of
  winning bidders' identifying information.

Live-verified 2026-10-06 (real responses, not assumed)
------------------------------------------------------
- Gate: GET / returns an ASP.NET WebForms page (form1, __VIEWSTATE/
  __VIEWSTATEGENERATOR/__EVENTVALIDATION). It shows the terms in a
  scrollable `.terms-container` and the checkbox `ctl00$PageContent$
  cbAcceptTerms`. The checkbox is server-rendered `disabled` until the
  terms box is scrolled to the bottom, which is a client-side read-through
  prompt. The server accepts the checked value either way. Step 1 is a full
  (non-async) postback with __EVENTTARGET=the checkbox and the checkbox set
  to "on". The response adds the submit button `ctl00$PageContent$
  btnAcceptTerms` ("View Property Listings"). Step 2 POSTs that button, and
  the response is the listing page. No cookie or session is ever set. The
  acceptance lives only in that one ViewState postback chain, which is why
  every run has to re-submit it (exactly what the owner authorized).
- ASP.NET sniffs the browser. With this codebase's plain User-Agent it
  serves "down-level" markup (no __doPostBack/__EVENTTARGET, no autopostback
  onclick). A Mozilla-compatible UA gets the JS-enabled markup. Both flows
  were tried live and both returned the identical 451-row grid with
  identical document links, so the module keeps foreclosure_notices'
  REQUEST_HEADERS unchanged rather than adopting a browser-like UA.
- Listings: one GridView, `table#PageContent_gvItems`, with ALL states in a
  single unpaged page (451 rows: 254 AR, 197 TN at verification). Headers:
  (link) / Address / City / County / State / Zip / Original Sale Date / New
  Sale Date. Dates look like "10/8/2026 12:00 PM", and New Sale Date is "--"
  when there's no postponement (172 of 451 rows had one). TN County is the
  bare county name ("Hamilton"; AR uses "Arkansas (Northern - Stuttgart)"
  style). There were 7 Hamilton rows at verification. The page does have
  State (autopostback) / County / date-range filters, but the full
  all-states grid already comes back on the "View Property Listings"
  postback, so filtering server-side would only add 2 more requests. The
  filter is applied client-side (State == TN, County starts with
  "Hamilton").
- Documents: each row's "View" link is `Document.ashx?p=<guid>&d=<guid>`,
  which returns a real text-layer `%PDF-1.7` (about 90 KB) with no cookie or
  session needed. The GUIDs were identical across two separate sessions.
  `p` is used as source_notice_id, and the doc cache is keyed by "p|d" so a
  replaced document (new d) gets re-fetched. All 7 Hamilton PDFs use one
  Foundation Legal template: "recorded ..., in Book No. GI 13283, at Page
  13" (the shared BOOK_PAGE_RE handles it, 7/7), "executed by NAME[,]
  conveying ..." (grantor), "the undersigned, Foundation Legal Group, LLP
  fka Wilson & Associates, P.L.L.C., having been appointed Successor
  Trustee" (trustee), "ALSO KNOWN AS: street, city, TN zip", "FLG No.
  NNNNNN" (the firm's file number, used as case_ref), and "DATED <date>".
  None printed a Map/Group/Parcel ID ("Map Number One" plat references
  aren't parcel IDs and are rejected by the shape check), so parcel
  resolution is left to enrich_assessor, as with the other sites.
- The shared GRANTOR_RES gets this template wrong: the "by NAME ... to X"
  heuristic swallows ", conveying certain property therein described". The
  shared TRUSTEE_RES returns the ORIGINAL deed-of-trust trustee, not the
  successor trustee holding the sale. So two site-specific regexes run
  first, and the shared extractors are only a fallback.

Record mapping and conventions
------------------------------
- sale_date = Original Sale Date, postponed_sale_date = New Sale Date (only
  when it differs from the original), the same as nwpostingservices
  (original date anchors identity, new date is the expected one).
- filing_date = Original Sale Date. The listing has no posted/published
  date. The PDF's "DATED" line is the date the notice was signed, not a
  posting date, so it's cached (`notice_dated`) but not used. This follows
  the module convention for sale-dated sites, which build_unified now
  labels "sale", not "filed".
- cancelled is always False. The site has no cancelled/withdrawn marker; a
  withdrawn or completed sale just drops out of the grid. Rows already
  upserted are kept, never deleted (module convention), so last_seen_at
  stops advancing.
- source_url is the site's gate page, not the Document.ashx deep link. The
  dashboard is public, and the operator requires viewers to accept its terms
  before seeing a notice, so the public dashboard links to the gate rather
  than straight past it. The direct document URL is still carried in the
  record's pdf_url for the run (the merge counts it). Changing this is an
  owner decision, not a technical one.
- A PDF without "HAMILTON COUNTY" keeps only its listing fields
  (book/page/grantor/parcel withheld, wrong_county_pdf=True). A PDF with
  under MIN_TEXT_LEN characters of text is flagged needs_ocr. Both mirror
  foreclosuretennessee's guards.

Politeness and failure semantics
--------------------------------
One requests.Session per run, foreclosure_notices.REQUEST_HEADERS (a
descriptive UA), REQUEST_TIMEOUT, at least REQUEST_DELAY_SECONDS between
requests to this host, and up to MAX_ATTEMPTS tries with exponential backoff
on connection errors/429/5xx. A typical day is 3 gate/listing requests plus
one PDF GET per never-seen Hamilton notice (the doc cache in source_state,
`foreclosure_notices:internetpostings:doc_cache`, means unchanged documents
are never re-downloaded), and at most MAX_DOC_FETCHES_PER_RUN new PDFs per
run. Rows over that cap still produce listing-only records and get their PDF
on a later run. All of these raise RuntimeError, so main() records a site
failure, never a silent "zero results":
- a terms page or a missing/empty grid returned after acceptance (expired
  or unaccepted ViewState)
- an all-states grid with zero rows
- changed terms
- a login or CAPTCHA wall
- changed gate structure
Nothing in this module prints. foreclosure_notices.main() prints only the
count-only stats dicts these functions return, never names or addresses.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

SITE_KEY = "internetpostings"
IP_BASE = "https://internetpostings.com/"
IP_DOC_ENDPOINT = IP_BASE + "Document.ashx?"
IP_SOURCE_URL = IP_BASE  # see docstring: public dashboard links the terms gate, not the deep PDF link

# SHA-256 of the gate's `.terms-container` text (whitespace collapsed to
# single spaces, stripped) as accepted by the owner on 2026-10-06. Changing
# this constant = re-authorizing acceptance of new terms; only do it after
# the owner has read the new text.
AUTHORIZED_TERMS_SHA256 = "c0285ad82dede7d1e5ae20f6fa919afbb0dee4d2d150de8d1b6c07ab3e1e4037"
AUTHORIZED_TERMS_DATE = "2026-10-06"

CHECKBOX_FIELD = "ctl00$PageContent$cbAcceptTerms"
CONTINUE_FIELD = "ctl00$PageContent$btnAcceptTerms"
GRID_ID = "PageContent_gvItems"

MAX_DOC_FETCHES_PER_RUN = 30
# Stop fetching notice PDFs after this many failures in a row — a stalled
# Document.ashx would otherwise cost ~3 tries x 30 s timeout per notice.
MAX_CONSECUTIVE_DOC_FAILURES = 3
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 3.0
MIN_TEXT_LEN = 200

_REQUIRED_HEADERS = ("Address", "City", "County", "State", "Zip", "Original Sale Date", "New Sale Date")

# Anything here on a gate/listing response means the site now wants more
# than the owner authorized (a login or a bot challenge) -> stop, don't adapt.
_BLOCKER_RES = [
    re.compile(r"<input[^>]+type=[\"']?password", re.IGNORECASE),
    re.compile(r"g-recaptcha|recaptcha/api|hcaptcha|cf-turnstile|challenges\.cloudflare\.com|captcha", re.IGNORECASE),
]

_DOC_QUERY_RE = re.compile(r"Document\.ashx\?([^'\"\s<>]+)", re.IGNORECASE)
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_P_RE = re.compile(rf"(?:^|&)p=({_GUID})")
_D_RE = re.compile(rf"(?:^|&)d=({_GUID})")
_LISTING_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

# Foundation Legal template (all 7 live Hamilton PDFs) — tried before the
# shared foreclosure_notices extractors, which mis-handle this template.
_IP_GRANTOR_RE = re.compile(r"executed by\s+(.+?),?\s+conveying\b", re.IGNORECASE)
_IP_TRUSTEE_RE = re.compile(
    r"the undersigned,\s+(.+?),\s+having been appointed\s+(?:Successor|Substitute)\s+Trustee", re.IGNORECASE
)
_IP_SALE_DATE_RE = re.compile(r"will,?\s+on\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE)
_IP_AKA_RE = re.compile(r"ALSO KNOWN AS:?\s*(.+?),\s*([A-Za-z .'\-]+?),\s*(?:TN|Tennessee)\s*(\d{5})", re.IGNORECASE)
_IP_FLG_RE = re.compile(r"FLG No\.?\s*(\d{4,})", re.IGNORECASE)
_IP_DATED_RE = re.compile(r"\bDATED:?\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})")


def _fn():
    """The foreclosure_notices module, without a circular import.

    foreclosure_notices imports this module at load time (for SITES), so this
    module only reaches back for its helpers lazily, at call time. When
    foreclosure_notices.py is the running script (CI runs `python
    foreclosure_notices.py`), it lives in sys.modules as "__main__". It's
    reused from there instead of being imported (and executed) a second
    time.
    """
    mod = sys.modules.get("foreclosure_notices")
    if mod is not None:
        return mod
    main = sys.modules.get("__main__")
    if main is not None and os.path.basename(getattr(main, "__file__", "") or "") == "foreclosure_notices.py":
        return main
    import foreclosure_notices  # noqa: PLC0415 — deliberate lazy import, see docstring

    return foreclosure_notices


class _PoliteClient:
    """One session per run, at least `delay` seconds between requests to the
    host, and bounded retries with exponential backoff on transient
    failures."""

    def __init__(self, fn):
        self.session = requests.Session()
        self.session.headers.update(fn.REQUEST_HEADERS)
        self.timeout = fn.REQUEST_TIMEOUT
        self.delay = fn.REQUEST_DELAY_SECONDS
        self._last = 0.0
        self.requests_made = 0

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                resp = None
            finally:
                self._last = time.monotonic()
                self.requests_made += 1
            if resp is not None and resp.status_code != 429 and resp.status_code < 500:
                return resp
            if resp is not None:
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise RuntimeError(f"internetpostings: {method} {url.split('?')[0]} failed after {MAX_ATTEMPTS} attempts: {last_exc}")


def _assert_no_blocker(html: str, where: str) -> None:
    for rx in _BLOCKER_RES:
        if rx.search(html or ""):
            raise RuntimeError(
                f"internetpostings: {where} now shows a login or CAPTCHA control — outside the owner's "
                "authorization (terms checkbox only); stopping this site, not adapting"
            )


def _terms_sha256(terms_div) -> str:
    text = re.sub(r"\s+", " ", terms_div.get_text(" ")).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _form_fields(soup: BeautifulSoup) -> dict:
    """Every successful-control value a browser would post, minus submit
    buttons (the caller adds the one it is "clicking")."""
    form = soup.find("form", id="form1") or soup.find("form")
    if form is None:
        raise RuntimeError("internetpostings: no <form> on gate page — page structure changed")
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "image", "button", "reset", "file"):
            continue
        if itype in ("checkbox", "radio"):
            if inp.get("checked") is not None:
                data[name] = inp.get("value", "on")
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opts = sel.find_all("option")
        chosen = [o.get("value", "") for o in opts if o.get("selected") is not None]
        data[name] = chosen[0] if chosen else (opts[0].get("value", "") if opts else "")
    return data


def _accept_terms_and_get_listing(client: _PoliteClient, stats: dict) -> str:
    """Owner-authorized gate flow (see docstring). Returns the listing HTML or
    raises — never returns a terms page."""
    r = client.request("GET", IP_BASE)
    r.raise_for_status()
    _assert_no_blocker(r.text, "landing page")
    soup = BeautifulSoup(r.text, "html.parser")
    terms_div = soup.find("div", class_="terms-container")
    if terms_div is None:
        if soup.find("table", id=GRID_ID) is not None:
            stats["terms_gate"] = "absent"  # gate removed by the operator — nothing to accept
            return r.text
        raise RuntimeError("internetpostings: neither the terms gate nor the listing grid found — page structure changed")

    observed = _terms_sha256(terms_div)
    if observed != AUTHORIZED_TERMS_SHA256:
        stats["terms_gate"] = "changed"
        raise RuntimeError(
            f"internetpostings: Terms of Service text changed since the owner's {AUTHORIZED_TERMS_DATE} acceptance "
            f"(sha256 {observed[:12]}... != authorized {AUTHORIZED_TERMS_SHA256[:12]}...) — NOT accepting the new "
            "terms; the owner must re-read them and AUTHORIZED_TERMS_SHA256 must be updated first"
        )
    if soup.find("input", {"name": CHECKBOX_FIELD}) is None:
        raise RuntimeError("internetpostings: terms checkbox missing from gate page — gate changed")
    stats["terms_gate"] = "pinned_terms_matched"

    # Step 1: tick the terms checkbox (its autopostback, sent as a full postback).
    data = _form_fields(soup)
    data["__EVENTTARGET"] = CHECKBOX_FIELD
    data["__EVENTARGUMENT"] = ""
    data[CHECKBOX_FIELD] = "on"
    r2 = client.request("POST", IP_BASE, data=data, headers={"Referer": IP_BASE})
    r2.raise_for_status()
    _assert_no_blocker(r2.text, "terms checkbox postback")
    soup2 = BeautifulSoup(r2.text, "html.parser")
    button = soup2.find("input", {"name": CONTINUE_FIELD})
    if button is None:
        raise RuntimeError(
            "internetpostings: terms checkbox postback not accepted (no 'View Property Listings' button returned) — gate changed"
        )

    # Step 2: "View Property Listings".
    data2 = _form_fields(soup2)
    data2["__EVENTTARGET"] = ""
    data2["__EVENTARGUMENT"] = ""
    data2[CHECKBOX_FIELD] = "on"
    data2[CONTINUE_FIELD] = button.get("value") or "View Property Listings"
    r3 = client.request("POST", IP_BASE, data=data2, headers={"Referer": IP_BASE})
    r3.raise_for_status()
    _assert_no_blocker(r3.text, "listing page")
    stats["terms_gate"] = "accepted_pinned_terms"  # _parse_grid() still verifies a real grid came back
    return r3.text


def _listing_date(raw: str) -> str | None:
    """"10/8/2026 12:00 PM" -> "2026-10-08"; "--" (no new sale date) -> None."""
    m = _LISTING_DATE_RE.search(raw or "")
    return _fn()._to_iso_date(m.group(1)) if m else None


def _text_date(raw: str | None) -> str | None:
    """"October 8,  2026" / "October 8 2026" -> "2026-10-08" via the shared parser."""
    if not raw:
        return None
    return _fn()._to_iso_date(re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", raw.strip())))


def _parse_grid(listing_html: str) -> list[dict]:
    soup = BeautifulSoup(listing_html, "html.parser")
    grid = soup.find("table", id=GRID_ID)
    if grid is None or soup.find("div", class_="terms-container") is not None:
        raise RuntimeError(
            "internetpostings: listing grid missing after terms acceptance — the terms gate was served again "
            "(ViewState/session not accepted or expired); treated as a fetch failure, not zero results"
        )
    trs = grid.find_all("tr")
    if not trs:
        raise RuntimeError("internetpostings: listing grid is empty (no header row) — treated as a fetch failure")
    headers = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    idx = {h: i for i, h in enumerate(headers)}
    missing = [h for h in _REQUIRED_HEADERS if h not in idx]
    if missing:
        raise RuntimeError(f"internetpostings: listing grid headers changed (missing {missing})")

    rows = []
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if len(tds) < len(headers):
            continue  # pager/empty-data row
        texts = [td.get_text(" ", strip=True) for td in tds]

        def cell(h: str, texts=texts) -> str:
            return texts[idx[h]]

        link_html = str(tds[0]).replace("&amp;", "&")
        m = _DOC_QUERY_RE.search(link_html)
        query = m.group(1) if m else None
        p = _P_RE.search(query).group(1).lower() if query and _P_RE.search(query) else None
        d = _D_RE.search(query).group(1).lower() if query and _D_RE.search(query) else None
        rows.append(
            {
                "address": cell("Address"),
                "city": cell("City"),
                "county": cell("County"),
                "state": cell("State"),
                "zip": cell("Zip"),
                "original_sale_date": _listing_date(cell("Original Sale Date")),
                "new_sale_date": _listing_date(cell("New Sale Date")),
                "p": p,
                "d": d,
                "doc_query": f"p={p}&d={d}" if p and d else query,
            }
        )
    if not rows:
        raise RuntimeError(
            "internetpostings: listing grid has zero rows across ALL states (normally hundreds of AR/TN rows) — "
            "treated as a fetch failure, not zero results"
        )
    return rows


def _is_hamilton(row: dict) -> bool:
    return (row.get("state") or "").strip().upper() == "TN" and (row.get("county") or "").strip().lower().startswith("hamilton")


def _clean_name(raw: str | None, max_len: int) -> str | None:
    if not raw:
        return None
    name = re.sub(r"\s+", " ", raw).strip(" ,.")
    if not (3 < len(name) < max_len):
        return None
    if "WHEREAS" in name.upper() or "deed of trust" in name.lower():
        return None
    return name


def _fetch_doc(client: _PoliteClient, doc_query: str) -> dict | None:
    """One notice PDF -> the cacheable extracted fields. None on fetch
    failure (not cached, retried on the next run)."""
    fn = _fn()
    try:
        resp = client.request("GET", IP_DOC_ENDPOINT + doc_query)
    except RuntimeError:
        return None
    if resp.status_code != 200 or resp.content[:4] != b"%PDF":
        return None
    text = fn._pdf_text(resp.content)
    flat = re.sub(r"\s+", " ", text)
    doc: dict = {"text_len": len(text), "needs_ocr": False, "wrong_county": False}
    if len(text.strip()) < MIN_TEXT_LEN:
        doc["needs_ocr"] = True  # scanned/no text layer — listing fields still usable
        return doc
    if "HAMILTON COUNTY" not in text.upper():
        doc["wrong_county"] = True  # don't trust this PDF's identity fields for a Hamilton row
        return doc

    book, page = fn._extract_book_page(text)
    m = _IP_GRANTOR_RE.search(flat[:2500])
    grantor = _clean_name(m.group(1), 150) if m else None
    m = _IP_TRUSTEE_RE.search(flat[:4000])
    trustee = _clean_name(m.group(1), 120) if m else None
    m = _IP_SALE_DATE_RE.search(flat)
    doc_sale = _text_date(m.group(1)) if m else None
    m = _IP_AKA_RE.search(flat)
    m_flg = _IP_FLG_RE.search(flat)
    m_dated = _IP_DATED_RE.search(flat)
    doc.update(
        {
            "book": book,
            "page": page,
            "grantor": grantor or fn._extract_grantor(text),
            "trustee": trustee or fn._extract_trustee(text),
            "native_parcel": fn._extract_native_parcel(text),
            "flg_no": m_flg.group(1) if m_flg else None,
            "notice_dated": _text_date(m_dated.group(1)) if m_dated else None,
            "doc_sale_date": doc_sale or fn._extract_sale_date_from_text(text),
            "doc_address": m.group(1).strip() if m else None,
            "doc_city": m.group(2).strip() if m else None,
            "doc_zip": m.group(3).strip() if m else None,
        }
    )
    return doc


def fetch_internetpostings(conn) -> tuple[list, dict]:
    fn = _fn()
    stats = {
        "terms_gate": None,
        "list_rows_all_states": 0,
        "list_rows_hamilton": 0,
        "no_doc_link": 0,
        "pdf_fetched": 0,
        "pdf_cache_hits": 0,
        "pdf_fetch_failed": 0,
        "pdf_deferred_by_cap": 0,
        "needs_ocr": 0,
        "wrong_county_pdf": 0,
        "requests": 0,
    }
    client = _PoliteClient(fn)
    try:
        listing_html = _accept_terms_and_get_listing(client, stats)
        rows = _parse_grid(listing_html)
        stats["list_rows_all_states"] = len(rows)
        hamilton = [r for r in rows if _is_hamilton(r)]
        stats["list_rows_hamilton"] = len(hamilton)

        cache = fn._load_doc_cache(conn, SITE_KEY)
        enriched = []
        fetched_this_run = 0
        consecutive_failures = 0
        try:
            for row in hamilton:
                if not row.get("doc_query"):
                    stats["no_doc_link"] += 1
                    enriched.append((row, {}))
                    continue
                cache_key = f"{row['p']}|{row['d']}" if row.get("p") and row.get("d") else row["doc_query"]
                doc = cache.get(cache_key)
                if doc is not None:
                    stats["pdf_cache_hits"] += 1
                elif fetched_this_run >= MAX_DOC_FETCHES_PER_RUN or consecutive_failures >= MAX_CONSECUTIVE_DOC_FAILURES:
                    stats["pdf_deferred_by_cap"] += 1
                    doc = {}
                else:
                    fetched_this_run += 1
                    doc = _fetch_doc(client, row["doc_query"])
                    if doc is None:
                        stats["pdf_fetch_failed"] += 1
                        consecutive_failures += 1
                        doc = {}
                    else:
                        consecutive_failures = 0
                        cache[cache_key] = doc
                        stats["pdf_fetched"] += 1
                        stats["needs_ocr"] += int(bool(doc.get("needs_ocr")))
                        stats["wrong_county_pdf"] += int(bool(doc.get("wrong_county")))
                enriched.append((row, doc))
        finally:
            # Persist whatever was extracted even if a later notice raised.
            fn._save_doc_cache(conn, SITE_KEY, cache)
        return enriched, stats
    finally:
        stats["requests"] = client.requests_made
        client.session.close()


def parse_internetpostings(enriched: list) -> tuple[list[dict], dict]:
    fn = _fn()
    records = []
    stats = {"parsed": 0, "postponed": 0, "listing_only": 0, "wrong_county_dropped_pdf_fields": 0}
    for row, doc in enriched:
        doc = doc or {}
        wrong_county = bool(doc.get("wrong_county"))
        orig = row.get("original_sale_date") or doc.get("doc_sale_date")
        new = row.get("new_sale_date")
        postponed = new if new and new != orig else None
        if postponed:
            stats["postponed"] += 1
        if not doc.get("book"):
            stats["listing_only"] += 1
        if wrong_county:
            stats["wrong_county_dropped_pdf_fields"] += 1
        doc_url = IP_DOC_ENDPOINT + row["doc_query"] if row.get("doc_query") else None
        flg = None if wrong_county else doc.get("flg_no")
        rec = fn._base_record(
            source_site=SITE_KEY,
            source_notice_id=row.get("p") or row.get("doc_query") or f"{row.get('address')}|{orig}",
            source_url=IP_SOURCE_URL,
            pdf_url=doc_url,
            raw_address=row.get("address") or (None if wrong_county else doc.get("doc_address")),
            city=row.get("city") or (None if wrong_county else doc.get("doc_city")),
            zip_code=row.get("zip") or (None if wrong_county else doc.get("doc_zip")),
            sale_date=orig,
            postponed_sale_date=postponed,
            filing_date=orig,  # no posted date on this site — module convention (sale-dated), see docstring
            book=None if wrong_county else doc.get("book"),
            page=None if wrong_county else doc.get("page"),
            grantor_name=None if wrong_county else doc.get("grantor"),
            trustee_name=doc.get("trustee"),
            native_parcel_tokens=None if wrong_county else doc.get("native_parcel"),
            cancelled=False,  # site has no cancellation marker; withdrawn sales just leave the grid
            case_ref=f"FLG No. {flg}" if flg else None,
            needs_ocr=bool(doc.get("needs_ocr")),
            wrong_county_pdf=wrong_county,
        )
        records.append(rec)
        stats["parsed"] += 1
    return records, stats
