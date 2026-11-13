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
- `src/beangrid/deals.py`: collapse grind variants into offers; sale / drop / value / new scoring
- `src/beangrid/pipeline.py`: daily run, per-site validation + carry-forward, quality gate, site build
- `site/`: static front end (vanilla JS, no build). Reads `_site/data/offers.json`
- `src/beangrid/canvass.py`: weekly store-list upkeep. Prunes only failures that look permanent (404, non-JSON,
  DNS) *and* that `data/sites.json` shows failing for `prune_after_days`; discovers via `candidates.txt` and
  `aggregator_sites` vendors, validated with Shopify `/meta.json` (country) + coffee classification
- `.github/workflows/publish.yaml`: daily cron; commits `data/`, deploys `_site/` to Pages
- `.github/workflows/canvass.yaml`: weekly cron; opens a PR editing `sites.txt`

## Conventions

- When a classification is wrong, add a case to `tests/test_classify.py` first, then adjust rules.
- Never commit generated site output; only `data/` is committed (by CI).
