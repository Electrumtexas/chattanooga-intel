"""
Export the encrypted skip-trace contact store to a CSV OUTSIDE the repo.

    python scripts/export_contacts.py                 # -> ~/Downloads/chattanooga_contacts_<timestamp>.csv
    python scripts/export_contacts.py --out D:/leads/contacts.csv

Key: --key-file, else %USERPROFILE%/chattanooga_contacts.key, else the
CONTACTS_KEY env var. The store is decrypted in memory only (nothing
plaintext is written except the CSV you asked for), and the CSV path is
refused if it resolves inside the repository — the repo is public.

Each row is one phone or email for one person on one parcel, joined with the
parcel's situs address, county owner of record, current score/tier and top
signal (computed live with build_unified's own functions, read-only against
data/chattanooga.db or CHATTANOOGA_DB). Compliance columns:
  DNC         YES = on the national Do Not Call registry per DealMachine.
  LITIGATOR   YES / no / UNKNOWN — DealMachine documents no litigator flag,
              so UNKNOWN is the normal value: scrub against a TCPA litigator
              list before any calling or texting campaign.
  CALL_STATUS "DO NOT CALL" when DNC or LITIGATOR is YES; "SCRUB FIRST" when
              the litigator status is unknown; "ok to dial (still TCPA rules)"
              otherwise. Flags are informational, not legal clearance.
stdout prints only the output path and row counts.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scraper"))

import contacts_store  # noqa: E402

COLUMNS = [
    "score", "tier", "parcel_id", "situs_address", "county_owner_of_record", "top_signal",
    "person_name", "role", "confidence", "phone", "phone_type", "DNC", "LITIGATOR", "CALL_STATUS",
    "email", "owner_match_quality", "traced_at", "last_seen_at", "assessor_card_url",
]


def _load_key(key_file: str | None) -> bytes | None:
    """Key file first (the owner's local copy), then the CONTACTS_KEY env var."""
    path = Path(key_file) if key_file else contacts_store.key_file_path()
    raw = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if raw:
        from cryptography.fernet import Fernet
        try:
            Fernet(raw.encode("ascii"))
        except (ValueError, TypeError):
            raise contacts_store.StoreKeyError("key file does not hold a valid Fernet key")
        return raw.encode("ascii")
    return contacts_store.load_key(allow_file=False)


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT.resolve())
        return True
    except ValueError:
        return False


def _default_out() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return home / "Downloads" / f"chattanooga_contacts_{datetime.now():%Y%m%d_%H%M}.csv"


def _fmt_phone(p: str | None) -> str:
    if p and len(p) == 10 and p.isdigit():
        return f"({p[:3]}) {p[3:6]}-{p[6:]}"
    return p or ""


