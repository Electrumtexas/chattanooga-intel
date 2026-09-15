"""
Phase 1 — Court records. HIGHEST PRIORITY source, and DELIBERATELY NOT YET
IMPLEMENTED beyond this skeleton.

The task spec is explicit: "FIRST TASK before writing scraper code: inspect
each portal's actual HTML/network behavior... Verify live, don't assume
from a site's marketing copy." That reconnaissance has not happened yet —
it's flagged as the point in this build to switch to a stronger model at
higher effort, because a wrong assumption about any of these four portals
cascades into every case_type/case_number/parsing decision built on top of
it. Writing real fetch()/parse() logic before that happens would mean
coding against a guess.

Four sources to reconnaissance-then-implement, each independently:

  1. TN Case Finder (tennesseecasefinder.com) — free, no login. Circuit
     Court + General Sessions Civil. Primary source for civil
     judgments/collections.
  2. Civitek OCRS via hamiltonclerk.com/court-search/
     (civitekflorida.com/ocrs/county/24/) — anonymous "Public" tier,
     covers Circuit, County Civil, Family, Probate. Has a click-through
     indemnification disclaimer to read and a scrape-cadence judgment
     call to make BEFORE automating against it — not just a technical
     question, see CLAUDE.md.
  3. hamiltonclerk.com/probate/ — probate case search entry point.
  4. Hamilton County Clerk & Master delinquent tax-sale docket
     (cmpti.hamiltontn.gov) — properties already in the Chancery Court
     tax-sale pipeline; treat as a foreclosure-strength signal.

For each, the reconnaissance needs to answer and record:
  - Is it plain server-rendered HTML (requests + BeautifulSoup), or does
    it need JS execution (Playwright)? Don't assume from the site's own
    marketing copy — watch actual network requests.
  - Is there a search API/endpoint behind the UI that can be called
    directly (faster, more stable than scraping rendered HTML)?
  - What does a real result page/response actually look like — field
    names, date formats, how case_type is expressed, whether amounts are
    present and in what format?
  - Session/cookie requirements, rate limits, anti-bot behavior (CAPTCHA,
    IP blocking, required headers).
  - Any dead ends (e.g. a Silverlight widget with no HTML fallback) that
    need a different approach or manual workaround.

Once that's done, this file should look like tax_delinquent.py or
code_enforcement.py: fetch() hits the confirmed real endpoint(s), parse()
turns real responses into case_type/filing_date/party_names/amount/
case_number records per the schema in db.py, and upsert() writes them via
db.upsert_record("court_records", ...) — with parcel_id left None and
resolution_method="unresolved" until enrich_assessor.py's address->parcel
matching runs (same pattern as code_enforcement.py). case_type should map
into scoring.py's CASE_TYPE_TIER keys: 'judgment' | 'lien' | 'lis_pendens'
| 'foreclosure_notice' | 'probate' | 'tax_sale_filing' | 'collections'.
"""

from __future__ import annotations

from db import get_connection, log_scrape


def fetch():
    raise NotImplementedError(
        "Phase 1 live portal reconnaissance hasn't happened yet — see this "
        "module's docstring. Do not guess at these four portals' scraping "
        "mechanics; inspect them live first."
    )


def parse(raw):
    raise NotImplementedError("Depends on fetch() — see module docstring.")


def upsert(conn, records) -> int:
    raise NotImplementedError("Depends on parse() — see module docstring.")


def main() -> None:
    conn = get_connection()
    try:
        raw = fetch()
        records = parse(raw)
        count = upsert(conn, records)
        conn.commit()
        log_scrape(conn, "court_records", record_count=count, status="ok")
    except NotImplementedError as e:
        log_scrape(conn, "court_records", record_count=0, status="error", notes=str(e))
        raise
    finally:
        conn.commit()


if __name__ == "__main__":
    main()
