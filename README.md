# Chattanooga Intel

Distressed-property intelligence for Hamilton County, TN. Pulls public records (tax
delinquency, code enforcement, court filings), stacks distress signals per property, scores
leads, and serves a map-based dashboard — fully automated via GitHub Actions, hosted on
GitHub Pages, no external server.

**Dashboard:** https://electrumtexas.github.io/chattanooga-intel/ (GitHub Pages, deployed by
the `deploy` workflow job — populates once the `scrape` workflow has run at least once).

## Status

Tax-delinquent data is live and real (7,926 Hamilton County parcels currently delinquent).
Court records, code-enforcement field verification, and GIS/lat-long enrichment are still in
progress — see [CLAUDE.md](CLAUDE.md) for exactly what's verified, what's assumed, and what's
next.

## Local development

```bash
cd scraper
pip install -r requirements.txt
python tax_delinquent.py
python code_enforcement.py
python quality_check.py
python build_unified.py
```

Preview the dashboard with the `dashboard` launch config, or:

```bash
python -m http.server 8420 --directory dashboard
```

## Structure

- `scraper/` — one module per data source (`fetch()` / `parse()` / `upsert()`), plus
  `scoring.py` and `build_unified.py`
- `data/chattanooga.db` — canonical SQLite store
- `dashboard/` — the static site (single HTML file + generated JSON)
- `.github/workflows/scrape.yml` — daily scrape → quality check → build → commit → deploy

Full architecture, verified scraping mechanics, scoring rationale, and the prioritized
pending-work list live in [CLAUDE.md](CLAUDE.md).
