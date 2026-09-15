"""
Data quality guardrail — run after all per-source scrapers, before
build_unified.py and before the workflow commits anything.

Compares each source's just-logged record count (the most recent
scrape_log row for that source) against a trailing 7-day baseline of prior
successful runs. If today's count is anomalously low (< THRESHOLD_RATIO of
baseline) or zero when the baseline is nonzero, this exits non-zero — which
fails the GitHub Actions step and (by default step-failure behavior) skips
build_unified.py and the commit/push step entirely. Yesterday's committed
chattanooga.db and dashboard/*.json are simply never overwritten; there's
no explicit rollback code because the bad run's in-progress changes only
ever exist in the ephemeral runner's filesystem.

Deliberate simplification: a failure in ANY one source fails the whole
check (and blocks the whole commit that run), even if the other sources
were fine. Doing per-source partial commits isn't really possible anyway —
chattanooga.db is one SQLite file holding all sources' tables, so there's
no clean way to commit "just the good tables" without surgical per-table
restore logic. Given the daily cadence, a one-day delay for a source that
had a bad day is an acceptable tradeoff for that added complexity. If this
ever becomes a real problem, see the pending-work note in CLAUDE.md.

A run with no baseline yet (a source's first-ever run) always passes —
there's nothing to compare against.
"""

from __future__ import annotations

import sys

from db import get_connection, latest_run, mark_run_status, trailing_baseline

THRESHOLD_RATIO = 0.30
BASELINE_DAYS = 7
SOURCES = ["court_records", "tax_delinquent", "code_enforcement"]


def check_source(conn, source: str) -> tuple[bool, str]:
    run = latest_run(conn, source)
    if run is None:
        return True, f"{source}: no run logged yet — skipping (nothing scraped this session)"

    baseline = trailing_baseline(conn, source, days=BASELINE_DAYS)
    count = run["record_count"]

    if baseline is None:
        mark_run_status(conn, run["id"], "ok", "first run — no baseline to compare against")
        return True, f"{source}: {count} records, no baseline yet (establishing history)"

    if baseline > 0 and count == 0:
        mark_run_status(conn, run["id"], "quality_check_failed", f"got 0 records, baseline {baseline:.1f}")
        return False, f"{source}: got 0 records, baseline is {baseline:.1f} — likely blocked or portal markup changed"

    if baseline > 0 and count < baseline * THRESHOLD_RATIO:
        pct = count / baseline
        mark_run_status(conn, run["id"], "quality_check_failed", f"{count} vs baseline {baseline:.1f} ({pct:.0%})")
        return False, f"{source}: got {count} records, only {pct:.0%} of the {baseline:.1f} trailing baseline"

    mark_run_status(conn, run["id"], "ok", None)
    return True, f"{source}: {count} records vs {baseline:.1f} baseline — OK"


def main() -> int:
    conn = get_connection()
    sources = sys.argv[1:] or SOURCES
    all_ok = True
    for source in sources:
        ok, message = check_source(conn, source)
        print(("[OK]  " if ok else "[FAIL]") + " " + message)
        all_ok = all_ok and ok
    conn.commit()
    if not all_ok:
        print(
            "\nQuality check failed — leaving yesterday's committed data in place. "
            "This run's changes will not be committed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
