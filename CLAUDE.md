# CLAUDE.md

Beangrid scrapes Canadian coffee roasters' Shopify stores daily, keeps only roasted coffee beans, archives
price changes, and publishes a static deals site at https://beangrid.mcconomy.org/

## Commands

```bash
uv sync --all-groups
uv run pytest
uv run beangrid run                 # scrape -> data/ (archive) + _site/ (static site)
uv run beangrid probe <store-url>   # debug classification/weights for one store (--all for non-coffee)
uv run beangrid backfill <ref>      # rebuild data/history/ from git snapshots of docs/beangrid.csv
uv run beangrid canvass [--write]   # prune dead stores from sites.txt, propose new Canadian roasters
```

## Layout

- `src/beangrid/fetch.py`: async Shopify `products.json` fetch (limit=250, retries, redirects, per-site isolation)
- `src/beangrid/classify.py`: coffee vs. not (EN/FR keyword rules) + origin/process/roast/decaf attributes
- `src/beangrid/weights.py`: bag size from labels; Shopify `grams` is shipping weight and only a fallback
- `src/beangrid/normalize.py`: Shopify JSON -> `Variant` records, quality flags
- `src/beangrid/history.py`: append-only change log (`data/history/<year>.csv`) + `data/catalog.csv`
- `src/beangrid/deals.py`: collapse grind variants into offers; sale / drop / value / new scoring.
  Green beans (`green_coffee`, incl. "Green Beans" variant options) are offers with `green=True`, valued
  among themselves; the site's Roasted / Green beans switch (`#beans=green`) shows one or the other
- `src/beangrid/pipeline.py`: daily run, per-site validation + carry-forward, quality gate, site build.
  Shopify rate-limits per IP, so: stalest stores first (`last_ok` in `data/sites.json`, ties rotate daily),
  paced requests + circuit breaker in `fetch.py`, stores already scraped today reused (02:23 + 14:23 UTC runs),
  gate counts carried-forward stores as usable but needs some fresh ones
- `src/beangrid/export.py`: Parquet copies of history + catalog for `_site/data/` (built each run, never committed)
- `site/`: static front end (vanilla JS, no build). Reads `_site/data/offers.json`
- `src/beangrid/promos.py`: homepage promo scan (codes, free-shipping thresholds, subscriber %), only in the
  14:23 run (`BEANGRID_SCAN_PROMOS`); `data/promos.json` keeps last known per store, `data/promo_log.csv`
  logs codes appearing/disappearing. Add a real banner to `tests/test_promos.py` before changing the regexes
- `src/beangrid/locks.py`: members-only products. Wholesale Lock Manager (Bows) lists hidden handles on every page,
  so the homepage promo scan picks them up for free (`hidden_products`). Locksmith (only visible on the product
  page): capped batch
  (`lock_check_max`) of product pages per 14:23 run for stores whose homepage loads Locksmith; verdicts in
  `data/locks.json` keep applying when off. Kill switches: `gh variable set BEANGRID_LOCK_CHECK --body off`
  (same for `BEANGRID_SCAN_PROMOS`)
- `src/beangrid/canvass.py`: weekly store-list upkeep. Prunes only failures that look permanent (404, non-JSON,
  DNS) *and* that `data/sites.json` shows failing for `prune_after_days`; discovers via `candidates.txt` and
  `aggregator_sites` vendors, validated with Shopify `/meta.json` (country) + coffee classification
- `.github/workflows/publish.yaml`: daily cron; commits `data/`, deploys `_site/` to Pages
- `.github/workflows/canvass.yaml`: weekly cron; opens a PR editing `sites.txt`

## Conventions

- When a classification is wrong, add a case to `tests/test_classify.py` first, then adjust rules.
- Never commit generated site output; only `data/` is committed (by CI).
