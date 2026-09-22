"""
Phase 3 — Hamilton County Assessor/GIS enrichment.

Live-verified 2026-09-22 against mapsdev.hamiltontn.gov (the county's new
ArcGIS Server stack, live since the 9/18 GIS-viewer switchover the task
brief flagged — the old gismaps.hamiltontn.gov Html5Viewer this module's
predecessor docstring was hunting for is gone, but the REST services behind
it are up and unauthenticated). Every fact below was hit live, not assumed.

The parcel layer
-----------------
  https://mapsdev.hamiltontn.gov/hcwa03/rest/services/Live_Parcels/MapServer/0
169,662 features confirmed via a returnCountOnly query (health_check()
re-checks this every run and aborts rather than enriching against a broken
or wildly-different service). maxRecordCount is 1000, but WHERE clauses
with a 100-item IN list are the practical batch-size limit before URLs/
query plans get unwieldy — 100 is what this module uses, POSTed (not GETted)
to /query since a 100-item IN list is well past a safe URL length.
returnCentroid is accepted by the endpoint but silently ignored (every
feature comes back with centroid=None) — centroids are computed client-side
from the returned polygon rings (returnGeometry=true, outSR=4326): area-
weighted centroid of the ring with the largest absolute shoelace area,
which folds in multipart geometries (disjoint pieces or donut holes) as a
single "pick the biggest piece" rule rather than needing real multipolygon
math. No shapely used or needed.

Fields actually used (of 58 on the layer — discovered via `/0?f=json`):
  MAP, GROUP_, PARCEL, TAX_MAP_NO   — join keys
  OWNERNAME1, OWNERNAME2            — owner (see placeholder note below)
  ADDRESS                           — situs address, already "123 MAIN ST"
                                       formatted; no separate situs city/zip
                                       field exists ANYWHERE on this layer,
                                       so situs_city/situs_zip cannot be
                                       backfilled from GIS at all (a real
                                       gap — see final report)
  MASTNUM/MADIRPFX/MASTNAME/MATYPESFX/MALINE2/MACITY/MASTATE/MAZIP
                                     — mailing address, joined into one line
  CURRENTUSE                        — short land-use code (RS/AG/CO/EX/...)
  APPVALUE, ASSVALUE                — appraised / assessed value

"Update in Progress" owner placeholder — filtered, not stored
---------------------------------------------------------------------------
A live sample of non-blank OWNERNAME2 rows turned up
OWNERNAME1="Update in Progress" / OWNERNAME2="Contact the Assessor of
Property 423-209-7300" on parcels mid-reassessment after a recent sale.
Storing that as owner_name would poison classify_owner_type() and the
probate owner-name fallback (step 4), so both fields are checked for this
placeholder text (case-insensitive) and treated as blank when found.

Canonical parcel_id <-> TAX_MAP_NO <-> assessor card URL
---------------------------------------------------------------------------
TAX_MAP_NO is MAP, a single space, GROUP_, a single space, PARCEL — with
GROUP_ blank (stored as a literal single space, e.g. "005  001") turning
into a DOUBLE space since the token is still there, just empty. Our
canonical parcel_id (parcel_utils.format_parcel_id) is the same three
tokens joined by "-", with a blank GROUP_ omitted entirely rather than
leaving a stray separator — e.g. "137A-T-016" or "005-001". So:
  parcel_id -> TAX_MAP_NO:  split parcel_id on "-"; 3 parts -> join with
                            single spaces; 2 parts (blank group) -> join
                            the two parts with a DOUBLE space.
  TAX_MAP_NO -> parcel_id:  don't parse the string back — a GIS query
                            response always carries MAP/GROUP_/PARCEL as
                            separate attributes, so just call
                            format_parcel_id(MAP, GROUP_, PARCEL) directly
                            (identical to how tax_delinquent.py builds it).
  parcel_id -> assessor card URL: assessor.hamiltontn.gov/card/<parcel_id
                            with every "-" replaced by "_">. Confirmed
                            EXACTLY against the layer's own RecordsOnl
                            field on multiple rows, including a blank-group
                            one: parcel_id "005-001" -> RecordsOnl was
                            literally ".../card/005_001" (blank group
                            omitted, not double-underscored) — matches
                            "137A-T-016" -> ".../card/137A_T_016" too.

Match-rate measurement (against the test DB's 7,926 Trustee-sourced
parcel_ids, all real Hamilton County parcels)
---------------------------------------------------------------------------
  - Direct TAX_MAP_NO IN (...) exact match: 7,588 / 7,926 = 95.7%.
  - The 338 misses split into two real, distinct causes (individually
    checked against the live layer, not assumed):
      * 288 have a PARCEL component with an embedded unit letter (condo/
        multi-unit parcels, e.g. our stored "004M001" or "186.01S005").
        GIS pads these internally with spaces around the letter
        ("004   M001") — a detail the upstream Trustee export collapses
        away. A per-miss fallback query `MAP='...' [AND GROUP_='...'] AND
        PARCEL LIKE '<numeric-base>%<letter-suffix>'` recovers 275 of
        these 288 (a single unambiguous hit); only run for misses whose
        parcel component actually matches this pattern, so it costs one
        extra request per genuine unit-suffix miss, not per miss overall.
      * The remaining 63 (338 - 275) were individually spot-checked by
        querying their MAP[/GROUP_] neighborhood on the live layer (e.g.
        067C-A-010, 100J-D-004, 058-186.01S005) — in every case the
        surrounding parcel numbers exist but that exact one doesn't; the
        base parcel has clearly been subdivided/resubdivided/merged since
        that Trustee bill was assessed (some of this ledger's rows go back
        to 1999 — see tax_delinquent.py). These are genuinely gone from
        today's live GIS, not a conversion bug, and are stored as
        geocode_status='unresolved' rather than retried every run.
  - FINAL measured match rate: (7,588 + 275) / 7,926 = 99.2%.

Geocoders — checked, mostly not used
---------------------------------------------------------------------------
  - Locator_TaxMapNo/GeocodeServer?SingleKey=<tax map no>: returns
    score=100 for an exact tax map no, but the /query endpoint above
    already gives every attribute (not just a point) for the same lookup,
    so this is redundant for step 1 and not called.
  - Locator_Parcels/GeocodeServer?SingleKey=<address>: also returns
    score=100 for a real situs address, but again only a point + no
    attributes — querying the layer's ADDRESS field directly (step 2)
    gets the same match plus full owner/value data in one round trip, so
    this is redundant too.
  - Locator_Addressing/GeocodeServer: tested with several known-good, live
    addresses in a few different formats (with/without abbreviating
    "Street", different capitalization) — every single one came back with
    an EMPTY candidate list. This locator appears non-functional or
    misconfigured on the post-9/18 dev server. Not used; if the county
    fixes it later, it'd only ever be a fallback for situs addresses that
    don't resolve directly against the parcel layer anyway, and Locator_
    Parcels already tests fine for that case.

What this module does (env vars in parens, defaults shown)
---------------------------------------------------------------------------
  0. Health check (returnCountOnly). Aborts with log_scrape(status="error")
     and a non-zero exit, touching nothing else, if the request fails or
     the count looks broken (< MIN_HEALTHY_PARCEL_COUNT).
  1. Parcel backfill (ENRICH_MAX_PARCELS, default 20000): parcels never
     enriched (last_enriched_at IS NULL) or last enriched over 30 days ago,
     never-enriched ones first, batched 100 at a time against TAX_MAP_NO
     IN (...) with the unit-suffix LIKE fallback described above. The gate
     is last_enriched_at ALONE, not "latitude IS NULL" — a genuinely-
     unresolved parcel also gets latitude=NULL forever, so gating on
     latitude too would re-query GIS for it every single run instead of
     respecting the 30-day cooldown (caught by this module's own second-
     run test: the first pass here used `latitude IS NULL OR ...` and the
     immediate second run still re-attempted all 63 unresolved parcels;
     fixed to gate on last_enriched_at only, then re-verified). Backfills
     lat/
     long (computed centroid), tax_map_no, owner_name/owner_type,
     mailing_*, land_use, assessed_value, appraised_value, is_absentee,
     geocode_source/status, last_enriched_at. situs_address is GIS's
     ADDRESS field ONLY when the parcel doesn't already have one (the
     Trustee/court-sourced address is treated as authoritative once
     present); everything else is always refreshed from GIS since GIS is
     the ground truth those fields describe. A parcel not found in GIS
     (even after the LIKE fallback) gets geocode_status='unresolved' +
     last_enriched_at so it isn't re-queried for 30 days.
  2. Address resolution (ENRICH_MAX_ADDRESSES, default 3000): court_records
     and code_enforcement rows with parcel_id IS NULL and a raw_address,
     normalized (parcel_utils.normalize_address) and batch-matched against
     the layer's ADDRESS field. Only an EXACT, UNAMBIGUOUS match (exactly
     one parcel with that address — a bare street name with no house
     number, e.g. "WHEELER AVE", is genuinely shared by 27 different
     parcels on the live layer and correctly skipped) sets parcel_id +
     resolution_method='address_match' on the source row and upserts the
     matched parcel's full GIS data.
  3. Point resolution (ENRICH_MAX_POINTS, default 5000): code_enforcement
     rows with latitude/longitude but no parcel_id. Points are bucketed
     into ~1.1km grid cells so nearby violations share one spatial query
     (an envelope around the bucket, padded ~1km) instead of one query per
     point; each returned candidate parcel is tested with a hand-rolled
     ray-casting point-in-polygon check (all rings, even-odd rule — proper
     hole handling, unlike the "just take the biggest ring" centroid
     shortcut) so no shapely dependency is needed. Sets the NEW
     resolution_method value 'point_in_parcel' (see final report — this
     value doesn't exist in db.py's documented vocabulary yet).
  4. Owner-name fallback (ENRICH_MAX_OWNER_LOOKUPS, default 500):
     court_records rows with case_type='probate', parcel_id IS NULL, and a
     raw_owner_name in "LAST FIRST[ MIDDLE]" form. Queries OWNERNAME1 for
     an exact match on the full name and, if that misses, on the name with
     a trailing middle initial dropped. Only accepted when exactly one
     parcel matches AND the matched owner classifies as 'individual' (a
     probate case whose decedent's parcel is titled to an LLC/trust isn't
     a fallback-name match — that's a data anomaly worth NOT auto-linking).
     Deceased owners' parcels routinely stay titled in the decedent's name
     for months/years, which is exactly the case this step is FOR; common
     names are the known failure mode (silently skipped as ambiguous, not
     guessed at).

Every step commits independently (main() commits after each numbered step)
so a mid-run crash keeps whatever progress the earlier steps made. A
step's failed/unmatched attempts are recorded in source_state (see
_load_attempts/_save_attempts) so a permanently-unmatchable address/owner-
name isn't re-queried against GIS every single day — retried after the
same 30-day window as the parcel refresh, and pruned to the 5,000 most
recent entries per bookkeeping key so this can't grow unbounded.

Second-run behavior (verified, not assumed): re-running this module
immediately afterward does almost no GIS work — every already-resolved
parcel has a fresh last_enriched_at (skipped by the `< 30 days` filter),
and every unresolved address/owner attempt is inside its 30-day retry
window (skipped by _should_attempt) — see this module's test run notes in
the final report for the actual before/after counts and timings.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from db import get_connection, get_state, log_scrape, now_iso, set_state, upsert_parcel, upsert_record
from parcel_utils import classify_owner_type, format_parcel_id, is_absentee, normalize_address

GIS_BASE = os.environ.get("HC_GIS_BASE", "https://mapsdev.hamiltontn.gov/hcwa03/rest/services")
PARCEL_LAYER = f"{GIS_BASE}/Live_Parcels/MapServer/0"
ASSESSOR_CARD_BASE = "https://assessor.hamiltontn.gov/card"

MAX_PARCELS = int(os.environ.get("ENRICH_MAX_PARCELS", "20000"))
MAX_ADDRESSES = int(os.environ.get("ENRICH_MAX_ADDRESSES", "3000"))
MAX_POINTS = int(os.environ.get("ENRICH_MAX_POINTS", "5000"))
MAX_OWNER_LOOKUPS = int(os.environ.get("ENRICH_MAX_OWNER_LOOKUPS", "500"))

BATCH_SIZE = 100                # IN-list batch size — see docstring
REFRESH_DAYS = 30               # re-enrich a resolved parcel, or retry an unresolved lookup, after this many days
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5
MIN_HEALTHY_PARCEL_COUNT = 100_000   # real count is ~169,662; well below this means the service is down/broken
POLITE_PAUSE_SECONDS = 0.05     # tiny pause after every request — sequential + modest, never hammering the server

QUERY_FIELDS = (
    "MAP,GROUP_,PARCEL,TAX_MAP_NO,OWNERNAME1,OWNERNAME2,ADDRESS,"
    "MASTNUM,MADIRPFX,MASTNAME,MATYPESFX,MALINE2,MACITY,MASTATE,MAZIP,"
    "CURRENTUSE,APPVALUE,ASSVALUE"
)

_UNIT_SUFFIX_RE = re.compile(r"^(\d+(?:\.\d+)?)([A-Z]\d+)$")
_PLACEHOLDER_OWNER_RE = re.compile(r"UPDATE IN PROGRESS|CONTACT THE ASSESSOR", re.IGNORECASE)
_TRAILING_INITIAL_RE = re.compile(r"^[A-Z]\.?$")


# --------------------------------------------------------------------------
# Low-level GIS access
# --------------------------------------------------------------------------

def _esc(value: str) -> str:
    """Escape a single-quote for embedding in an ArcGIS SQL WHERE clause."""
    return value.replace("'", "''")


def _post_query(params: dict, stats: dict) -> dict:
    """POST to the parcel layer's /query endpoint with a couple of retries
    and backoff — a couple of retries per the ground rules, not an
    unbounded loop. Always POST (never GET): several of this module's WHERE
    clauses are 100-item IN lists that would blow past a safe URL length.
    Retries both network-level failures and ArcGIS's own in-band error
    responses (e.g. the transient "Unable to complete operation" seen
    during development), since either can be transient on a shared server.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(f"{PARCEL_LAYER}/query", data=params, timeout=REQUEST_TIMEOUT)
            stats["requests"] += 1
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
        else:
            if isinstance(data, dict) and "error" in data:
                last_exc = RuntimeError(f"ArcGIS error: {data['error']}")
            else:
                time.sleep(POLITE_PAUSE_SECONDS)
                return data
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def health_check(stats: dict) -> int:
    data = _post_query({"where": "1=1", "returnCountOnly": "true", "f": "json"}, stats)
    return int(data.get("count", 0))


