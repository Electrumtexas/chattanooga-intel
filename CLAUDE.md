# Chattanooga Intel

Distressed-property intelligence for Hamilton County, TENNESSEE (Chattanooga). Owner: Jarrod,
Electrum Texas. Scrapes public records, stacks distress signals per property, scores leads, and
serves a static dashboard from GitHub Pages. Everything lives in this repo: GitHub Actions runs
the scrapers on a schedule, commits results back, and deploys `dashboard/` to Pages.

- Repo (public): https://github.com/Electrumtexas/chattanooga-intel
- Dashboard: https://electrumtexas.github.io/chattanooga-intel/

**Read this file before touching anything.** It's the record of what has been verified live
versus assumed. The original project brief contained wrong URLs (see "Brief errors confirmed");
do not re-derive sources from the brief.

## Status (2026-10-06, after Phase 3: estate-owned parcels, municipal liens, scoring v2)

Recon method (2026-09-15): 7 discovery agents (one per source category), 7 independent agents
that re-ran each discovery's key requests trying to refute it, and a critic that reconciled
conflicts. A second round (2026-09-22) covered the 19 SOS-registered foreclosure posting
companies, probate contact sources, and free lien options. Phase 3 (2026-10-06): each new module
was built, then attacked by an independent verifier (live re-fetches, fake-server failure modes,
lifecycle injection) on scratch copies of the DB; scoring was recalibrated from three competing
proposals judged by two independent judges. "Verified" below means a real request/response was
observed, re-observed by an independent verifier, and (for the scrapers) confirmed by running the
actual module end-to-end against a real, disposable copy of the DB.

| Signal | Source(s) | Mechanic | State |
|---|---|---|---|
| Tax delinquency | Trustee `CTRUDELQCSV.zip` | bulk zip | **LIVE** (`tax_delinquent.py`) |
| Tax-sale filings | Clerk & Master annual Delinquent Tax Sale LIST PDF | PDF parse | **LIVE** (`tax_sale.py`) |
| Code enforcement | City of Chattanooga ArcGIS Hub CSV item | ~90MB CSV | **LIVE** (`code_enforcement.py`, rewritten — the old Socrata endpoint was dead) |
| Parcel → lat/long, owner, land use, value | County ArcGIS `Live_Parcels` (new `mapsdev` host, post-9/18 migration) | ArcGIS REST JSON | **LIVE** (`enrich_assessor.py`) |
| Foreclosure notices | 6 posting sites (betterchoicenotices, nwpostingservices, foreclosuretennessee, tnlegalpub, capitalcitypostings, tennesseepostings) | JSON APIs / ASP.NET postback / static HTML, cross-site merge on deed book/page | **LIVE** (`foreclosure_notices.py`); a 7th site, internetpostings.com, is pending an owner OK (see Decisions still open) |
| Probate / estates | Chancery Court Part 2 motion docket PDFs | PDF parse | **LIVE** (`probate_dockets.py`); partial coverage by design (estates with a pending motion only) |
| Collections / detainers (General Sessions) | edockets.us | page XHR + PDFs | **LIVE** (`sessions_dockets.py`); tenant/defendant names never stored for detainer cases |
| Estate-owned property (standing inventory) | County GIS `Live_Parcels` owner records (`OWNERNAME1/2` + `MASTNAME` C/O lines) | ArcGIS REST LIKE pre-filter + word-boundary classifier | **LIVE** (`estate_parcels.py`; ~254 parcels: heirs 219, heirs_coowner 16, estate 11, executor_admin 8) |
| Liens (municipal) | City of Chattanooga tax portal (OpenGov) | per-parcel enrichment lookup of already-flagged parcels | **LIVE** (`municipal_liens.py`; capped 100 parcels / 540 s per run) |
| Liens / lis pendens (county-wide) | Register of Deeds only | none | **No compliant automated source exists** |
| Civil judgments (non-detainer) | TennesseeCaseFinder.com | account + terms click-through | Manual only — owner decision below |

The original Phase 1 `court_records.py` stub (which targeted the wrong-state hamiltonclerk.com /
Civitek portals) was deleted on 2026-10-06. The `court_records` **table** is very much alive: it
is fed by `tax_sale.py`, `probate_dockets.py`, `sessions_dockets.py`, `foreclosure_notices.py`,
`estate_parcels.py` and `municipal_liens.py`, each under its own `scrape_log` source name.

## Brief errors confirmed

- `hamiltonclerk.com` (root, `/court-search/`, `/probate/`) is the Hamilton County, **FLORIDA**
  Clerk (Jasper FL 32052, (386) 792-1288, Florida statute citations). Its "I accept" link goes to
  `civitekflorida.com/ocrs/county/24/` — Civitek numbers Florida counties alphabetically; 24 is
  Hamilton, FL. The Civitek disclaimer/cadence checkpoint is therefore moot for this project.
- Hamilton County TN probate is handled by the **Chancery Court Clerk & Master, Probate
  Division** (300 Courthouse, 625 Georgia Ave, 423-209-6615). Docket format `YY-P-NNN`.
- `tennesseecasefinder.com` is real Hamilton TN (Circuit + General Sessions Civil), but not open:
  account registration, a mandatory terms checkbox, and an emailed access code.
- The code-enforcement endpoint `chattadata.org/resource/qcrz-rvw7.json` is dead. The city retired
  Socrata and moved to an ArcGIS Hub at `data.chattanooga.gov` (press release 2025-12-16).
  `chattadata.org` only 301s to the Hub over plain HTTP; there is no SODA compatibility layer.
- `cmpti.hamiltontn.gov` is correct and public, but it's a per-parcel delinquent-bill lookup, not
  a tax-sale list.
- Tennessee residential foreclosures are **non-judicial** trustee sales (Tenn. Code Ann.
  35-5-101 et seq.), confirmed against a live Hamilton notice — they are not court filings, so
  "foreclosure notices" can't come from court records.

## Network caveat — read before any live testing

The developer machine's internet egress registers as outside the US (a Brazilian residential
ISP, observed 2026-09-15 with the VPN off). Several Tennessee hosts filter non-US traffic:
`sos.tn.gov` (CloudFront country block), `tennesseecasefinder.com` and `edockets.us` (TCP
timeouts; confirmed up via a third-party reader), `hamiltoncountyherald.com`, `tnbear.tn.gov`.
Anthropic's WebFetch is a second vantage but is refused by some of the same hosts.

**Reachability measured from the dev machine does not predict production.** Use
`.github/workflows/probe.yml` (workflow_dispatch) to make one request per source from GitHub's
runners, where the scraper actually runs. Its logs are public, so it prints only status and
markers, never names or addresses.

First probe run from GitHub (2026-09-15, run 35015760744):
- **Reachable from GitHub but not from the dev machine:** `tennesseecasefinder.com` and
  `edockets.us` (both the page and its docket-list XHR returned 200).
- **200 from GitHub:** ForeclosureTennessee.com (python-requests default UA), tnlegalpub.com JSON
  (12 Hamilton notices in total, newest 2026-08-21), the hamiltontn.gov Circuit / Sessions /
  Chancery docket pages, the 2026 tax-sale LIST PDF, the Trustee zip, cmpti, the Register's
  monthly report, `Live_Parcels` (count 169,662; key query and `Locator_TaxMapNo` both work), an
  Assessor card, pwgis city parcels (85,939), and the ArcGIS violations item and CSV.
- **Still failing from GitHub:** `hamiltoncountyherald.com` (connect timeout — the site itself is
  unreachable, not a geo block) and the SOS posting-company registry (403 — it blocks datacenter
  traffic too, so it has to be read manually in a US browser).

---

## Source details — verified mechanics

### Tax delinquency — Hamilton County Trustee (`scraper/tax_delinquent.py`) — LIVE

Direct download, no form or session: `https://www.hamiltontn.gov/_downloadsTrusteeDelinquent/CTRUDELQCSV.zip`
(linked from `tpti.hamiltontn.gov` as "Delinquent File Download"). Monthly (Last-Modified
2026-09-01, 3,667,850 bytes). Official layout PDF: `CTRUDELQ.PDF`.

