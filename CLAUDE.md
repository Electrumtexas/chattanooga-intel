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

## Status (2026-09-22, after Phase 2 build)

Recon method (2026-09-15): 7 discovery agents (one per source category), 7 independent agents
that re-ran each discovery's key requests trying to refute it, and a critic that reconciled
conflicts. A second round (2026-09-22) covered the 19 SOS-registered foreclosure posting
companies, probate contact sources, and free lien options. "Verified" below means a real
request/response was observed, re-observed by an independent verifier, and (for the scrapers)
confirmed by running the actual module end-to-end against a real, disposable copy of the DB.

| Signal | Source(s) | Mechanic | State |
|---|---|---|---|
| Tax delinquency | Trustee `CTRUDELQCSV.zip` | bulk zip | **LIVE** (`tax_delinquent.py`) |
| Tax-sale filings | Clerk & Master annual Delinquent Tax Sale LIST PDF | PDF parse | **LIVE** (`tax_sale.py`) |
| Code enforcement | City of Chattanooga ArcGIS Hub CSV item | ~90MB CSV | **LIVE** (`code_enforcement.py`, rewritten — the old Socrata endpoint was dead) |
| Parcel → lat/long, owner, land use, value | County ArcGIS `Live_Parcels` (new `mapsdev` host, post-9/18 migration) | ArcGIS REST JSON | **LIVE** (`enrich_assessor.py`) |
| Foreclosure notices | 6 posting sites (betterchoicenotices, nwpostingservices, foreclosuretennessee, tnlegalpub, capitalcitypostings, tennesseepostings) | JSON APIs / ASP.NET postback / static HTML, cross-site merge on deed book/page | **LIVE** (`foreclosure_notices.py`) |
| Probate / estates | Chancery Court Part 2 motion docket PDFs | PDF parse | **LIVE** (`probate_dockets.py`); partial coverage by design (estates with a pending motion only) |
| Collections / detainers (General Sessions) | edockets.us | page XHR + PDFs | **LIVE** (`sessions_dockets.py`); tenant/defendant names never stored for detainer cases |
| Estate-owned property (standing inventory) | County GIS owner-name pattern match | ArcGIS REST JSON | Identified (~273 parcels), not yet a dedicated scraper — see Pending work |
| Liens (municipal) | City of Chattanooga tax portal | per-parcel enrichment lookup | Identified, not yet built — see Pending work |
| Liens / lis pendens (county-wide) | Register of Deeds only | none | **No compliant automated source exists** |
| Civil judgments (non-detainer) | TennesseeCaseFinder.com | account + terms click-through | Manual only — owner decision below |

`court_records.py`'s original stub is superseded by the four modules above (`tax_sale.py`,
`probate_dockets.py`, `sessions_dockets.py`, `foreclosure_notices.py`) plus `enrich_assessor.py`;
the stub file itself is stale and should be deleted next time this file is touched.

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
(Wilson & Associates/Foundation Legal — the single largest unbuilt block, ~20% of notices) sits
behind a Terms-of-Service checkbox only Jarrod can tick — flagged as a manual decision, not built.
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
- **A better free lead list exists and isn't yet a dedicated scraper:** ~273 county parcels are
  *already titled* to "HEIRS", an estate, or an executor/administrator wording, discovered via a
  one-time GIS owner-name pattern query — this needs no docket parsing at all and gives a mailing
  address directly. See Pending work.
- Official case index `hamilton.tncrtinfo.com`: Cloudflare Turnstile + `robots.txt: Disallow: /` +
  no-scrape terms — **excluded** from automation, manual lookup tool only.

### General Sessions (detainers/collections) — LIVE (`sessions_dockets.py`)

- **edockets.us/hamiltontn**: POST to `/cgi-bin/webshell.asp` returns a JS-array literal of
  upcoming dockets; PDFs stay fetchable well after they drop off that list. **Answers US IPs
  only** — unreachable from the dev machine's connection; the daily GitHub Actions run is the only
  place this can be exercised live (confirmed working from a runner). A `--samples-dir` dev mode
  ships in the module for local testing against saved sample PDFs.
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

### Liens and lis pendens — one free automated path, rest manual

- **City of Chattanooga property-tax portal** (`chattanoogatn.taxandrevenue.opengov.com`) — free,
  no login, no CAPTCHA. A parcel's current-charges page can show a "MUNCIPAL LIEN" line (the
  city's own misspelling) next to unpaid tax/stormwater and an owner mailing address. Key by tax
  map number (spaces removed) via `/User/ViewCurrentCharges?pidn=<PIDN>` — PIDN is a per-tax-year
  id, not a stable property id, so re-derive it each time from a MAP search. **Build as
  enrichment only** (look up parcels already flagged by another source), never as a crawl — not
  yet built, see Pending work.
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
| internetpostings.com | Behind a ToS checkbox only Jarrod can accept; largest unbuilt block (~20% of notices) if he ever does |
| tnforeclosurenotices.com | Agree/Disagree click-through gate |
| timesfreepress.com legal notices | Terms ban robots/spiders/data mining; AI policy; robots.txt disallows Claude agents |
| hamilton.tncrtinfo.com (probate/case index) | Turnstile + robots Disallow all + no-scrape terms |
| TennesseeCaseFinder.com automation | Government "official use only" login, MFA-trusted session — manual lookups only |
| Register of Deeds search | Paid; subscriber agreement bans scraping |
| hamiltonclerk.com, civitekflorida.com county 24 | Hamilton County, **Florida** — was wrongly listed as a TN probate source in the original `court_records.py` stub; drop entirely |
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