# --------------------------------------------------------------------------
# parcel_id <-> TAX_MAP_NO
# --------------------------------------------------------------------------

def parcel_id_to_tax_map_no(parcel_id: str) -> str | None:
    """See module docstring for the verified conversion rule."""
    parts = parcel_id.split("-")
    if len(parts) == 3:
        return f"{parts[0]} {parts[1]} {parts[2]}"
    if len(parts) == 2:
        return f"{parts[0]}  {parts[1]}"
    return None


def assessor_card_url(parcel_id: str) -> str:
    """Confirmed byte-for-byte against the layer's own RecordsOnl field —
    see module docstring. Not persisted anywhere (no column for it), just
    documented here and in the final report as a derivable value.
    """
    return f"{ASSESSOR_CARD_BASE}/{parcel_id.replace('-', '_')}"


def _query_by_tax_map_no(tax_map_nos: list[str], stats: dict) -> dict:
    quoted = ",".join(f"'{_esc(t)}'" for t in tax_map_nos)
    where = f"TAX_MAP_NO IN ({quoted})"
    return _post_query(
        {"where": where, "outFields": QUERY_FIELDS, "returnGeometry": "true", "outSR": "4326", "f": "json"}, stats
    )


def _try_unit_suffix_fallback(parcel_id: str, stats: dict) -> dict | None:
    """Recover the ~95% of unit-suffix misses (condo/multi-unit parcels
    whose PARCEL value GIS pads with internal spaces around the letter,
    e.g. "004   M001" vs. our collapsed "004M001") with a LIKE query. Only
    called for a parcel_id whose parcel component actually matches the
    letter-in-the-middle pattern, so it never fires on a plain numeric
    miss (those are genuinely-gone parcels — see docstring).
    """
    parts = parcel_id.split("-")
    match = _UNIT_SUFFIX_RE.match(parts[-1])
    if not match:
        return None
    base, suffix = match.group(1), match.group(2)
    if len(parts) == 3:
        where = f"MAP='{_esc(parts[0])}' AND GROUP_='{_esc(parts[1])}' AND PARCEL LIKE '{_esc(base)}%{_esc(suffix)}'"
    elif len(parts) == 2:
        where = f"MAP='{_esc(parts[0])}' AND PARCEL LIKE '{_esc(base)}%{_esc(suffix)}'"
    else:
        return None
    data = _post_query(
        {"where": where, "outFields": QUERY_FIELDS, "returnGeometry": "true", "outSR": "4326", "f": "json"}, stats
    )
    feats = data.get("features", [])
    return feats[0] if len(feats) == 1 else None


