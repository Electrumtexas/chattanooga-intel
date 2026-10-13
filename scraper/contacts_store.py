"""
Encrypted contact store for skip-trace results (phones, emails, contact names).

The repo is public, so contact data never exists in plaintext in anything
committed. The committed artifact is `data/contacts.db.enc`: a SQLite file
encrypted as a whole with Fernet (AES-128-CBC + HMAC-SHA256, from the
`cryptography` package). A run decrypts it to the gitignored temp file
`data/contacts.db`, works on it, re-encrypts, and deletes the plaintext in a
`finally` — including when the run crashes. The plaintext file is also
deleted on entry if a previous run was killed before its own cleanup.

Key handling (never committed, never printed):
  - CONTACTS_KEY env var (GitHub secret of the same name, a Fernet key) —
    what Actions uses.
  - Otherwise the key file at CONTACTS_KEY_FILE, default
    %USERPROFILE%/chattanooga_contacts.key — the owner's local copy, used by
    scripts/export_contacts.py and local runs.
A missing key is reported as None (callers skip cleanly); a malformed key or
a key that doesn't decrypt the existing file raises StoreKeyError — the
existing encrypted file is never overwritten in that case, so a wrong key
can't wipe the store.

Re-encryption only happens when the plaintext actually changed (sha256 of
the file before vs after), so a run that traced nothing doesn't produce a new
ciphertext (Fernet output is randomized) and a no-op commit.

Tables
------
skip_traces  one row per parcel per trace attempt: parcel_id, traced_at,
             provider, request_kind ('apn' | 'apn_00' | 'address'),
             status ('matched' | 'no_match' | 'location_mismatch' | 'error'),
             match_quality (owner-name agreement with parcels.owner_name:
             'exact' | 'strong' | 'weak' | 'none' | 'unknown'), credits_used
             (this parcel's share of the response's credits.used), plus
             dm_property_id / distance_m / verify_method for auditing. A parcel_id of
             '_run' (status 'error') records credits billed for a response whose
             items couldn't be mapped to parcels (counts toward the daily cap).
contacts     one row per (parcel, person, phone-or-email): person_name, role,
             phone, phone_type, dnc_flag, litigator_flag, email, confidence,
             first_seen_at, last_seen_at, plus provider / dm_person_id /
             carrier. dnc_flag and litigator_flag are sticky (once 1, stays
             1): a DNC/litigator flag is never dropped or downgraded by a
             later trace. litigator_flag is NULL when the provider didn't say
             (DealMachine documents no litigator field) — NULL means
             UNKNOWN, not "clean".

Paths are overridable for testing: CONTACTS_STORE = path of the .enc file
(the plaintext temp file is the same path without the trailing ".enc").
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENC_PATH = REPO_ROOT / "data" / "contacts.db.enc"
DEFAULT_KEY_FILE = Path(os.environ.get("USERPROFILE") or Path.home()) / "chattanooga_contacts.key"

SCHEMA = """
CREATE TABLE IF NOT EXISTS skip_traces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parcel_id       TEXT NOT NULL,
    traced_at       TEXT NOT NULL,
    provider        TEXT NOT NULL,
    request_kind    TEXT,
    status          TEXT NOT NULL,
    match_quality   TEXT,
    credits_used    REAL,
    dm_property_id  TEXT,
    distance_m      REAL,
    verify_method   TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_key     TEXT NOT NULL UNIQUE,
    parcel_id       TEXT NOT NULL,
    person_name     TEXT,
    role            TEXT,
    phone           TEXT,
    phone_type      TEXT,
    dnc_flag        INTEGER,
    litigator_flag  INTEGER,
    email           TEXT,
    confidence      TEXT,
    provider        TEXT,
    dm_person_id    TEXT,
    carrier         TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skip_traces_parcel ON skip_traces(parcel_id, traced_at);
CREATE INDEX IF NOT EXISTS idx_contacts_parcel ON contacts(parcel_id);
"""

# Statuses that count as "traced" for the refresh window. 'error' doesn't —
# an errored attempt is retried on the next run.
COMPLETED_STATUSES = ("matched", "no_match", "location_mismatch")


class StoreKeyError(RuntimeError):
    """Malformed key, or a key that doesn't decrypt the existing store."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enc_path() -> Path:
    return Path(os.environ.get("CONTACTS_STORE") or DEFAULT_ENC_PATH)


