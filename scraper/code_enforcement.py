"""
Phase 3 — City of Chattanooga Code Enforcement Violations, ArcGIS Hub CSV item.

Live-verified 2026-09-22 against the real ~86MB file (item
19f35e4e09c041718905088a1fc7a6bb on arcgis.com, owner odextract_CHATTGIS),
not guessed from the dead Socrata dataset the previous version of this
module targeted (chattadata.org/resource/qcrz-rvw7 — retired, see CLAUDE.md
"Brief errors confirmed"). Every number below came from parsing the actual
downloaded CSV, not from the ArcGIS docs.

No query API — download the whole file
-----------------------------------------
The Hub's CSV-to-FeatureServer query proxy 413s above ~2.5MB, so there is no
way to page or filter server-side; `fetch()` does a single plain streamed
GET of the full file (no Range/resume — a resumed request against this
endpoint previously produced IncompleteRead in recon) and change detection
happens against a separate lightweight metadata call first.

Change detection: `modified`, checked defensively with `size`
-----------------------------------------------------------------
`.../items/<id>?f=json` (no `/data`) returns `size` (bytes) and `modified`
(epoch ms) instantly. Both are stored in `source_state` after a successful
ingest and compared next run; the ~86MB download is skipped only when
*both* match, which is slightly more conservative than the brief's
"just compare modified" — CLAUDE.md's earlier recon flagged that `modified`
alone can move on a scheduled re-upload with no content change, and pairing
it with `size` costs nothing (one extra stored string) while catching that
case: a same-size, same-content re-upload with a new `modified` still
downloads once more than strictly necessary, but a genuinely-changed file
almost never keeps the exact same byte size, so this does not create a
false "unchanged" skip. A skipped run still logs a `scrape_log` row with
`record_count` = however many rows this source currently has in the DB
(not 0), so `quality_check.py`'s 7-day-average floor never fires just
because nothing changed upstream — see `main()`.

Real schema (22 columns, all confirmed against actual rows)
-----------------------------------------------------------------
`case_number, date_entered, date_cited, date_paid, date_corrected,
citation_amount, description, description_extended, flag_dangerous,
flag_paid, comments, record_id, status, street_number, street_name, city,
state, location_geography, latitude, longitude, location_wkt,
council_district`. `date_cited`/`date_paid`/`date_corrected`/
`citation_amount` are essentially always blank/zero in this export
(confirmed: 0 non-blank `date_paid` and 0 nonzero `citation_amount` across
all 107,452 rows) — `date_entered` really is the only usable date, matching
the brief. `comments` and `description_extended` both carry embedded
newlines, handled correctly by the csv module's quoted-field parsing.
`description_extended` turned out to be static per-code ordinance
boilerplate (the same paragraph repeats verbatim across every case sharing
a code), not a case-specific note — still worth a short hint since it spells
out what the code section actually means, but not a source of per-property
detail. `comments` is NOT ingested: a manual sample showed free-text
inspector notes that can include names and phone numbers, and nothing here
needs it.

Status codes (all 6 seen, tabulated from the real file, n=107,452)
-----------------------------------------------------------------------
  C     104,710 (97.5%)  closed
  LIT     1,459 ( 1.4%)  open — case referred to litigation (highest
                          flag_dangerous rate of the open statuses, 21.6%)
  INPR    1,257 ( 1.2%)  open — in progress / actively being worked
  DUP        22 (<0.1%)  duplicate case entry — skipped entirely per spec,
                          never reaches the kept-record count
  O           3 (<0.1%)  open (too rare to be sure of the exact meaning
                          beyond "not closed")
  OUT         1 (<0.1%)  open (single row; same caveat as `O`)
No `date_cited`/`date_corrected` pattern distinguishes a finer "pending"
sub-state within the open codes, so `status` collapses to exactly
'open' | 'closed' (never 'pending' in practice, though the column and the
db.py schema comment allow it) — see "Judgment calls" below for why the
raw code is preserved in `description` instead of a new column.

Row-count filter, measured
------------------------------
Of 107,452 total rows: 22 are DUP (skipped). Of the remaining 107,430,
keeping "open at any age" OR "entered within CLOSED_ROW_MAX_AGE_DAYS=730
days" keeps 29,361 rows (2,720 open + 26,641 closed-and-recent), dropping
78,069 closed rows older than 2 years. Collapsing exact-duplicate
`record_id`s (774 record_ids had >1 row; status and description never
differed within a duplicate group in this file — only geocoding fields like
latitude/longitude sometimes did, in 255 of those 774 groups) brings it to
28,640 rows actually upserted. 28,640 rows is the same order of magnitude
as tax_delinquent's ~54K-row ledger, so this does not meaningfully bloat
the DB; no need to shorten the 730-day window.

Ordinance-code -> violation_type mapping, built from what's actually here
--------------------------------------------------------------------------
`CODE_CATEGORY` below maps every one of the 100+ distinct code prefixes
seen in the kept rows to one of scoring.py's five `VIOLATION_TYPE_TIER`
buckets, grouped by what the ordinance section actually governs (confirmed
against each code's own label text and, as a sanity check, its
`flag_dangerous` TRUE-rate — e.g. 21-76(e) "Dangerous Structure or
Premises" is 77.7% flag_dangerous, 21-133 "Litter" is 0.0%):
  condemnation (85): occupancy/re-occupancy of a condemned or unlawfully
    used structure prohibited, sale of a violation-noticed property
    (21-76(a)/(c)/(d), 21-80, 21-82, 21-83, 21-61).
  unsafe_structure (80): structural, fire, and life-safety hazards
    (21-76(b)/(e), 21-84, 21-129, 21-172, 21-175, structural-member and
    security subsections of 21-128/21-171, electrical/heating/fire-safety
    sections 21-188 through 21-197).
  vacant_building (70): boarding/securing an empty structure (21-87 and
    its subsections).
  nuisance (45): exterior/property-level nuisances not about the
    structure's physical condition — litter, overgrowth, junk/inoperable
    vehicles, yard trash, defacement, pests (21-127 through 21-136,
    18-32(d), 18-86, 24-286(f)).
  property_maintenance (40, also the fallback default): routine building
    upkeep — roofs, windows, plumbing, mechanical/duct systems, accessory
    structures, pools (the remaining 21-128/21-171/21-17x/21-18x
    subsections).
Per the spec, `flag_dangerous='true'` escalates a row to at least
unsafe_structure even if its code would otherwise map lower — measured
impact: this reclassifies rows for codes like 21-128 (Exterior of
Structure, 8.6% flag_dangerous) or 21-133 (Litter, 0.0%) only when that
specific row was actually flagged, not the whole code family. Only 8 low-
volume codes seen in the kept rows (27 rows total, e.g. 21-89 "Stop Work
Order", 21-186 "Storm drainage") aren't in the table and fall through to
`DEFAULT_VIOLATION_TYPE_TIER` in scoring.py unless `flag_dangerous` lifts
them — negligible volume, listed in the module's own run summary if it
recurs.

Judgment calls
------------------
- Raw status code kept in `description`, not a new column: the schema only
  allows 'open'/'closed'/'pending' and this repo's ground rules for this
  task forbid editing db.py, so the LIT/INPR/O/OUT distinction (all
  collapsed to 'open') is preserved as a bracketed suffix, e.g.
  "21-76(e) - Dangerous Structure or Premises — <ordinance hint> [LIT]".
- `raw_address` is `<normalize_address(street_number + street_name)>, <CITY>,
  <STATE>` using the CSV's own `city`/`state` values (falling back to
  "CHATTANOOGA"/"TN" only when blank) rather than hardcoding Chattanooga —
  107,383/107,452 rows (99.9%) are literally CHATTANOOGA, but a small
  number are HIXSON/HAMILTON COUNTY/RED BANK/EAST RIDGE, presumably annexed
  or contracted areas the City's Cityview system still covers.
- lat/long are validated against a Hamilton County bounding box
  (34.98-35.47N, -85.50 to -84.95W) before being stored; every one of the
  78,264 populated pairs in the live file was already inside it, so this is
  pure defense against a future bad export, not a real filter today.
- Duplicate `record_id`s collapse to whichever copy has more populated
  geo/district fields (a simple completeness score), since the only
  observed difference between duplicate rows of the same record_id was
  geocoding completeness, never status or description (see "Row-count
  filter" above) — ties keep the first-seen row.
- `parcel_id` is always NULL and `resolution_method` always 'unresolved' on
  insert, with `keep_existing=("parcel_id", "resolution_method")` on
  update, so a future `enrich_assessor.py` match is never clobbered by a
  daily re-scrape. NOTE for that future work: ~1.5% of kept rows (425/28,640)
  are vacant lots with `street_number='0'` where `street_name` embeds the
  county's own Map/Group/Parcel key in brackets, e.g.
  "VACANT LOT [110A B 002.11] HWY 153" — worth a native-parcel-id fast path
  before falling back to address matching, deliberately NOT implemented
  here since the spec calls for `parcel_id=None` on every insert from this
  module.
"""

