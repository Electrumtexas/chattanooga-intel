"""
Phase 3 — Assessor/GIS enrichment. DELIBERATELY NOT YET IMPLEMENTED beyond
this skeleton — this is the checkpoint-flagged GIS/ArcGIS endpoint
discovery step.

What this module is for
-------------------------
Every court_records and code_enforcement row lands with parcel_id=None and
resolution_method='unresolved' (they only have a raw address). This module
is the address -> parcel_id -> lat/long resolution step: for every
unresolved record's raw_address (capped per run — see main()), look it up
against Hamilton County's parcel data and, on a match, upsert_parcel()
with the resolved parcel_id, latitude, longitude, and owner backfill, then
go back and set resolution_method='address_match' + parcel_id on the
source record(s) that matched.

Only fall back to owner-name matching for records whose address can't
resolve at all (per the spec) — track that fallback rate explicitly
(already surfaced in dashboard/meta.json's per-source fallback_rate, via
build_unified.py's resolution_method accounting).

The discovery task, not yet done
-----------------------------------
The spec's instruction: check whether gismaps.hamiltontn.gov/Html5Viewer
is backed by an ArcGIS REST MapServer/FeatureServer endpoint (common with
Esri stacks) and query THAT directly — via a /query endpoint returning
JSON/GeoJSON with a WHERE clause or geometry filter — rather than
screen-scraping the map UI. If confirmed, a single-address lookup becomes
a plain authenticated-or-not GET against something like
  https://gismaps.hamiltontn.gov/arcgis/rest/services/<service>/MapServer/<layer>/query
      ?where=SITUS_ADDR+LIKE+'...'&outFields=*&f=json
(exact service/layer path, field names, and whether an address-to-parcel
lookup is even the right layer vs. needing a separate geocoder — TBD by
actually opening the network tab while using the live viewer).

assessor.hamiltontn.gov is the other half — likely has an owner-name /
parcel-number search UI in front of the same or a related data source,
useful for the owner-name-fallback path and for backfilling owner_type /
mailing address when the Trustee/court data doesn't already have it.

This is exactly the kind of "wrong assumption cascades into everything
built on top" dependency the task calls out for a model/effort switch:
every source's join key and every dashboard map pin depends on getting
this endpoint right, so it's worth reasoning through carefully with a
live look at the real network traffic rather than guessing at a
plausible-looking ArcGIS URL pattern.

Once confirmed, this module should look like tax_delinquent.py: fetch()
hits the real endpoint, parse() extracts parcel_id/lat/long/owner fields
from its actual response shape, and main() should be capped (e.g. a
`--limit` / LOOKBACK env var matching the workflow's lookback-days input)
so a full backlog doesn't get hammered against the GIS service in one run.
"""

from __future__ import annotations

from db import get_connection, log_scrape


def fetch_unresolved(conn, limit: int = 200):
    """Pull up to `limit` unresolved (parcel_id IS NULL) rows across all
    three source tables, for address lookup. This part doesn't depend on
    the GIS discovery and can be implemented now — kept here rather than
    in build_unified.py since it's specific to what this module consumes.
    """
    rows = []
    for table in ("court_records", "tax_delinquent", "code_enforcement"):
        rows.extend(
            dict(r, _table=table)
            for r in conn.execute(
                f"SELECT * FROM {table} WHERE parcel_id IS NULL AND raw_address IS NOT NULL LIMIT ?",
                (limit,),
            ).fetchall()
        )
    return rows


def resolve_address(raw_address: str):
    raise NotImplementedError(
        "GIS endpoint not yet discovered — see this module's docstring. "
        "Do not guess at an ArcGIS URL; confirm it live against the real "
        "gismaps.hamiltontn.gov network traffic first."
    )


def main(limit: int = 200) -> None:
    conn = get_connection()
    try:
        unresolved = fetch_unresolved(conn, limit=limit)
        resolved_count = 0
        for row in unresolved:
            resolve_address(row["raw_address"])
            resolved_count += 1  # unreachable until resolve_address is implemented
        conn.commit()
        log_scrape(conn, "enrich_assessor", record_count=resolved_count, status="ok")
    except NotImplementedError as e:
        log_scrape(conn, "enrich_assessor", record_count=0, status="error", notes=str(e))
        raise
    finally:
        conn.commit()


if __name__ == "__main__":
    main()
