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

## Status (2026-09-15, after live multi-agent recon)

Recon method: 7 discovery agents (one per source category), 7 independent agents that re-ran
each discovery's key requests trying to refute it, and a critic that reconciled conflicts.
"Verified" below means a real request/response was observed, and re-observed by the verifier.

| Signal | Best verified public source | Mechanic | State |
|---|---|---|---|
| Tax delinquency | Trustee `CTRUDELQCSV.zip` | bulk zip | **LIVE** (`tax_delinquent.py`) |
| Tax-sale filings | Clerk & Master annual Delinquent Tax Sale LIST PDF | PDF parse | Verified, not built |
| Code enforcement | City of Chattanooga ArcGIS Hub CSV item | ~90MB CSV | **BROKEN** — `code_enforcement.py` still targets the dead Socrata API |
| Parcel → lat/long | County ArcGIS `Live_Parcels` + locators | ArcGIS REST JSON | Verified, not built — county GIS migration announced for 2026-09-18 |
| Foreclosure notices | ForeclosureTennessee.com (+ tnlegalpub.com JSON) | ASP.NET postback / WP REST | Verified; **terms need owner decision** |
| Probate / estates | Chancery Court Part 2 motion docket PDFs | PDF parse | Verified; partial coverage (estates with pending motions) |
| Civil judgments | Circuit Court weekly docket PDFs | PDF parse | Verified but low value (no amounts); real data is account-gated |
| Collections / detainers | edockets.us General Sessions dockets | page XHR + PDFs | Verified live from GitHub's runners (2026-09-15); not built |
| Liens / lis pendens | Register of Deeds only | none | **No compliant automated source exists** |

Court-record scraping in `court_records.py` is still a `NotImplementedError` stub, and
`enrich_assessor.py` too — but the recon they were waiting on is now done (below).

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

### Tax-sale filings — Clerk & Master — verified, not built

- **Annual Delinquent Tax Sale LIST PDF**:
  `https://www.hamiltontn.gov/Clerkmasterforms/taxsale/2026TaxSale/DELINQUENT%20TAX%20SALE%20LIST%202026.pdf`
  (linked from the hamiltontn.gov home page "Featured Information"). Text layer; 185/185 rows
  parsed with pypdf + regex: status (PAID/REMOVED/blank), Chancery docket (11256 = Hamilton
  County suit; 11255 inferred City), item #, address, MAP, GROUP, PARCEL (+ suffixes like C158),
  minimum bid. Year paths persist (the 2025 list is still up) — useful for backfill. Filenames
  vary by year; HEAD daily and compare Last-Modified. Annual, and most rows are PAID by sale
  day. The 2026 sale date/format is unconfirmed (the INFORMATION PDF still carries 2025 text).
- **cmpti.hamiltontn.gov** — per-parcel delinquent bills. Search is a two-step ASP.NET postback
  (post the tab `__EVENTTARGET` first; skipping it returns 500). Detail pages GET directly:
  `CM_PropertyInfo.aspx?pmuid=N`, `CM_Taxbill.aspx?pmuid=N&tbuid=M`. Blank Group renders as a
  double space (`150  270`). Use for enrichment of known parcels only — walking pmuids is crawling.
- Redemption stage: Chancery Part 1 motion dockets carry suit 11256 motions; the Real Property
  Office's "High Bid list" PDF (county-owned post-sale parcels) has a text layer.

### Code enforcement — City of Chattanooga ArcGIS Hub — verified, scraper not yet updated

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

### Parcel → lat/long — Hamilton County GIS — verified, not built

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
  `GROUP_` first (a blank group is stored as a single space). Verify unit-suffix handling against
  Trustee rows when implementing.
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

### Foreclosure notices — verified; terms decision pending

Since 2025-07-01 (Tennessee Foreclosure Modernization Act, HB1127/SB0727): two newspaper
publications (first ≥20 days before sale) **plus** ≥20 days on a third-party internet posting
company registered with the Secretary of State, named in the notice.

