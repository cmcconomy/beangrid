from pathlib import Path
from typing import Any

import pytest

from beangrid.config import REPO_ROOT, Settings


def make_product(
    pid: int = 1,
    title: str = "Ethiopia Guji Natural",
    product_type: str = "Coffee",
    tags: list[str] | None = None,
    variants: list[dict[str, Any]] | None = None,
    handle: str | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "title": title,
        "handle": handle or f"p{pid}",
        "body_html": "<p>Notes of <b>blueberry</b> &amp; jasmine.</p>",
        "published_at": "2026-10-01T00:00:00-04:00",
        "vendor": "Test Roasters",
        "product_type": product_type,
        "tags": tags or [],
        "options": [{"name": "Size", "values": [v.get("title", "") for v in (variants or [])]}],
        "images": [{"src": "https://cdn.example/img.png"}],
        "variants": variants
        or [{"id": pid * 10, "title": "340g", "price": "24.00", "grams": 360, "available": True, "compare_at_price": None}],
    }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    sites = tmp_path / "sites.txt"
    sites.write_text("# comment\nhttps://alpha.example\nhttps://www.beta.example/\n")
    return Settings(
        sites_file=sites,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        site_src_dir=REPO_ROOT / "site",
        site_out_dir=tmp_path / "_site",
        max_retries=1,
        backoff_base_s=0.01,
    )