from __future__ import annotations

import csv
import datetime
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

import requests

from db import get_connection, get_state, log_scrape, set_state, upsert_record
from parcel_utils import normalize_address

ITEM_ID = "19f35e4e09c041718905088a1fc7a6bb"
DATA_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}/data"
META_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}?f=json"
SOURCE_URL = f"https://www.arcgis.com/home/item.html?id={ITEM_ID}"

STATE_KEY_MODIFIED = "code_enforcement:item_modified"
STATE_KEY_SIZE = "code_enforcement:item_size"

CLOSED_STATUS_CODES = {"C"}
SKIP_STATUS_CODES = {"DUP"}
# Everything else seen (INPR, LIT, OUT, O) — and, defensively, anything not
# yet seen — normalizes to 'open' rather than being silently dropped.

CLOSED_ROW_MAX_AGE_DAYS = 730  # keep a closed row only if entered within this many days

HAMILTON_LAT_RANGE = (34.98, 35.47)
HAMILTON_LON_RANGE = (-85.50, -84.95)

DESCRIPTION_HINT_MAX_CHARS = 150

CODE_RE = re.compile(r"^(\d+-\d+(?:\([^)]+\)|[a-z])?)\s*-?\s*(.*)$")

# --- violation_type categories: must match scoring.VIOLATION_TYPE_TIER keys ---
CONDEMNATION = "condemnation"
UNSAFE_STRUCTURE = "unsafe_structure"
VACANT_BUILDING = "vacant_building"
NUISANCE = "nuisance"
PROPERTY_MAINTENANCE = "property_maintenance"