Verified against a real pull:
- **One row per (Map, Group, Parcel, Bill Year)**, bill years back to 1999, up to 26 years on one
  parcel. ~97.5% of rows carry a nonzero current-owed balance (a running ledger). We ingest rows
  where county+muni+stormwater current owed > 0; per-parcel totals are aggregated in
  `build_unified.py`.
- **Money conventions differ by column** (verified by population percentiles, not one row):
  `Current * Owed` and `* Amount` are **cents** (÷100); `Assessment` fields are **whole dollars**.
- **~77% of currently-owed rows are personal property, not real estate — filtered out.**
  Map `PER` (40,592) and `OSAP` (219) have 100% blank Land Use. Only digit-leading Maps are kept:
  12,347 bills across 7,926 real-estate parcels.
- `Back Tax Indicator` Y/N (Y = active back-tax collection) feeds a scoring bonus.
- ~2.3% ragged rows (skipped and counted), trailing-whitespace header names, 7 `Filler` columns.
- No separate situs city/ZIP — only a single `Property Address` string.
- **The Trustee file carries NO City of Chattanooga tax or stormwater** (verified 2026-10-06 while
  building `municipal_liens.py`): all 7,859 District 1 (city) rows have zero muni and zero
  stormwater owed; muni tax appears only for the small-town districts. Six parcels checked in both
  sources show city tax on the city portal and county-only amounts here. So city tax arrears are
  not double counted anywhere — they are simply missing from the project (see Pending work).
- Rows that drop out of a later monthly file (paid bills) are left in the table with an old
  `last_seen_at`; `build_unified.py` does not score them (see Scoring — stale-row rule). Only a
  real re-ingest may move `last_seen_at`; backfills pass `upsert_record(touch_last_seen=False)`.

### Tax-sale filings — Clerk & Master — LIVE (`scraper/tax_sale.py`)

- **Annual Delinquent Tax Sale LIST PDF**:
  `https://www.hamiltontn.gov/Clerkmasterforms/taxsale/2026TaxSale/DELINQUENT%20TAX%20SALE%20LIST%202026.pdf`
  (linked from the hamiltontn.gov home page "Featured Information"). Text layer; 185/185 rows
  parsed with pypdf + regex: status (PAID/REMOVED/blank), Chancery docket (11256 = Hamilton
  County suit; 11255 inferred City), item #, address, MAP, GROUP, PARCEL (+ suffixes like C158),
  minimum bid. Year paths persist (the 2025 list is still up) — useful for backfill. Filenames
  vary by year; HEAD daily and compare Last-Modified. Annual, and most rows are PAID by sale
  day. The 2026 sale was held 2026-06-04 and the list was frozen at sale day: 66 ACTIVE rows
  (went to auction unpaid — scored as owner-in-redemption-window leads), 119 resolved
  (`tax_sale_resolved`, PAID 113 / REMOVED 6).
- **cmpti.hamiltontn.gov** — per-parcel delinquent bills. Search is a two-step ASP.NET postback
  (post the tab `__EVENTTARGET` first; skipping it returns 500). Detail pages GET directly:
  `CM_PropertyInfo.aspx?pmuid=N`, `CM_Taxbill.aspx?pmuid=N&tbuid=M`. Blank Group renders as a
  double space (`150  270`). Use for enrichment of known parcels only — walking pmuids is crawling.
- Redemption stage: Chancery Part 1 motion dockets carry suit 11256 motions; the Real Property
  Office's "High Bid list" PDF (county-owned post-sale parcels) has a text layer.

### Code enforcement — City of Chattanooga ArcGIS Hub — LIVE (`scraper/code_enforcement.py`)

Rows are one per cited ordinance, not per case (29,082 rows = 21,296 cases on 2026-10-06; one
inspection produced 35 rows) — scoring therefore groups by `case_number`. An unchanged item
(same size + max `date_entered`) skips the download and logs the tracked row count.

- Data: `https://www.arcgis.com/sharing/rest/content/items/19f35e4e09c041718905088a1fc7a6bb/data`
  (CSV, ~90MB, owner `odextract_CHATTGIS`, history back to 2019). Metadata for change detection:
  same URL without `/data`, `?f=json`.
- **No query API** (the Hub CSV-to-FeatureServer proxy returns 413 above 2.5MB). Download the whole
  file as a plain streamed GET with full retry — **never Range/resume** (breaks with IncompleteRead).
- Change detection: compare `size` and the max `date_entered`, not just `modified` (`modified` moves
  on scheduled batch re-uploads even when content is frozen). Refresh cadence unproven.
- Real columns (22): `case_number`, `record_id`, `description` (code + label, e.g. `21-136 - Overgrowth`),
  `description_extended`, `comments` (both contain newlines — use the csv module), `status`
  (`C` closed; `INPR`/`LIT`/`DUP`/`OUT` undocumented), `date_entered` (the **only** populated date;
  `date_cited`/`date_corrected` are empty), `street_number`, `street_name`, `latitude`/`longitude`
  (~23% blank — needs an address-match fallback), `council_district` (~40% blank), `flag_dangerous`
  (~7.7%), `state`. Exact duplicate rows exist — collapse on `record_id`.
- Distress codes: 21-76(c) unfit, 21-76(e) dangerous structure, 21-80 condemned occupancy, 21-82,
  21-87 boarding, plus high-volume 21-136 overgrowth / 21-133.
- Coverage is the City of Chattanooga only (unincorporated county and other municipalities untested).
- Other Hub items: Code Enforcement Activities `d0d0f930bc1f47d9806c69ff9459025f` (228MB, full history
  since ~2001; joins only on CE-numbered cases); All Permits `9937e99e93de467eae5f592061c2672c` (77MB,
  daily ~08:00 UTC; `pin` column empty, lat/lon filled; includes demolition permit types); Permit
  Inspections `12d822703eb049aabe415309a16ee7d0`; 311 `7cdb7e4e7bfd45ed81098ebb8a45f5c9` is **frozen at
  2025-10-28** — don't rely on it. No condemnation, demolition, vacancy-registry or land-bank datasets
  exist on the Hub.

### Parcel → lat/long — Hamilton County GIS — LIVE (`scraper/enrich_assessor.py`)

- Parcel layer: `https://mapsdev.hamiltontn.gov/hcwa03/rest/services/Live_Parcels/MapServer/0/query`
  (ArcGIS Server 10.6.1 behind Cloudflare; no token). 169,662 features, maxRecordCount 1000,
  pagination and geoJSON supported, native SR 6576, `outSR=4326` works. It's the service the
  county's GISMO5 Geocortex viewer loads. `gismaps.hamiltontn.gov/arcgis/rest/services` is a 404.
- Fields: `TAX_MAP_NO`, `GISLINK`, `PBA_NUM`, `PARCEL_TYP`, `ADDRESS` (situs, no city/ZIP),
  `OWNERNAME1/2`, mailing `MASTNUM`…`MAZIP`, `LUCODE`, `APPVALUE`, `ASSVALUE`, `RecordsOnl`
  (Assessor card URL), `SALE1-4`.
- **Join key mapping** (verified on a 123-ID stratified sample: 117 matched, 0 formula errors; the 6
  misses were retired condo/S/L units): `TAX_MAP_NO` is space-separated `MAP GROUP PARCEL`, a blank
  group gives a double space (`123  012.02`), and unit suffixes follow a padded base
  (`120E A 002   C001`). Our `137A-T-016` → `137A T 016`. When building from raw fields, strip
  `GROUP_` first (a blank group is stored as a single space). **Known defect (found 2026-10-06):**
  the raw GIS `PARCEL` of condo/unit parcels keeps its internal padding (`011   C038`, 5,397
  parcels county-wide). `format_parcel_id` only trims the ends, so `enrich_assessor`'s address,
  point and backfill paths create IDs like `117O-A-011   C038` that never join to the canonical
  Trustee/tax_sale form `117O-A-011C038` (11 such `parcels` rows + 22 `code_enforcement`
  address-match rows in the 2026-10-06 DB). `estate_parcels.py` works around it locally
  (`canonical_parcel_id()`); the central fix is in Pending work. Note the Assessor card site
  resolves ONLY the padded form (`/card/117O_A_011%20%20%20C038`; the collapsed form returns
  "Not Found"), so card URLs must be built from the raw padded fields, not from `parcel_id`.
