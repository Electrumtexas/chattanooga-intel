# Chattanooga Intel

Distressed-property intelligence for Hamilton County, TN. Owner: Jarrod, Electrum Texas.
Scrapes public records, stacks distress signals per property, scores leads, and serves a
static dashboard from GitHub Pages. Everything lives in this repo — GitHub Actions runs the
scrapers on a schedule, commits results back, and deploys `dashboard/` to Pages. No external
server, no database service, no paid infra beyond a normal GitHub account.

**Read this file before touching anything.** It's the only record of what's actually been
verified against live data vs. what's still a documented assumption.

## Status as of this session (2026-09-15)

| Source | Status | Notes |
|---|---|---|
| Tax delinquent (Trustee) | ✅ Live, verified, real data ingested | 7,926 real-estate parcels currently delinquent |
| Code enforcement (ChattaData) | ⚠️ Built, NOT field-verified | Dataset confirmed current; exact JSON field names unverified — see below |
| Court records (4 portals) | ❌ Not started | Stub raises `NotImplementedError` by design — needs live reconnaissance first |
| Assessor/GIS enrichment | ❌ Not started | Stub raises `NotImplementedError` by design — no parcel has lat/long yet |
| Register of Deeds | ❌ Blocked on manual signup | $50/mo + account application — Jarrod must do this |
| Dashboard | ✅ Built and tested | Works end-to-end against real tax-delinquent data; map is empty until GIS enrichment lands |
| GitHub repo / Pages | ⏳ Pending | `gh` installed but not authenticated — see Pending Work |

**The dashboard today only shows tax-delinquent leads.** Court records — the spec's
highest-priority source — isn't live yet, and without GIS enrichment nothing has a resolved
lat/long, so Prospect Mode's map is legitimately empty (it says so, via a banner). This is
expected, not broken.

---

## Data sources — verified mechanics

### Tax delinquent — Hamilton County Trustee (`scraper/tax_delinquent.py`) ✅

**Direct file download, not the manual fallback the original spec assumed.**
`tpti.hamiltontn.gov`'s "Delinquent File Download" link actually points to a different host
(`www.hamiltontn.gov`) and resolves to a plain file, no form/session/postback:

```
https://www.hamiltontn.gov/_downloadsTrusteeDelinquent/CTRUDELQCSV.zip
```

Updates roughly monthly (observed file date: 2026-09-01). No auth, no rate limiting observed.

Verified facts about the file format, each confirmed against a real downloaded copy (not
assumed from documentation):

- **~54,500 rows, one row per (Map, Group, Parcel, Bill Year)** — a parcel accumulates a new
  row per delinquent year. Some carry 26 years of history. ~97.5% of rows have a nonzero
  current-owed balance — this file is a running ledger, not a "newly delinquent this month"
  list, so we only ingest rows where current owed > 0.