_CATEGORY_RANK = {
    PROPERTY_MAINTENANCE: 0,
    NUISANCE: 1,
    VACANT_BUILDING: 2,
    UNSAFE_STRUCTURE: 3,
    CONDEMNATION: 4,
}

# Built from the actual code frequency table in the live file (see module
# docstring "Row-count filter" / "Ordinance-code mapping" for the counts
# and reasoning). Keys are the code exactly as it appears in `description`
# (e.g. "21-76(e)"); a bare section number with no lettered subsection is
# its own key when the source uses one (e.g. "21-87").
CODE_CATEGORY = {
    # --- condemnation: structure/occupancy declared unfit, condemned, or unlawful ---
    "21-76(a)": CONDEMNATION,   # Unsound Structure
    "21-76(c)": CONDEMNATION,   # Structure unfit for human occupancy
    "21-76(d)": CONDEMNATION,   # Unlawful Structure
    "21-80": CONDEMNATION,      # Occupancy of Condemned Structure prohibited
    "21-82": CONDEMNATION,      # Certificate of Occupancy required before re-occupancy
    "21-83": CONDEMNATION,      # Renting Unsafe or Condemned Structures prohibited
    "21-61": CONDEMNATION,      # Transfer of ownership of property under violation notice

    # --- unsafe_structure: structural / life-safety hazards ---
    "21-76(b)": UNSAFE_STRUCTURE,   # Unsafe equipment
    "21-76(e)": UNSAFE_STRUCTURE,   # Dangerous Structure or Premises
    "21-84": UNSAFE_STRUCTURE,      # Repair or demolition of Unsafe Structures
    "21-129": UNSAFE_STRUCTURE,     # Unsafe conditions
    "21-172": UNSAFE_STRUCTURE,     # Unsafe conditions
    "21-175": UNSAFE_STRUCTURE,     # Component serviceability
    "21-175(a)": UNSAFE_STRUCTURE,  # Component serviceability (General)
    "21-175(b)": UNSAFE_STRUCTURE,  # Component serviceability (Unsafe conditions)
    "21-171(b)": UNSAFE_STRUCTURE,  # Structural members
    "21-128(n)": UNSAFE_STRUCTURE,  # Structure members
    "21-128(o)": UNSAFE_STRUCTURE,  # Structure security
    "21-188": UNSAFE_STRUCTURE,     # Heating facilities
    "21-189": UNSAFE_STRUCTURE,     # Mechanical equipment (fire/CO risk, kept with heating/electrical)
    "21-190": UNSAFE_STRUCTURE,     # Electrical facilities
    "21-191": UNSAFE_STRUCTURE,     # Electrical equipment
    "21-194": UNSAFE_STRUCTURE,     # General fire safety requirements
    "21-195": UNSAFE_STRUCTURE,     # Means of egress
    "21-197": UNSAFE_STRUCTURE,     # Fire protection systems
    "21-197(c)": UNSAFE_STRUCTURE,  # Smoke alarms

    # --- vacant_building: boarding / securing an empty structure ---
    "21-87": VACANT_BUILDING,
    "21-87(a)": VACANT_BUILDING,   # Duty to remove waste and secure Structure
    "21-87(b)": VACANT_BUILDING,   # Boarding procedures
    "21-87(c)": VACANT_BUILDING,   # Continued maintenance
    "21-87(d)": VACANT_BUILDING,   # Boarding not to exceed one (1) year

    # --- nuisance: exterior / property-level nuisance, not structural ---
    "21-127(a)": NUISANCE,   # Sanitary condition
    "21-127(b)": NUISANCE,   # Grading and drainage
    "21-127(c)": NUISANCE,   # Sidewalks and driveways
    "21-127(d)": NUISANCE,   # Exhaust vents
    "21-127(e)": NUISANCE,   # Defacement of property
    "21-127(f)": NUISANCE,   # Rodent harborage
    "21-132": NUISANCE,      # Accumulation of litter prohibited
    "21-133": NUISANCE,      # Litter on occupied or vacant property
    "21-134": NUISANCE,      # Appliances not allowed on exterior Premises
    "21-135": NUISANCE,      # No indoor furniture allowed on exterior Premises
    "21-136": NUISANCE,      # Overgrowth
    "21-151": NUISANCE,      # Abandoned Vehicles prohibited
    "21-152": NUISANCE,      # Inoperable Vehicles on private property prohibited
    "21-153": NUISANCE,      # Automotive Repair
    "21-153(a)": NUISANCE,
    "21-173": NUISANCE,      # Pest elimination
    "21-59": NUISANCE,       # Unauthorized tampering with City documents or signs
    "21-37": NUISANCE,       # Interference with enforcement
    "18-32(d)": NUISANCE,    # Residential storage (removal after emptying)
    "18-86(a)": NUISANCE,    # Special services (Residential Bulky Trash)
    "18-86(b)": NUISANCE,    # Special services (Residential Yard Trash)
    "24-286(f)": NUISANCE,   # Oversized/commercial vehicles prohibited in certain places

    # --- property_maintenance: routine building-upkeep defaults ---
    "21-89": PROPERTY_MAINTENANCE,     # Stop Work Order
    "21-128": PROPERTY_MAINTENANCE,
    "21-128(a)": PROPERTY_MAINTENANCE,
    "21-128(b)": PROPERTY_MAINTENANCE,
    "21-128(c)": PROPERTY_MAINTENANCE,
    "21-128(d)": PROPERTY_MAINTENANCE,
    "21-128(e)": PROPERTY_MAINTENANCE,
    "21-128(f)": PROPERTY_MAINTENANCE,
    "21-128(g)": PROPERTY_MAINTENANCE,
    "21-128(h)": PROPERTY_MAINTENANCE,
    "21-128(i)": PROPERTY_MAINTENANCE,  # Piers
    "21-128(j)": PROPERTY_MAINTENANCE,
    "21-128(k)": PROPERTY_MAINTENANCE,
    "21-128(l)": PROPERTY_MAINTENANCE,
    "21-128(m)": PROPERTY_MAINTENANCE,
    "21-128(p)": PROPERTY_MAINTENANCE,
    "21-130": PROPERTY_MAINTENANCE,   # Accessory Structures
    "21-131": PROPERTY_MAINTENANCE,   # Swimming pools, spas and hot tubs
    "21-171": PROPERTY_MAINTENANCE,
    "21-171(a)": PROPERTY_MAINTENANCE,
    "21-171(c)": PROPERTY_MAINTENANCE,
    "21-171(d)": PROPERTY_MAINTENANCE,
    "21-171(e)": PROPERTY_MAINTENANCE,
    "21-171(f)": PROPERTY_MAINTENANCE,
    "21-174": PROPERTY_MAINTENANCE,   # Handrails and guardrails
    "21-177": PROPERTY_MAINTENANCE,   # Light
    "21-177(b)": PROPERTY_MAINTENANCE,  # Common halls and stairways (Light)
    "21-178": PROPERTY_MAINTENANCE,   # Ventilation
    "21-178(b)": PROPERTY_MAINTENANCE,
    "21-178(c)": PROPERTY_MAINTENANCE,
    "21-178(e)": PROPERTY_MAINTENANCE,
    "21-179(c)": PROPERTY_MAINTENANCE,
    "21-179(d)": PROPERTY_MAINTENANCE,
    "21-179(f)": PROPERTY_MAINTENANCE,
    "21-181": PROPERTY_MAINTENANCE,   # Required facilities
    "21-182": PROPERTY_MAINTENANCE,   # Toilet Rooms
    "21-182a": PROPERTY_MAINTENANCE,
    "21-183": PROPERTY_MAINTENANCE,   # Plumbing systems and fixtures
    "21-184": PROPERTY_MAINTENANCE,   # Water system
    "21-184(d)": PROPERTY_MAINTENANCE,
    "21-185": PROPERTY_MAINTENANCE,   # Sanitary drainage system
    "21-186": PROPERTY_MAINTENANCE,   # Storm drainage
    "21-192": PROPERTY_MAINTENANCE,   # Elevators, Escalators, and Dumbwaiters
    "21-193": PROPERTY_MAINTENANCE,   # Duct systems
}


