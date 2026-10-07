"""
City of Chattanooga municipal liens — per-parcel ENRICHMENT of parcels that
another source has already flagged, read from the City's public property-tax
portal (OpenGov Tax & Revenue). Stored in court_records as
case_type='municipal_lien' (one row per parcel).

Why this source: Jarrod asked for liens "without having to pay". The Register
of Deeds is paid ($50/month) and its subscriber agreement bans scraping, so
this portal is the one free path recon found. Owner-approved design:
ENRICHMENT ONLY — look up parcels already flagged by code_enforcement /
tax_delinquent / court_records, never enumerate or crawl the portal.

Everything below was observed live on 2026-10-06 (requests from the dev
machine), not assumed from the recon notes.

Terms / robots gate (checked before anything was built)
---------------------------------------------------------------------------
  - https://chattanoogatn.taxandrevenue.opengov.com/robots.txt -> HTTP 404
    (no robots file, so nothing is disallowed). main() re-checks this every
    run and aborts if a robots.txt ever appears that disallows the two paths
    used here, or if it answers 401/403.
  - Landing page footer: "Terms And Privacy" ->
    https://opengov.com/products/tax-and-revenue/ — a product MARKETING page
    ("Tax & Revenue Collection Software for Local Government | OpenGov"), no
    terms-of-use text on it.
  - Inner pages (PIDNHomePage, ViewCurrentCharges): "Terms and Privacy" ->
    http://govcollect.com/terms-of-service-and-privacy-policy-terms/ which
    now 301s to that same OpenGov product page. The old GovCollect terms
    text is no longer reachable from the portal. The archived copy
    (Wayback 20240302105804, "Date of Last Revision: June 2, 2020"; read
    and verified 2026-10-06) has:
      * under "Account Terms", next to the account-registration
        requirements (legal organization name, email, etc.): "You must be a
        human. Accounts registered by 'bots' or other automated methods are
        not permitted." That governs ACCOUNTS; this module uses no account,
        login or registration.
      * under "General Conditions", a general clause not to "reproduce,
        duplicate, copy, sell, resell or exploit any portion of the
        Service" without permission. No robots / scraping / automated-
        access clause anywhere in it.
    Since that page is no longer linked (dead link -> marketing page), it
    doesn't change the decision below; the no-exploit wording is left for
    the owner's judgment.
  - Footer "FAQ" (/Utilities/LaunchFAQ, a 2-page PDF): checked 2026-10-06,
    no use terms (0 hits for terms/automated/robot/scrape/prohibit).
  - The product page's footer links, read in full:
      * https://opengov.com/privacy-policy/ ("Last Updated: September 8,
        2026"). Covers "digital properties that link to this Privacy
        Policy" and says constituent data in government-facing apps "is
        collected on behalf of the applicable government agency". Its only
        bot/scraper wording sits in the Cookie Notice's vendor table,
        describing Cloudflare on opengov.com: "Identifies and blocks
        malicious automated traffic, including scrapers, credential stuffing
        attempts, and spam submissions." That describes a cookie on OpenGov's
        own websites. It is not a rule addressed to portal visitors.
      * https://opengov.com/terms-of-service/ lists only CUSTOMER contracts
        (Master Services Agreement, two EULAs): "These are the current
        agreements that govern the use of OpenGov products and services."
        MSA 3.1 restricts the Customer (the City), not the public: "Customer
        shall not, and shall not knowingly or negligently, permit or enable
        any third party to: ... (c) sell, license, rent, lease, assign,
        distribute, display, host, disclose, outsource, copy or otherwise
        commercially exploit the Software Services; (d) perform or disclose
        any benchmarking or performance testing of the Software Services,
        including but not limited to load testing or stress testing". It
        has no automated-access, scraping or bot clause (searched).
      * /resources/ai-policies/ and the DPA: no automated-access language.
  - Decision rule outcome: no robots disallow, and no currently linked,
    reachable terms prohibit automated access. Built at low volume
    (>= 1.6 s between requests, per-run caps), with the archived
    (unlinked) no-exploit wording flagged for the owner's final call.

Portal mechanics (verified live)
---------------------------------------------------------------------------
  - No login, no CAPTCHA. GET / returns the landing page with (a) a hidden
    __RequestVerificationToken input and (b) an inline-script token inside
    SearchForRecords() ("exp" + GUID). Both must come from the SAME
    requests.Session (the antiforgery cookie is set on that GET).
  - MAP search: GET /Employee/GetSearchResults?searchData=<MAP>&
    searchType=MAP&application=Property&token=<script token>, header
    __RequestVerificationToken=<hidden input>. It returns a JSON list with one
    row per tax year (2005/2008 through 2026), ~10 KB per row, with PIDN, MAP,
    TAX_YEAR, TOTAL (that year's balance), STATUS, OWNER_NAME,
    LOCNUMB/GPSNS/LOCSTREET/LOCSTREET2, M_ADD/M_ADD2/M_CITY/M_STATE/M_ZIP
    and ClerkAndMasterDelinquent. Those name/location/mailing fields match
    the PIDNHomePage "Name On File" / "Physical Location" / "Mailing
    Address" exactly (checked), so the PIDN page is never fetched. The
    payload also carries many unused columns, including SSN/DL/phone/email
    columns (all empty in every sample). Only the whitelisted fields above
    are read, and the raw JSON is never stored.
  - MAP is the county TAX_MAP_NO with all whitespace removed, and the
    search is a CONTAINS match: "090062" returned 5 parcels (090062,
    090062.01 ... .04; 95 rows, ~1 MB). Rows are therefore filtered to an
    exact MAP. Verified formats: "147HK029" (3-part), "090062.01" (blank
    group: GIS "090 062.01"), "108DB001C009" (unit suffix: GIS
    "108D B 001 C009", parcel_id "108D-B-001C009"). parcels.tax_map_no is
    whitespace-collapsed by enrich_assessor, which doesn't matter here
    because all spaces are stripped. When tax_map_no is NULL the key is
    parcel_id minus its dashes, which gives the same string for all three
    formats. If the two derivations differ (enrich_assessor's unit-suffix
    LIKE fallback can store a slightly different PARCEL), the
    parcel_id-derived key is tried second.
  - PIDN is a per-tax-year sequence, NOT a property id (one parcel had
    PIDNs 2023-47950, 2024-87880, 2025-88421, 2026-89796), so the search
    runs every time.
  - GET /User/ViewCurrentCharges?pidn=<that year's PIDN> is PER TAX YEAR:
    a server-rendered table [Tax Year, Date Due, Description, Normal,
    Discount, Penalty, Interest, Gross, Payment], or "You currently have no
    charges due." GROSS IS ALREADY NET OF PAYMENTS: Gross = Normal +/-
    Discount + Penalty + Interest - Payment on all 542 live lines captured
    (e.g. "ATTORNEY FEES" normal 121.50, payment 40.00, gross 81.50), so
    Gross is the line's outstanding balance and Payment is informational.
    The search's per-year TOTAL equals the sum of that year's Gross column
    (exact on 226/226 captured pages; e.g. 135.10 tax + 180.00 lien = TOTAL
    315.10). check_parcel enforces this per year and fails the parcel if it
    ever stops holding. Only years with TOTAL > 0 are fetched, newest
    first, at most LIENS_MAX_YEARS_PER_PARCEL (plus prior lien years, see
    Lifecycle).
    Observed descriptions:
      "MUNCIPAL LIEN" (the city's misspelling), "MUNICIPAL LIEN BOOK 14299
      PAGE 786" (correct spelling plus the Register of Deeds book/page),
      "REAL TAX 2025", "STORMWATER FEE 2025", and on legacy (<=2015)
      years "Real Property Tax", "Property Tax Lien", "Delq Water Quality
      Fee", "Delq Court Cost", "Delq Attorney Fee".
    No lien DATE is shown anywhere (ChargeHistory was empty, ViewBills2's
    table is AJAX-filled and empty server-side). "Date Due" is the bill's
    due date, not a lien date, so filing_date stays NULL.
  - City process context: after notice, the city abates vacant, overgrown
    or open property and liens the cost. A municipal lien therefore marks a
    probably-vacant, city-liened parcel.

What counts as a municipal lien
---------------------------------------------------------------------------
  Any charge line matching /\\bMUNI?CIPAL\\s+LIEN\\b/i (both spellings) with
  an outstanding balance (Gross > 0; Gross is already net of payments, so
  Payment is never subtracted), on any inspected year. The
  legacy "Property Tax Lien" label (seen on 2012/2013 bills dated 03/01,
  next to a separate "Real Property Tax" line) is NOT counted. It is
  probably a legacy-system abatement or demolition lien, but nothing on the
  portal says so. Such lines are named in the description when a municipal
  lien row exists, and parcels that have ONLY that label are counted in
  the run notes (other_lien_only=N) so the owner can decide with data.

Stored row (court_records, only when a municipal lien exists)
---------------------------------------------------------------------------
  dedupe_key f"municipal_lien:{parcel_id}", case_type 'municipal_lien',
  source_portal 'chattanooga_tax_portal', amount = total outstanding
  municipal-lien balance across inspected years, case_number = Register
  book/page refs when the line carries them (e.g. "BK 14299 PG 786"),
  filing_date NULL (never invented), raw_address/raw_owner_name =
  portal's location / name on file, resolution_method 'native_parcel_id',
  source_url = the latest tax year's PIDNHomePage (human-facing; PIDN is
  per-year), description = lien lines + amounts, past-due city tax and
  stormwater years/amounts, Clerk & Master back-tax years, other
  lien-labelled lines, "mail: <mailing on file>". The parcels table is not
  touched (GIS stays the ground truth for owner/mailing there).

Lifecycle
---------------------------------------------------------------------------
  A parcel re-checked with no municipal-lien line flips its row to
  case_type='municipal_lien_resolved' (0-tier, audit only), with amount
  NULL and the previous lien summary kept in the description. If the
  lien reappears later, the row flips back to 'municipal_lien'. If a
  parcel that has a lien row disappears from the portal search entirely,
  the row is left alone, because a paid lien can't be confirmed from a
  missing record.
  Truncation safety: the description's "(bill yrs ...)" token records every
  tax year that carried a lien line. On refresh those years are inspected
  even when newer unpaid bills have pushed them past the newest
  LIENS_MAX_YEARS_PER_PARCEL balance years (prior_lien_years_extra=N). A
  row is only resolved when no balance year was skipped, or when every
  prior lien year is known and none of them was skipped. Otherwise it is
  left as municipal_lien (resolve_unconfirmed=N in the notes).

Candidate policy (enrichment only, never enumeration)
---------------------------------------------------------------------------
  Tiers: 0 = parcels with an active municipal_lien row (refresh, to catch
  resolution); 1 = code_enforcement rows in abatement-prone codes (21-136
  overgrowth, 21-132..21-135 litter/junk/appliances/furniture, 21-87x
  vacant/boarding, 21-76(x) unsafe/unfit, 21-80/82/83 condemned, 21-84
  repair-or-demolish, or violation_type condemnation/vacant_building);
  2 = any other code_enforcement parcel (all inside the City); 3 =
  tax_delinquent parcels (Trustee is county-wide, so many are outside city
  limits); 4 = other court_records leads.
  Order each run: tier-0 parcels due for refresh first; then "fresh"
  parcels (never checked, or with flagging evidence newer than the last
  check, i.e. a new violation_date or a new first_seen_at) by tier. Inside
  tiers 1-2, parcels that are ALSO in tax_delinquent go first (the portal
  only shows UNPAID liens, and in the measured samples every hit had
  unpaid county tax too; see "Measured"). Then newest evidence first. Last
  come re-checks due after REFRESH_DAYS (30), oldest check first.
  Never-checked parcels go ahead of re-checks because ~16.5k parcels are
  eligible, against ~50-100 checks a day. Pure "newest first" every run
  would keep re-checking the same newest ~2,000 parcels every 30 days and
  never reach older ones.
  A MAP search with no exact row means the parcel is not on the city roll
  (outside city limits). That is cached for NOT_IN_CITY_DAYS (90) in
  source_state. Guard: tier 1-2 parcels are City code-enforcement parcels
  (0 of 47 missing in a live run), so if the first 5 checked in a run are
  all not found, or more than half once 10 are checked, the search is
  treated as broken (e.g. it started returning [] for everything): that
  run's not-found bookkeeping is undone and the run aborts non-zero. A
  per-parcel request/parse failure is retried after ERROR_RETRY_DAYS (3).
  Bookkeeping: source_state keys municipal_liens:checked,
  municipal_liens:not_in_city, municipal_liens:errors, stored compactly as
  {"YYYY-MM-DD": [parcel_id, ...]}. Entries past their usefulness are
  pruned, and each map is capped (STATE_CAP / ERROR_STATE_CAP) so it can't
  grow without limit.

Caps (env, defaults shown) — see "Measured" below for how they were picked
  LIENS_MAX_PARCELS (100)          parcels checked per run
  LIENS_MAX_SECONDS (540)          wall-clock budget; stops before starting
                                   a parcel that wouldn't fit, and starts no
                                   request at all once 60 s past it
                                   (HARD_OVERRUN_SECONDS — the parcel in
                                   progress is dropped untouched and stays
                                   due), so a slow portal can't outrun the
                                   workflow step's 15-minute timeout
  LIENS_MAX_YEARS_PER_PARCEL (8)   charge pages per parcel (newest balance
                                   years first; the rest are summarized
                                   from the search's TOTALs), plus any
                                   older year that carried this parcel's
                                   lien last time (see Lifecycle)
  Plus: >= 1.6 s between requests (end of one to start of the next), 30 s
  timeouts, 3 tries with backoff (Retry-After honoured up to 60 s), and an
  abort after 5 consecutive failed parcels.

Double counting with the Trustee file (tax_delinquent.py) — VERIFIED
---------------------------------------------------------------------------
  tax_delinquent.amount_due = Current County + Current Mun + Current Stw
  owed. For City of Chattanooga parcels the Trustee file does NOT carry city
  tax or stormwater. In the Trustee CSV, every one of the 7,859 kept
  District '1' rows has Current Mun Owed = 0, Municipal Amount = 0 and
  Current Stw Owed = 0. Muni amounts appear only for the small-town
  districts (2C, 2E, 2R, 3L, 3LS, 3R, 3SD, 3W). Current Stw Owed is 0 on
  every kept row in every district. Spot checks of parcels present in both
  sources, all District 1:
    - 156K-C-004: portal 2025 REAL TAX $455.96 + STORMWATER $183.54
      outstanding. Trustee 2025 row: county $358.08, mun $0, stw $0.
    - 155N-Q-097 and 155N-Q-098: portal city tax 2021-2026 + liens. Trustee
      2021-2025 rows: county only, mun 0, stw 0.
    - 136D-G-026: portal 2024 REAL TAX + stormwater 2024-2026. Trustee
      2020 and 2022-2024 rows: county only.
    - 136L-G-031 and 147H-K-029: portal legacy (<=2015) city balances.
      Trustee rows (2011-2014 and 2008-2011): county only.
    (Trustee side read from the cached 2026-09-01 CTRUDELQCSV.csv.)
  So city tax and stormwater arrears are not double counted, because they
  are absent from the project entirely. This module only describes them
  (description text). A proposal for storing them is in the build report
  (shared_changes), not implemented here.

Measured (dev machine, 2026-10-06, disposable copies of the real DB)
---------------------------------------------------------------------------
  Search ~1.5 s server time (~200 KB for 19 years); charges page ~0.6 s.
  With the 1.6 s spacing, a parcel with no city balance costs ~3.5 s, and
  a parcel with k balance years costs ~3.5 + 2.3k s.
    - Newest 40 abatement-prone parcels (before the tax-delinquent
      sub-order existed): 126 requests, 272.9 s (6.8 s/parcel), 2 liens.
      Both hits were among the 8 parcels that were also county-tax-
      delinquent (2/8, versus 0/32 for the others). 2 other parcels had
      only the legacy "Property Tax Lien" label.
    - 12 tax-delinquent-only parcels (tier 3): 56 requests, 114.6 s,
      1 lien, 1 not on the city roll.
    - 25 abatement-prone parcels with ~12-month-old cases: 63 requests,
      142.9 s, 0 liens (3 of the 25 were tax-delinquent).
    - Current ordering (dual-flagged first) from a fresh copy: 20 parcels,
      115 requests, 225.3 s (11.3 s/parcel, since these carry more balance
      years), 4 liens totalling $34,046.59 (one $31,178.30). 3 more
      parcels had only the legacy label.
    Across all samples (89 distinct parcels; the first and last runs share
    8): 5 distinct liens, i.e. 5/35 among parcels also in tax_delinquent
    and 0/54 among parcels that aren't.
  Lifecycle/idempotency verified on the 40-parcel copy: a planted lien on a
  lien-free parcel flipped to municipal_lien_resolved (amount NULL, audit
  text kept). A real lien refreshed in place (same amount, first_seen
  kept, no duplicate). A row set to resolved flipped back (reappeared=1).
  An immediate re-run checked 2 new parcels and none of the first 40. A
  not-on-roll parcel stayed out of the queue until both its 90-day
  not-in-city entry and its 30-day check entry had aged out.
  After the Gross-is-net fix (same day, fresh copy): default run 47
  parcels, 275 requests, 538 s, 8 liens / $40,045.54, all 226 inspected
  years reconciling exactly with the search TOTAL. A forced 30-day refresh
  (cap 12) re-checked all 8 lien rows in place (amounts, case numbers,
  first_seen unchanged; 0 false resolutions) plus 4 new parcels.
  Defaults: 100 parcels / 540 s, so a run ends by ~9.5 minutes even when
  the time budget binds (~48 dual-flagged parcels at 11.3 s each).

stdout and scrape_log notes carry counts only, never names or addresses,
because GitHub Actions logs on this repo are public.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

from db import get_connection, get_state, log_scrape, set_state, upsert_record

SOURCE = "municipal_liens"
SOURCE_PORTAL = "chattanooga_tax_portal"
PORTAL_BASE = os.environ.get("CHATT_TAX_PORTAL_BASE", "https://chattanoogatn.taxandrevenue.opengov.com").rstrip("/")
SEARCH_PATH = "/Employee/GetSearchResults"
CHARGES_PATH = "/User/ViewCurrentCharges"
PIDN_PAGE_PATH = "/User/PIDNHomePage"
USER_AGENT = (
    "chattanooga-intel/1.0 (low-volume per-parcel municipal lien lookups for already-flagged parcels; "
    "contact via github.com/Electrumtexas/chattanooga-intel)"
)

MAX_PARCELS = int(os.environ.get("LIENS_MAX_PARCELS", "100"))
MAX_SECONDS = float(os.environ.get("LIENS_MAX_SECONDS", "540"))
MAX_YEARS_PER_PARCEL = int(os.environ.get("LIENS_MAX_YEARS_PER_PARCEL", "8"))

REFRESH_DAYS = 30            # re-check a parcel (lien or not) after this many days
NOT_IN_CITY_DAYS = 90        # a MAP search with no exact row is cached as "not on the city roll" this long
ERROR_RETRY_DAYS = 3         # a parcel whose lookup failed is retried after this many days
CHECKED_MEMORY_DAYS = 180    # "checked" entries older than this are pruned (they'd be re-checked anyway)
STATE_CAP = 20_000           # max parcels remembered per bookkeeping map (~17k candidates today)
ERROR_STATE_CAP = 2_000

MIN_REQUEST_INTERVAL = 1.6   # seconds between the end of one request and the start of the next (ground rule: >= 1.5)
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3.0
MAX_RETRY_AFTER_SECONDS = 60
MAX_CONSECUTIVE_FAILURES = 5
# Hard stop: no new request (or retry) is started once the run is this many
# seconds past MAX_SECONDS. The pre-parcel check alone let one slow-but-answering
# parcel (a search + 8+ charge pages, each up to 3 x 30 s + backoff) run past
# the workflow step's 15-minute timeout, which kills the step before the
# bookkeeping save and the scrape_log row (release review, 2026-10-06). With
# this, the worst case is ~MAX_SECONDS + 60 s + one in-flight request (30 s).
HARD_OVERRUN_SECONDS = 60
# Abort (as a contract change) when tier-1/2 (City code-enforcement) parcels
# stop being found: the first SEARCH_GUARD_FIRST all missing, or more than
# SEARCH_GUARD_RATIO missing once SEARCH_GUARD_MIN have been checked.
SEARCH_GUARD_FIRST = 5
SEARCH_GUARD_MIN = 10
SEARCH_GUARD_RATIO = 0.5

LANDING_MARKERS = ("Property Tax Search", "modal_searchBy", "MAP/Parcel", SEARCH_PATH)
REQUIRED_SEARCH_KEYS = ("PIDN", "MAP", "TAX_YEAR", "TOTAL", "STATUS")

STATE_CHECKED = f"{SOURCE}:checked"
STATE_NOT_IN_CITY = f"{SOURCE}:not_in_city"
STATE_ERRORS = f"{SOURCE}:errors"

# Abatement-prone code_enforcement codes (see code_enforcement.CODE_CATEGORY).
ABATEMENT_CODES = ("21-136", "21-132", "21-133", "21-134", "21-135", "21-84", "21-80", "21-82", "21-83")
ABATEMENT_CODE_PREFIXES = ("21-87", "21-76(")
ABATEMENT_VIOLATION_TYPES = ("condemnation", "vacant_building")

_SEARCH_TOKEN_RE = re.compile(
    r"function\s+SearchForRecords\s*\(\s*\).*?['\"]/Employee/GetSearchResults['\"]\s*;\s*token\s*=\s*'([^']+)'", re.S
)
_SEARCH_TOKEN_FALLBACK_RE = re.compile(r"searchResults\s*:\s*'(exp[^']+)'")
MUNICIPAL_LIEN_RE = re.compile(r"\bMUNI?CIPAL\s+LIEN\b", re.I)   # "MUNCIPAL" (city's spelling) and "MUNICIPAL"
OTHER_LIEN_RE = re.compile(r"\bLIEN\b", re.I)
BOOK_PAGE_RE = re.compile(r"\bBOOK\s+(\d+)\s+PAGE\s+(\d+)", re.I)
CITY_TAX_RE = re.compile(r"\bREAL\s+(?:PROPERTY\s+)?TAX\b", re.I)
STORMWATER_RE = re.compile(r"STORM\s*WATER|WATER\s+QUALITY", re.I)
_MONEY_RE = re.compile(r"^\(?-?\$?\s*[\d,]*\.?\d+\)?$")


class PortalError(RuntimeError):
    """One lookup failed (network, HTTP, unexpected page) — skip this parcel."""


class PortalBroken(RuntimeError):
    """The portal's contract changed or is down — abort the whole run."""