# --------------------------------------------------------------------------
# Geometry — area-weighted centroid (largest ring) and point-in-polygon,
# both hand-rolled since no shapely is available.
# --------------------------------------------------------------------------

def _ring_signed_area(ring: list[list[float]]) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    area = _ring_signed_area(ring)
    if abs(area) < 1e-12:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else (0.0, 0.0)
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx / (6 * area), cy / (6 * area)


def feature_centroid_latlon(geometry: dict | None) -> tuple[float | None, float | None]:
    """(lat, lon) of the area-weighted centroid of the largest ring by
    absolute area — folds in multipart geometry (see module docstring).
    """
    if not geometry or not geometry.get("rings"):
        return None, None
    best_ring = max(geometry["rings"], key=lambda r: abs(_ring_signed_area(r)))
    cx, cy = _ring_centroid(best_ring)  # x=lon, y=lat (outSR=4326)
    return cy, cx


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    """Even-odd rule across every ring (holes included) — the full-fidelity
    test used for point resolution, unlike the "biggest ring only" centroid
    shortcut which is fine for a marker but not for containment.
    """
    count = sum(1 for ring in rings if _point_in_ring(lon, lat, ring))
    return count % 2 == 1


# --------------------------------------------------------------------------
# GIS attributes -> parcels table fields
# --------------------------------------------------------------------------

