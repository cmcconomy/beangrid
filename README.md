# Beangrid: Canadian coffee bean deals

A daily price survey of whole-bean coffee from Canadian specialty roasters: **https://beangrid.mcconomy.org/**

Every morning a GitHub Action reads each roaster's public Shopify catalogue, keeps only roasted coffee
beans, works out the real bag size, records any price/stock changes in a permanent archive, and publishes
a static site that surfaces price drops, sales, best value per kilo and new arrivals.

## How it works

```
sites.txt ─► fetch (httpx, retries, per-site isolation)
          ─► validate (truncated/failed store? carry yesterday's copy forward)
          ─► normalize + classify (coffee or not, bag size, origin, process, roast, decaf)
          ─► history (append price/stock *changes* to data/history/<year>.csv)
          ─► deals (sale %, drop vs 30-day typical price, $/kg percentile, new, back in stock)
          ─► _site/ (static HTML/JS + data/offers.json) ─► GitHub Pages
```

* **Coffee only.** `src/beangrid/classify.py` filters out gear, merch, syrups, tea, subscriptions, gift
  cards, bundles, pods, instant, ready-to-drink and green coffee, in English and French. Everything is still
  published in `beangrid.csv` with its category, for transparency.
* **Real bag sizes.** Shopify's `grams` is a shipping weight and is often wrong (a 5 lb bag at 454 g,
  10.6 oz at 170 g). The size printed on the label wins; shipping weight is only a fallback, with packaging
  stripped. `src/beangrid/weights.py`.
* **Questionable data is flagged, not trusted.** Missing sizes, implausible $/kg, or a bag far cheaper per kg
  than the roaster's own norm are marked `suspect`. They're hidden by default and never ranked as deals.
* **Bad runs don't publish.** A store that errors or returns far fewer products than last time is treated as
  failed and its previous data is carried forward (marked stale) for up to 5 days. If too many stores fail,
  the run aborts before committing or deploying.

## The archive

`data/` is committed daily and is the long-term record (backfilled from the old daily CSV snapshots back to
Nov 2023):

| file | contents |
|---|---|
| `data/history/<year>.csv` | `date,key,price,compare_at,available,listed`: one row per variant per *change* |
| `data/catalog.csv` | what each `key` (`<store>:<variant id>`) is: title, size, category, url, first seen |
| `data/sites.json` | per-store status, last good scrape and product count |

To get the price of any variant on any day, take the latest history row on or before that day.

## Development

```bash
uv sync --all-groups
uv run pytest                                           # tests
uv run beangrid probe https://pilotcoffeeroasters.com   # how one store's coffee gets classified
uv run beangrid probe https://pilotcoffeeroasters.com --all
uv run beangrid run                                     # full scrape -> data/ and _site/
python -m http.server -d _site                          # view the site locally
uv run beangrid backfill origin/main                    # rebuild history from git snapshots (one-off)
```

Settings can be overridden with `BEANGRID_*` environment variables (see `src/beangrid/config.py`).

## Contributing

Know a Canadian roaster on Shopify that's missing? Add its URL to
[`src/beangrid/sites.txt`](src/beangrid/sites.txt) and open a PR. `uv run beangrid probe <url>` shows how its
products will be classified.

Appreciate this project? [Buy me a coffee!](https://www.buymeacoffee.com/Cmcconomy)