def plain_path_for(enc: Path) -> Path:
    s = str(enc)
    return Path(s[:-4]) if s.endswith(".enc") else Path(s + ".plain")


def key_file_path() -> Path:
    return Path(os.environ.get("CONTACTS_KEY_FILE") or DEFAULT_KEY_FILE)


def load_key(allow_file: bool = True) -> bytes | None:
    """CONTACTS_KEY env first, then (if allow_file) the local key file.
    Returns None when neither exists. Raises StoreKeyError on a malformed key.
    Never logs the key or its path contents."""
    raw = (os.environ.get("CONTACTS_KEY") or "").strip()
    if not raw and allow_file:
        path = key_file_path()
        if path.is_file():
            raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    key = raw.encode("ascii", errors="ignore")
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise StoreKeyError("contacts key is not a valid Fernet key") from exc
    return key


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_plaintext(plain: Path) -> None:
    for p in (plain, Path(str(plain) + "-journal"), Path(str(plain) + "-wal"), Path(str(plain) + "-shm")):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def contact_key(parcel_id: str, person_ref: str | None, phone: str | None, email: str | None) -> str:
    raw = "|".join([parcel_id or "", person_ref or "", phone or "", (email or "").lower()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ContactsStore:
    """Context manager: decrypt -> sqlite connection -> re-encrypt + delete plaintext.

        with ContactsStore(key) as store:
            store.record_trace(...); store.upsert_contact(...); store.commit()

    On a normal exit pending writes are committed; on an exception they're
    rolled back to the last commit() — but everything committed before the
    exception is still encrypted and kept (credits already spent on those
    results shouldn't be thrown away). The plaintext file is removed in every
    case, even if encryption itself fails.
    """

    def __init__(self, key: bytes, enc: Path | str | None = None):
        self.fernet = Fernet(key)
        self.enc = Path(enc) if enc else enc_path()
        self.plain = plain_path_for(self.enc)
        self.conn: sqlite3.Connection | None = None
        self._hash_before: str | None = None
        self.changed = False

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> "ContactsStore":
        _remove_plaintext(self.plain)  # stale leftover from a killed run
        self.plain.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.enc.exists():
                try:
                    data = self.fernet.decrypt(self.enc.read_bytes())
                except InvalidToken as exc:
                    raise StoreKeyError("contacts key does not decrypt the existing store") from exc
                self.plain.write_bytes(data)
            self._hash_before = _sha256(self.plain)
            self.conn = sqlite3.connect(self.plain)
            self.conn.row_factory = sqlite3.Row
            # Keep the rollback journal in memory: no extra plaintext file on disk.
            self.conn.execute("PRAGMA journal_mode=MEMORY")
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        except BaseException:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            _remove_plaintext(self.plain)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.conn is not None:
                try:
                    if exc_type is None:
                        self.conn.commit()
                    else:
                        self.conn.rollback()
                finally:
                    self.conn.close()
                    self.conn = None
                if self.plain.exists() and (_sha256(self.plain) != self._hash_before or not self.enc.exists()):
                    token = self.fernet.encrypt(self.plain.read_bytes())
                    tmp = Path(str(self.enc) + ".tmp")
                    tmp.write_bytes(token)
                    os.replace(tmp, self.enc)
                    self.changed = True
        finally:
            _remove_plaintext(self.plain)
            try:
                Path(str(self.enc) + ".tmp").unlink()
            except FileNotFoundError:
                pass

    def commit(self) -> None:
        assert self.conn is not None
        self.conn.commit()

    # -- reads ---------------------------------------------------------------
    def last_traced(self) -> dict[str, str]:
        """{parcel_id: latest traced_at} over completed (non-error) attempts."""
        assert self.conn is not None
        q = (
            "SELECT parcel_id, MAX(traced_at) FROM skip_traces "
            f"WHERE status IN ({','.join('?' for _ in COMPLETED_STATUSES)}) GROUP BY parcel_id"
        )
        return {r[0]: r[1] for r in self.conn.execute(q, COMPLETED_STATUSES)}

    def credits_since(self, cutoff_iso: str) -> float:
        """Credits recorded in skip_traces at/after cutoff_iso (UTC 'YYYY-MM-DDTHH:MM:SSZ')
        - the basis of the rolling daily cap. Includes the '_run' rows that hold
        credits billed for responses whose items couldn't be mapped."""
        assert self.conn is not None
        return float(self.conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM skip_traces WHERE traced_at >= ?", (cutoff_iso,)
        ).fetchone()[0] or 0.0)

    def counts(self) -> dict[str, int]:
        assert self.conn is not None
        c = self.conn
        return {
            "traces": c.execute("SELECT COUNT(*) FROM skip_traces").fetchone()[0],
            "parcels_traced": c.execute("SELECT COUNT(DISTINCT parcel_id) FROM skip_traces WHERE parcel_id <> '_run'").fetchone()[0],
            "contact_rows": c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
            "phones": c.execute("SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL").fetchone()[0],
            "emails": c.execute("SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL").fetchone()[0],
            "dnc_phones": c.execute("SELECT COUNT(*) FROM contacts WHERE dnc_flag = 1").fetchone()[0],
        }

    # -- writes --------------------------------------------------------------
    def record_trace(
        self,
        parcel_id: str,
        provider: str,
        request_kind: str | None,
        status: str,
        match_quality: str | None = None,
        credits_used: float | None = None,
        dm_property_id: str | None = None,
        distance_m: float | None = None,
        verify_method: str | None = None,
        notes: str | None = None,
        traced_at: str | None = None,
    ) -> None:
        assert self.conn is not None
        self.conn.execute(
            "INSERT INTO skip_traces (parcel_id, traced_at, provider, request_kind, status, match_quality, "
            "credits_used, dm_property_id, distance_m, verify_method, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (parcel_id, traced_at or now_iso(), provider, request_kind, status, match_quality,
             credits_used, dm_property_id, distance_m, verify_method, notes),
        )

    def upsert_contact(
        self,
        parcel_id: str,
        provider: str,
        person_name: str | None,
        role: str | None,
        phone: str | None = None,
        phone_type: str | None = None,
        dnc_flag: int | None = None,
        litigator_flag: int | None = None,
        email: str | None = None,
        confidence: str | None = None,
        dm_person_id: str | None = None,
        carrier: str | None = None,
        seen_at: str | None = None,
    ) -> bool:
        """Returns True if a new row was inserted, False if an existing one was refreshed."""
        assert self.conn is not None
        seen_at = seen_at or now_iso()
        key = contact_key(parcel_id, dm_person_id or (person_name or "").upper(), phone, email)
        existing = self.conn.execute("SELECT id FROM contacts WHERE contact_key = ?", (key,)).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO contacts (contact_key, parcel_id, person_name, role, phone, phone_type, dnc_flag, "
                "litigator_flag, email, confidence, provider, dm_person_id, carrier, first_seen_at, last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, parcel_id, person_name, role, phone, phone_type, dnc_flag, litigator_flag, email,
                 confidence, provider, dm_person_id, carrier, seen_at, seen_at),
            )
            return True
        # Sticky compliance flags: MAX() keeps a 1 once seen; COALESCE keeps a
        # known value when the new response omits the field.
        self.conn.execute(
            "UPDATE contacts SET person_name = COALESCE(?, person_name), role = COALESCE(?, role), "
            "phone_type = COALESCE(?, phone_type), carrier = COALESCE(?, carrier), "
            "confidence = COALESCE(?, confidence), "
            "dnc_flag = CASE WHEN ? IS NULL THEN dnc_flag ELSE MAX(COALESCE(dnc_flag, 0), ?) END, "
            "litigator_flag = CASE WHEN ? IS NULL THEN litigator_flag ELSE MAX(COALESCE(litigator_flag, 0), ?) END, "
            "last_seen_at = ? WHERE id = ?",
            (person_name, role, phone_type, carrier, confidence, dnc_flag, dnc_flag,
             litigator_flag, litigator_flag, seen_at, existing[0]),
        )
        return False


def open_readonly(key: bytes, enc: Path | str | None = None) -> sqlite3.Connection:
    """Decrypt into an in-memory SQLite connection — nothing plaintext touches
    disk. For read-only consumers (scripts/export_contacts.py). Raises
    FileNotFoundError if there's no store yet, StoreKeyError on a wrong key."""
    enc = Path(enc) if enc else enc_path()
    try:
        data = Fernet(key).decrypt(enc.read_bytes())
    except InvalidToken as exc:
        raise StoreKeyError("contacts key does not decrypt the store") from exc
    conn = sqlite3.connect(":memory:")
    conn.deserialize(data)
    conn.row_factory = sqlite3.Row
    return conn