- **ForeclosureTennessee.com** (Public Postings LLC / TN Bankers Association): GET `/` for
  `__VIEWSTATE`; POST all fields with the County ListBox (name ending
  `ResultsGrid$Sheet0$Input0$ctl00$ListBox`) = `Hamilton` and SubmitButton = `Find`; optional
  sale-date range in the two `BusinessCalendar1_TextBox` fields (M/D/YYYY; verified). Grid: Sale
  Date, Continuance Date, City, Address, Zip, County, Firm/Trustee, submissionID. Detail:
  `/Foreclosure/Foreclosure-Listing.aspx?submissionID=N` returns labeled fields, the notice as an
  inline base64 PDF, and OCR text. No CAPTCHA. A Claude-identifying User-Agent gets a Cloudflare
  403; the python-requests default gets 200. **Terms** (browsewrap, eff. 7/1/25): view/search/
  download "for informational purposes only"; no republishing or reuse without written permission.
  Coverage is partial: only trustees who use this posting company (6 active Hamilton listings vs
  ~27 newspaper notices per 30 days).
- **tnlegalpub.com**: WordPress REST `https://tnlegalpub.com/wp-json/wp/v2/legal_notice?county=132`
  (132 = Hamilton), full text on the linked server-rendered page. Low volume. No terms found.
- Unread: the SOS registry of posting companies (`sos.tn.gov/publications/foreclosure-listing-companies`)
  — 403 from the dev machine, from WebFetch, **and** from GitHub's runners. Read it manually in a
  US browser; other registered posting sites may carry more Hamilton notices than
  ForeclosureTennessee + tnlegalpub.

### Probate / estates — verified, partial

- **Chancery Part 2 motion docket PDFs**: `https://www.hamiltontn.gov/ChanceryCourt_Dockets.aspx` →
  `pdf/courts/Chancery/data/MMDDYY.pdf`. Alternating Mondays per the Motion Call Schedule PDF.
  Text layer. Parse `IN THE MATTER OF THE ESTATE OF:` blocks and dockets rendered `YY-P NNN`
  (normalize to `YY-P-NNN`), attorney, motion type (e.g. MOTION TO APPROVE SALE OF REAL PROPERTY),
  disposition. The Sep 14, 2026 docket had ~39 probate dockets. A missing date returns **HTTP 200
  with an HTML "Page Not Found"** — check the `%PDF` magic bytes. Old PDFs stay online.
  Match decedents to parcels by `OWNERNAME1`.
- Limits: only estates with pending motions, every other week; no date of death, personal
  representative or property address.
- Official case index `hamilton.tncrtinfo.com` (Local Government Corporation TnCIS): Cloudflare
  Turnstile before search, `robots.txt: Disallow: /`, and terms forbidding scraping — **excluded**.

### Civil judgments and collections — low confidence

- Circuit Court Clerk (500 Courthouse, 423-209-6700) clerks Circuit Court and General Sessions
  Civil. Chancery (Clerk & Master) is separate.
- **Circuit weekly docket PDFs** (`hamiltontn.gov/CircuitCourt_Dockets.aspx`): fixed URLs
  `CircuitClerkDockets/Daily1-4.pdf` and `MOTION1-4.pdf`, overwritten weekly (archive every pull).
  Columns: CASE NO | ATTORNEY | STYLE OF CASE | COMMENT | SERVICE. Mostly domestic (D) cases; civil
  case numbers look like `YYC####`. No judgment amounts, dates or addresses. (`PSTEP1-4.pdf` are soft
  404s; the real procedural lists are `CIR.PROCEDURAL.STEPS.LIST DIV I-IV.pdf`, rarely updated.)
