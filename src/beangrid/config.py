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
    concurrency: int = 3
    timeout_s: float = 30.0
    max_retries: int = 6
    backoff_base_s: float = 5.0
    backoff_max_s: float = 60.0
    page_limit: int = 250  # Shopify's maximum page size for products.json
    max_pages: int = 40

    # A site whose product count drops below this fraction of its last good count is
    # treated as a failed scrape (truncated pagination, maintenance page, ...).
    min_site_ratio: float = 0.5
    # ...unless the lower count repeats this many runs in a row: then it's the new normal.
    confirm_shrink_runs: int = 2
    # Abort the run (no commit, no deploy) if fewer than this fraction of sites succeed.
    min_sites_ok_ratio: float = 0.6
    # Carry a failed site's last good data forward for at most this many days.
    carry_forward_days: int = 5


def load_sites(path: Path) -> list[str]:
    sites = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            sites.append(line.rstrip("/"))
    return sites