class BudgetExceeded(RuntimeError):
    """The run's hard time limit passed mid-parcel — stop cleanly (not an error)."""


# --------------------------------------------------------------------------
# HTTP client — one requests.Session for the whole run (tokens + cookie must match)
# --------------------------------------------------------------------------

class PortalClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.requests = 0
        self._last_request_end = 0.0
        self.af_token: str | None = None
        self.search_token: str | None = None
        self.deadline: float | None = None   # time.time() past which no request starts

    def _get(self, path: str, params: dict | None = None, headers: dict | None = None) -> requests.Response:
        """GET with polite spacing, timeout, and a few retries with backoff on
        network errors / 429 / 5xx. Other statuses are returned to the caller.
        """
        last_problem = "no attempt made"
        for attempt in range(MAX_RETRIES):
            if self.deadline is not None and time.time() > self.deadline:
                raise BudgetExceeded("run time limit reached")
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request_end)
            if wait > 0:
                time.sleep(wait)
            resp = None
            try:
                resp = self.session.get(PORTAL_BASE + path, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                last_problem = f"{type(exc).__name__} on {path}"
            finally:
                self._last_request_end = time.monotonic()
                self.requests += 1
            delay = RETRY_BACKOFF_SECONDS * (attempt + 1)
            if resp is not None:
                if resp.status_code not in (429, 500, 502, 503, 504):
                    return resp
                last_problem = f"HTTP {resp.status_code} on {path}"
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, min(int(retry_after), MAX_RETRY_AFTER_SECONDS))
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
        raise PortalError(last_problem)

    def check_robots(self) -> None:
        """Abort if a robots.txt has appeared that disallows what we fetch.
        404/410 = no robots file = allowed (the state observed 2026-10-06).
        """
        try:
            resp = self._get("/robots.txt")
        except PortalError as exc:
            raise PortalBroken(f"robots.txt check failed: {exc}") from exc
        if resp.status_code in (404, 410):
            return
        if resp.status_code in (401, 403):
            raise PortalBroken(f"robots.txt answered HTTP {resp.status_code} (treated as disallow-all)")
        if resp.status_code != 200:
            raise PortalBroken(f"robots.txt answered HTTP {resp.status_code}")
        rp = robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        for path in (SEARCH_PATH, CHARGES_PATH):
            if not rp.can_fetch(USER_AGENT, PORTAL_BASE + path):
                raise PortalBroken(f"robots.txt now disallows {path} — stop and re-review terms")

    def bootstrap(self) -> None:
        """Health check + token load: the landing page must answer 200 and
        still carry the property-search markup and both tokens.
        """
        try:
            resp = self._get("/")
        except PortalError as exc:
            raise PortalBroken(f"landing page unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise PortalBroken(f"landing page HTTP {resp.status_code}")
        html = resp.text
        missing = [m for m in LANDING_MARKERS if m not in html]
        if missing:
            raise PortalBroken(f"landing page markers missing: {missing}")
        soup = BeautifulSoup(html, "html.parser")
        af_input = soup.find("input", attrs={"name": "__RequestVerificationToken"})
        token_match = _SEARCH_TOKEN_RE.search(html) or _SEARCH_TOKEN_FALLBACK_RE.search(html)
        if not af_input or not af_input.get("value") or not token_match:
            raise PortalBroken("landing page no longer carries the antiforgery input and/or search token")
        self.af_token = af_input["value"]
        self.search_token = token_match.group(1)

    def search_map(self, key: str) -> list[dict]:
        """Every tax-year row whose MAP is exactly `key` (the search itself is
        a contains-match — see module docstring). A non-list answer gets one
        session refresh (stale token) before being treated as a contract change.
        """
        for attempt in (1, 2):
            params = {"searchData": key, "searchType": "MAP", "application": "Property", "token": self.search_token}
            headers = {
                "__RequestVerificationToken": self.af_token or "",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            }
            resp = self._get(SEARCH_PATH, params=params, headers=headers)
            data = None
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
            if isinstance(data, list):
                break
            if attempt == 1:
                self.bootstrap()
                continue
            raise PortalBroken(f"MAP search returned HTTP {resp.status_code} with a non-list body after a session refresh")
        for row in data:
            if not isinstance(row, dict) or any(k not in row for k in REQUIRED_SEARCH_KEYS):
                raise PortalBroken("MAP search rows no longer carry PIDN/MAP/TAX_YEAR/TOTAL/STATUS")
        return [row for row in data if _norm_map(row.get("MAP")) == key]

    def current_charges(self, pidn: str) -> list[dict]:
        resp = self._get(CHARGES_PATH, params={"pidn": pidn})
        if resp.status_code != 200:
            raise PortalError(f"charges page HTTP {resp.status_code}")
        return parse_charges(resp.text)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _norm_map(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _clean(value) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def _num(value) -> float:
    """The search JSON's TOTAL is a number today; tolerate a string or null."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(text: str | None) -> float | None:
    if text is None:
        return None
    t = text.strip()
    if not t or not _MONEY_RE.match(t):
        return None
    negative = t.startswith("(") or "-" in t
    value = float(re.sub(r"[^\d.]", "", t) or 0)
    return -value if negative else value


def _date_iso(text: str | None) -> str | None:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_charges(html: str) -> list[dict]:
    """Rows of the View Current Charges table, or [] for "no charges due".
    Anything else (a different page, a changed layout) raises PortalError.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for table in soup.find_all("table"):
        header = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if "description" not in header or "gross" not in header:
            continue
        idx = {name: header.index(name) for name in header}
        lines = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) != len(header):
                continue
            gross = _money(cells[idx["gross"]])
            if gross is None:
                continue
            year_text = cells[idx["tax year"]] if "tax year" in idx else ""
            lines.append({
                "tax_year": int(year_text) if year_text.isdigit() else None,
                "date_due": _date_iso(cells[idx["date due"]]) if "date due" in idx else None,
                "description": _clean(cells[idx["description"]]) or "",
                # Gross is ALREADY net of payments (Gross = Normal +/- Discount
                # + Penalty + Interest - Payment on all 542 live lines captured
                # 2026-10-06), so it
                # IS the outstanding balance. The Payment column is shown for
                # information only and must not be subtracted again.
                "outstanding": round(gross, 2),
            })
        return lines
    text = soup.get_text(" ", strip=True).lower()
    if "no charges due" in text:
        return []
    raise PortalError("charges page has neither a charges table nor the 'no charges due' message")


def _year_ranges(years) -> str:
    ys = sorted({int(y) for y in years})
    if not ys:
        return ""
    parts, start, prev = [], ys[0], ys[0]
    for y in ys[1:] + [None]:
        if y is not None and y == prev + 1:
            prev = y
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if y is not None:
            start = prev = y
    return ",".join(parts)


def _location(row: dict) -> str | None:
    return _clean(" ".join(str(row.get(k) or "") for k in ("LOCNUMB", "GPSNS", "LOCSTREET", "LOCSTREET2")))


def _mailing(row: dict) -> str | None:
    street = " ".join(str(row.get(k) or "").strip() for k in ("M_ADD", "M_ADD2", "M_ADD3") if str(row.get(k) or "").strip())
    city = _clean(row.get("M_CITY"))
    state_zip = _clean(f"{row.get('M_STATE') or ''} {row.get('M_ZIP') or ''}")
    tail = ", ".join(p for p in (city, state_zip) if p)
    return _clean(", ".join(p for p in (street, tail) if p))


# --------------------------------------------------------------------------
# One parcel
# --------------------------------------------------------------------------

def check_parcel(client: PortalClient, keys: list[str], stats: dict,
                 must_years: frozenset[int] | set[int] = frozenset()) -> dict | None:
    """Returns None when no key finds the parcel on the city roll, else a
    result dict describing its municipal liens and city arrears.
    `must_years` (tax years that carried this parcel's lien lines last time)
    are inspected even when they fall outside the newest
    MAX_YEARS_PER_PARCEL balance years, so a lien isn't "lost" just because
    newer unpaid bills pushed its year out of the window.
    """
    rows: list[dict] = []
    for key in keys:
        rows = client.search_map(key)
        stats["searches"] += 1
        if rows:
            break
    if not rows:
        return None

    def _year(row) -> int:
        y = str(row.get("TAX_YEAR") or "")
        return int(y) if y.isdigit() else 0

    rows.sort(key=_year, reverse=True)
    active = [r for r in rows if str(r.get("STATUS") or "").strip().lower() == "active"]
    latest = (active or rows)[0]
    balance_rows = [r for r in active if _num(r.get("TOTAL")) > 0]
    inspected = balance_rows[:MAX_YEARS_PER_PARCEL]
    beyond = balance_rows[MAX_YEARS_PER_PARCEL:]
    extra = [r for r in beyond if _year(r) in must_years]
    skipped = [r for r in beyond if _year(r) not in must_years]
    if extra:
        stats["prior_lien_years_extra"] += len(extra)
        inspected = inspected + extra
    if skipped:
        stats["truncated_parcels"] += 1

    today = date.today().isoformat()
    lien_lines, other_lien_lines = [], []
    tax_due: dict[int, float] = {}
    stw_due: dict[int, float] = {}
    for row in inspected:
        lines = client.current_charges(str(row["PIDN"]))
        # Contract guard: a year's charge lines must add up to the search's
        # TOTAL for that year (exact on 226/226 live pages, 2026-10-06). If a
        # layout change ever shifts what the columns mean, fail the parcel
        # (retried in ERROR_RETRY_DAYS; 5 in a row abort the run) instead of
        # silently storing wrong amounts.
        if abs(round(sum(l["outstanding"] for l in lines), 2) - _num(row.get("TOTAL"))) > 0.01:
            raise PortalError("charges lines don't reconcile with search TOTAL")
        for line in lines:
            stats["charge_pages_lines"] += 1
            amt = line["outstanding"]
            if amt <= 0:
                continue
            year = _year(row) or line["tax_year"]   # the PIDN's year (equal to the line's Tax Year on all 542 live lines)
            desc = line["description"]
            if MUNICIPAL_LIEN_RE.search(desc):
                bp = BOOK_PAGE_RE.search(desc)
                lien_lines.append({"year": year, "description": desc, "amount": amt,
                                   "book_page": f"BK {bp.group(1)} PG {bp.group(2)}" if bp else None})
            elif OTHER_LIEN_RE.search(desc):
                other_lien_lines.append({"year": year, "description": desc, "amount": amt})
            elif line["date_due"] and line["date_due"] < today:
                if CITY_TAX_RE.search(desc):
                    tax_due[year] = round(tax_due.get(year, 0.0) + amt, 2)
                elif STORMWATER_RE.search(desc):
                    stw_due[year] = round(stw_due.get(year, 0.0) + amt, 2)
        stats["years_inspected"] += 1

    return {
        "latest_pidn": str(latest["PIDN"]),
        "owner": _clean(latest.get("OWNER_NAME")),
        "location": _location(latest),
        "mailing": _mailing(latest),
        "lien_lines": lien_lines,
        "other_lien_lines": other_lien_lines,
        "tax_due": tax_due,
        "stw_due": stw_due,
        "cm_years": [_year(r) for r in active if r.get("ClerkAndMasterDelinquent")],
        "balance_total": round(sum(_num(r.get("TOTAL")) for r in active), 2),
        "skipped_years": [_year(r) for r in skipped],
        "skipped_total": round(sum(_num(r.get("TOTAL")) for r in skipped), 2),
    }


def build_description(res: dict) -> str:
    total = sum(l["amount"] for l in res["lien_lines"])
    shown = res["lien_lines"][:4]
    lien_txt = "; ".join(f"{l['description']} ({l['year']} bill) ${l['amount']:,.2f}" for l in shown)
    if len(res["lien_lines"]) > len(shown):
        lien_txt += f"; +{len(res['lien_lines']) - len(shown)} more"
    # "bill yrs" lists EVERY tax year carrying a lien line; _prior_lien_years()
    # reads it back on the next refresh (keep the format in sync).
    lien_years = _year_ranges(l["year"] for l in res["lien_lines"] if l["year"])
    parts = [f"Municipal lien ${total:,.2f} (bill yrs {lien_years}): {lien_txt}"]
    if res["tax_due"]:
        parts.append(f"city tax past due {_year_ranges(res['tax_due'])} ${sum(res['tax_due'].values()):,.2f}")
    if res["stw_due"]:
        parts.append(f"stormwater past due {_year_ranges(res['stw_due'])} ${sum(res['stw_due'].values()):,.2f}")
    parts.append(f"city balance all yrs ${res['balance_total']:,.2f}")
    if res["skipped_years"]:
        parts.append(f"{len(res['skipped_years'])} older balance yrs not itemized ({_year_ranges(res['skipped_years'])}, ${res['skipped_total']:,.2f})")
    if res["cm_years"]:
        parts.append(f"C&M back-tax yrs {_year_ranges(res['cm_years'])}")
    if res["other_lien_lines"]:
        others = "; ".join(f"{l['description']} ({l['year']}) ${l['amount']:,.2f}" for l in res["other_lien_lines"][:3])
        parts.append(f"other lien-labelled: {others}")
    if res["mailing"]:
        parts.append(f"mail: {res['mailing']}")
    return " | ".join(parts)


_BILL_YRS_RE = re.compile(r"\(bill yrs ([\d,\-]+)\)")
_BILL_YEAR_RE = re.compile(r"\((\d{4}) bill\)")


def _prior_lien_years(description: str | None) -> tuple[set[int], bool]:
    """Tax years that carried lien lines when the row was last written, and
    whether that list is complete. Reads the "(bill yrs 2019,2021-2022)"
    token build_description writes; rows written before that token existed
    fall back to the per-line "(2019 bill)" labels, which are complete only
    when no "+N more" lines were cut off.
    """
    head = (description or "").split(" | ")[0]
    m = _BILL_YRS_RE.search(head)
    if m:
        years: set[int] = set()
        for part in m.group(1).split(","):
            lo, _, hi = part.partition("-")
            if lo.isdigit() and (not hi or hi.isdigit()):
                years.update(range(int(lo), int(hi or lo) + 1))
        return years, bool(years)
    years = {int(y) for y in _BILL_YEAR_RE.findall(head)}
    return years, bool(years) and "more" not in head


def apply_result(conn, parcel_id: str, res: dict, stats: dict) -> None:
    dedupe_key = f"municipal_lien:{parcel_id}"
    existing = conn.execute(
        "SELECT case_type, amount, description FROM court_records WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    source_url = f"{PORTAL_BASE}{PIDN_PAGE_PATH}?PIDN={res['latest_pidn']}"

    if res["lien_lines"]:
        stats["liens_found"] += 1
        if existing is None:
            stats["new_liens"] += 1
        elif existing["case_type"] != "municipal_lien":
            stats["reappeared"] += 1
        book_pages = [l["book_page"] for l in res["lien_lines"] if l["book_page"]]
        upsert_record(
            conn, "court_records", dedupe_key,
            keep_existing=("parcel_id", "resolution_method"),
            parcel_id=parcel_id,
            source_portal=SOURCE_PORTAL,
            case_number="; ".join(dict.fromkeys(book_pages)) or None,
            case_type="municipal_lien",
            filing_date=None,   # the portal shows no lien date; never invented
            party_names=None,
            amount=round(sum(l["amount"] for l in res["lien_lines"]), 2),
            raw_address=res["location"],
            raw_owner_name=res["owner"],
            resolution_method="native_parcel_id",
            source_url=source_url,
            description=build_description(res),
        )
        return

    if res["other_lien_lines"]:
        stats["other_lien_only"] += 1
    if existing is not None and existing["case_type"] == "municipal_lien":
        # Only resolve when the lien can't be hiding in an un-itemized year:
        # either no balance year was skipped, or every year that carried a
        # lien line last time is known and was inspected (check_parcel's
        # must_years) or no longer has a balance at all.
        if res["skipped_years"]:
            prior_years, complete = _prior_lien_years(existing["description"])
            if not (complete and prior_years.isdisjoint(res["skipped_years"])):
                stats["resolve_unconfirmed"] += 1   # row left as municipal_lien; re-checked next refresh
                return
        stats["resolved"] += 1
        previous = (existing["description"] or "").split(" | ")[0]
        upsert_record(
            conn, "court_records", dedupe_key,
            keep_existing=("parcel_id", "resolution_method"),
            case_type="municipal_lien_resolved",
            amount=None,
            raw_address=res["location"],
            raw_owner_name=res["owner"],
            source_url=source_url,
            description=f"Resolved {date.today().isoformat()}: no municipal-lien line on the city portal (was: {previous})",
        )


# --------------------------------------------------------------------------
# Candidates + bookkeeping
# --------------------------------------------------------------------------

def _load_dated_map(conn, key: str) -> dict[str, str]:
    """source_state value {"YYYY-MM-DD": [parcel_id, ...]} -> {parcel_id: date}."""
    raw = get_state(conn, key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for day, pids in data.items():
            if isinstance(pids, list):
                for pid in pids:
                    if isinstance(pid, str) and (pid not in out or out[pid] < day):
                        out[pid] = day
    return out


def _save_dated_map(conn, key: str, mapping: dict[str, str], max_age_days: int, cap: int) -> None:
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    kept = sorted(((d, p) for p, d in mapping.items() if d >= cutoff), reverse=True)[:cap]
    grouped: dict[str, list[str]] = {}
    for d, p in kept:
        grouped.setdefault(d, []).append(p)
    set_state(conn, key, json.dumps({d: sorted(ps) for d, ps in sorted(grouped.items())}, separators=(",", ":")))


def _age_days(day: str | None) -> int | None:
    if not day:
        return None
    try:
        return (date.today() - date.fromisoformat(day[:10])).days
    except ValueError:
        return None


def _parcel_keys(parcel_id: str, tax_map_no: str | None) -> list[str]:
    keys = []
    if tax_map_no:
        keys.append(_norm_map(tax_map_no))
    derived = _norm_map(parcel_id.replace("-", ""))
    if derived and derived not in keys:
        keys.append(derived)
    return keys


def select_candidates(conn, checked: dict, not_in_city: dict, errors: dict) -> list[dict]:
    today = date.today().isoformat()
    cands: dict[str, dict] = {}

    def add(pid: str, tier: int, evidence: str | None, weight: float = 0.0) -> None:
        evidence = min(evidence[:10], today) if evidence else None
        cur = cands.get(pid)
        if cur is None or tier < cur["tier"]:
            cands[pid] = {"parcel_id": pid, "tier": tier, "evidence": evidence, "weight": weight}

    abate_sql = " OR ".join(
        [f"violation_code IN ({','.join('?' for _ in ABATEMENT_CODES)})"]
        + ["violation_code LIKE ?" for _ in ABATEMENT_CODE_PREFIXES]
        + [f"violation_type IN ({','.join('?' for _ in ABATEMENT_VIOLATION_TYPES)})"]
    )
    abate_params = [*ABATEMENT_CODES, *(p + "%" for p in ABATEMENT_CODE_PREFIXES), *ABATEMENT_VIOLATION_TYPES]
    for r in conn.execute(
        f"SELECT parcel_id, MAX(violation_date) AS d, MAX(CASE WHEN {abate_sql} THEN violation_date END) AS ad "
        "FROM code_enforcement WHERE parcel_id IS NOT NULL GROUP BY parcel_id",
        abate_params,
    ):
        if r["ad"] is not None:
            add(r["parcel_id"], 1, r["ad"])
        else:
            add(r["parcel_id"], 2, r["d"])
    for r in conn.execute(
        "SELECT parcel_id, MAX(first_seen_at) AS d, SUM(amount_due) AS amt FROM tax_delinquent "
        "WHERE parcel_id IS NOT NULL GROUP BY parcel_id"
    ):
        add(r["parcel_id"], 3, r["d"], r["amt"] or 0.0)
    for r in conn.execute(
        "SELECT parcel_id, MAX(first_seen_at) AS d FROM court_records "
        "WHERE parcel_id IS NOT NULL AND source_portal != ? GROUP BY parcel_id",
        (SOURCE_PORTAL,),
    ):
        add(r["parcel_id"], 4, r["d"])
    for r in conn.execute(
        "SELECT parcel_id, last_seen_at, description FROM court_records "
        "WHERE case_type = 'municipal_lien' AND parcel_id IS NOT NULL"
    ):
        cands[r["parcel_id"]] = {"parcel_id": r["parcel_id"], "tier": 0, "evidence": None, "weight": 0.0,
                                 "last_seen": (r["last_seen_at"] or "")[:10],
                                 "lien_years": frozenset(_prior_lien_years(r["description"])[0])}

    tmn = {r["parcel_id"]: r["tax_map_no"] for r in conn.execute("SELECT parcel_id, tax_map_no FROM parcels")}
    # Inside the code-enforcement tiers, parcels that are ALSO county-tax-
    # delinquent go first: an outstanding (unpaid) lien tracks unpaid taxes —
    # see the docstring's measured hit rates.
    tax_delinquent = {r[0] for r in conn.execute("SELECT DISTINCT parcel_id FROM tax_delinquent WHERE parcel_id IS NOT NULL")}

    refresh, fresh, recheck = [], [], []
    for pid, c in cands.items():
        err_age = _age_days(errors.get(pid))
        if err_age is not None and err_age < ERROR_RETRY_DAYS:
            continue
        last = checked.get(pid)
        last_age = _age_days(last)
        c["last_checked"] = last
        c["keys"] = _parcel_keys(pid, tmn.get(pid))
        if c["tier"] == 0:
            age = last_age if last_age is not None else _age_days(c.get("last_seen"))
            if age is None or age >= REFRESH_DAYS:
                refresh.append(c)
            continue
        nic_age = _age_days(not_in_city.get(pid))
        if nic_age is not None and nic_age < NOT_IN_CITY_DAYS:
            continue
        if last is None or (c["evidence"] and c["evidence"] > last):
            fresh.append(c)
        elif last_age is not None and last_age >= REFRESH_DAYS:
            recheck.append(c)

    refresh.sort(key=lambda c: c.get("last_checked") or c.get("last_seen") or "")
    fresh.sort(key=lambda c: (
        c["tier"],
        0 if c["tier"] in (1, 2) and c["parcel_id"] in tax_delinquent else 1,
        _neg_date(c["evidence"]), -c["weight"], c["parcel_id"],
    ))
    recheck.sort(key=lambda c: (c["last_checked"] or "", c["tier"], c["parcel_id"]))
    return refresh + fresh + recheck


def _neg_date(day: str | None) -> int:
    """Sort key putting the newest date first (and no date last)."""
    if not day:
        return 0
    try:
        return -date.fromisoformat(day).toordinal()
    except ValueError:
        return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _new_stats() -> dict:
    return {
        "checked": 0, "liens_found": 0, "new_liens": 0, "reappeared": 0, "resolved": 0,
        "not_in_city": 0, "lien_parcel_missing": 0, "other_lien_only": 0, "truncated_parcels": 0,
        "resolve_unconfirmed": 0, "prior_lien_years_extra": 0, "hard_stop": 0,
        "errors": 0, "searches": 0, "years_inspected": 0, "charge_pages_lines": 0,
        "by_tier": {},
    }


def _notes(stats: dict, client: PortalClient, started: float, extra: str = "") -> str:
    tiers = ",".join(f"t{t}={n}" for t, n in sorted(stats["by_tier"].items()))
    notes = (
        f"liens_found={stats['liens_found']}, new={stats['new_liens']}, reappeared={stats['reappeared']}, "
        f"resolved={stats['resolved']}, not_in_city={stats['not_in_city']}, "
        f"lien_parcel_missing={stats['lien_parcel_missing']}, other_lien_only={stats['other_lien_only']}, "
        f"truncated_parcels={stats['truncated_parcels']}, resolve_unconfirmed={stats['resolve_unconfirmed']}, "
        f"prior_lien_years_extra={stats['prior_lien_years_extra']}, years_inspected={stats['years_inspected']}, "
        f"errors={stats['errors']}, hard_stop={stats['hard_stop']}, checked_by_tier=[{tiers}], requests={client.requests}, "
        f"seconds={round(time.time() - started, 1)}"
    )
    return f"{notes}, {extra}" if extra else notes


def main() -> None:
    conn = get_connection()
    client = PortalClient()
    stats = _new_stats()
    started = time.time()

    try:
        client.check_robots()
        client.bootstrap()
    except (PortalBroken, PortalError) as exc:
        log_scrape(conn, SOURCE, 0, status="error", notes=f"health check failed: {exc}")
        conn.commit()
        print(f"{SOURCE}: health check failed ({exc}) — aborting without touching data.")
        sys.exit(1)

    client.deadline = started + MAX_SECONDS + HARD_OVERRUN_SECONDS
    checked = _load_dated_map(conn, STATE_CHECKED)
    not_in_city = _load_dated_map(conn, STATE_NOT_IN_CITY)
    errors = _load_dated_map(conn, STATE_ERRORS)
    queue = select_candidates(conn, checked, not_in_city, errors)
    today = date.today().isoformat()

    abort_reason = None
    consecutive_failures = 0
    per_parcel_seconds: list[float] = []
    city_checked = city_missing = 0                       # tier-1/2 search sanity guard
    run_not_found: list[tuple[str, str | None, str | None]] = []
    try:
        for cand in queue:
            if stats["checked"] >= MAX_PARCELS:
                break
            elapsed = time.time() - started
            expected = (sum(per_parcel_seconds) / len(per_parcel_seconds)) if per_parcel_seconds else 10.0
            if elapsed + expected > MAX_SECONDS:
                break
            pid = cand["parcel_id"]
            t0 = time.time()
            try:
                res = check_parcel(client, cand["keys"], stats, cand.get("lien_years", frozenset()))
            except BudgetExceeded:
                # Mid-parcel hard stop: this parcel is left untouched (not marked
                # checked, no row written) and simply stays due for the next run.
                stats["hard_stop"] = 1
                break
            except PortalError:
                stats["errors"] += 1
                errors[pid] = today
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    abort_reason = f"{consecutive_failures} consecutive parcel lookups failed"
                    break
                continue
            consecutive_failures = 0
            errors.pop(pid, None)
            stats["checked"] += 1
            stats["by_tier"][cand["tier"]] = stats["by_tier"].get(cand["tier"], 0) + 1
            if res is None:
                run_not_found.append((pid, checked.get(pid), not_in_city.get(pid)))
            checked[pid] = today
            if res is None:
                stats["not_in_city"] += 1
                not_in_city[pid] = today
                if cand["tier"] == 0:
                    stats["lien_parcel_missing"] += 1   # keep the lien row — can't confirm it was paid
            else:
                not_in_city.pop(pid, None)
                apply_result(conn, pid, res, stats)
            conn.commit()
            per_parcel_seconds.append(time.time() - t0)
            if cand["tier"] in (1, 2):
                city_checked += 1
                city_missing += res is None
                # Tier 1-2 parcels are City code-enforcement parcels, so nearly
                # all should be on the city roll (0 of 47 missing in a live run).
                # A search that suddenly finds nothing is a contract change,
                # not "outside city limits": undo this run's not-found
                # bookkeeping (before the finally-block saves it) so nothing
                # is cached for NOT_IN_CITY_DAYS, then abort.
                if (city_checked >= SEARCH_GUARD_FIRST and city_missing == city_checked) or (
                    city_checked >= SEARCH_GUARD_MIN and city_missing / city_checked > SEARCH_GUARD_RATIO
                ):
                    for nf_pid, prev_checked, prev_nic in run_not_found:
                        for mapping, prev in ((checked, prev_checked), (not_in_city, prev_nic)):
                            if prev is None:
                                mapping.pop(nf_pid, None)
                            else:
                                mapping[nf_pid] = prev
                    raise PortalBroken(
                        f"in-city parcels not found ({city_missing} of {city_checked} tier-1/2) — search contract changed"
                    )
    except PortalBroken as exc:
        abort_reason = f"portal contract changed mid-run: {exc}"
    except Exception as exc:  # noqa: BLE001 — log an error row instead of dying silently; message carries no row data
        abort_reason = f"unexpected {type(exc).__name__}: {str(exc)[:160]}"
    finally:
        _save_dated_map(conn, STATE_CHECKED, checked, CHECKED_MEMORY_DAYS, STATE_CAP)
        _save_dated_map(conn, STATE_NOT_IN_CITY, not_in_city, NOT_IN_CITY_DAYS, STATE_CAP)
        _save_dated_map(conn, STATE_ERRORS, errors, ERROR_RETRY_DAYS, ERROR_STATE_CAP)
        conn.commit()

    queued_after = max(len(queue) - stats["checked"] - stats["errors"], 0)
    if abort_reason:
        notes = _notes(stats, client, started, f"aborted: {abort_reason}")
        log_scrape(conn, SOURCE, stats["checked"], status="error", notes=notes)
        conn.commit()
        print(f"{SOURCE}: {notes}")
        sys.exit(1)

    notes = _notes(stats, client, started, f"still_due={queued_after}")
    log_scrape(conn, SOURCE, stats["checked"], status="ok", notes=notes)
    conn.commit()
    print(f"{SOURCE}: checked {stats['checked']} parcels. {notes}")


if __name__ == "__main__":
    main()