- **Money field decimal convention is inconsistent across columns** — verified via population
  percentiles, not a single-row guess (a wrong guess here would silently make every dollar
  figure in the dashboard 100x wrong with no error to catch it):
  - `Current County/Mun/Stw Owed`, `*Amount` columns: **cents** (÷100). Confirmed: whole-dollar
    reading gives a median "current owed" of $16,519 (implausible for one year's bill);
    cents reading gives $165 median / $847 p90 (matches real tax-bill magnitudes).
  - `Assessment`, `Original Assessment`: **whole dollars**, NOT cents. Confirmed the opposite
    way: cents reading gives a median assessed value of $72 (impossible); whole-dollar reading
    gives $7,200, consistent with TN's 25% residential assessment ratio against a low
    appraisal — exactly what a delinquent-tax list should skew toward.
- **~77% of "delinquent" rows are personal property, not real estate — filtered out.**
  Map='PER' (40,592 rows) or 'OSAP' (219 rows) are business personal-property tax accounts
  (equipment/inventory), confirmed by 100% blank `Land Use` on those rows vs. a real
  classification code (111, 910, 112, ...) on every digit-leading Map. A personal-property
  debt isn't a piece of real estate Jarrod could approach an owner about — only rows whose
  Map starts with a digit are kept. **This cut the raw file from ~53,110 currently-owed rows
  to 12,347 real ones (7,926 distinct parcels).** If this dashboard ever looks like it's
  "missing" delinquent accounts compared to a raw pull of this file, this filter is why.
- **`Back Tax Indicator`** ('Y'/'N') marks a bill already in active back-tax collection — used
  as a scoring bonus. ~14% of well-formed rows are 'Y'.
- **~2.3% of rows are ragged** (fewer columns than the header — some embedded-character issue
  in the legacy export). Skipped and counted (`stats['malformed']`), not crashed on.
- **Three header names carry stray trailing whitespace** (`"Mail Addr 3 "`,
  `"Legal Description 2 "`, `"Legal Description 3 "`) and `"Filler"` appears 7 times. Headers
  are stripped before use.
- Situs address has **no separate city/zip field** in this source — only a single
  `Property Address` string. `situs_city`/`situs_zip` stay NULL for tax-delinquent-only
  parcels until GIS enrichment backfills them. This is a real data gap, not a bug — the
  dashboard's City/Zip filter and columns will be empty for these leads until then.

Raw CSV cached to `data/raw/tax_delinquent/CTRUDELQCSV.csv` (gitignored) so `parse()` can be
fixed and replayed without re-downloading a ~29MB file.

### Code enforcement — ChattaData Socrata API (`scraper/code_enforcement.py`) ⚠️

Dataset ID `qcrz-rvw7` ("Code Enforcement - Violations") confirmed current via web search —
matches the spec's "as of Sept 2026" note. Standard Socrata SODA API:
`https://www.chattadata.org/resource/qcrz-rvw7.json`, paginated via `$limit`/`$offset`. Public,
no auth mentioned anywhere.

**Exact field names were never actually observed.** Every attempt to fetch JSON from
`chattadata.org` during this session failed — WebFetch returned "socket closed" (3 attempts,
two different paths), a local Windows `curl` failed at the TLS handshake stage (schannel,
before any HTTP request), and Python `requests` failed with `SSLEOFError`. Three independent
HTTP clients failing the same way against the same host strongly suggests a network-level
restriction specific to that dev sandbox, not a dead endpoint (search results describe the
dataset as live and current). **A GitHub Actions ubuntu-latest runner is a completely
different network and TLS stack and should not hit the same wall — but this needs confirming
on the first real run.**

Because of this, `FIELD_CANDIDATES` in the module is a best-guess set of common Socrata
naming conventions, not a verified schema. `parse()` prints the real key names from the first
fetched row to stderr and tries several candidates per logical field, degrading to `None`
rather than crashing on a wrong guess. **First priority on the first real GitHub Actions run:
read that log output and fix `FIELD_CANDIDATES` if needed** — see Pending Work.

### Court records — 4 portals (`scraper/court_records.py`) ❌ NOT STARTED

Deliberately left as a stub that raises `NotImplementedError`. This is the highest-priority
source per the original spec, and the spec is explicit that live reconnaissance has to happen
*before* writing scraper code — a wrong assumption here cascades into every case_type/date/
amount parsing decision built on top of it. See the module's docstring for the four portals to
investigate (TN Case Finder, Civitek OCRS, hamiltonclerk.com probate, Clerk & Master tax-sale
docket) and what each needs answered (requests+BeautifulSoup vs. Playwright, session/rate-limit
behavior, real field shapes). **This is the top item in Pending Work.**

### Assessor/GIS enrichment (`scraper/enrich_assessor.py`) ❌ NOT STARTED

Also a stub. This resolves every unresolved `raw_address` to a `parcel_id` + lat/long, and is
why **no parcel in the current dashboard has coordinates** — Prospect Mode's map is correctly
empty right now, not broken. See the module's docstring for the ArcGIS/`gismaps.hamiltontn.gov`
discovery task. **Second-highest Pending Work item** — it's a dependency for every source's map
pin and for court_records'/code_enforcement's address→parcel resolution.

### Register of Deeds ❌ MANUAL, BLOCKED ON JARROD

`register.hamiltontn.gov`'s "BETA" search needs an account application + $50/month. Not
signed up, not paid — that's Jarrod's call to make and action to take. Once he has
credentials, they should arrive as a GitHub Actions Secret (never committed to the repo); a
future session can then build the ingestion against it. No code exists for this yet.

---

## Database schema (`data/chattanooga.db`, via `scraper/db.py`)

Canonical join key: `parcel_id`, formatted as Hamilton County's Map-Group-Parcel triplet
joined with `-` (blank Group omitted) — see `parcel_utils.format_parcel_id()`.

- **`parcels`** — one row per resolved parcel_id. Enriched incrementally by whichever source
  resolves first (`upsert_parcel()` merges non-null fields, never overwrites a real value with
  NULL). Holds situs/mailing address, owner_name/type, `is_absentee`, lat/long, geocode status.
- **`court_records`**, **`tax_delinquent`**, **`code_enforcement`** — one row per case / per
  (parcel, tax_year) / per violation respectively. All three share: `parcel_id` (nullable until
  resolved), `raw_address`/`raw_owner_name` (kept even after resolution, for audit), a single
  `dedupe_key TEXT UNIQUE` (not a multi-column constraint — several sources don't reliably
  provide every field a composite key would need), `resolution_method` (`'native_parcel_id'` |
  `'address_match'` | `'owner_name_fallback'` | `'unresolved'` — tax_delinquent uses
  `native_parcel_id` since the Trustee file provides Map/Group/Parcel directly, which is
  stronger confidence than a geocoded match), and `first_seen_at`/`last_seen_at`.
- **`scrape_log`** — one row per scraper run per source (`source`, `run_at`, `record_count`,
  `status`). This is what `quality_check.py`'s trailing-baseline comparison reads from.

Deliberately NOT stored per-row: `years_delinquent` (tax_delinquent) and
`open_violation_count` (code_enforcement) aren't columns — they're computed as aggregates
across a parcel's rows in `build_unified.py`, because storing them per-row would either be
redundant or require an extra write-back pass every time a new row arrives for a parcel that
already has others.

## Scoring (`scraper/scoring.py`) — framework is real, calibration is a placeholder

Avoids flat base+bonus-capped-at-100 (saturates once 2-3 signals stack, destroying ranking
within the top tier). Instead:

1. Each of the ≤3 **source categories** (court/tax/code) that has ≥1 record for a parcel
   collapses to ONE raw value (0-100ish) via that source's `*_raw()` function — category tier
   (`CASE_TYPE_TIER`, `VIOLATION_TYPE_TIER`) scaled by a log-scale dollar-amount multiplier
   (`_amount_scale`), plus a small stacking bonus for multiple records within that one source
   (e.g. 5 delinquent tax years, or 2 open code violations). **Important:** multiple records
   within the SAME source collapse to one raw value before combination — they do NOT each
   enter the cross-source combination independently (an earlier draft this session got this
   wrong; a parcel with 5 tax-delinquent years was briefly double-counting each year as an
   independent "source," inflating scores for parcels with a long thin history over parcels
   with genuinely stacked distress types).
2. Absentee-owner (+8) and entity-ownership (+6) bonuses apply to each source's raw value
   *before* combination (small, additive — per spec).
3. The (≤3) source-level raw values combine via a noisy-OR: `1 - Π(1 - raw_i/100)`, rescaled
   to 0-100. This gives diminishing marginal returns per stacked signal — verified against
   synthetic data to actually discriminate (a triple-threat absentee-LLC case scored 100.0, a
   two-signal case scored 69.0, a single weak $320 tax bill scored 34.8 — not clumped).
4. Tier: `triple_threat` (3 distinct source categories) / `multi_factor` (2) / `single_signal` (1).
5. Completeness (`owner_name`, `situs_address`, `mailing_address`, `parcel_id`, `latitude`)
   tracked separately from score, per spec — an incomplete record gets flagged for enrichment,
   not defaulted to a low score.

**`CASE_TYPE_TIER`, `VIOLATION_TYPE_TIER`, and `_amount_scale`'s low/high breakpoints are
placeholder defaults, NOT calibrated against real percentile data.** Real data only exists for
tax_delinquent so far. Once Phase 1 (court records) and a verified Phase 3 (code enforcement)
are live, run `scoring.calibrate_from_data(conn)` to print real 25th/50th/75th/90th percentile
breakpoints per source and hand-tune the constants against them — flagged in the original spec
as worth the model/effort switch to do carefully, since a bad tune doesn't error, it just
quietly produces scores that don't discriminate.

## Dashboard (`dashboard/index.html`)

Single static file, vanilla HTML/CSS/JS, no build step. Leaflet + OpenStreetMap tiles +
Leaflet.markercluster via CDN (no API key needed). Config-driven: `SECTIONS` object maps each
nav section to its `dataUrl` and extra columns; `COMMON_COLUMNS` are shared across all four.
Adding a Phase 4 source means adding a config entry, not copy-pasting render logic.

**Tested live** against real data (server: `python -m http.server --directory dashboard`, or
`.claude/launch.json`'s `dashboard` config) — Prospect Mode, Verify Mode, section switching,
filtering, sorting, CSV export, detail modal, light/dark theme, and mobile nav all confirmed
working. Three real bugs were found and fixed during that pass:
- `unified_leads.json` rows were missing the `sources` array entirely (the multi-select signal
  filter had nothing to filter on) — fixed in `build_unified.py`.
- `#verify-view`'s flex container had no explicit `flex-direction`, defaulting to `row` instead
  of `column` — the export bar, pagination, and table were laying out side-by-side instead of
  stacked. Fixed with an explicit `flex-direction: column`.
- Rendering all matching rows into the DOM at once (thousands, given the real data) caused
  real slowdowns — added client-side pagination (100/page) to both Prospect Mode's card list
  and Verify Mode's table. The map still plots every filtered lead with coordinates regardless
  of pagination; only the list/table rendering is paginated.

**Known limitation:** the map is empty until `enrich_assessor.py` resolves lat/long — the UI
shows a banner explaining this rather than a silently blank map.

`dashboard/meta.json` (not in the original file list — added because the quality guardrail and
"how fresh/complete is this data" both needed somewhere to surface) carries generation
timestamp, per-tier counts, and per-source resolution/fallback rates.

Export files are compact JSON (no pretty-printing) except `meta.json` — these are fetched and
parsed client-side with no backend to paginate them, so byte size matters. Nested `signals`
inside `unified_leads.json` are trimmed of pipeline-internal bookkeeping fields
(`dedupe_key`, `first_seen_at`, etc.) — this alone roughly halved the file's size on the first
real pull (7,926 leads: ~14MB → ~6MB before gzip).

## File structure

```
chattanooga-intel/
├── .github/workflows/scrape.yml   # court_records + enrich_assessor steps present but
│                                     commented out until Phase 1 / GIS checkpoints land
├── dashboard/
│   ├── index.html                 # the whole dashboard
│   ├── unified_leads.json         # generated — never hand-edit
│   ├── court_records.json         # generated, currently []
│   ├── tax_delinquent.json        # generated, real data
│   ├── code_enforcement.json      # generated, currently []
│   └── meta.json                  # generated, pretty-printed
├── data/
│   ├── chattanooga.db             # canonical SQLite store, committed
│   └── raw/tax_delinquent/        # gitignored cache of the raw CSV pull
├── scraper/
│   ├── db.py                      # schema + upsert helpers
│   ├── parcel_utils.py            # parcel_id formatting, owner-type/absentee classification
│   ├── tax_delinquent.py          # ✅ real, verified
│   ├── code_enforcement.py        # ⚠️ built, field names unverified
│   ├── court_records.py           # ❌ stub, NotImplementedError by design
│   ├── enrich_assessor.py         # ❌ stub, NotImplementedError by design
│   ├── scoring.py                 # all score math
│   ├── build_unified.py           # joins + scores + exports dashboard/*.json
│   ├── quality_check.py           # trailing-baseline guardrail, gates the commit
│   └── requirements.txt
├── .claude/launch.json            # `python -m http.server --directory dashboard` for local preview
└── CLAUDE.md                      # this file
```

## GitHub Actions workflow

Two jobs, `scrape` then `deploy` (gated on `scrape` actually producing a change). Currently
wired to run `tax_delinquent.py` and `code_enforcement.py` daily, then `quality_check.py`
(fails the build before anything commits — see its own docstring for the "any one source
fails the whole run" tradeoff and why), then `build_unified.py`, then commit+push if changed.

`court_records.py` and `enrich_assessor.py` steps are present in the workflow YAML but
**commented out** — running them now would just fail every day (`NotImplementedError`) until
they're implemented. Uncomment both once Phase 1 and the GIS checkpoint are done.

## Pending work, prioritized

1. **Phase 1: court records reconnaissance + implementation** (`scraper/court_records.py`).
   Highest priority per spec, and explicitly flagged as worth a model/effort switch (Opus 5,
   high/xhigh) — live-inspect all 4 portals (network tab, not marketing copy) before writing
   any fetch/parse code. Includes reading Civitek OCRS's indemnification disclaimer and
   deciding a reasonable scrape cadence — a judgment call, not a coding task.
2. **GIS/ArcGIS endpoint discovery** (`scraper/enrich_assessor.py`). Also flagged for the
   model/effort switch — load-bearing for every source's parcel_id/lat-long. Blocks the map
   entirely and blocks address→parcel resolution for court_records and code_enforcement.
3. **Verify `code_enforcement.py`'s real field names** on the first successful GitHub Actions
   run (check the stderr log line printing the first row's actual keys) and fix
   `FIELD_CANDIDATES` if the guesses were wrong. Should take minutes once network access works.
4. **Scoring calibration** once Phase 1 + a verified Phase 3 produce real data — run
   `scoring.calibrate_from_data(conn)`, hand-tune `CASE_TYPE_TIER`/`VIOLATION_TYPE_TIER`/
   `_amount_scale` breakpoints against the real percentiles. Also flagged for the model switch.
5. **Register of Deeds** — manual account signup + $50/month, Jarrod's call. Credentials
   arrive as a GitHub Secret when ready; no code exists yet.
6. **`data/raw/` caching policy per source** — currently only tax_delinquent's raw pull is
   cached locally (gitignored, not committed). Once Phase 1's portals are reconnaissance'd,
   decide per-portal whether its rate-limiting is aggressive enough to warrant committing a
   raw-response cache to the repo for replay (per the original spec's suggestion) — nothing
   currently needs this, but Civitek OCRS is the most likely candidate given its disclaimer.
7. **Nice-to-have:** if `tax_delinquent.json`'s per-bill-year row count keeps growing, consider
   trimming further (right now every row repeats the full common-field set; gzip handles most
   of the redundancy over the wire, but it's not free).
8. **Nice-to-have:** `quality_check.py` fails the ENTIRE run if any ONE source looks broken,
   even if the other sources were fine that day — a deliberate v1 simplification (see its
   docstring) since chattanooga.db is one file with no clean way to commit "just the good
   tables." Revisit if a single flaky source starts blocking good data from the others often.

## Running things locally

```bash
cd scraper
pip install -r requirements.txt
python tax_delinquent.py       # real, hits the live Trustee download
python code_enforcement.py     # real API call, field names unverified — check stderr output
python quality_check.py
python build_unified.py
```

Preview the dashboard: open this repo in Claude Code and use the `dashboard` launch config
(`.claude/launch.json`), or manually: `python -m http.server 8420 --directory dashboard`.

## Hosting / repo status

Repo not yet pushed to GitHub as of this session — `gh` CLI is installed locally but not
authenticated (Jarrod needs to run `gh auth login` interactively once; it can't be done on his
behalf). Intended to be a **public** repo (Jarrod's choice, given GitHub Pages on a free
account needs a public repo or a paid plan for private Pages) — meaning the scraper code,
scoring logic, and lead data (owner names, addresses, dollar amounts) will all be visible to
anyone. Worth Jarrod re-confirming that's still fine once there's real court-records data in
here too, not just tax-delinquent amounts.