- **edockets.us/hamiltontn** (General Sessions, same host as TennesseeCaseFinder): ungated. The
  page's own XHR is a POST to `/cgi-bin/webshell.asp` with `XGATEWAY=DocketGetInfo`, `APPID=ham`,
  `DEVPATH=/INNOVISION/HAMILTON/HAMMAIN.DATA`, and page-embedded `OPERCODE=dummy`/`PASSWD=dummy`
  (not a login), returning a JS-array literal of `[date, time, office, division, docket type,
  pdfPath]` rows, with paths like `dockets/ses.<n>.<n>.pdf` under `/hamiltontn/`. **Confirmed live
  from GitHub's runners on 2026-09-15:** the page's own script builds exactly these params, and the
  POST returned 6 dockets (Trial, Detainer, Appearance) for 09/15–09/17. Only upcoming dockets are
  listed, so poll daily. Per recon, the PDFs carry case number (`YYGS####`), plaintiff VS defendant,
  case type (e.g. Detainer/Rental) and file date — confirm when building the parser. Unreachable
  from the dev machine's connection.
  Sessions Civil schedule: Monday DEFAULT / SLOW PAY, Thursday detainers.
- **TennesseeCaseFinder.com** (courTNet): the real civil data — file-date range and 32 case types
  (Civil Warrant, Contract/Debt, Detainer, Sue & Attach, Wage Assignment…) — but gated by account,
  terms click-through and emailed code (reachable from GitHub's runners; filtered from the dev
  machine), and its grid still has no amounts. Owner decision.
- `cjuscaseinfo.hamiltontn.gov` is criminal-only — irrelevant.

### Liens and lis pendens — no compliant automated source

- The only index is the Register of Deeds BETA search (`register.hamiltontn.gov/OnlineRecordSearch/Beta/Search.aspx`):
  anonymous visitors get a 302 to Login; $50/month; no free index tier. The **subscriber agreement
  bans "data botting, mining, scraping"**, requires searches keyed by a person, and requires US-only
  use — so paying would not permit automation either. The classic search requires Silverlight (dead).
  The Register's public-records policy bans electronic capture of records.
- Free `.../Home/Report.aspx` shows current-month totals only (e.g. JDGMT-LIEN 180 for Aug 2026) —
  a volume check, not data.
- Type codes (DRG 2024 Revised 7-16-2024): L06 lis pendens; J01/J02 judgment; F01 federal tax lien,
  F02 FT notice, F03–F06 FT releases/withdrawals/subordinations; L04 lien; L07 Dept of Labor lien;
  C05 child support lien; D04 decree lien; O03 order lien; D16 vendor's lien deed; N02 notice of
  completion; S02/S03 substitute/successor trustee; U01/U02 UCC.
- Compliant routes: a recurring Tennessee Public Records Act request (the statute limits it to TN
  citizens) or a negotiated index extract from the Register (register@hamiltontn.gov, 423-209-6560).

### Sources rejected — don't retry without an owner decision

| Source | Why |
|---|---|
| foreclosurestn.com, tnpublicnotice.com (TN Press Service) | Terms: personal non-commercial use, no incorporation "in any database, compilation, archive or cache"; Turnstile on every detail page; tnpublicnotice robots.txt disallows Claude agents |
| timesfreepress.com legal notices | Terms ban robots/spiders/data mining; AI policy; robots.txt disallows Claude agents |
| Capital City Postings | Disclaimer: personal use only, no download-and-store, no marketing lists |
| tnforeclosurenotices.com | Agree/Disagree gate + personal-use-only terms |
| hamiltoncountyherald.com | Unreachable from every vantage, including GitHub's runners (connect timeout) |
| hamilton.tncrtinfo.com | Turnstile + robots Disallow all + no-scrape terms |
| Register of Deeds search | Paid; subscriber agreement bans scraping |
| hamiltonclerk.com, civitekflorida.com county 24 | Hamilton County, Florida |
| courtclerk.org, hcso.org, hamiltoncountyauditor.org | Hamilton County, Ohio |
| OpenGov_HamiltonTN MapServer/2 | Incomplete parcel coverage |
| Commercial aggregators (auction.com, RealtyTrac, foreclosure.com, UniCourt, Trellis…) | Paid, derivative, terms-restricted |

---

## Decisions only Jarrod can make

1. **Foreclosures:** does internal lead scoring fit ForeclosureTennessee.com's "informational
   purposes only / no reuse without written permission" terms, or ask Public Postings LLC (TN
   Bankers Association) for written permission first?