def _safe_cell(v):
    """CSV/formula-injection guard: a string cell that starts with = + - @ TAB or CR
    would be evaluated by Excel/Sheets; prefix an apostrophe so it stays text.
    (Provider-supplied names/emails and county owner names are untrusted.)"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _yn(v, unknown: str = "") -> str:
    if v is None:
        return unknown
    return "YES" if int(v) else "no"


def _parcel_context(db_path: Path) -> dict[str, dict]:
    """{parcel_id: {situs_address, owner, score, tier, top_signal}} — read-only."""
    import skip_trace  # imports build_unified/scoring from scraper/
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        _, aggs = skip_trace.compute_aggregates(conn)
        ctx = {pid: {
            "situs_address": (a.get("parcel") or {}).get("situs_address"),
            "owner": (a.get("parcel") or {}).get("owner_name"),
            "score": a.get("score"), "tier": a.get("tier"), "top_signal": a.get("top_signal"),
        } for pid, a in aggs.items()}
        # Parcels traced earlier that no longer carry any signal still get address/owner.
        for r in conn.execute("SELECT parcel_id, situs_address, owner_name FROM parcels"):
            ctx.setdefault(r["parcel_id"], {"situs_address": r["situs_address"], "owner": r["owner_name"],
                                            "score": None, "tier": None, "top_signal": None})
        return ctx
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="CSV path (or directory) outside the repo; default ~/Downloads")
    ap.add_argument("--key-file", help="Fernet key file; default %%USERPROFILE%%/chattanooga_contacts.key")
    ap.add_argument("--db", help="chattanooga.db path; default CHATTANOOGA_DB or data/chattanooga.db")
    ap.add_argument("--store", help="contacts.db.enc path; default CONTACTS_STORE or data/contacts.db.enc")
    args = ap.parse_args(argv)

    try:
        key = _load_key(args.key_file)
    except contacts_store.StoreKeyError as exc:
        print(f"export_contacts: {exc}")
        return 1
    if not key:
        print("export_contacts: no key (key file not found and CONTACTS_KEY not set)")
        return 1

    out = Path(args.out) if args.out else _default_out()
    if out.is_dir():
        out = out / _default_out().name
    if _inside_repo(out):
        print("export_contacts: refusing to write inside the repository (it is public) — choose a path outside it")
        return 1

    store_path = Path(args.store) if args.store else contacts_store.enc_path()
    try:
        store = contacts_store.open_readonly(key, store_path)
    except FileNotFoundError:
        print("export_contacts: no contact store yet (data/contacts.db.enc missing)")
        return 1
    except contacts_store.StoreKeyError as exc:
        print(f"export_contacts: {exc}")
        return 1

    db_path = Path(args.db or os.environ.get("CHATTANOOGA_DB") or REPO_ROOT / "data" / "chattanooga.db")
    ctx = _parcel_context(db_path)

    latest_quality = {r[0]: r[1] for r in store.execute(
        "SELECT t.parcel_id, t.match_quality FROM skip_traces t JOIN ("
        "  SELECT parcel_id, MAX(traced_at) AS mx FROM skip_traces WHERE status = 'matched' GROUP BY parcel_id"
        ") m ON m.parcel_id = t.parcel_id AND m.mx = t.traced_at WHERE t.status = 'matched'")}
    latest_trace = {r[0]: r[1] for r in store.execute(
        "SELECT parcel_id, MAX(traced_at) FROM skip_traces GROUP BY parcel_id")}

    rows = []
    for c in store.execute("SELECT * FROM contacts"):
        pid = c["parcel_id"]
        p = ctx.get(pid, {})
        dnc, lit = c["dnc_flag"], c["litigator_flag"]
        if (dnc or 0) == 1 or (lit or 0) == 1:
            call = "DO NOT CALL"
        elif lit is None:
            call = "SCRUB FIRST" if c["phone"] else ""
        else:
            call = "ok to dial (still TCPA rules)" if c["phone"] else ""
        rows.append({
            "score": p.get("score"), "tier": p.get("tier"), "parcel_id": pid,
            "situs_address": p.get("situs_address"), "county_owner_of_record": p.get("owner"),
            "top_signal": p.get("top_signal"), "person_name": c["person_name"], "role": c["role"],
            "confidence": c["confidence"], "phone": _fmt_phone(c["phone"]), "phone_type": c["phone_type"],
            "DNC": _yn(dnc) if c["phone"] else "", "LITIGATOR": _yn(lit, "UNKNOWN") if c["phone"] else "",
            "CALL_STATUS": call, "email": c["email"] or "",
            "owner_match_quality": latest_quality.get(pid), "traced_at": latest_trace.get(pid),
            "last_seen_at": c["last_seen_at"],
            "assessor_card_url": f"https://assessor.hamiltontn.gov/card/{pid.replace('-', '_')}",
        })
    store.close()
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (-(r["score"] or 0), r["parcel_id"], conf_rank.get(r["confidence"], 3),
                             r["person_name"] or "", r["phone"], r["email"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows({k: _safe_cell(v) for k, v in r.items()} for r in rows)
    n_dnc = sum(1 for r in rows if r["CALL_STATUS"] == "DO NOT CALL")
    print(f"export_contacts: wrote {len(rows)} rows for {len({r['parcel_id'] for r in rows})} parcels "
          f"({n_dnc} flagged DO NOT CALL) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