def _clean(value) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def _owner_name_from_attrs(attrs: dict) -> str | None:
    name1 = _clean(attrs.get("OWNERNAME1"))
    name2 = _clean(attrs.get("OWNERNAME2"))
    if name1 and _PLACEHOLDER_OWNER_RE.search(name1):
        name1 = None
    if name2 and _PLACEHOLDER_OWNER_RE.search(name2):
        name2 = None
    combined = " ".join(p for p in (name1, name2) if p)
    return _clean(combined)


def _mailing_address_from_attrs(attrs: dict) -> str | None:
    parts = [attrs.get(f) for f in ("MASTNUM", "MADIRPFX", "MASTNAME", "MATYPESFX", "MALINE2")]
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return " ".join(cleaned) or None


def gis_attrs_to_parcel_kwargs(attrs: dict, geometry: dict | None) -> dict:
    situs_address = _clean(attrs.get("ADDRESS"))
    mailing_address = _mailing_address_from_attrs(attrs)
    owner_name = _owner_name_from_attrs(attrs)
    kwargs = {
        "tax_map_no": _clean(attrs.get("TAX_MAP_NO")),
        "owner_name": owner_name,
        "owner_type": classify_owner_type(owner_name),
        "mailing_address": mailing_address,
        "mailing_city": _clean(attrs.get("MACITY")),
        "mailing_state": _clean(attrs.get("MASTATE")),
        "mailing_zip": _clean(attrs.get("MAZIP")),
        "situs_address": situs_address,
        "land_use": _clean(attrs.get("CURRENTUSE")),
        "appraised_value": attrs.get("APPVALUE"),
        "assessed_value": attrs.get("ASSVALUE"),
        "geocode_source": "assessor_gis",
        "geocode_status": "resolved",
        "last_enriched_at": now_iso(),
    }
    lat, lon = feature_centroid_latlon(geometry)
    if lat is not None and lon is not None:
        kwargs["latitude"] = lat
        kwargs["longitude"] = lon
    absentee = is_absentee(situs_address, mailing_address)
    if absentee is not None:
        kwargs["is_absentee"] = absentee
    return kwargs


