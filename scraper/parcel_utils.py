"""
Shared helpers used by every scraper that touches an owner name, a raw
address, or a Map/Group/Parcel triplet — kept in one place so the
classification/formatting logic (and its regexes) isn't duplicated per
source.
"""

from __future__ import annotations

import re

ENTITY_PATTERNS = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|LP|L\.P|LTD|LIMITED|COMPANY|PARTNERSHIP|HOLDINGS)\b",
    re.IGNORECASE,
)
TRUST_PATTERN = re.compile(r"\bTRUST\b", re.IGNORECASE)


def format_parcel_id(map_: str | None, group: str | None, parcel: str | None) -> str | None:
    """Canonical parcel_id from Hamilton County's Map/Group/Parcel triplet —
    every source needs to agree on this exact format to join correctly.
    Blank Group (common) is simply omitted rather than leaving a stray
    separator.
    """
    parts = [p.strip() for p in (map_, group, parcel) if p and p.strip()]
    return "-".join(parts) if parts else None


def classify_owner_type(owner_name: str | None) -> str | None:
    """Best-effort classification from the raw owner name string alone.
    'individual' is the default; only escalates to 'llc'/'corp'/'trust' on
    a clear textual match. Good enough for the absentee/entity scoring
    flag bonus — not a substitute for real business-registry data.
    """
    if not owner_name:
        return None
    name = owner_name.upper()
    if TRUST_PATTERN.search(name):
        return "trust"
    if "LLC" in name:
        return "llc"
    if ENTITY_PATTERNS.search(name):
        return "corp"
    return "individual"


def normalize_address(address: str | None) -> str | None:
    """Loose normalization for absentee-owner comparison: uppercase,
    strip punctuation, collapse whitespace. Not a USPS-grade normalizer —
    won't catch every abbreviation variant (St vs Street), but catches the
    common case cheaply without an external service.
    """
    if not address:
        return None
    address = re.sub(r"[^\w\s]", "", address.upper())
    address = re.sub(r"\s+", " ", address).strip()
    return address or None


def is_absentee(situs_address: str | None, mailing_address: str | None) -> bool | None:
    """None means "can't tell" (one side missing) — the caller should treat
    that as unknown, not as False, so a thin record doesn't get scored as
    if it were confirmed owner-occupied.
    """
    situs_n = normalize_address(situs_address)
    mail_n = normalize_address(mailing_address)
    if situs_n is None or mail_n is None:
        return None
    return situs_n not in mail_n and mail_n not in situs_n


def parse_cents(raw: str | None) -> float | None:
    """Parse a zero-padded fixed-width integer field that represents a
    dollar amount with an implied 2 decimal places (this source's
    convention for money AMOUNTS — but NOT for its Assessment fields,
    which are whole dollars; see tax_delinquent.py for why that
    distinction matters and how it was verified).
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or not re.fullmatch(r"-?\d+", raw):
        return None
    return int(raw) / 100


def parse_whole_dollars(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or not re.fullmatch(r"-?\d+", raw):
        return None
    return float(int(raw))
