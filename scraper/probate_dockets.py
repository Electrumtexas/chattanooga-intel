"""
Phase 1 (probate slice) — Hamilton County Chancery Court Part 2 motion
docket PDFs. Estates only: "Ultimately we need name, address and any
contact information" for decedents who may have owned real estate.

Live-verified 2026-09-22 against four real dockets (Sonnet session),
not assumed from CLAUDE.md's prior recon note alone — every claim below
was re-checked against downloaded PDF bytes.

Index page and URL pattern
---------------------------
`https://www.hamiltontn.gov/ChanceryCourt_Dockets.aspx` links exactly two
"current" docket PDFs at any time — "Current Motion Call Docket Part 1"
and "...Part 2" — at paths like `pdf/courts/Chancery/data/091426.pdf`
(MMDDYY). There is NO historical archive on the index page; once a new
Monday's docket is posted the old link disappears from the page (though
the old file stays reachable directly by URL — confirmed by fetching
081026.pdf and 072726.pdf, both still live and unlinked from the current
page). Part 1 and Part 2 alternate Mondays per the "Motion Call Schedule"
PDF (also linked from the index page): confirmed Sep 14/Aug 24/Aug
10/Jul 27, 2026 are all Part 2 Mondays, matching that schedule exactly.

Which "part" holds estates — verified, not assumed
----------------------------------------------------
Downloaded and text-scanned both currently-linked PDFs: Part 2
(091426.pdf, 30 pages) contains 39 "IN THE MATTER OF THE ESTATE OF:"
blocks; Part 1 (092126_revised.pdf, 21 pages) contains ZERO — it carries
general Chancery civil motions (tax-sale excess-proceeds claims, contract
disputes, family-law motions) under plain "YY-NNNN" case numbers, not
"YY-P NNN". Three more Part 2 dockets pulled for parser validation
(082426.pdf: 42 raw "ESTATE OF" mentions but 37 real estate blocks —
the other 5 are decedents' estates referenced as a PARTY in an unrelated
civil case, e.g. "18-0093 ESTATE OF JOHNNY HYLER b/n/f", correctly
excluded since they aren't "IN THE MATTER OF THE ESTATE OF:" blocks under
a P-docket; 081026.pdf: 30 blocks; 072726.pdf: 34 blocks) — 140 real
estate blocks total across the four dockets, every one of which parsed
with a non-empty decedent name, docket number, and at least one attorney
name + motion description after two rounds of fixing edge cases found by
inspecting the output (multi-line parenthetical attorney names; wrapped
DISPOSITION continuation lines that were being mis-detected as attorney
names because Mc/Mac/De-prefixed surnames like "McBRIDE" aren't fully
upper-case). Only one of 106 unique docket numbers seen across all four
dockets showed any inconsistency at all: the same case's decedent printed
as "ROLAND DARDEN, JR." on one date and "ROLAND D. DARDEN, JR." on
another — a source-side inconsistency (not a parser bug); harmless here
since raw_owner_name simply reflects whatever the latest docket says.

Conservatorships/guardianships: none appeared in any of the four sampled
dockets (all 39/37/30/34 "IN THE MATTER OF..." blocks were ESTATE OF).
The parser still checks for "IN THE MATTER OF THE CONSERVATORSHIP OF:"
and "...GUARDIANSHIP OF:" and SKIPS them (counted, not ingested) rather
than assuming they can't occur — a conservatorship concerns a living
person's affairs, not a decedent's real estate, so it doesn't fit this
table's case_type='probate' semantics or scoring.CASE_TYPE_TIER.

Docket block layout (per estate, in the PDF's plain-text reading order —
NOT its visual column order; pypdf's default extract_text() happens to
follow logical/content-stream order here, confirmed against
extraction_mode="layout" for the same pages)
--------------------------------------------------------------------------
    <YY>-P[-]              (docket number, part 1 — sometimes printed
    <NNN>                   with a trailing hyphen, e.g. "26-P-"; part 2
                             is a separate line, zero-padded, e.g. "057")
    IN THE MATTER OF THE ESTATE OF:
    <DECEDENT NAME>          (normal order, e.g. "KATHRINA H. MACLELLAN")
    DISPOSITION: <optional status text, sometimes wrapped to a 2nd line>
    <ATTORNEY/FIRM NAME>      (ALL CAPS; a solo attorney's own name and a
    (<individual name(s)>)    firm name are printed identically — no
    <MOTION TYPE TEXT...>     structural way to tell them apart)
    <ATTORNEY/FIRM NAME 2>    (optional — a second attorney of record;
    (<individual name(s)>)    role vs. attorney 1 [movant/successor/
                               opposing counsel] is not machine-readable)
No date of death, personal representative label, phone number, or
property address ever appears in an estate block — confirmed by grepping
all four dockets' full text for "PERSONAL REPRESENTATIVE", "EXECUTOR",
"ADMINISTRATOR", "PHONE", "ADDRESS": the only hits are either the generic
"MOTION FOR APPROVAL OF EXECUTOR'S FEES" motion-type boilerplate, or
occurrences inside the unrelated general-civil-docket pages (out of
scope here). One useful exception: some attorney-slot entries read like
"WHITNEY BRAZELL, Pro Se" — a named individual with no firm, representing
themselves. That's essentially never a hired attorney; in an estate
matter it's almost always the personal representative, an heir, or
another interested party filing their own motion, so it's pulled out
separately into party_names as the closest thing to a PR name this
source actually prints, rather than being labeled an attorney.

Filing_date = earliest date seen, without a read-before-write
-----------------------------------------------------------------
fetch() only ever sees the CURRENTLY linked Part 2 docket (there is no
historical archive to backfill from — see above), so every run moves
forward in time relative to the last. That means the first time this
scraper ever sees a given docket number IS necessarily the earliest date
it can know about. db.upsert_record()'s keep_existing mechanism
(`UPDATE ... SET filing_date = COALESCE(filing_date, ?)`) already gives
exactly "keep the first value ever written, ignore later ones" — so
filing_date is included in keep_existing here instead of doing a manual
SELECT-then-min() before every upsert. This would stop being correct if
a future change made fetch() backfill older dockets out of chronological
order — flagged so nobody "fixes" this into keep_existing by copy-paste
without re-deriving why it's safe today.

record_count is "estates on today's docket," not "new since last check"
-----------------------------------------------------------------------
Part 2 only gets a new docket every OTHER Monday, but this scraper runs
daily. If record_count only reflected NEWLY changed content, ~13 of every
14 daily runs would legitimately log 0 records — which is indistinguishable
from a broken scraper to quality_check.py's baseline-ratio check (0 records
with a nonzero baseline always fails, no threshold escape). Rather than
change quality_check.py's baseline logic (out of scope for this module),
this scraper sidesteps the problem: every run re-parses and re-upserts
whatever Part 2 docket is CURRENTLY linked (cheap — one ~200KB PDF, ~30-42
estate blocks), so record_count is stable day to day regardless of whether
the content actually changed since yesterday. The sha256-tracking in
source_state (key f"{STATE_KEY}") exists only to annotate notes/logging
with whether the docket's content actually changed since it was last
checked — it does not gate parsing or upserting. See "Proposed shared-file
changes" in this build's report for a quality_check.py threshold that
would let this module report 0 truthfully on a scraper-crash day without
being confused with an ordinary no-new-docket day, if that's ever wanted.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from db import get_connection, get_state, log_scrape, now_iso, set_state, upsert_record

INDEX_URL = "https://www.hamiltontn.gov/ChanceryCourt_Dockets.aspx"
STATE_KEY = "probate_dockets:processed"
MAX_STATE_ENTRIES = 60  # ~2+ years of biweekly Part 2 dockets — bounded so source_state can't grow forever
REQUEST_HEADERS = {"User-Agent": "chattanooga-intel/1.0 (distressed-property lead research; contact via github.com/Electrumtexas/chattanooga-intel)"}
REQUEST_DELAY_SECONDS = 1.5

MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}

# --- Block-boundary / field regexes (see module docstring for the layout) --
DOCKET_START_RE = re.compile(r"^(\d{2})-P-?$")
DOCKET_NUM_RE = re.compile(r"^(\d{1,6})$")
HEADER_RE = re.compile(r"^IN THE MATTER OF THE (ESTATE|CONSERVATORSHIP|GUARDIANSHIP) OF:?$", re.IGNORECASE)
DISPOSITION_RE = re.compile(r"^DISPOSITION:?\s*(.*)$", re.IGNORECASE)
PAGE_HEADER_RE = re.compile(r"^CHANCERY COURT PART \d MOTION DOCKET", re.IGNORECASE)
MOTION_START_RE = re.compile(r"^(\(\d+\)\s*)?MOTION\b", re.IGNORECASE)
SUFFIX_RE = re.compile(r"(?:,\s*|\s+)(JR|SR|II|III|IV|V)\.?$", re.IGNORECASE)
PRO_SE_RE = re.compile(r"^(.*\S)\s*,\s*PRO SE$", re.IGNORECASE)
DATE_IN_HEADER_RE = re.compile(r"MOTION DOCKET\s+([A-Z]+)\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)
DATE_IN_FILENAME_RE = re.compile(r"(\d{2})(\d{2})(\d{2})")


def fetch_index_links() -> list[tuple[str, str]]:
    """Returns [(absolute_url, link_text), ...] for every PDF link on the
    docket index page. Filtering to "which ones are Part 2" happens in
    fetch(), not here, so a page-structure change is easy to diagnose from
    one printed list.
    """
    resp = requests.get(INDEX_URL, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            links.append((urljoin(resp.url, href), a.get_text(strip=True)))
    return links


def _docket_date_from_header(text: str) -> str | None:
    m = DATE_IN_HEADER_RE.search(text.upper())
    if not m:
        return None
    month = MONTHS.get(m.group(1))
    if not month:
        return None
    try:
        return datetime(int(m.group(3)), month, int(m.group(2))).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _docket_date_from_url(url: str) -> str | None:
    m = DATE_IN_FILENAME_RE.search(url.rsplit("/", 1)[-1])
    if not m:
        return None
    mm, dd, yy = (int(g) for g in m.groups())
    try:
        return datetime(2000 + yy, mm, dd).strftime("%Y-%m-%d")
    except ValueError:
        return None


def fetch() -> tuple[list[dict], dict]:
    """Fetch the index page, then download every docket PDF that LOOKS like
    a Part 2 (probate) docket — identified by the link text containing
    "Part 2" (confirmed live: the index page's own anchor text says
    "Current Motion Call Docket Part 2"). Falls back to opening every
    data/*.pdf link and checking its own first-page header if no link text
    matches, so a future wording change degrades to "a bit slower" rather
    than "silently stops finding probate dockets."

    A missing/renamed date returns HTTP 200 with an HTML "Page Not Found"
    body (confirmed live) — the `%PDF` magic-byte check below is what
    actually decides whether a link resolved to a real docket.

    Returns (docket_pdfs, stats) where each docket_pdf is
    {"url", "content" (bytes), "docket_date" ("YYYY-MM-DD" or None)}.
    """
    stats = {"index_pdf_links": 0, "part2_candidates": 0, "not_pdf_skipped": 0, "fetched": 0}
    # Deliberately NOT caught here: if the index page itself is unreachable
    # that's a total failure worth main() logging as status='error' and
    # raising (matches tax_delinquent.py, which doesn't guard its request
    # either) — a single bad PDF fetch below is handled more leniently
    # since there are usually 1-2 candidates and one failing shouldn't sink
    # the whole run.
    links = fetch_index_links()
    stats["index_pdf_links"] = len(links)

    candidates = [(url, text) for url, text in links if "/data/" in url.lower() and re.search(r"part\s*2", text, re.IGNORECASE)]
    if not candidates:
        # Fallback: text-match found nothing (page wording changed) — check
        # every data/*.pdf link's own content instead of giving up.
        candidates = [(url, text) for url, text in links if "/data/" in url.lower()]
    stats["part2_candidates"] = len(candidates)

    docket_pdfs = []
    seen_urls = set()
    for url, _text in candidates:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as e:
            stats.setdefault("fetch_errors", []).append(f"{url}: {e}")
            continue
        content = resp.content
        if content[:4] != b"%PDF":
            stats["not_pdf_skipped"] += 1
            continue
        header_text = _first_page_text(content)
        if "PART 2" not in header_text.upper():
            # Only relevant when we fell back to "every data/*.pdf link" above
            continue
        docket_date = _docket_date_from_header(header_text) or _docket_date_from_url(url)
        docket_pdfs.append({"url": url, "content": content, "docket_date": docket_date})
        stats["fetched"] += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return docket_pdfs, stats


def _first_page_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return reader.pages[0].extract_text() or ""


def _extract_lines(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    lines = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if PAGE_HEADER_RE.match(line):
                continue
            lines.append(line)
    return lines


def _join_wrapped(parts: list[str]) -> str:
    """Joins wrapped motion-text lines. A trailing hyphen is treated as a
    line-wrap artifact splitting one word (e.g. "DISTRIBUTION DOCUMENTA-" +
    "TION" -> "...DOCUMENTATION") and removed rather than kept — this is
    right for the common case seen in every sample docket, but would
    incorrectly join a real compound-word hyphen (e.g. "NON-JUDICIAL") if
    the line happened to wrap exactly at that hyphen. Not observed in the
    four dockets checked; noted as a known limitation.
    """
    out = ""
    for p in parts:
        if not p:
            continue
        if out.endswith("-"):
            out = out[:-1] + p
        elif out:
            out = out + " " + p
        else:
            out = p
    return out


def normalize_decedent_name(name: str) -> str | None:
    """"KATHRINA H. MACLELLAN" -> "MACLELLAN KATHRINA H" — matches the
    Hamilton County Assessor/Trustee convention already in parcels.owner_name
    ("LAST FIRST MIDDLE", uppercase, no periods; a trailing generational
    suffix stays at the very end, e.g. parcels has "FLOYD LEROY JR"),
    confirmed by inspecting parcels.owner_name in the test DB. Matching this
    convention is what lets enrich_assessor.py's later owner-name matching
    against county parcel data actually have a chance of hitting.
    """
    if not name:
        return None
    name = name.strip()
    suffix_m = SUFFIX_RE.search(name)
    suffix = None
    if suffix_m:
        suffix = suffix_m.group(1).upper()
        name = name[: suffix_m.start()]
    name = name.replace(".", "").replace(",", "")
    tokens = [t for t in name.split() if t]
    if not tokens:
        return None
    if len(tokens) == 1:
        normalized = tokens[0]
    else:
        normalized = f"{tokens[-1]} {' '.join(tokens[:-1])}"
    if suffix:
        normalized = f"{normalized} {suffix}"
    return re.sub(r"\s+", " ", normalized).strip().upper()


def _parse_blocks(lines: list[str]) -> tuple[list[dict], dict]:
    """State-machine parse of one docket's already-extracted, page-header-
    stripped lines into estate blocks. See module docstring for the exact
    line-by-line layout this assumes.
    """
    starts = []
    i, n = 0, len(lines)
    while i < n:
        m = DOCKET_START_RE.match(lines[i])
        if m:
            j = i + 1
            while j < n and lines[j] == "":
                j += 1
            if j < n and DOCKET_NUM_RE.match(lines[j]):
                starts.append((i, m.group(1), lines[j]))
                i = j + 1
                continue
        i += 1

    blocks = []
    stats = {"conservatorship_or_guardianship_skipped": 0, "no_header_skipped": 0}
    for idx, (start_i, yy, nnn) in enumerate(starts):
        end_i = starts[idx + 1][0] if idx + 1 < len(starts) else n
        body = lines[start_i + 2 : end_i]

        k = 0
        while k < len(body) and body[k] == "":
            k += 1
        header_m = HEADER_RE.match(body[k]) if k < len(body) else None
        if not header_m:
            stats["no_header_skipped"] += 1
            continue
        if header_m.group(1).upper() != "ESTATE":
            stats["conservatorship_or_guardianship_skipped"] += 1
            continue
        k += 1

        while k < len(body) and body[k] == "":
            k += 1
        name_lines = []
        while k < len(body) and body[k] != "":
            name_lines.append(body[k])
            k += 1
        decedent_raw = " ".join(name_lines).strip()
        if not decedent_raw:
            stats["no_header_skipped"] += 1
            continue

        while k < len(body) and body[k] == "":
            k += 1
        disposition = None
        if k < len(body) and DISPOSITION_RE.match(body[k]):
            disposition = DISPOSITION_RE.match(body[k]).group(1).strip()
            k += 1
            # A wrapped continuation line has a genuinely lowercase WORD in
            # it (e.g. "for", "hearing"); an attorney/firm name line never
            # does, even ones with a Mc/Mac/De- prefix like "McBRIDE" (that
            # token itself isn't ALL-lowercase, just mixed-case).
            while k < len(body) and body[k] != "" and any(
                tok.islower() for tok in re.findall(r"[A-Za-z']+", body[k])
            ):
                disposition = (disposition + " " + body[k]).strip()
                k += 1
            disposition = disposition or None
        tail = body[k:]

        paragraphs, cur = [], []
        for line in tail:
            if line == "":
                if cur:
                    paragraphs.append(cur)
                    cur = []
            else:
                cur.append(line)
        if cur:
            paragraphs.append(cur)

        attorneys: list[tuple[str, str | None]] = []
        motion_lines: list[str] = []
        for p in paragraphs:
            first = p[0]
            is_attorney_start = not MOTION_START_RE.match(first)
            if is_attorney_start:
                firm = first
                rest = p[1:]
                individuals = None
                if rest and rest[0].startswith("(") and not MOTION_START_RE.match(rest[0]):
                    paren_lines, idx2 = [], 0
                    while idx2 < len(rest) and idx2 < 3:
                        paren_lines.append(rest[idx2])
                        closed = rest[idx2].endswith(")")
                        idx2 += 1
                        if closed:
                            break
                    else:
                        paren_lines, idx2 = [], 0
                    if paren_lines:
                        individuals = " ".join(paren_lines).strip().strip("()")
                        rest = rest[idx2:]
                attorneys.append((firm, individuals))
                if rest:
                    motion_lines.append(_join_wrapped(rest))
            else:
                motion_lines.append(_join_wrapped(p))

        blocks.append({
            "docket_no": f"{yy}-P-{nnn}",
            "decedent_raw": decedent_raw,
            "disposition": disposition,
            "attorneys": attorneys,
            "motion_text": " | ".join(m for m in motion_lines if m),
        })
    return blocks, stats


def parse(pdf_bytes: bytes, source_url: str, docket_date: str | None) -> tuple[list[dict], dict]:
    """Turns one Part 2 PDF into estate-case records ready for upsert()."""
    lines = _extract_lines(pdf_bytes)
    blocks, stats = _parse_blocks(lines)

    records = []
    for b in blocks:
        atty_strs = []
        pro_se_names = []
        for firm, individual in b["attorneys"]:
            firm_clean = (firm or "").strip()
            if not firm_clean:
                continue
            if firm_clean.upper() == "PRO SE":
                continue  # bare "no counsel of record" marker, no name attached
            pro_se_m = PRO_SE_RE.match(firm_clean)
            if pro_se_m:
                pro_se_names.append(pro_se_m.group(1).strip())
                continue
            atty_strs.append(f"{firm_clean} ({individual.strip()})" if individual else firm_clean)

        motion_text = b["motion_text"] or None
        desc_parts = [motion_text or "Motion (type not parsed from docket)"]
        if atty_strs:
            desc_parts.append("Atty: " + "; ".join(dict.fromkeys(atty_strs)))
        if pro_se_names:
            desc_parts.append("Pro Se: " + "; ".join(dict.fromkeys(pro_se_names)))
        if b["disposition"]:
            desc_parts.append(f"[{b['disposition']}]")
        description = " — ".join(desc_parts)
        if len(description) > 600:
            description = description[:597] + "..."

        party_names = b["decedent_raw"]
        if pro_se_names:
            party_names = party_names + "; Pro Se: " + "; ".join(dict.fromkeys(pro_se_names))

        records.append({
            "docket_no": b["docket_no"],
            "decedent_raw": b["decedent_raw"],
            "raw_owner_name": normalize_decedent_name(b["decedent_raw"]) or b["decedent_raw"].upper(),
            "party_names": party_names,
            "description": description,
            "docket_date": docket_date,
            "source_url": source_url,
        })
    stats["estate_blocks"] = len(blocks)
    return records, stats


def upsert(conn, records: list[dict]) -> int:
    for r in records:
        dedupe_key = f"chancery_probate:{r['docket_no']}"
        upsert_record(
            conn, "court_records", dedupe_key,
            keep_existing=("parcel_id", "resolution_method", "filing_date"),
            parcel_id=None,
            source_portal="hamilton_chancery_probate",
            case_number=r["docket_no"],
            case_type="probate",
            filing_date=r["docket_date"],
            party_names=r["party_names"],
            amount=None,
            raw_address=None,
            raw_owner_name=r["raw_owner_name"],
            description=r["description"],
            resolution_method="unresolved",
            source_url=r["source_url"],
        )
    return len(records)


def main() -> None:
    conn = get_connection()
    try:
        docket_pdfs, fetch_stats = fetch()
        processed = json.loads(get_state(conn, STATE_KEY) or "{}")

        all_records: list[dict] = []
        parse_stats_total = {"estate_blocks": 0, "conservatorship_or_guardianship_skipped": 0, "no_header_skipped": 0}
        changed_urls = []
        for item in docket_pdfs:
            sha = hashlib.sha256(item["content"]).hexdigest()
            prev = processed.get(item["url"])
            changed = prev is None or prev.get("sha256") != sha
            if changed:
                changed_urls.append(item["url"])
            records, stats = parse(item["content"], item["url"], item["docket_date"])
            all_records.extend(records)
            for k, v in stats.items():
                if isinstance(v, int):
                    parse_stats_total[k] = parse_stats_total.get(k, 0) + v
            processed[item["url"]] = {
                "sha256": sha,
                "docket_date": item["docket_date"],
                "last_checked": now_iso(),
            }

        # Bound source_state: keep only the most recently checked entries.
        if len(processed) > MAX_STATE_ENTRIES:
            ordered = sorted(processed.items(), key=lambda kv: kv[1].get("last_checked", ""), reverse=True)
            processed = dict(ordered[:MAX_STATE_ENTRIES])
        set_state(conn, STATE_KEY, json.dumps(processed))

        count = upsert(conn, all_records)
        conn.commit()

        notes = (
            f"fetch={fetch_stats}, parse={parse_stats_total}, "
            f"dockets_fetched={len(docket_pdfs)}, dockets_changed_since_last_check={len(changed_urls)}"
        )
        log_scrape(conn, "probate_dockets", record_count=count, status="ok", notes=notes)
        conn.commit()
        print(f"probate_dockets: upserted {count} estate cases from {len(docket_pdfs)} docket PDF(s). {notes}")
    except Exception as e:
        log_scrape(conn, "probate_dockets", record_count=0, status="error", notes=str(e))
        conn.commit()
        raise


if __name__ == "__main__":
    main()
