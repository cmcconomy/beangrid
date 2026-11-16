from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).parent
REPO_ROOT = PACKAGE_DIR.parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a BEANGRID_<NAME> env var."""

    model_config = SettingsConfigDict(env_prefix="BEANGRID_")

    sites_file: Path = PACKAGE_DIR / "sites.txt"
    # Committed, append-friendly archive (price change log + catalog).
    data_dir: Path = REPO_ROOT / "data"
    # Last good raw payload per site; restored from the Actions cache so a failing site
    # can be carried forward without bloating git history.
    cache_dir: Path = REPO_ROOT / ".cache" / "sites"
    # Static site sources and build output.
    site_src_dir: Path = REPO_ROOT / "site"
    site_out_dir: Path = REPO_ROOT / "_site"

    user_agent: str = "beangrid/2.0 (+https://beangrid.mcconomy.org)"
    # Shopify rate-limits products.json per client IP across *all* its stores, so stay gentle.
    concurrency: int = 2
    # Minimum gap between any two request starts (all workers share it): a steady trickle stays under
    # Shopify's per-IP limit far better than bursts plus retries.
    request_interval_s: float = 1.0
    # After this many stores in a row fail on rate limits/timeouts (no success in between), stop
    # starting new ones; they keep their last good data and lead the queue next run.
    breaker_after: int = 5
    # A store already scraped OK today reuses that scrape (a second daily run only retries the misses).
    reuse_scraped_today: bool = True
    timeout_s: float = 30.0
    max_retries: int = 6
    backoff_base_s: float = 5.0
    backoff_max_s: float = 60.0
    page_limit: int = 250  # Shopify's maximum page size for products.json
    max_pages: int = 40
    # Stop starting requests/retries after this long; must stay well under the CI job timeout.
    fetch_budget_s: float = 1200.0
    # Read store homepages for promo codes / free-shipping thresholds this run (the 14:23 catch-up run).
    scan_promos: bool = False
    promo_budget_s: float = 600.0

    # A site whose product count drops below this fraction of its last good count is
    # treated as a failed scrape (truncated pagination, maintenance page, ...).
    min_site_ratio: float = 0.5
    # ...unless the lower count repeats this many runs in a row: then it's the new normal.
    confirm_shrink_runs: int = 2
    # Abort the run (no commit, no deploy) if fewer than this fraction of sites succeed.
    min_sites_ok_ratio: float = 0.6  # of sites that must be usable: fresh today or carried forward
    min_fresh_ratio: float = 0.1  # ...and at least this share freshly scraped, or there's nothing to publish
    # Carry a failed site's last good data forward for at most this many days.
    carry_forward_days: int = 7

    # Weekly canvass (`beangrid canvass`): prune dead stores, propose new Canadian roasters.
    # Roaster names or storefront URLs to try, one per line; reviewed additions get moved to sites_file.
    candidates_file: Path = PACKAGE_DIR / "candidates.txt"
    # Multi-roaster Canadian Shopify retailers; the vendors of their coffee products are candidate roasters.
    aggregator_sites: list[str] = ["https://theroasterspack.com", "https://www.idrinkcoffee.com"]
    aggregator_pages: int = 5
    # Only comment a store out if the daily run has also not scraped it for this long.
    prune_after_days: int = 7
    # A discovered store needs at least this many products classified as coffee.
    canvass_min_coffee: int = 3
    canvass_countries: list[str] = ["CA"]
    # Upper bound on roaster names resolved per run (each name costs a handful of requests).
    canvass_max_names: int = 60
    # products.json pages read per store during the canvass; one page of 250 is plenty to classify.
    canvass_pages: int = 1


def load_sites(path: Path) -> list[str]:
    """Active site URLs; `#` starts a comment, either for a whole line or after the URL."""
    sites = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            sites.append(line.rstrip("/"))
    return sites