2. **Newspaper-notice licensing:** exclude the TN Press Service sites and the Times Free Press,
   use them manually only, or request a licensed feed (publicnotice@tnpress.com) covering
   foreclosure, probate and tax-sale notices.
3. **Probate:** accept the biweekly Chancery docket coverage, or pursue new filings via a public
   records request or an extract/permission from the Clerk & Master (423-209-6600).
4. **TennesseeCaseFinder:** register an account (accepting courTNet's terms) or not.
5. **Liens / lis pendens:** pay $50/month for manual lookups on leads found elsewhere; request a
   recurring extract by type code; or skip and rely on court dockets + the Trustee file.
6. **GIS:** build now against `mapsdev` with a configurable URL, or wait for the 2026-09-18 migration.
7. **Code enforcement:** accept a change-triggered ~90MB daily check/download in Actions, and/or ask
   opendata@chattanooga.gov for an API and the refresh schedule. Decide whether non-Chattanooga
   municipalities matter (needs new recon).
8. **PII on a public site:** the repo and Pages dashboard are public, and the Trustee data already
   publishes owner names and mailing addresses. Foreclosure and probate data would add borrowers
   and decedents. Reconsider public hosting before adding those.

## Pending work, prioritized

1. **Replace `code_enforcement.py`** with the ArcGIS Hub CSV ingestion above (change detection,
   streamed whole-file download, csv module, dedupe on `record_id`, `date_entered` as event date,
   distress-code filter). It currently fails every run (the step is `continue-on-error`).
2. **Read the SOS posting-company registry manually** (US browser — it 403s all automation) and
   check whether other registered posting sites carry Hamilton notices. Re-run `probe.yml` after
   any endpoint change (e.g. the GIS migration).
3. **`enrich_assessor.py`** against `Live_Parcels` + locators, with a configurable base URL and a
   health check; re-verify endpoints on/after 2026-09-18. Then uncomment its workflow step.
4. **Tax-sale LIST PDF parser** (store as `court_records` with `case_type='tax_sale_filing'`, or a
   new module) and cmpti enrichment for listed parcels.
5. **Chancery Part 2 probate docket parser** (`case_type='probate'`), decedent → parcel by owner name.
6. After decisions 1–2: foreclosure notice ingestion (`case_type='foreclosure_notice'`).
7. edockets.us General Sessions dockets (XHR confirmed live from GitHub; parse the docket PDFs as
   `case_type='collections'` / detainer), plus weekly Circuit docket PDFs as a secondary signal.
8. **Scoring calibration** once at least two more sources are live — `scoring.calibrate_from_data()`;
   tiers and amount breakpoints are still placeholders.
9. Gaps no one covered: code enforcement outside the City of Chattanooga; state/federal tax lien
   publication outside the Register; bankruptcy filings (PACER, E.D. Tenn.); judicial/partition sale
   notices; the GOGov CHA 311 portal.
10. Nice-to-haves: `quality_check.py` also flagging a missing scrape_log entry (a crashed source
    currently logs nothing), per-source partial commits, trimming `tax_delinquent.json`.

---

## Database schema (`data/chattanooga.db`, via `scraper/db.py`)

Canonical join key: `parcel_id` = Map-Group-Parcel joined with `-`, blank Group omitted
(`parcel_utils.format_parcel_id()`). Maps to the county GIS `TAX_MAP_NO` as described above.

- **`parcels`** — one row per parcel_id; `upsert_parcel()` merges non-null fields and never
  overwrites a value with NULL. Situs/mailing address, owner name/type, `is_absentee`, lat/long.
- **`court_records`**, **`tax_delinquent`**, **`code_enforcement`** — one row per case / per
  (parcel, tax_year) / per violation. Shared: nullable `parcel_id`, `raw_address`/`raw_owner_name`,
  a single `dedupe_key TEXT UNIQUE`, `resolution_method` (`native_parcel_id` | `address_match` |
  `owner_name_fallback` | `unresolved`), `first_seen_at`/`last_seen_at`.
- **`scrape_log`** — one row per scraper run per source; `quality_check.py` reads it.

Not stored per-row: `years_delinquent` and violation counts — aggregated in `build_unified.py`.

## Scoring (`scraper/scoring.py`) — framework real, calibration placeholder

1. Each source category present for a parcel collapses to ONE raw value (category tier × log-scale
   amount multiplier + a small stacking bonus for multiple records within that source). Records
   within the same source never enter the cross-source combination separately.
2. Absentee (+8) and entity-owner (+6) bonuses apply to each raw value before combination.
3. Noisy-OR combination `1 - Π(1 - raw_i/100)` → 0–100 with diminishing returns (synthetic test:
   triple threat 100.0, two signals 69.0, single weak signal 34.8).
4. Tier: `triple_threat` (3 categories) / `multi_factor` (2) / `single_signal` (1).
5. Completeness tracked separately (`owner_name`, `situs_address`, `mailing_address`, `parcel_id`,
   `latitude`) — thin records get flagged for enrichment, not scored low.

`CASE_TYPE_TIER`, `VIOLATION_TYPE_TIER` and `_amount_scale` breakpoints are placeholders. Once real
multi-source data exists, run `scoring.calibrate_from_data(conn)` and hand-tune against real
percentiles. `VIOLATION_TYPE_TIER` keys will also need mapping to the real city code sections
(21-76(e), 21-80, …) when `code_enforcement.py` is rebuilt.

## Dashboard (`dashboard/index.html`)

Single static file, vanilla HTML/CSS/JS; Leaflet + OSM tiles + markercluster via CDN. Config-driven
(`SECTIONS`, `COMMON_COLUMNS`) — a new source is a config entry. Tested live: Prospect/Verify modes,
section switching, filters, sorting, CSV export, detail modal, themes, mobile nav. Client-side
pagination (100/page) because rendering thousands of rows was slow; the map still plots every
filtered lead. The map is empty until GIS enrichment runs (a banner says so). Export JSON is compact
(except `meta.json`), and nested `signals` are trimmed of bookkeeping fields (~14MB → ~6MB).

## File structure

```
chattanooga-intel/
├── .github/workflows/
│   ├── scrape.yml        # daily: scrapers (continue-on-error) → quality_check → build → commit → deploy
│   └── probe.yml         # manual: one request per candidate source from GitHub's runners
├── dashboard/            # index.html + generated JSON (never hand-edit the JSON)
├── data/
│   ├── chattanooga.db    # canonical SQLite store, committed
│   └── raw/              # gitignored caches
├── scraper/
│   ├── db.py, parcel_utils.py, scoring.py, build_unified.py, quality_check.py
│   ├── tax_delinquent.py   # LIVE
│   ├── code_enforcement.py # BROKEN — dead Socrata endpoint; rebuild per "Code enforcement"
│   ├── court_records.py    # stub
│   ├── enrich_assessor.py  # stub
│   └── requirements.txt
├── .claude/launch.json   # python -m http.server --directory dashboard
└── CLAUDE.md
```

## GitHub Actions

`scrape.yml`: daily 09:00 UTC + workflow_dispatch. Each scrape step is `continue-on-error: true`
(a crashing source must not stop `quality_check.py` from running, or it silently blocks the other
sources' good data); `quality_check.py` and `build_unified.py` stay hard gates. `deploy` runs only
when the scrape committed changes. The `court_records` and `enrich_assessor` steps are commented
out until implemented. Actions were bumped to their Node 24 majors.

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

On this Windows dev machine: use `python` (not `python3`); Git Bash `/tmp` is the shared Windows
temp folder; `gh` may not be on PATH (`C:\Program Files\GitHub CLI\gh.exe`). Remember the network
caveat above before trusting any local reachability result.

## Hosting

Public repo + public GitHub Pages (Jarrod's choice; private Pages needs a paid plan). Everything
committed — code, the SQLite DB, and dashboard JSON with owner names, addresses and amounts — is
visible to anyone. See decision 8.