def upsert_parcel_from_gis(conn, parcel_id: str, attrs: dict, geometry: dict | None) -> None:
    """upsert_parcel() merges non-null fields unconditionally — fine (even
    desirable) for owner/value/mailing fields since GIS is the ground truth
    for those, but situs_address should only fill a GAP, not overwrite a
    perfectly good address the source record already supplied (per spec).
    """
    kwargs = gis_attrs_to_parcel_kwargs(attrs, geometry)
    existing = conn.execute("SELECT situs_address FROM parcels WHERE parcel_id = ?", (parcel_id,)).fetchone()
    if existing and existing["situs_address"]:
        kwargs.pop("situs_address", None)
    upsert_parcel(conn, parcel_id, **kwargs)


# --------------------------------------------------------------------------
# Attempt bookkeeping (source_state) — bounds retries of addresses/owner
# names/points that don't resolve, without needing a new DB column.
# --------------------------------------------------------------------------

ATTEMPTS_CAP = 5000


def _attempts_key(name: str) -> str:
    return f"enrich_assessor:attempts:{name}"


def _load_attempts(conn, key: str) -> dict:
    raw = get_state(conn, key)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_attempts(conn, key: str, attempts: dict) -> None:
    if len(attempts) > ATTEMPTS_CAP:
        attempts = dict(sorted(attempts.items(), key=lambda kv: kv[1])[-ATTEMPTS_CAP:])
    set_state(conn, key, json.dumps(attempts))


def _should_attempt(attempts: dict, dedupe_key: str, retry_days: int = REFRESH_DAYS) -> bool:
    last = attempts.get(dedupe_key)
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_dt).days >= retry_days


# --------------------------------------------------------------------------
# Step 1 — parcel backfill
# --------------------------------------------------------------------------