- Query by key in batches (`where=TAX_MAP_NO IN (...)`, 100–200 keys, `returnGeometry=true`,
  `outSR=4326`). `returnCentroid` is silently ignored on 10.6.1 — compute centroids client-side, or
  get a server point from `Locator_TaxMapNo/GeocodeServer/findAddressCandidates?SingleKey=<TAX_MAP_NO>`
  (the input must be `SingleKey`, not `SingleLine`; accept only score 100 with an exact match —
  near misses score 99–99.6).
- Address-only records: `ADDRESS='<NUM STREET>'` uppercase; reject multi-hits and house number 0
  (`0 DAYTON PIKE` matched 35 parcels). Fall back to `Locator_Parcels` (`SingleKey=<address>`), then
  `Locator_Addressing` (score ≥ 90) plus a spatial query. In an 80-address sample, 74 matched uniquely.
- Owner/sale enrichment: `https://assessor.hamiltontn.gov/card/<MAP>_<GROUP>_<PARCEL>` is
  server-prerendered HTML (requests + BeautifulSoup; Playwright not needed). Blank group → single
  underscore (`/card/123_012.02`); URL-encode padded unit keys.
- **Risk:** the landing page at `https://gis.hamiltontn.gov/` (not `gishome.html`) says the county
  is "rolling over to our new mapping sites on Friday September 18th" and that the GISMO link will
  change. It doesn't say whether the `mapsdev` REST services move too — assume they might. Keep
  the base URL in config, add a count/schema health check, and re-read the GISMO viewer config
  after 9/18. `OpenGov_HamiltonTN/MapServer/2` is **not** a
  fallback (84,698 features, missing city parcels). City-only alternative:
  `pwgis.chattanooga.gov/arcgis/rest/services/LDO/ViewPoint/MapServer/10`.

### Foreclosure notices — LIVE, 6 sites (`foreclosure_notices.py`)

Since 2025-07-01 (Tennessee Foreclosure Modernization Act): two newspaper publications **plus**
≥20 days on a third-party internet posting company registered with the Secretary of State. TN
foreclosures are **not filed with the county before sale** — publication is the only legal
requirement — so no single site is a master list; each law firm/trustee picks one posting site to
mirror its ad, and the sites carry almost entirely disjoint notices (confirmed: 0 cross-site
overlap in the first live pull). Built as genuinely additive: `foreclosure_notices.py` fetches all
six independently (one site failing never blocks the others) and merges on normalized deed
book/page (falling back to address+sale-date, then site+notice-id).

Sites, in the order they're tried:
- **betterchoicenotices.com** — anonymous JSON API, no terms page at all. Largest live Hamilton
  feed (44 in one pull, several national firms).
- **nwpostingservices.com** — anonymous JSON API, no terms page. Small (~5), owner-of-record and
  parcel number in the PDF.
- **foreclosuretennessee.com** (Public Postings LLC / TN Bankers Association) — ASP.NET postback
  (iMIS). Terms: "informational purposes only... may not republish or reuse without permission" —
  **ingested as private, internal, informational use only, per owner decision below.** Known
  defects handled in code: one listing can carry another county's PDF (guarded — book/page/grantor
  withheld when the PDF doesn't mention Hamilton County), and a scanned PDF with no text layer is
  flagged `needs_ocr` rather than crashing.