## Decisions still open

1. **internetpostings.com:** accept its ToS checkbox (Jarrod only, one-time, then the daily
   scraper would need to persist that acceptance) to unlock the largest single unbuilt block of
   Hamilton notices — or skip it permanently.
2. **Code enforcement footprint:** current source is City of Chattanooga only. Decide whether
   other Hamilton County municipalities matter (needs new recon; no known equivalent dataset yet).
3. **PII on a public site:** the dashboard now publishes owner names, mailing addresses, and
   distressed-property signals across six live sources. Reconsider public hosting once the build
   is complete, per Jarrod's original option to flip it private.

## Pending work, prioritized

1. **Municipal lien enrichment** against the City of Chattanooga tax portal (per-parcel lookup
   only, keyed by tax map number with spaces removed) for parcels already flagged by another
   source — the one identified free lien path, not yet built.
2. **Estate-owned-property scraper**: a one-time + periodic sweep of `Live_Parcels` for
   `OWNERNAME2` matching HEIRS/executor/administrator/estate wording (~273 parcels identified) —
   a standing probate-adjacent lead list that needs no docket parsing.
3. **Scoring calibration** now that six sources are live — `scoring.calibrate_from_data()`; tiers
   and amount breakpoints are still placeholders. `probate`'s tier could also distinguish a real
   "motion to sell property" from a routine housekeeping motion (see `scoring.py` comments).
4. **situs_city/situs_zip gap**: the county GIS parcel layer has no situs city/zip field at all —
   cannot be backfilled from GIS regardless of source. Needs another source if it matters.
5. **`internetpostings.com`** — build once Jarrod accepts its terms (decision above).
6. Delete the stale `court_records.py` stub (superseded by four modules — see Status table).
7. Gaps no one covered: code enforcement outside the City of Chattanooga; state/federal tax lien
   publication outside the Register; bankruptcy filings (PACER, E.D. Tenn.); judicial/partition
   sale notices; the GOGov CHA 311 portal.
8. Nice-to-haves: `quality_check.py` also flagging a missing scrape_log entry (a crashed source
   currently logs nothing), per-source partial commits, trimming `tax_delinquent.json`, repo size
   (the committed `chattanooga.db` grows daily — code_enforcement's ~28.6k rows alone added several
   MB; watch `data/chattanooga.db`'s size and revisit if it becomes unwieldy).

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

`case_type` vocabulary (`court_records`): `foreclosure_notice` / `foreclosure_notice_cancelled` /
`tax_sale_filing` / `tax_sale_resolved` / `probate` / `detainer` / `collections`. The two
`_cancelled`/`_resolved` variants score 0 (`scoring.py`'s `CASE_TYPE_TIER`) and are excluded from
`build_unified.py`'s scoring group entirely — kept for audit trail, never treated as live distress.

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

`CASE_TYPE_TIER`, `VIOLATION_TYPE_TIER` and `_amount_scale` breakpoints are placeholders. Now that
six sources are live, run `scoring.calibrate_from_data(conn)` and hand-tune against real
percentiles — see Pending work. `VIOLATION_TYPE_TIER` mapping to real city code sections
(21-76(e), 21-80, …) is done (`code_enforcement.py`'s `CODE_CATEGORY` table, 97 codes).

## Dashboard (`dashboard/index.html`)

Single static file, vanilla HTML/CSS/JS; Leaflet + OSM tiles + markercluster via CDN. Config-driven
(`SECTIONS`, `COMMON_COLUMNS`) — a new source is a config entry. Tested live: Prospect/Verify modes,
section switching, filters, sorting, CSV export, detail modal, themes, mobile nav. Client-side
pagination (100/page) because rendering thousands of rows was slow; the map plots every filtered
lead that has lat/long — GIS enrichment (and code_enforcement's own lat/long, surfaced even before
parcel resolution) now populates most of it. `COMMON_COLUMNS` includes `land_use`,
`appraised_value`, and a link to the county assessor card (`fmt: "link"`). Export JSON is compact
(except `meta.json`), and nested `signals` are trimmed of bookkeeping fields.

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
│   ├── tax_delinquent.py       # LIVE — Trustee delinquent tax ledger
│   ├── tax_sale.py             # LIVE — Clerk & Master tax-sale PDF
│   ├── code_enforcement.py     # LIVE — City ArcGIS Hub CSV (rewritten; old Socrata endpoint dead)
│   ├── probate_dockets.py      # LIVE — Chancery Part 2 motion dockets
│   ├── sessions_dockets.py     # LIVE — edockets.us General Sessions (US-runner only)
│   ├── foreclosure_notices.py  # LIVE — 6 posting sites, merged
│   ├── enrich_assessor.py      # LIVE — county GIS parcel/address/point/owner resolution
│   ├── court_records.py        # stale stub, superseded — delete next time this file is touched
│   └── requirements.txt
├── .claude/launch.json   # python -m http.server --directory dashboard
└── CLAUDE.md
```

## GitHub Actions

`scrape.yml`: daily 09:00 UTC + workflow_dispatch. Order: the five source scrapers (each
`continue-on-error: true` — a crashing source must not stop `quality_check.py` from running, or it
silently blocks the other sources' good data) → `enrich_assessor.py` (also continue-on-error) →
`quality_check.py` (hard gate) → `build_unified.py` (hard gate) → commit → `deploy` (only if the
scrape committed changes). `enrich_assessor`'s step sets explicit `ENRICH_MAX_*` env caps so a
first-run backlog can't run unbounded. Actions were bumped to their Node 24 majors.

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
