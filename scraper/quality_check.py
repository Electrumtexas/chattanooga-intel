"""
Data quality guardrail — run after all per-source scrapers, before
build_unified.py and before the workflow commits anything.

Compares each source's just-logged record count (the most recent
scrape_log row for that source) against a trailing 7-day baseline of prior
successful runs. If today's count is anomalously low (< that source's
threshold ratio) or zero when the baseline is nonzero, a BLOCKING source
exits non-zero — which fails the GitHub Actions step and (by default
step-failure behavior) skips build_unified.py and the commit/push step
entirely. Yesterday's committed chattanooga.db and dashboard/*.json are
simply never overwritten; there's no explicit rollback code because the bad
run's in-progress changes only ever exist in the ephemeral runner's
filesystem.

Not every source can be judged the same way, so SOURCES carries per-source
settings rather than one global threshold:

  blocking=True   A drop means something broke (the file moved, the markup
                  changed, we got blocked). Worth stopping the whole commit
                  for. The bulk ledgers — Trustee delinquency, city code
                  enforcement — behave this way: they're large and stable
                  day to day, so a sudden collapse is always a defect.

  blocking=False  The source is genuinely lumpy, so a low day is
                  information, not a fault. It still prints a warning, but
                  it never blocks the other sources' data from being
                  committed. Court dockets are the clear case: General
                  Sessions only publishes UPCOMING dockets (frequently an
                  empty list), Chancery probate dockets fall on alternating
                  Mondays, and the annual tax-sale PDF disappears from the
                  county site once that year's sale is over.

  allow_zero=True Zero is a legitimate reading for that source, so it's
                  recorded as a normal run. That matters beyond the pass/
                  fail: trailing_baseline() averages only runs marked 'ok',
                  so letting honest zeros into the baseline lets a lumpy
                  source's baseline settle near its real average instead of
                  staying pinned at its busiest week and warning forever.

A run with no baseline yet (a source's first-ever run) always passes —
there's nothing to compare against. A source that didn't run at all this
session is skipped rather than failed; scrape steps are continue-on-error
in the workflow, and a source that crashed has already logged its own
'error' row.

Deliberate simplification: any blocking source's failure fails the whole
check (and blocks the whole commit that run), even if other sources were
fine. Per-source partial commits aren't really possible anyway —
chattanooga.db is one SQLite file holding all sources' tables, so there's
no clean way to commit "just the good tables" without surgical per-table
restore logic. Given the daily cadence, a one-day delay for a source that
had a bad day is an acceptable tradeoff for that added complexity.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from db import get_connection, latest_run, mark_run_status, trailing_baseline

BASELINE_DAYS = 7


@dataclass(frozen=True)
class SourceCheck:
    threshold_ratio: float = 0.30
    blocking: bool = True
    allow_zero: bool = False
    note: str = ""


SOURCES: dict[str, SourceCheck] = {
    "tax_delinquent": SourceCheck(
        note="Monthly Trustee ledger, ~12k currently-owed real-property rows; stable between refreshes.",
    ),
    "code_enforcement": SourceCheck(
        note="City violation export; large and slow-moving, and a skipped (unchanged) run still logs the tracked row count.",
    ),
    "tax_sale": SourceCheck(
        threshold_ratio=0.50,
        blocking=False,
        allow_zero=True,
        note="One PDF per year that vanishes from the county site after the sale; a zero means 'no list posted', not a break.",
    ),
    "probate_dockets": SourceCheck(
        threshold_ratio=0.20,
        blocking=False,
        allow_zero=True,
        note="Chancery Part 2 dockets fall on alternating Mondays, so most days have nothing new.",
    ),
    "sessions_dockets": SourceCheck(
        threshold_ratio=0.20,
        blocking=False,
        allow_zero=True,
        note="edockets lists only upcoming dockets and is often empty outright.",
    ),
    "enrich_assessor": SourceCheck(
        blocking=False,
        allow_zero=True,
        note="Resolves a backlog, so counts fall off naturally once it's caught up; it fails loudly on its own if GIS is down.",
    ),
    "foreclosure_notices": SourceCheck(
        threshold_ratio=0.30,
        blocking=False,
        allow_zero=True,
        note="Additive feed across 6 independent posting sites; one site being blocked or down "
             "doesn't zero out the others, so a low day is normal variation, not a broken pipeline.",
    ),
}


def check_source(conn, source: str, config: SourceCheck | None = None) -> tuple[bool, str]:
    config = config or SOURCES.get(source) or SourceCheck()
    run = latest_run(conn, source)
    if run is None:
        return True, f"{source}: no run logged yet — skipping (nothing scraped this session)"

    baseline = trailing_baseline(conn, source, days=BASELINE_DAYS)
    count = run["record_count"]
    tag = "" if config.blocking else " (non-blocking)"

    if baseline is None:
        mark_run_status(conn, run["id"], "ok", "first run — no baseline to compare against")
        return True, f"{source}: {count} records, no baseline yet (establishing history)"

    if baseline > 0 and count == 0:
        if config.allow_zero:
            mark_run_status(conn, run["id"], "ok", f"0 records, baseline {baseline:.1f} — expected for this source")
            return True, f"{source}: 0 records vs {baseline:.1f} baseline — normal for this source (nothing new posted)"
        mark_run_status(conn, run["id"], "quality_check_failed", f"got 0 records, baseline {baseline:.1f}")
        return False, f"{source}: got 0 records, baseline is {baseline:.1f} — likely blocked or portal markup changed{tag}"

    if baseline > 0 and count < baseline * config.threshold_ratio:
        pct = count / baseline
        if config.allow_zero and not config.blocking:
            mark_run_status(conn, run["id"], "ok", f"{count} vs baseline {baseline:.1f} ({pct:.0%}) — lumpy source")
            return True, f"{source}: {count} records, {pct:.0%} of the {baseline:.1f} baseline — low but expected to vary"
        mark_run_status(conn, run["id"], "quality_check_failed", f"{count} vs baseline {baseline:.1f} ({pct:.0%})")
        return False, f"{source}: got {count} records, only {pct:.0%} of the {baseline:.1f} trailing baseline{tag}"

    mark_run_status(conn, run["id"], "ok", None)
    return True, f"{source}: {count} records vs {baseline:.1f} baseline — OK"


def main() -> int:
    conn = get_connection()
    sources = sys.argv[1:] or list(SOURCES)
    blocked = False
    for source in sources:
        config = SOURCES.get(source) or SourceCheck()
        ok, message = check_source(conn, source, config)
        if ok:
            prefix = "[OK]  "
        elif config.blocking:
            prefix = "[FAIL]"
            blocked = True
        else:
            prefix = "[WARN]"
        print(prefix + " " + message)
    conn.commit()
    if blocked:
        print(
            "\nQuality check failed — leaving yesterday's committed data in place. "
            "This run's changes will not be committed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