def _classify(code: str | None, flag_dangerous: bool) -> str | None:
    """Map a parsed ordinance code to a scoring.py violation_type. A row
    flagged dangerous is escalated to at least unsafe_structure regardless
    of what its code alone would map to (per spec) — but never downgraded
    if its code already maps to something more severe (condemnation).
    """
    category = CODE_CATEGORY.get(code) if code else None
    if flag_dangerous and _CATEGORY_RANK.get(category, -1) < _CATEGORY_RANK[UNSAFE_STRUCTURE]:
        return UNSAFE_STRUCTURE
    return category


def _normalize_status(raw_status: str) -> str | None:
    """Returns None for a status this source means to skip (DUP)."""
    if raw_status in SKIP_STATUS_CODES:
        return None
    if raw_status in CLOSED_STATUS_CODES:
        return "closed"
    return "open"


def _parse_date_entered(raw: str | None) -> tuple[str | None, datetime.date | None]:
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        d = datetime.datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None, None
    return d.isoformat(), d


def _clean_hint(text: str, code: str) -> str:
    """description_extended is static per-code ordinance boilerplate (see
    module docstring) — trimmed to a short, single-line hint, with a
    leading "<code> - " self-reference stripped since the code is already
    shown separately in `description`.
    """
    text = " ".join((text or "").split())
    prefix = f"{code} -"
    if code and text.startswith(prefix):
        text = text[len(prefix):].strip()
    if len(text) > DESCRIPTION_HINT_MAX_CHARS:
        text = text[:DESCRIPTION_HINT_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return text


def _build_description(code: str | None, label: str, hint: str, raw_status: str) -> str:
    base = f"{code} - {label}" if code else (label or "Code enforcement violation")
    if hint:
        base = f"{base} — {hint}"
    return f"{base} [{raw_status}]"


def _latlon(raw_lat: str | None, raw_lon: str | None) -> tuple[float | None, float | None]:
    raw_lat = (raw_lat or "").strip()
    raw_lon = (raw_lon or "").strip()
    if not raw_lat or not raw_lon:
        return None, None
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except ValueError:
        return None, None
    if not (HAMILTON_LAT_RANGE[0] <= lat <= HAMILTON_LAT_RANGE[1] and HAMILTON_LON_RANGE[0] <= lon <= HAMILTON_LON_RANGE[1]):
        return None, None
    return lat, lon


def fetch(conn) -> tuple[Path | None, dict]:
    """Change-detection metadata call, then (if changed) a full streamed
    download to a temp file. Returns (csv_path, info); csv_path is None
    when the item is unchanged since the last successful ingest, in which
    case `info["skipped"]` is True and no download happens at all.
    """
    resp = requests.get(META_URL, timeout=30)
    resp.raise_for_status()
    meta = resp.json()
    if "error" in meta:
        raise RuntimeError(f"ArcGIS item metadata returned an error: {meta['error']}")
    size = meta.get("size")
    modified = meta.get("modified")
    if size is None or modified is None:
        raise RuntimeError(f"ArcGIS item metadata missing size/modified: {meta}")

    prev_modified = get_state(conn, STATE_KEY_MODIFIED)
    prev_size = get_state(conn, STATE_KEY_SIZE)
    unchanged = prev_modified is not None and str(modified) == prev_modified and str(size) == prev_size

    info = {"size": size, "modified": modified, "skipped": unchanged}
    if unchanged:
        return None, info

    fd, tmp_name = tempfile.mkstemp(prefix="chattanooga_code_enforcement_", suffix=".csv")
    tmp_path = Path(tmp_name)
    downloaded = 0
    try:
        with requests.get(DATA_URL, stream=True, timeout=(15, 300)) as r:
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").lower()
            if "html" in content_type:
                raise RuntimeError(f"Expected a CSV download, got Content-Type: {content_type!r}")
            with os.fdopen(fd, "wb") as out:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        out.write(chunk)
                        downloaded += len(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    if downloaded == 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded 0 bytes for the code enforcement CSV")

    with open(tmp_path, "rb") as f:
        head = f.read(512).lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file looks like an HTML error page, not a CSV")

    info["downloaded_bytes"] = downloaded
    return tmp_path, info


def parse(csv_path: Path) -> tuple[list[dict], dict]:
    """Returns (records, stats). One record per kept, deduped record_id —
    see module docstring "Row-count filter" and "Duplicate record_ids
    collapse" for the exact rule and measured counts.
    """
    stats = {
        "total_rows": 0,
        "dup_status_skipped": 0,
        "dropped_closed_stale": 0,
        "malformed": 0,
        "duplicate_record_id_collapsed": 0,
        "kept": 0,
    }
    today = datetime.date.today()
    best_by_record_id: dict[str, tuple[int, dict]] = {}

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["total_rows"] += 1

            record_id = (row.get("record_id") or "").strip()
            raw_status = (row.get("status") or "").strip().upper()
            if not record_id or not raw_status:
                stats["malformed"] += 1
                continue

            norm_status = _normalize_status(raw_status)
            if norm_status is None:
                stats["dup_status_skipped"] += 1
                continue

            date_iso, date_obj = _parse_date_entered(row.get("date_entered"))
            if date_obj is None:
                stats["malformed"] += 1
                continue

            is_open = norm_status == "open"
            age_days = (today - date_obj).days
            if not is_open and age_days > CLOSED_ROW_MAX_AGE_DAYS:
                stats["dropped_closed_stale"] += 1
                continue

            lat, lon = _latlon(row.get("latitude"), row.get("longitude"))
            council_district = (row.get("council_district") or "").strip()
            completeness = (1 if lat is not None else 0) + (1 if council_district else 0)

            desc_raw = (row.get("description") or "").strip()
            match = CODE_RE.match(desc_raw) if desc_raw else None
            code = match.group(1) if match else None
            label = match.group(2).strip() if match else desc_raw
            flag_dangerous = (row.get("flag_dangerous") or "").strip().lower() == "true"
            violation_type = _classify(code, flag_dangerous)

            hint = _clean_hint(row.get("description_extended") or "", code or "")
            description = _build_description(code, label, hint, raw_status)

            street = " ".join(
                p for p in [(row.get("street_number") or "").strip(), (row.get("street_name") or "").strip()] if p
            )
            street_norm = normalize_address(street)
            city = (row.get("city") or "").strip().upper() or "CHATTANOOGA"
            state = (row.get("state") or "").strip().upper() or "TN"
            raw_address = f"{street_norm}, {city}, {state}" if street_norm else None

            record = {
                "record_id": record_id,
                "case_number": (row.get("case_number") or "").strip() or None,
                "violation_code": code,
                "violation_type": violation_type,
                "violation_date": date_iso,
                "status": norm_status,
                "description": description,
                "raw_address": raw_address,
                "latitude": lat,
                "longitude": lon,
            }

            existing = best_by_record_id.get(record_id)
            if existing is None:
                best_by_record_id[record_id] = (completeness, record)
            else:
                stats["duplicate_record_id_collapsed"] += 1
                if completeness > existing[0]:
                    best_by_record_id[record_id] = (completeness, record)

    records = [r for _, r in best_by_record_id.values()]
    stats["kept"] = len(records)
    return records, stats


def upsert(conn, records: list[dict]) -> int:
    for r in records:
        dedupe_key = f"chattanooga_code:{r['record_id']}"
        # Address -> parcel resolution happens in enrich_assessor.py, not
        # here — keep_existing protects its work from being overwritten by
        # tomorrow's re-scrape of the same still-open case.
        upsert_record(
            conn, "code_enforcement", dedupe_key,
            keep_existing=("parcel_id", "resolution_method"),
            parcel_id=None,
            record_id=r["record_id"],
            case_number=r["case_number"],
            violation_code=r["violation_code"],
            violation_type=r["violation_type"],
            violation_date=r["violation_date"],
            status=r["status"],
            description=r["description"],
            raw_address=r["raw_address"],
            latitude=r["latitude"],
            longitude=r["longitude"],
            resolution_method="unresolved",
            source_url=SOURCE_URL,
        )
    return len(records)


def main() -> None:
    conn = get_connection()
    csv_path, fetch_info = fetch(conn)

    if fetch_info.get("skipped"):
        current_count = conn.execute("SELECT COUNT(*) FROM code_enforcement").fetchone()[0]
        note = (
            f"unchanged since item modified={fetch_info['modified']} size={fetch_info['size']}; "
            f"skipped download, {current_count} records already tracked"
        )
        log_scrape(conn, "code_enforcement", record_count=current_count, status="ok", notes=note)
        conn.commit()
        print(f"code_enforcement: source unchanged since last ingest, skipped download. {current_count} records already tracked.")
        return

    try:
        records, stats = parse(csv_path)
        count = upsert(conn, records)
        set_state(conn, STATE_KEY_MODIFIED, str(fetch_info["modified"]))
        set_state(conn, STATE_KEY_SIZE, str(fetch_info["size"]))
        conn.commit()
        log_scrape(conn, "code_enforcement", record_count=count, status="ok", notes=str(stats))
        conn.commit()

        status_counts = Counter(r["status"] for r in records)
        type_counts = Counter(r["violation_type"] or "(unmapped)" for r in records)
        latlon_n = sum(1 for r in records if r["latitude"] is not None)
        pct = f"{latlon_n / count:.1%}" if count else "n/a"
        print(
            f"code_enforcement: upserted {count} records from {stats['total_rows']} fetched rows "
            f"({fetch_info.get('downloaded_bytes', 0):,} bytes). "
            f"status={dict(status_counts)} violation_type={dict(type_counts)} "
            f"latlon_coverage={latlon_n}/{count} ({pct}). stats={stats}"
        )
    finally:
        csv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
