# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Beangrid scrapes Canadian coffee roaster Shopify stores and aggregates product data into a searchable interface at https://beangrid.mcconomy.org/

## Commands

```bash
# Install dependencies
poetry install

# Run scraper for all sites (outputs CSV to stdout)
poetry run python src/beangrid/scrape_shopify.py

# Run scraper for a single site
poetry run python src/beangrid/scrape_shopify.py <url>

# Build Jupyter Lite environment
poetry run jupyter lite build --contents notebooks/jupyterlite --output-dir docs/jupyterlite
```

## Architecture

**Data Flow:** Shopify sites → Python scraper → CSV → Static frontend (AG Grid)

**Backend (`src/beangrid/`):**
- `scrape_shopify.py` - Async scraper using httpx to fetch `/products.json` endpoints from Shopify stores. Handles pagination, merges product/variant data with pandas, strips HTML from descriptions.
- `shopify_sites.txt` - List of Shopify store URLs to scrape
- `non_shopify_sites.txt` - Placeholder for future non-Shopify sites

**Frontend (`docs/`):**
- `index.html` + `main.js` - AG Grid interface with filtering, sorting, computed price-per-kilo column
- `beangrid.csv` - Generated data file (committed by CI)
- Served via GitHub Pages

**CI/CD (`.github/workflows/publish.yaml`):**
- Runs daily at 05:00 UTC via cron
- Scrapes all sites, commits updated CSV, builds Jupyter Lite, deploys to GitHub Pages