def backfill_parcels(conn, stats: dict, limit: int) -> dict:
    counters = {"candidates": 0, "resolved": 0, "unit_suffix_recovered": 0, "unresolved": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT parcel_id FROM parcels
        WHERE last_enriched_at IS NULL OR last_enriched_at < ?
        ORDER BY (last_enriched_at IS NOT NULL), last_enriched_at
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    parcel_ids = [r["parcel_id"] for r in rows]
    counters["candidates"] = len(parcel_ids)

    for i in range(0, len(parcel_ids), BATCH_SIZE):
        batch = parcel_ids[i:i + BATCH_SIZE]
        tax_map_nos = {pid: tmn for pid in batch if (tmn := parcel_id_to_tax_map_no(pid))}
        if not tax_map_nos:
            continue
        data = _query_by_tax_map_no(list(tax_map_nos.values()), stats)

        found: dict[str, dict] = {}
        for feat in data.get("features", []):
            attrs = feat["attributes"]
            pid = format_parcel_id(attrs.get("MAP"), attrs.get("GROUP_"), attrs.get("PARCEL"))
            if pid:
                found[pid] = feat

        still_missing = [pid for pid in tax_map_nos if pid not in found]
        for pid in still_missing[:]:
            recovered = _try_unit_suffix_fallback(pid, stats)
            if recovered:
                found[pid] = recovered
                still_missing.remove(pid)
                counters["unit_suffix_recovered"] += 1

        for pid, feat in found.items():
            upsert_parcel_from_gis(conn, pid, feat["attributes"], feat.get("geometry"))
            counters["resolved"] += 1

        now = now_iso()
        for pid in still_missing:
            upsert_parcel(conn, pid, geocode_source="assessor_gis", geocode_status="unresolved", last_enriched_at=now)
            counters["unresolved"] += 1

        conn.commit()  # per-batch, so a crash mid-backlog keeps everything done so far

    return counters


# --------------------------------------------------------------------------
# Step 2 — address resolution (court_records, code_enforcement)
# --------------------------------------------------------------------------

ADDRESS_SOURCE_TABLES = ("court_records", "code_enforcement")


def _street_only(raw_address: str | None) -> str | None:
    """GIS's ADDRESS field is bare street-only ("123 MAIN ST", no city/
    state/zip — confirmed above). Some sources' raw_address is already that
    bare form (tax_sale.py's PDF has no city column at all), but others
    append city/state (code_enforcement.py's CSV has real per-row city
    data, e.g. "450 BEREAN LN, CHATTANOOGA, TN") — an exact-string match
    against GIS would silently never match those rows. Strip everything
    from the first comma onward before normalizing, so both forms compare
    on the street portion alone.
    """
    if not raw_address:
        return None
    return raw_address.split(",", 1)[0]


def _fetch_address_candidates(conn, limit: int) -> list[dict]:
    candidates = []
    for table in ADDRESS_SOURCE_TABLES:
        attempts = _load_attempts(conn, _attempts_key(table))
        for r in conn.execute(
            f"SELECT id, dedupe_key, raw_address FROM {table} WHERE parcel_id IS NULL AND raw_address IS NOT NULL"
        ):
            if _should_attempt(attempts, r["dedupe_key"]):
                candidates.append({"table": table, "dedupe_key": r["dedupe_key"], "raw_address": r["raw_address"]})
    return candidates[:limit]


def resolve_addresses(conn, stats: dict, limit: int) -> dict:
    counters = {"attempted": 0, "matched": 0, "ambiguous": 0, "not_found": 0}
    candidates = _fetch_address_candidates(conn, limit)
    counters["attempted"] = len(candidates)
    if not candidates:
        return counters

    by_norm: dict[str, list[dict]] = {}
    for c in candidates:
        norm = normalize_address(_street_only(c["raw_address"]))
        if norm:
            by_norm.setdefault(norm, []).append(c)

    norm_list = list(by_norm.keys())
    match_attrs: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for i in range(0, len(norm_list), BATCH_SIZE):
        batch = norm_list[i:i + BATCH_SIZE]
        quoted = ",".join(f"'{_esc(n)}'" for n in batch)
        data = _post_query(
            {
                "where": f"ADDRESS IN ({quoted})", "outFields": QUERY_FIELDS,
                "returnGeometry": "true", "outSR": "4326", "f": "json",
            },
            stats,
        )
        grouped: dict[str, list[dict]] = {}
        for feat in data.get("features", []):
            addr = (feat["attributes"].get("ADDRESS") or "").strip().upper()
            grouped.setdefault(addr, []).append(feat)
        for norm in batch:
            feats = grouped.get(norm, [])
            if len(feats) == 1:
                match_attrs[norm] = feats[0]
            elif len(feats) > 1:
                ambiguous.add(norm)

    attempts_by_table = {t: _load_attempts(conn, _attempts_key(t)) for t in ADDRESS_SOURCE_TABLES}
    now = now_iso()
    for norm, rows in by_norm.items():
        feat = match_attrs.get(norm)
        pid = None
        if feat:
            pid = format_parcel_id(feat["attributes"].get("MAP"), feat["attributes"].get("GROUP_"), feat["attributes"].get("PARCEL"))
        if pid:
            upsert_parcel_from_gis(conn, pid, feat["attributes"], feat.get("geometry"))
            for row in rows:
                upsert_record(conn, row["table"], row["dedupe_key"], parcel_id=pid, resolution_method="address_match")
                attempts_by_table[row["table"]].pop(row["dedupe_key"], None)
                counters["matched"] += 1
            continue
        for row in rows:
            attempts_by_table[row["table"]][row["dedupe_key"]] = now
        if norm in ambiguous:
            counters["ambiguous"] += len(rows)
        else:
            counters["not_found"] += len(rows)

    for table, attempts in attempts_by_table.items():
        _save_attempts(conn, _attempts_key(table), attempts)
    return counters


# --------------------------------------------------------------------------
# Step 3 — point resolution (code_enforcement lat/long -> parcel)
# --------------------------------------------------------------------------

POINT_BUCKET_PRECISION = 2      # ~1.1km grid cells at this latitude
POINT_ENVELOPE_PAD = 0.01       # degrees, ~1km — comfortably covers a bucket + its parcels


def resolve_points(conn, stats: dict, limit: int) -> dict:
    counters = {"attempted": 0, "matched": 0, "unmatched": 0}
    attempts = _load_attempts(conn, _attempts_key("code_enforcement_points"))
    rows = [
        r for r in conn.execute(
            "SELECT id, dedupe_key, latitude, longitude FROM code_enforcement "
            "WHERE parcel_id IS NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
        )
        if _should_attempt(attempts, r["dedupe_key"])
    ][:limit]
    counters["attempted"] = len(rows)
    if not rows:
        return counters

    buckets: dict[tuple[float, float], list] = {}
    for r in rows:
        key = (round(r["latitude"], POINT_BUCKET_PRECISION), round(r["longitude"], POINT_BUCKET_PRECISION))
        buckets.setdefault(key, []).append(r)

    now = now_iso()
    for bucket_rows in buckets.values():
        lats = [r["latitude"] for r in bucket_rows]
        lons = [r["longitude"] for r in bucket_rows]
        envelope = {
            "xmin": min(lons) - POINT_ENVELOPE_PAD, "ymin": min(lats) - POINT_ENVELOPE_PAD,
            "xmax": max(lons) + POINT_ENVELOPE_PAD, "ymax": max(lats) + POINT_ENVELOPE_PAD,
            "spatialReference": {"wkid": 4326},
        }
        data = _post_query(
            {
                "where": "1=1", "geometry": json.dumps(envelope), "geometryType": "esriGeometryEnvelope",
                "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                "outFields": QUERY_FIELDS, "returnGeometry": "true", "outSR": "4326", "f": "json",
            },
            stats,
        )
        feats = [f for f in data.get("features", []) if f.get("geometry", {}).get("rings")]

        for row in bucket_rows:
            containing = [f for f in feats if point_in_polygon(row["longitude"], row["latitude"], f["geometry"]["rings"])]
            if len(containing) == 1:
                attrs = containing[0]["attributes"]
                pid = format_parcel_id(attrs.get("MAP"), attrs.get("GROUP_"), attrs.get("PARCEL"))
                if pid:
                    upsert_parcel_from_gis(conn, pid, attrs, containing[0].get("geometry"))
                    upsert_record(conn, "code_enforcement", row["dedupe_key"], parcel_id=pid, resolution_method="point_in_parcel")
                    attempts.pop(row["dedupe_key"], None)
                    counters["matched"] += 1
                    continue
            attempts[row["dedupe_key"]] = now
            counters["unmatched"] += 1
        conn.commit()

    _save_attempts(conn, _attempts_key("code_enforcement_points"), attempts)
    return counters


# --------------------------------------------------------------------------
# Step 4 — owner-name fallback (probate)
# --------------------------------------------------------------------------

def _name_variants(raw_owner_name: str) -> list[str]:
    name = re.sub(r"\s+", " ", raw_owner_name.strip().upper())
    variants = [name]
    parts = name.split(" ")
    if len(parts) >= 3 and _TRAILING_INITIAL_RE.match(parts[-1]):
        variants.append(" ".join(parts[:-1]))
    return variants


def resolve_owner_fallback(conn, stats: dict, limit: int) -> dict:
    counters = {"attempted": 0, "matched": 0, "ambiguous": 0, "not_individual": 0, "not_found": 0}
    attempts = _load_attempts(conn, _attempts_key("owner_fallback"))
    rows = [
        r for r in conn.execute(
            "SELECT id, dedupe_key, raw_owner_name FROM court_records "
            "WHERE case_type = 'probate' AND parcel_id IS NULL AND raw_owner_name IS NOT NULL"
        )
        if _should_attempt(attempts, r["dedupe_key"])
    ][:limit]
    counters["attempted"] = len(rows)
    if not rows:
        return counters

    now = now_iso()
    for row in rows:
        feat = None
        was_ambiguous = False
        for variant in _name_variants(row["raw_owner_name"]):
            data = _post_query(
                {
                    "where": f"OWNERNAME1='{_esc(variant)}'", "outFields": QUERY_FIELDS,
                    "returnGeometry": "true", "outSR": "4326", "f": "json",
                },
                stats,
            )
            feats = data.get("features", [])
            if len(feats) == 1:
                feat = feats[0]
                break
            if len(feats) > 1:
                was_ambiguous = True
                break

        if feat:
            attrs = feat["attributes"]
            owner_name = _owner_name_from_attrs(attrs)
            if classify_owner_type(owner_name) != "individual":
                counters["not_individual"] += 1
            else:
                pid = format_parcel_id(attrs.get("MAP"), attrs.get("GROUP_"), attrs.get("PARCEL"))
                if pid:
                    upsert_parcel_from_gis(conn, pid, attrs, feat.get("geometry"))
                    upsert_record(conn, "court_records", row["dedupe_key"], parcel_id=pid, resolution_method="owner_name_fallback")
                    attempts.pop(row["dedupe_key"], None)
                    counters["matched"] += 1
                    continue
        elif was_ambiguous:
            counters["ambiguous"] += 1
        else:
            counters["not_found"] += 1
        attempts[row["dedupe_key"]] = now

    conn.commit()
    _save_attempts(conn, _attempts_key("owner_fallback"), attempts)
    return counters


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    conn = get_connection()
    stats = {"requests": 0}
    started = time.time()

    try:
        count = health_check(stats)
    except Exception as exc:  # noqa: BLE001 — any failure here must not touch data
        log_scrape(conn, "enrich_assessor", 0, status="error", notes=f"GIS health check request failed: {exc}")
        conn.commit()
        print(f"enrich_assessor: health check failed ({exc}) — aborting without touching data.")
        sys.exit(1)

    if count < MIN_HEALTHY_PARCEL_COUNT:
        notes = f"GIS health check returned count={count}, expected >= {MIN_HEALTHY_PARCEL_COUNT}"
        log_scrape(conn, "enrich_assessor", 0, status="error", notes=notes)
        conn.commit()
        print(f"enrich_assessor: {notes} — aborting without touching data.")
        sys.exit(1)

    parcel_stats = backfill_parcels(conn, stats, MAX_PARCELS)
    conn.commit()
    address_stats = resolve_addresses(conn, stats, MAX_ADDRESSES)
    conn.commit()
    point_stats = resolve_points(conn, stats, MAX_POINTS)
    conn.commit()
    owner_stats = resolve_owner_fallback(conn, stats, MAX_OWNER_LOOKUPS)
    conn.commit()

    elapsed = round(time.time() - started, 1)
    total_resolved = parcel_stats["resolved"] + address_stats["matched"] + point_stats["matched"] + owner_stats["matched"]
    total_unresolved = parcel_stats["unresolved"] + address_stats["not_found"] + address_stats["ambiguous"] + point_stats["unmatched"] + owner_stats["not_found"] + owner_stats["ambiguous"] + owner_stats["not_individual"]
    notes = (
        f"parcels={parcel_stats}, addr={address_stats}, points={point_stats}, "
        f"owners={owner_stats}, unresolved={total_unresolved}, requests={stats['requests']}, seconds={elapsed}"
    )
    log_scrape(conn, "enrich_assessor", record_count=total_resolved, status="ok", notes=notes)
    conn.commit()
    print(f"enrich_assessor: {notes}")


if __name__ == "__main__":
    main()