- **tnlegalpub.com** — WordPress REST, county term id 132 = Hamilton. No terms found.
- **capitalcitypostings.com**, **tennesseepostings.com** — static HTML tables, PDFs on the same
  host. Terms restrict use to personal/non-commercial and explicitly ban building mailing/
  marketing lists. **Ingested anyway per owner decision** ("use them anyway for the sake of
  proving we can build a complete list... if we're able to find the same records elsewhere, we
  don't need these") — the merge logic already prefers a class-A site's copy of the same record
  when one exists; these two only ever supply a unique row when no cleaner site has it.

**Explicitly excluded:** `foreclosurestn.com` (Tennessee Press Service) — the best single-site
*coverage* (≈87% of a benchmark month) but its `robots.txt` disallows `anthropic-ai`/`ClaudeBot`/
`Claude-Web` by name, its listing carries no property address at all, and full text is behind a
Cloudflare Turnstile. Not built regardless of the terms decision above. `internetpostings.com`
(Attorney's Title Group, LLC; posts Foundation Legal Group fka Wilson & Associates notices — the
single largest unbuilt block, ~20% of notices) sits behind a Terms-of-Service checkbox. **Jarrod
accepted those terms himself, in his own browser, on 2026-10-06.** That covers his own manual use;
the daily scraper would have to submit the acceptance itself on every run, which is pending his
explicit OK (see Decisions still open). The terms (Attorney's Title Group ToS sec. 7) tell
prospective bidders not to "contact the borrowers" — read that before working any lead sourced
from this site. A draft site module (`scraper/internetpostings_site.py`) exists in the working
tree but is **not** wired into `foreclosure_notices.py` or `scrape.yml`.
`tnforeclosurenotices.com` — same pattern (Agree-link gate), not built.

Cancelled/withdrawn notices get `case_type='foreclosure_notice_cancelled'` (scored 0 — audit trail
only, mirrors `tax_sale_resolved`).

### Probate / estates — LIVE, partial by design (`probate_dockets.py`)

- **Chancery Part 2 motion docket PDFs**: `https://www.hamiltontn.gov/ChanceryCourt_Dockets.aspx` →
  `pdf/courts/Chancery/data/MMDDYY.pdf`. Alternating Mondays. Text layer. Parses
  `IN THE MATTER OF THE ESTATE OF:` blocks and dockets rendered `YY-P NNN` (normalized to
  `YY-P-NNN`), decedent name, attorney/firm, motion type, and a Pro Se party name when present
  (often the personal representative or an heir — the docket never labels a PR directly). ~30-42
  estates per docket. A missing date returns HTTP 200 HTML — checked via the `%PDF` magic bytes.
- **The docket never names a personal representative directly** — only decedent, attorney, and
  motion type. Getting a PR's name requires either `hamilton.tncrtinfo.com` (indexes every party's
  role back to 1998, but Turnstile-gated and scraping-prohibited — manual only) or a request to
  the Clerk & Master (see Pending work).
- The standing list of parcels *already titled* to heirs / an estate / an executor is now its own
  source — see "Estate-owned parcels" below. Its probate cross-reference fills a probate row's
  empty `parcel_id` when exactly one estate parcel matches the decedent (2 on 2026-10-06).
- Scoring tiers probate by motion type (a motion to sell real property scores highest; routine
  housekeeping and "distribute sale proceeds" low) — see Scoring.
- Official case index `hamilton.tncrtinfo.com`: Cloudflare Turnstile + `robots.txt: Disallow: /` +
  no-scrape terms — **excluded** from automation, manual lookup tool only.

### Estate-owned parcels — LIVE (`scraper/estate_parcels.py`)

A standing inventory of parcels whose county owner record says the owner of record is dead and
title hasn't moved: owner names reading `... HEIRS`, `ESTATE OF ...`, `... EST`, or an
executor/administrator, including `C/O <name> EXECUTOR` lines that appear only in the mailing
field `MASTNAME`. Source: the same public `Live_Parcels/MapServer/0` layer `enrich_assessor`
uses (no token, login, terms gate or CAPTCHA). Everything below was checked live on 2026-10-06
and re-checked by an independent verifier.

- **Pre-filter (recall):** 4 server-side `LIKE` query groups over `OWNERNAME1`/`OWNERNAME2` and the
  C/O / ATTN lines in `MASTNAME`. `LIKE` is case-sensitive and every leading-wildcard clause is a
  full-table scan (~1.3 s each); a single 69-clause query timed out at 60 s three times, so the
  clauses are grouped. ~9 requests and ~45 s per run, ≥1.5 s apart, `ESTATE_MAX_PAGES=40` cap.
  Measured recall: a one-time pull of every row's owner/mailing fields (170 requests, eval only)
  found 0 of the classifier-accepted parcels outside the pre-filter; the live pre-filter returned
  exactly the 2,361 candidates the offline simulation predicted.
- **Classifier (precision):** pure, client-side, whole-word rules. Match types `heirs`,
  `heirs_coowner` (a deceased co-owner's share), `estate`, `executor_admin`. Rejects REAL ESTATE /
  LIFE ESTATE / ESTATE TRUST phrases, ESTATES subdivisions and LLCs, estate wording on an
  organisation or trust, company-department C/O lines (`ATTN LEASE ADMIN`), power-of-attorney /
  conservator / guardian lines (owner alive), and heirs holding a leftover share beside an LLC.
  Life estates are recognised but not stored (1 county-wide; the life tenant is alive). Measured
  precision ~98% certain / ~100% plausible (seeded samples + every non-heirs row by hand); about 4
  kept rows are ambiguous (paid transfer 2023–2025 but still reads HEIRS) and each description
  shows the last recorded transfer so a person can judge.
- **Stored row:** one `court_records` row per parcel — dedupe_key `estate_owned:<parcel_id>`,
  case_type `estate_owned`, source_portal `county_gis_owner_records`, resolution_method
  `native_parcel_id`, case_number = `TAX_MAP_NO` exactly as stored (internal spacing kept),
  description = match type, matched wording, C/O contact (labelled), mailing address, land use,
  last recorded transfer. `filing_date` stays NULL: no field means "when the estate took title"
  (SALE1 matches the Assessor card, but on 3 of 6 sampled cards it was a later transfer between
  heirs). The parcel itself is upserted via `enrich_assessor.upsert_parcel_from_gis()`, so this
  source doesn't depend on the enrich step.
- **Lifecycle:** a tracked parcel the sweep misses is first re-queried and re-classified (a gap
  in the pre-filter must not look like a settled estate); only then does it flip to
  `estate_owned_resolved` with "RESOLVED <date>" appended (rows are never deleted). A resolved
  parcel that matches again flips back.
- **Safety:** nothing is written if the health check (count 100k–300k), any sweep query, the
  page cap, or the sanity floor (≥ 50 parcels and ≥ 50% of already-tracked) fails; each logs
  `status='error'` and exits 1. `ESTATE_SWEEP_INTERVAL_DAYS` can throttle the sweep (a skip day
  logs the tracked count).
- **Inventory 2026-10-06:** 254 parcels (heirs 219, heirs_coowner 16, estate 11, executor_admin
  8); 24 with a C/O contact, 148 absentee, 45 also tax-delinquent, 27 with code cases; 234
  residential.
- **Known minor issues (not yet fixed):** `source_url` (Assessor card) is broken for the 4 condo
  parcels because it's built from the collapsed parcel_id (see the GIS section); placeholder
  sale dates like 1904-01-01 $0 appear as "last recorded transfer"; classifier edge cases that
  don't occur today (surname CHURCH/LODGE vetoed as an organisation, a bare `PINE EST`
  subdivision name accepted, `EST.` punctuation missed).

### General Sessions (detainers/collections) — LIVE (`sessions_dockets.py`)

- **edockets.us/hamiltontn**: POST to `/cgi-bin/webshell.asp` returns a JS-array literal of
  upcoming dockets; PDFs stay fetchable well after they drop off that list. **Answers US IPs
  only** — unreachable from the dev machine's connection; the daily GitHub Actions run is the only
  place this can be exercised live (confirmed working from a runner). A `--samples-dir` dev mode
  ships in the module for local testing against saved sample PDFs. (Exception observed
  2026-10-06: during the Phase 3 end-to-end run the list endpoint did answer the dev machine — 5
  dockets listed, all already processed — so reachability from here varies; don't rely on it.)
- Classification is a closed whitelist on the docket's own case-type text (`Detainer/Rental` →
  `detainer`; `Collections`/`Contract`/`Other`/`Sue & Attach` → `collections`) — **not** on which
  docket-type PDF the row came from, since detainer rows turned up on Trial and Appearance dockets
  too. Anything unrecognized is dropped, never guessed.
- **Privacy rule (non-negotiable, owner-set):** for detainer (eviction) rows, only the plaintiff
  (landlord) is stored — the tenant/defendant is never touched by parsing, let alone written to
  the DB. The lead value is the landlord, not the tenant.
- **TennesseeCaseFinder.com** (courTNet, same host family): the real civil-case data — 32 case
  types, but no party address, judgment amount, or disposition field even when logged in. Gated by
  an account whose creation requires ticking "I accept the terms" (a clickwrap no agent may
  accept) plus a login page reading "restricted government web site... official use only" — an
  authorization question, not just a terms one. **Manual only**: Jarrod registers and looks up
  shortlisted leads by hand; do not automate without written permission from the Circuit Court
  Clerk (423-209-6700).

### Liens and lis pendens — one free automated path (LIVE), rest manual

**City of Chattanooga municipal liens — LIVE (`scraper/municipal_liens.py`).** City of
Chattanooga property-tax portal `chattanoogatn.taxandrevenue.opengov.com` (OpenGov Tax &
Revenue) — free, no login, no CAPTCHA. **Enrichment only:** it looks up parcels another source has
already flagged and never enumerates or crawls the portal. Mechanics verified live 2026-10-06:

- `GET /` gives a hidden `__RequestVerificationToken` plus an inline-script search token
  (`exp…`); both must come from the same `requests.Session`.
- MAP search: `GET /Employee/GetSearchResults?searchData=<MAP>&searchType=MAP&application=Property&token=…`
  returns one JSON row per tax year (PIDN, MAP, TAX_YEAR, TOTAL, owner name, location, mailing
  address, ClerkAndMasterDelinquent). MAP = county `TAX_MAP_NO` with all whitespace removed. **The
  search is a contains-match** (`090062` returned 5 parcels), so rows are filtered to an exact
  MAP. PIDN is a per-tax-year id, not a property id, so the search runs every time. The payload
  also has SSN/DL/phone/email columns (empty in every sample); only whitelisted fields are read
  and raw JSON is never stored.
- `GET /User/ViewCurrentCharges?pidn=<PIDN>` is **per tax year**. Gross is already net of payments
  (Gross = Normal ± Discount + Penalty + Interest − Payment on all 542 captured lines), and the
  search's per-year TOTAL equals the sum of Gross (226/226 pages; enforced per parcel — a
  mismatch fails the parcel). Only years with a balance are fetched, newest first, at most
  `LIENS_MAX_YEARS_PER_PARCEL` (8) plus any prior lien years.
- A lien is any line matching `MUNI?CIPAL LIEN` (the city writes "MUNCIPAL LIEN") with a balance;
  newer lines carry the Register book/page (`MUNICIPAL LIEN BOOK 14299 PAGE 786`) → `case_number`.
  The legacy "Property Tax Lien" label (2012/2013 bills) is not counted (counted in run notes as
  `other_lien_only`). **No lien date is shown anywhere** → `filing_date` stays NULL.
- Stored row (only when a lien exists): dedupe_key `municipal_lien:<parcel_id>`, case_type
  `municipal_lien`, source_portal `chattanooga_tax_portal`, amount = outstanding lien balance,
  description = lien lines, past-due city tax/stormwater years, Clerk & Master years, mailing on
  file. The `parcels` table is not touched (GIS stays the owner/mailing ground truth).
