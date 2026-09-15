"""
SQLite access layer for Chattanooga Intel — the single canonical store.

Schema summary
--------------
parcels           One row per resolved Hamilton County parcel (Map/Group/Parcel
                   number = canonical join key). Enriched incrementally by
                   whichever source resolves first, then by enrich_assessor.py
                   for lat/long + owner backfill. upsert_parcel() merges
                   non-null fields so an early partial write (e.g. an address
                   from court_records before parcel_id is known) never gets
                   clobbered back to NULL by a later, less-complete write.

court_records     One row per case, source_portal + case_type + dedupe_key.
tax_delinquent    One row per parcel+tax_year.
code_enforcement  One row per violation case.

  All three source tables carry the same shape: parcel_id (nullable until
  resolved), raw_address/raw_owner_name (kept even after resolution, for
  auditing the match), resolution_method ('address_match' | 'owner_name_fallback'
  | 'unresolved'), a source-specific set of columns, and first_seen_at/
  last_seen_at so a record that stops appearing in the source can be told
  apart from one that was never there.

scrape_log        One row per scraper run per source: what it fetched, so
                   quality_check.py has a trailing baseline to compare against.

Dedup strategy: each source table has a single `dedupe_key` TEXT UNIQUE
column instead of a multi-column UNIQUE constraint, because several sources
don't reliably provide every field a composite key would need (e.g. a lis
pendens notice without a formal case number). Each scraper module is
responsible for building a stable key, e.g. f"{portal}:{case_number}" when
available, else a hash of whatever identifying fields exist.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "chattanooga.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
    parcel_id           TEXT PRIMARY KEY,
    situs_address       TEXT,
    situs_city          TEXT,
    situs_zip           TEXT,
    owner_name          TEXT,
    mailing_address     TEXT,
    mailing_city        TEXT,
    mailing_state       TEXT,
    mailing_zip         TEXT,
    owner_type          TEXT,      -- 'individual' | 'llc' | 'corp' | 'trust' | 'other'
    is_absentee         INTEGER,   -- 0/1 — mailing address differs from situs
    latitude            REAL,
    longitude           REAL,
    geocode_source      TEXT,      -- 'assessor_gis' | 'manual'
    geocode_status      TEXT,      -- 'resolved' | 'unresolved' | 'pending'
    assessed_value      REAL,
    last_enriched_at    TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS court_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key          TEXT NOT NULL UNIQUE,
    parcel_id           TEXT,
    source_portal       TEXT NOT NULL,   -- 'tn_case_finder' | 'civitek_ocrs' | 'hamilton_probate' | 'clerk_master_tax_sale'
    case_number         TEXT,
    case_type           TEXT,            -- 'judgment' | 'lien' | 'lis_pendens' | 'foreclosure_notice' | 'probate' | 'tax_sale_filing' | 'collections'
    filing_date         TEXT,
    party_names         TEXT,
    amount              REAL,
    raw_address         TEXT,
    raw_owner_name      TEXT,
    resolution_method   TEXT,            -- 'native_parcel_id' | 'address_match' | 'owner_name_fallback' | 'unresolved'
    source_url          TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tax_delinquent (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key          TEXT NOT NULL UNIQUE,
    parcel_id           TEXT,
    tax_year            INTEGER,         -- one row per (parcel, tax_year) bill; a parcel accumulates one row per delinquent year
    amount_due          REAL,            -- CURRENT amount still owed on this specific year's bill (county+muni+stormwater), not the original billed amount
    status              TEXT,
    back_tax_indicator  TEXT,            -- 'Y'/'N' from the Trustee file — 'Y' means already in active back-tax collection, a step past routine delinquency
    mortgage_company    TEXT,            -- present on ~1% of rows; a mortgage escrowing taxes softens the signal somewhat but is informational only, not scored
    raw_address         TEXT,
    raw_owner_name      TEXT,
    resolution_method   TEXT,
    source_url          TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_enforcement (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key          TEXT NOT NULL UNIQUE,
    parcel_id           TEXT,
    case_number         TEXT,
    violation_type      TEXT,
    violation_date      TEXT,
    status              TEXT,            -- 'open' | 'closed' | 'pending'
    description         TEXT,
    raw_address         TEXT,
    resolution_method   TEXT,
    source_url          TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    run_at              TEXT NOT NULL,
    record_count        INTEGER NOT NULL,
    status              TEXT NOT NULL,   -- 'ok' | 'quality_check_failed' | 'error'
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_court_parcel ON court_records(parcel_id);
CREATE INDEX IF NOT EXISTS idx_tax_parcel ON tax_delinquent(parcel_id);
CREATE INDEX IF NOT EXISTS idx_code_parcel ON code_enforcement(parcel_id);
CREATE INDEX IF NOT EXISTS idx_scrape_log_source_run ON scrape_log(source, run_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_parcel(conn: sqlite3.Connection, parcel_id: str, **fields) -> None:
    """Insert a parcel, or merge non-null fields into an existing one.

    Never overwrites an existing non-null value with None — sources resolve
    different fields at different times (court_records may supply an address
    before enrich_assessor resolves lat/long), so a later partial write must
    not erase an earlier one.
    """
    if not parcel_id:
        return
    now = now_iso()
    existing = conn.execute(
        "SELECT 1 FROM parcels WHERE parcel_id = ?", (parcel_id,)
    ).fetchone()
    if existing is None:
        columns = ["parcel_id", "created_at", "updated_at"] + list(fields.keys())
        values = [parcel_id, now, now] + list(fields.values())
        placeholders = ",".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO parcels ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
    else:
        merged = {k: v for k, v in fields.items() if v is not None}
        if not merged:
            return
        merged["updated_at"] = now
        set_clause = ",".join(f"{k} = ?" for k in merged)
        conn.execute(
            f"UPDATE parcels SET {set_clause} WHERE parcel_id = ?",
            [*merged.values(), parcel_id],
        )


def upsert_record(conn: sqlite3.Connection, table: str, dedupe_key: str, **fields) -> None:
    """Shared upsert for the three source tables, keyed on dedupe_key.

    `table` is always a hardcoded literal from a caller in this codebase
    (never user/request input), so building the statement with an f-string
    is safe here — there is no injection surface.
    """
    now = now_iso()
    existing = conn.execute(
        f"SELECT id FROM {table} WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    if existing is None:
        columns = ["dedupe_key", "first_seen_at", "last_seen_at"] + list(fields.keys())
        values = [dedupe_key, now, now] + list(fields.values())
        placeholders = ",".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
    else:
        set_clause = ",".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE {table} SET {set_clause}, last_seen_at = ? WHERE dedupe_key = ?",
            [*fields.values(), now, dedupe_key],
        )


def log_scrape(
    conn: sqlite3.Connection,
    source: str,
    record_count: int,
    status: str = "ok",
    notes: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO scrape_log (source, run_at, record_count, status, notes) VALUES (?, ?, ?, ?, ?)",
        (source, now_iso(), record_count, status, notes),
    )


def trailing_baseline(conn: sqlite3.Connection, source: str, days: int = 7) -> float | None:
    """Average record_count over the `days` most recent *prior* successful
    runs for `source` — excludes today's own just-logged row (OFFSET 1) so
    a bad run never gets averaged into the baseline it's being judged against.
    Returns None if there's no history yet (first run for that source).
    """
    rows = conn.execute(
        """
        SELECT record_count FROM scrape_log
        WHERE source = ? AND status = 'ok'
        ORDER BY run_at DESC
        LIMIT ? OFFSET 1
        """,
        (source, days),
    ).fetchall()
    if not rows:
        return None
    counts = [r[0] for r in rows]
    return sum(counts) / len(counts)


def latest_run(conn: sqlite3.Connection, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scrape_log WHERE source = ? ORDER BY run_at DESC LIMIT 1",
        (source,),
    ).fetchone()


def mark_run_status(conn: sqlite3.Connection, log_id: int, status: str, notes: str | None = None) -> None:
    conn.execute(
        "UPDATE scrape_log SET status = ?, notes = ? WHERE id = ?",
        (status, notes, log_id),
    )