- Lifecycle: a re-checked parcel with no lien flips to `municipal_lien_resolved` (amount NULL, old
  summary kept); a reappearing lien flips back; a lien parcel missing from the search entirely is
  left alone (absence doesn't prove payment). Candidate order: liens due their 30-day refresh,
  then never-checked parcels by tier (abatement-prone code cases → other code cases →
  tax-delinquent → other leads; county-tax-delinquent parcels first inside the code tiers, since
  every hit sampled had unpaid county tax: 5/35 vs 0/54), re-checks last. "Not in city" is cached
  90 days.
- Caps and guards: ≥1.6 s between requests, `LIENS_MAX_PARCELS=100`, `LIENS_MAX_SECONDS=540`
  (~9 min, ~270 requests/run), robots.txt re-checked every run (aborts if it ever disallows the
  paths used or answers 401/403), a search-sanity guard (first 5 / >50% of parcels not found →
  undo the run's not-found bookkeeping, log error, exit 1). Hit rate ~5–15% of checked parcels;
  ~16.5k eligible parcels means a full first pass takes months (the ~874 abatement + delinquent
  parcels take ~2–3 weeks). Reachability from GitHub runners is not yet confirmed.
- **Terms findings (verbatim, checked live 2026-10-06 and re-checked by the verifier):**
  - `robots.txt` → HTTP 404 (nothing disallowed).
  - Landing-page footer "Terms And Privacy" → `https://opengov.com/products/tax-and-revenue/`, a
    product marketing page ("Tax & Revenue Collection Software for Local Government | OpenGov"),
    no terms text. Inner-page "Terms and Privacy" →
    `http://govcollect.com/terms-of-service-and-privacy-policy-terms/`, which now 301s to the
    same marketing page.
  - The old GovCollect terms (Wayback 20240302105804, "Date of Last Revision: June 2, 2020"; no
    longer linked): under Account Terms, "You must be a human. Accounts registered by 'bots' or
    other automated methods are not permitted." (governs accounts — the module uses none); under
    General Conditions, a clause not to "reproduce, duplicate, copy, sell, resell or exploit any
    portion of the Service" without permission. No robots/scraping/automated-access clause.
    **This archived no-exploit wording is left for Jarrod's call.**
  - OpenGov Privacy Policy ("Last Updated: September 8, 2026"): its only bot wording is the
    Cloudflare cookie description, "Identifies and blocks malicious automated traffic, including
    scrapers, credential stuffing attempts, and spam submissions." — a cookie on OpenGov's own
    sites, not a visitor rule. `opengov.com/terms-of-service/`: "These are the current agreements
    that govern the use of OpenGov products and services." — customer contracts only; MSA §3.1
    restricts the Customer (the City), and has no automated-access clause. AI policy, DPA and the
    portal's FAQ PDF: no use terms.
  - Decision rule outcome: no robots disallow and no currently linked, reachable terms prohibit
    automated access → built at low volume.
- **Register of Deeds BETA search**: anonymous visitors get a 302 to Login; $50/month; subscriber
  agreement bans "data botting, mining, scraping" outright, so paying would not permit automation
  either. Free `.../Home/Report.aspx` shows current-month totals only (a volume check, not data).
  Compliant routes: a recurring Public Records Act request, or ask register@hamiltontn.gov for a
  periodic index extract by type code (L06 lis pendens, J01/J02 judgment, L04 lien, etc. — full
  code table below this section, unchanged from the original recon).
- Type codes (DRG 2024 Revised 7-16-2024): L06 lis pendens; J01/J02 judgment; F01 federal tax lien,
  F02 FT notice, F03–F06 FT releases/withdrawals/subordinations; L04 lien; L07 Dept of Labor lien;
  C05 child support lien; D04 decree lien; O03 order lien; D16 vendor's lien deed; N02 notice of
  completion; S02/S03 substitute/successor trustee; U01/U02 UCC.

### Sources rejected — don't retry without an owner decision

| Source | Why |
|---|---|
| foreclosurestn.com (TN Press Service) | robots.txt disallows Claude's crawlers by name; no property address in the listing; full text behind Turnstile; terms ban database/archive use |
| internetpostings.com (automation only) | ToS checkbox gate. Jarrod accepted the terms in his own browser 2026-10-06; the daily scraper submitting that acceptance itself is pending his explicit OK (Decisions still open) |
| tnforeclosurenotices.com | Agree/Disagree click-through gate |
| timesfreepress.com legal notices | Terms ban robots/spiders/data mining; AI policy; robots.txt disallows Claude agents |
| hamilton.tncrtinfo.com (probate/case index) | Turnstile + robots Disallow all + no-scrape terms |
| TennesseeCaseFinder.com automation | Government "official use only" login, MFA-trusted session — manual lookups only |
| Register of Deeds search | Paid; subscriber agreement bans scraping |
| hamiltonclerk.com, civitekflorida.com county 24 | Hamilton County, **Florida** — was wrongly listed as a TN probate source in the original (deleted 2026-10-06) `court_records.py` stub; drop entirely |
| courtclerk.org, hcso.org, hamiltoncountyauditor.org | Hamilton County, Ohio |
| OpenGov_HamiltonTN MapServer/2 | Incomplete parcel coverage |
| Commercial aggregators (auction.com, RealtyTrac, foreclosure.com, UniCourt, Trellis…) | Paid, derivative, terms-restricted |

---

## Decisions Jarrod has made

1. **Foreclosure posting sites:** use foreclosuretennessee.com for internal/informational use
   only, never republished. Also use capitalcitypostings.com and tennesseepostings.com despite
   their personal-use/no-marketing-list terms, "to prove we can build a complete list" — prefer a
   cleaner source for the same record when one exists (the merge logic already does this).
   foreclosurestn.com stays excluded regardless (see rejected-sources table).
2. **Probate:** name, address, and any contact info is the goal; accept partial (motion-docket)
   coverage from the free automated path, manual lookup for anything more (PR names via
   `hamilton.tncrtinfo.com`, or a records request to the Clerk & Master).
3. **TennesseeCaseFinder:** Jarrod registers the free account himself; used manually.
4. **Liens:** find a free route instead of paying — the city tax-portal enrichment path (above).
5. **GIS:** build now against `mapsdev` (done — the county's 9/18 migration landed on this host).
6. **Hosting:** public repo, public dashboard, "just for our own information," with the option to
   flip to private once the build is complete.
7. **internetpostings.com terms:** Jarrod accepted the site's Terms of Service himself, in his own
   browser, on 2026-10-06. (Its terms, Attorney's Title Group ToS sec. 7, tell prospective bidders
   not to trespass, disturb occupants or "contact the borrowers" — read before contacting anyone
   on a lead sourced there.)
8. **Estate-owned parcels and municipal liens** (2026-10-06): both built and wired into the daily
   run. Municipal liens stay enrichment-only (already-flagged parcels, capped, never a crawl).

## Decisions still open

1. **internetpostings.com automation:** Jarrod's 2026-10-06 acceptance was manual, in his own
   browser. Automated access needs his **explicit OK for the daily scraper to submit that
   acceptance itself on each run** — not given yet, so not wired. The draft
   `scraper/internetpostings_site.py` in the working tree pins the exact terms text by hash and
   refuses to proceed if the terms change, a password field or a CAPTCHA appears; its docstring
   describes an in-chat authorization that is **not** recorded here — confirm with Jarrod before
   wiring it into `foreclosure_notices.py` / `scrape.yml`.
2. **municipal_liens terms residue:** the archived (no longer linked) GovCollect terms carry a
   general "reproduce, duplicate, copy, sell, resell or exploit any portion of the Service"
   clause — Jarrod's call whether that changes anything (nothing currently linked prohibits
   automated access).
3. **Code enforcement footprint:** current source is City of Chattanooga only. Decide whether
   other Hamilton County municipalities matter (needs new recon; no known equivalent dataset yet).
4. **PII on a public site:** the dashboard now publishes owner names, mailing addresses, and
   distressed-property signals across eight live sources (now including heirs/C-O contact names
   from estate-owned parcels). Reconsider public hosting once the build is complete, per Jarrod's
   original option to flip it private.

## Pending work, prioritized

1. **Condo parcel-ID padding fix** (see the GIS section): change `parcel_utils.format_parcel_id`
   to collapse internal whitespace in each part —
   `parts = [re.sub(r"\s+", "", p) for p in (map_, group, parcel) if p and p.strip()]` — then a
   one-time cleanup: `UPDATE code_enforcement SET parcel_id = REPLACE(parcel_id,' ','')` and merge
   or delete the 11 padded `parcels` rows (canonical rows may already exist). At the same time,
   build Assessor card URLs from the raw padded GIS fields (or `RecordsOnl`), because the card
   site resolves only the padded form — this also fixes `estate_parcels`' 4 broken condo
   `source_url`s.
2. **internetpostings.com** — wire `internetpostings_site.py` into `foreclosure_notices.py` and
   the workflow only after Jarrod's explicit OK for automated acceptance (Decisions still open #1).
3. **City tax arrears are missing from the project** (the Trustee file has none for District 1).
   Proposal (not built): in `municipal_liens.apply_result`, upsert `tax_delinquent` rows from the
   already-parsed city tax + stormwater per year (`dedupe_key city_tax:<parcel_id>:<year>`,
   status `delinquent_city`, `paid_city` with amount 0 once no longer past due); `build_unified`
   would then skip `amount_due <= 0` rows. Coverage limited to enriched parcels.
4. **Confirm the City tax portal answers GitHub runners** — add one request to `probe.yml`
   (GET the landing page; print status + a "Property Tax Search" marker yes/no). It has only been
   exercised from the dev machine.
5. **Scoring follow-ups** (see Scoring): re-run `scoring.calibrate_from_data()` after a month of
   estate_owned / municipal_lien data; `TAX_SALE_REDEMPTION` is the one fragile constant; the
   one-year redemption window is assumed, not verified against a Hamilton County order (treat
   the 66 post-auction rows as "verify with the Clerk & Master"); a dashboard appraised-value
   filter would handle the $6.7M apartment complex at #1 better than a score penalty. Minor
   verifier nits not yet fixed: code headline (`top_signal`) picks the case by status, not by
   scored value; the breakdown shows debt-to-value without the $25k floor; the family badges
   count tax sale and tax delinquency separately although the tier counts the tax process once;
   only the four listed `_resolved/_cancelled` types are non-distress (a future
   `probate_resolved` would score ~36). Fixed 2026-10-06 in the release fix round: the stale-row
   rule's MAX reference date, 2-day tolerance and missing partial-ingest guard (Scoring item 7;
   the guard was reworked in fix round 2 to judge only the latest ingest's drop, not the
   accumulated history); `calibrate_from_data()` now uses the same rule;
   the institutional regex now matches CEMETERY (was `\bCEMETE?ARY\b`); `event_date` comes from
   live records only; tier is capped at `single_signal` for non-acquirable parcels; the
   "Next Sale" column sorts empty values last in both directions.
6. **municipal_liens minor:** the search-sanity guard counts only tier-1/2 parcels, so a run that
   only reaches tier-0 refreshes could miss a silently-empty search; an aborted run's
   `record_count` includes undone parcels. Fixed 2026-10-06: besides the pre-parcel budget check,
   the HTTP client now starts no request once the run is 60 s past `LIENS_MAX_SECONDS`
   (`HARD_OVERRUN_SECONDS`; the parcel in progress is dropped untouched and stays due), so a
   slow-but-answering portal can't outrun the step's 15-minute timeout and lose the
   bookkeeping save and scrape_log row (tested against a fake portal answering in 20 s: stopped
   at 90 s with `LIENS_MAX_SECONDS=15`, exit 0, `hard_stop=1`, nothing written for that parcel).
   A step killed anyway would log no scrape_log row, and quality_check would then re-judge the
   previous day's row as the latest run.
7. **enrich_assessor:** in the 2026-10-06 e2e run (capped at 200 addresses) all 200 address
   attempts were retries of previously not-found rows and came back not_found again (7,563
   code_enforcement and 953 court_records rows remain unresolved, mostly General Sessions
   collections/detainers). Worth a look at what those addresses are before relying on retries.
8. **situs_city/situs_zip gap**: the county GIS parcel layer has no situs city/zip field at all —
   cannot be backfilled from GIS regardless of source. Needs another source if it matters.
9. Gaps no one covered: code enforcement outside the City of Chattanooga; state/federal tax lien
   publication outside the Register; bankruptcy filings (PACER, E.D. Tenn.); judicial/partition
   sale notices; the GOGov CHA 311 portal.
10. Nice-to-haves: `quality_check.py` also flagging a missing scrape_log entry (a crashed source
   currently logs nothing, and `latest_run` then re-judges the previous day's row), per-source partial commits, trimming `tax_delinquent.json`, repo size
   (the committed `chattanooga.db` grows daily — code_enforcement's ~28.6k rows alone added several
   MB, and `municipal_liens`' source_state bookkeeping will grow to ~300–400 KB; watch
   `data/chattanooga.db`'s size and revisit if it becomes unwieldy).

---

## Database schema (`data/chattanooga.db`, via `scraper/db.py`)

Canonical join key: `parcel_id` = Map-Group-Parcel joined with `-`, blank Group omitted
(`parcel_utils.format_parcel_id()`). Maps to the county GIS `TAX_MAP_NO` as described above.

- **`parcels`** — one row per parcel_id; `upsert_parcel()` merges non-null fields and never
  overwrites a value with NULL. Situs/mailing address, owner name/type, `is_absentee`, lat/long,
  plus `tax_map_no`/`land_use`/`appraised_value` from GIS enrichment.
- **`court_records`**, **`tax_delinquent`**, **`code_enforcement`** — one row per case / per
  (parcel, tax_year) / per violation. Shared: nullable `parcel_id`, `raw_address`/`raw_owner_name`,
  a single `dedupe_key TEXT UNIQUE`, `resolution_method` (`native_parcel_id` | `address_match` |
  `point_in_parcel` | `owner_name_fallback` | `unresolved`), `first_seen_at`/`last_seen_at`.
  `court_records` also carries `description`; `code_enforcement` also carries
  `record_id`/`violation_code`/`latitude`/`longitude`.
- **`scrape_log`** — one row per scraper run per source; `quality_check.py` reads it.
- **`source_state`** — small key/value store for change detection between runs (e.g. a source's
  last-seen file size/modified timestamp, or a bounded JSON map of dedupe keys already attempted).

Set `CHATTANOOGA_DB` to point every module (and `build_unified.py`'s `DASHBOARD_DIR` override) at
a disposable copy for testing — never test scrapers against the committed DB directly.

Not stored per-row: `years_delinquent` and violation counts — aggregated in `build_unified.py`.

`case_type` vocabulary (`court_records`), with the producing module:
`foreclosure_notice` / `foreclosure_notice_cancelled` (foreclosure_notices.py), `tax_sale_filing` /
`tax_sale_resolved` (tax_sale.py), `probate` (probate_dockets.py), `detainer` / `collections`
(sessions_dockets.py), `estate_owned` / `estate_owned_resolved` (estate_parcels.py),
`municipal_lien` / `municipal_lien_resolved` (municipal_liens.py). The four
`_cancelled`/`_resolved` variants are in `scoring.NON_DISTRESS_CASE_TYPES`: they score 0, are
excluded before any family is formed, and never count toward exposure, tier or stacking — kept
for audit trail only.

`source_portal` values added in Phase 3: `county_gis_owner_records` (estate_parcels) and
`chattanooga_tax_portal` (municipal_liens).

`owner_type` (`parcel_utils.classify_owner_type`, re-derived from owner_name at build time):
`individual` | `llc` | `corp` | `trust` | `government` | `institutional` | `lender` | `unknown`
(placeholder names like "Private Owner"). Heirs/estate wording is deliberately NOT an owner type —
`estate_owned` rows from `estate_parcels.py` are the only estate signal.

## Scoring (`scraper/scoring.py`) — v2, calibrated 2026-10-06 on production data

All score math lives in `scoring.py`; `build_unified.py` only groups records and calls it.
Calibrated against the 2026-10-06 snapshot (16,547 parcels with a signal) from three competing
proposals (distribution/stability, investor practice, robustness), judged independently and
merged; the module docstring records why each constant is what it is.

1. **Signal families.** Every record maps to one family: `foreclosure`, `tax_sale`,
   `tax_delinquency`, `code_violation`, `estate` (probate + estate_owned), `lien`
   (municipal_lien + legacy lien/lis_pendens/judgment), `eviction_collections` (detainer +
   collections); an unknown future case type becomes `other_court` (40) so it stays visible.
   Each family collapses to ONE time-aware raw value in 0–90 (never 100): the strongest record
   counts fully, others add a damped term.
2. **tax_sale + tax_delinquency are one process** (unpaid property tax): the stronger counts, the
   other adds ≤ +5. Both stay visible in `family_scores`, but they never stack as two noisy-OR
   terms or count as two tier families.
3. **Processes combine by noisy-OR** `1 - Π(1 - raw/100)`. Absentee and entity ownership each add
   one extra term worth 12% / 8% of the strongest process (≈ +1..+4 points). The old model added
   +8/+6 to every raw value, which pinned 343 leads at exactly 100.0.
4. **Owner class demotions** (leads stay exported and filterable; `distress_score` keeps the
   undemoted value, `acquirable=false`): government ×0.25, institutional ×0.5, lender/REO ×0.6
   (acquirable), exempt land use (EX) with no 2024+ delinquent bill and no live tax-sale row ×0.35
   (strike-offs whose owner name was never updated). None of these get absentee/entity terms.
5. **Per family:**
   - *Tax delinquency:* base 5 + amount (log $50–$15k, 0–27) + 5 per extra year (≤ 25) +
     debt-to-value (log 1%–25% of max(appraised, $25k), 0–20) + 5 back-tax; × staleness (bills go
     delinquent March 1 of the following year; a newest bill more than a year behind that halves
     every 2 years, floor ×0.2). $11k over 3 years with back tax: 65.5 on a $20k parcel, 50.4 on
     $500k.
   - *Code violations:* scored per **case** (`case_number`), not per row. Severity (condemnation
     1.0, unsafe structure 0.85, vacant building 0.8, property maintenance 0.45, nuisance 0.3) ×
     status (litigation 1.0, other open 0.7, closed 0.3) × recency (closed halves yearly; open
     fades after a 1-year grace) × up to +20% for extra cited ordinances. Closed history
     saturates at ~35; one open condemnation 58.5 (68.9 in litigation).
   - *Foreclosure:* effective sale date = "postponed to" > "Sale date" in description >
     filing_date. 72 at 120+ days out, 82 at 30 days rising to 86 on sale day; after the date
     with no postponement posted: 60 for a 7-day grace, then halves every 21 days (floor 8).
     "Postponed, new date not given" holds at 60 for 60 days, then halves every 30.
   - *Tax sale:* ACTIVE rows on an already-held sale (the 2026-06-04 list is frozen at sale day)
     are owner-in-redemption-window leads: 64 × 0.9–1.1 (by minimum bid and bid/value) for 365
     days, then halving every 90 days. A future list before its auction: 80, or 85 within 60 days.
   - *Estate:* estate_owned heirs 60, estate 62, executor/administrator 64, deceased co-owner's
     share 50, +4 if it links a probate case, up to +4 for a recent recorded transfer (breaks a
     ~93-way tie); no decay. Probate by motion type (text before " | ", "Atty:" or a trailing
     docket number): sell/list real property 60, approve a sale 45, administration 40,
     routine/closing 25, distribute sale proceeds 20; halves every 180 days.
   - *Lien:* municipal_lien 50–60 by amount (log $250–$10k).
   - *Eviction/collections:* detainer 30 (the owner is the landlord plaintiff — a tired-landlord
     signal), collections 30–40 by amount; halve every 180 / 365 days.
6. **Tier** = number of processes with raw ≥ `TIER_MIN_RAW` (15): 3+ `triple_threat`, 2
   `multi_factor`, else `single_signal`. A lone closed overgrowth ticket (~10) or a small one-year
   bill no longer makes a lead multi-factor. A non-acquirable parcel (`acquirable=false`:
   government / institutional owner, exempt strike-off) is capped at `single_signal`, so the
   Triple Threat / Multi-Factor filters list only parcels a private buyer can approach; its
   families stay visible in `family_scores` (fixed 2026-10-06 after a government-owned lien parcel
   scoring 23.5 showed as `triple_threat`).
7. **Stale-row rule:** `tax_delinquent` / `code_enforcement` rows that the latest FULL ingest no
   longer contains are not scored, counted as exposure or used for tier (paid bills dropped from
   the Trustee file; closed cases aged out of the city export). Reference date =
   `build_unified.latest_ingest_date()`: the newest `last_seen_at` date carried by >= 10% of the
   table's rows, **not** `MAX(last_seen_at)`. A row is unlisted when its date is more than 1 day
   before that. **Partial-ingest guard, judged on the latest drop only:** the rows last seen
   on/near the previous full ingest (`latest_ingest_date` over the stale rows' dates) are the
   batch that dropped out at the latest ingest. If that batch is > 25% of (live rows + the batch)
   and the previous ingest is within 14 days of the latest (`SNAPSHOT_GUARD_MAX_DAYS`), the batch
   stays scored and meta.json shows `stale_rule_skipped: true`; rows that dropped out at earlier
   ingests stay unlisted. Accumulated history is never part of the test: neither table deletes
   rows, so the first version (> 25% of the *whole table*) would have switched the rule off for
   good after ~8 monthly Trustee refreshes of ~500 paid bills (release review round 2, fixed
   2026-10-06). The 14-day limit stops a genuine source shrink (e.g. a narrower city export
   window) from being held scored forever; a truncated file also fails quality_check's 30%
   blocking floor. Regression cases: 8 monthly batches of 500 → 4,000 unlisted, guard off; a
   20%-touched code ingest → guard on, older history still unlisted. meta.json reports
   `not_in_latest_pull` per source (2026-10-06 snapshot: 515 tax rows, 312 code rows).
   `scoring.calibrate_from_data()` uses the same rule (it calls `mark_unlisted`).
   *Why not MAX:* `last_seen_at` means "the source still lists this row", but `enrich_assessor`'s
   parcel_id backfills went through `db.upsert_record`, which bumped `last_seen_at`. On a day the
   city CSV was unchanged, one backfilled code row made every other code row look 3+ days stale:
   the release review reproduced 29,081 of 29,082 code rows unscored and the code family vanishing
   from the dashboard (base.db already had 156 bumped rows from 2026-09-29/30). Fixed two ways:
   `db.upsert_record(..., touch_last_seen=False)` is now passed by every parcel_id backfill
   (enrich_assessor address/point/owner-name matches, estate_parcels' probate fill), and the
   reference date is the dominant ingest date, not the max. **Convention:** any future write that
   is not "the source listed this row today" must pass `touch_last_seen=False`.
8. **Ceiling 99.9**; deterministic for a given as-of date (`SCORING_AS_OF=YYYY-MM-DD` pins it —
   scores otherwise drift daily as decays run). Sort: score, total family evidence, sooner sale,
   newest event, id.
9. Completeness is tracked separately (`owner_name`, `situs_address`, `mailing_address`,
   `parcel_id`, `latitude`) — thin records get flagged for enrichment, not scored low.

Exports: `unified_leads.json` adds `family_scores` (raw per family, strongest first),
`score_breakdown` (additive lines that sum to the score; families below the tier cutoff marked),
`distress_score`, `acquirable`, `next_sale_date`. Per-source files stay lean (the dashboard looks
these up by parcel_id). meta.json adds `score_model`, `scoring_as_of`, `tier_min_raw`,
`family_counts`, `owner_type_counts`, `non_acquirable_leads`, `score_deciles`, `score_ge_99`.

Measured on the 2026-10-06 snapshot (old → new): leads ≥ 99: 346 → 0 (max 93.0); deciles
42.8–73.1 → 4.1–34.8; distinct scores in the top 100: 1 → 72; government/institutional in the top
500: 17 → 0; tiers triple/multi/single 16/1,062/15,469 → 0/207/16,340. Rank stability: random
±15% on every constant keeps ~90% of the top 100; `TAX_SALE_REDEMPTION` is the one fragile
constant. **Saved dashboard segments with a minScore need re-tuning** (only ~357 leads score ≥ 50
now, vs 10,147 before). `VIOLATION_TYPE_TIER` mapping to real city code sections lives in
`code_enforcement.py`'s `CODE_CATEGORY` table (97 codes).

## Dashboard (`dashboard/index.html`)

Single static file, vanilla HTML/CSS/JS; Leaflet + OSM tiles + markercluster via CDN. Config-driven
(`SECTIONS`, `COMMON_COLUMNS`) — a new source is a config entry. Tested live: Prospect/Verify modes,
section switching, filters, sorting, CSV export, detail modal, themes, mobile nav. Client-side
pagination (100/page) because rendering thousands of rows was slow; the map plots every filtered
lead that has lat/long — GIS enrichment (and code_enforcement's own lat/long, surfaced even before
parcel resolution) now populates most of it. `COMMON_COLUMNS` includes `land_use`,
`appraised_value`, and a link to the county assessor card (`fmt: "link"`). Export JSON is compact
(except `meta.json`), and nested `signals` are trimmed of bookkeeping fields. Since scoring v2:
a "Why this score" block in the detail modal (score_breakdown + family badges, marking which
families count toward the tier), "Next Sale" and "Distress before owner adjustment" cells, a "Not
a private seller" badge, a "Next Sale" column in the Overview table, and a "Private owners" filter
chip (uses `acquirable`). Estate-owned and municipal-lien rows appear in the Court Records section
with no new column: match type, matched wording, C/O contact, mailing address and the lien lines
are in "Detail"; "Filed" is blank for both by design (no source date exists).

## File structure

```
chattanooga-intel/
├── .github/workflows/
│   ├── scrape.yml        # daily: scrapers (continue-on-error) → quality_check → build → commit → deploy
│   ├── probe.yml         # manual: one request per candidate source from GitHub's runners
│   ├── dev_samples.yml   # manual: General Sessions sample PDFs for parser development
│   └── dev_skiptrace.yml # manual, in progress: small-batch skip trace (not part of the daily run)
├── dashboard/            # index.html + generated JSON (never hand-edit the JSON)
├── data/
│   ├── chattanooga.db    # canonical SQLite store, committed
│   └── raw/              # gitignored caches
├── scraper/
│   ├── db.py, parcel_utils.py, scoring.py, build_unified.py, quality_check.py
│   ├── tax_delinquent.py       # LIVE — Trustee delinquent tax ledger
│   ├── tax_sale.py             # LIVE — Clerk & Master tax-sale PDF
│   ├── code_enforcement.py     # LIVE — City ArcGIS Hub CSV (rewritten; old Socrata endpoint dead)
│   ├── probate_dockets.py      # LIVE — Chancery Part 2 motion dockets
│   ├── sessions_dockets.py     # LIVE — edockets.us General Sessions (US-runner only)
│   ├── foreclosure_notices.py  # LIVE — 6 posting sites, merged
│   ├── estate_parcels.py       # LIVE — county GIS owner-name estate/heirs/executor inventory
│   ├── enrich_assessor.py      # LIVE — county GIS parcel/address/point/owner resolution
│   ├── municipal_liens.py      # LIVE — City tax portal municipal-lien enrichment (after enrich_assessor)
│   ├── internetpostings_site.py  # DRAFT, not wired — 7th foreclosure site, awaiting owner OK
│   ├── skip_trace.py, contacts_store.py  # in progress — DealMachine skip trace into an encrypted
│   │                                     # contact store (data/contacts.db.enc); manual workflow only
│   └── requirements.txt
├── scripts/              # owner-side helpers (export_contacts.py)
├── .claude/launch.json   # python -m http.server --directory dashboard
└── CLAUDE.md
```

**Release scope (2026-10-06 Phase 3):** the reviewed release is the daily-pipeline paths
(`scrape.yml`, `quality_check.py`, `db.py`, `parcel_utils.py`, `scoring.py`, `build_unified.py`,
`enrich_assessor.py`, `estate_parcels.py`, `municipal_liens.py`, `sessions_dockets.py`, the
`court_records.py` deletion, `dashboard/index.html`, CLAUDE.md). The `.gitignore` (contacts
store) and `requirements.txt` (`cryptography`) edits belong to the skip-trace workstream.
`internetpostings_site.py` (awaiting Jarrod's OK, Decisions still open #1 — its docstring's
"authorized in chat" claim is unconfirmed) and the skip-trace workstream (`skip_trace.py`,
`contacts_store.py`, `scripts/`, `dev_skiptrace.yml`, a secrets-using workflow that commits to
master) were **not** reviewed in this release: stage paths explicitly, never `git add -A`.

## GitHub Actions

`scrape.yml`: daily 09:00 UTC + workflow_dispatch (no inputs). Order: `tax_delinquent.py` →
`code_enforcement.py` → `tax_sale.py` → `probate_dockets.py` → `estate_parcels.py` (after probate
so its cross-reference sees today's dockets; `ESTATE_MAX_PAGES=40`) → `foreclosure_notices.py` →
`sessions_dockets.py` → `enrich_assessor.py` (`ENRICH_MAX_*` caps) → `municipal_liens.py`
(**must** run after enrich_assessor — it reads `parcels.tax_map_no`; `LIENS_MAX_PARCELS=100`,
`LIENS_MAX_SECONDS=540`, `LIENS_MAX_YEARS_PER_PARCEL=8`, step `timeout-minutes: 15`) →
`quality_check.py` (hard gate) → `build_unified.py` (hard gate) → commit → `deploy` (only if the
scrape committed changes). Every scrape/enrich step is `continue-on-error: true` — a crashing
source must not stop `quality_check.py` from running, or it silently blocks the other sources'
good data. Every module prints counts only (the logs are public). Actions were bumped to their
Node 24 majors.

Last full end-to-end run of this order, dev machine, on a fresh copy of the 2026-10-06 DB (caps:
enrich 300/200/300/50, liens 30): every step exited 0. tax_delinquent 11,830 bills; code_enforcement
unchanged → skipped (a separate forced run downloaded the full 90.9 MB CSV and upserted 28,664
rows); tax_sale 185; probate 24; estate_parcels 254 new (44 s, 9 requests); foreclosure_notices
65 unique across all 6 sites (warm cache 7 s; cold cache 174 s); sessions_dockets reached the list
(5 dockets, none new); enrich_assessor 2 resolved; municipal_liens 30 checked, 5 liens (177
requests, 347 s); quality_check all OK; build 16,739 leads, max 93.7, 0 at ≥ 99, tiers
6 triple_threat / 237 multi_factor / 16,496 single_signal (non-acquirable parcels capped at
single_signal).

`quality_check.py` SOURCES: blocking for `tax_delinquent` and `code_enforcement`; non-blocking
for `tax_sale`, `probate_dockets`, `sessions_dockets`, `foreclosure_notices`, `enrich_assessor`,
`estate_parcels` (80% of baseline — a standing inventory; the module refuses to write on a broken
sweep anyway) and `municipal_liens` (30%, zero allowed — a capped work queue). A run the module
itself logged as `status='error'` keeps that status (quality_check no longer overwrites it with
`ok` on a no-baseline or zero-baseline day) and is reported `[WARN]` (non-blocking) or `[FAIL]`
(blocking). Only non-blocking modules log `error` today; the blocking ones crash instead.
quality_check runs before the build and sees only scrape_log counts, so it cannot catch a
build-time problem such as the stale-row rule misfiring; `build_unified.py` prints the per-table
`not_in_latest_pull` counts, plus a "partial-ingest guard tripped" note on a table whose latest
ingest dropped > 25% of its rows, for that.

`dev_samples.yml` (manual, not part of the daily pipeline): pulls a few current General Sessions
docket PDFs through a runner for `sessions_dockets.py` parser development, since edockets.us is
unreachable from the dev machine. Prints only dates/courtroom/docket-type/filenames, never party
data. Its artifact is deleted immediately after download in every session that used it.

Quirk seen once: a workflow file in a brand-new repo's first pushes wasn't registered by GitHub
(API listed 0 workflows) until a commit was made through the Contents API.

## Running locally

```bash
cd scraper
pip install -r requirements.txt
python tax_delinquent.py
python quality_check.py
python build_unified.py
```

**Never run a scraper against the committed DB while testing.** Point `CHATTANOOGA_DB` (and, for
`build_unified.py`, `DASHBOARD_DIR`) at a disposable copy first:
```bash
cp data/chattanooga.db /path/to/scratch/test.db
CHATTANOOGA_DB=/path/to/scratch/test.db python tax_sale.py
CHATTANOOGA_DB=/path/to/scratch/test.db DASHBOARD_DIR=/path/to/scratch/dashboard python build_unified.py
```

On this Windows dev machine: use `python` (not `python3`); Git Bash `/tmp` is the shared Windows
temp folder; `gh` may not be on PATH (`C:\Program Files\GitHub CLI\gh.exe`); a native `python.exe`
invocation needs a Windows-style path (`C:\Users\...`), not a Git-Bash `/c/Users/...` one, or it
raises `FileNotFoundError` even when `ls` on the same path succeeds. Remember the network caveat
above before trusting any local reachability result — `sessions_dockets.py` in particular only
works from a US IP (a GitHub Actions runner), never from this dev machine.

## Hosting

Public repo + public GitHub Pages (Jarrod's choice; private Pages needs a paid plan). Everything
committed — code, the SQLite DB, and dashboard JSON with owner names, addresses and amounts — is
visible to anyone. See "Decisions still open" above.
