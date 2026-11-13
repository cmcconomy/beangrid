"""One-off: rebuild the price history from the daily docs/beangrid.csv snapshots in git history.

Each historical snapshot is regrouped into Shopify-shaped products and fed through the same
normalize + validate path as a live run, so backfilled history obeys the same quality rules
(truncated or missing sites on a given day are skipped, not recorded as delisted).
"""

import ast
import csv
import io
import json
import logging
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from beangrid.config import REPO_ROOT, Settings
from beangrid.history import History, Observation, update_catalog
from beangrid.normalize import Variant, normalize_site
from beangrid.pipeline import count_gate

logger = logging.getLogger(__name__)
SNAPSHOT_PATH = "docs/beangrid.csv"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True).stdout


def snapshot_commits(ref: str) -> list[tuple[date, str]]:
    """Last commit per UTC day that touched the snapshot, oldest first."""
    out = _git("log", ref, "--format=%H %ct", "--", SNAPSHOT_PATH).decode().split("\n")
    per_day: dict[date, tuple[int, str]] = {}
    for line in out:
        if not line.strip():
            continue
        sha, ts = line.split()
        day = datetime.fromtimestamp(int(ts), UTC).date()
        if day not in per_day or int(ts) > per_day[day][0]:
            per_day[day] = (int(ts), sha)
    return [(d, per_day[d][1]) for d in sorted(per_day)]


def _literal(s: str) -> Any:
    try:
        return ast.literal_eval(s) if s else None
    except (ValueError, SyntaxError):
        return None


def rows_to_products(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """Regroup flattened variant rows into {site_url: [shopify product dict, ...]}."""
    products: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        url = r.get("product_url") or ""
        if "/products/" not in url:
            continue
        site_url = url.split("/products/")[0]
        pid = r.get("product_id") or r.get("id") or ""
        p = products.get((site_url, pid))
        if p is None:
            options = _literal(r.get("options", ""))
            p = {
                "id": int(float(pid)) if pid else 0,
                "title": r.get("title", ""),
                "handle": url.split("/products/")[1],
                "product_type": r.get("product_type", ""),
                "vendor": r.get("vendor", ""),
                "tags": _literal(r.get("tags", "")) or [],
                "options": options if isinstance(options, list) else [],
                "published_at": r.get("published_at"),
                "body_html": None,
                "images": [],
                "variants": [],
            }
            products[(site_url, pid)] = p
        src = r.get("featured_image.src")
        p["variants"].append({
            "id": int(float(r["item_id"])) if r.get("item_id") else 0,
            "title": r.get("item_title", ""),
            "price": r.get("price"),
            "compare_at_price": r.get("compare_at_price") or None,
            "grams": r.get("grams"),
            "available": (r.get("available") or "").lower() == "true",
            "featured_image": {"src": src} if src else None,
        })
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (site_url, _), p in products.items():
        by_site[site_url].append(p)
    return by_site


def backfill(settings: Settings, ref: str = "HEAD", until: date | None = None) -> History:
    csv.field_size_limit(sys.maxsize)
    history = History()
    gates: dict[str, dict[str, Any]] = {}
    latest_variants: dict[str, Variant] = {}
    commits = snapshot_commits(ref)
    for i, (day, sha) in enumerate(commits):
        if until and day > until:
            break
        raw = _git("show", f"{sha}:{SNAPSHOT_PATH}").decode("utf-8", errors="replace")
        rows = list(csv.DictReader(io.StringIO(raw)))
        by_site = rows_to_products(rows)
        skipped = []
        for site_url, products in by_site.items():
            variants = normalize_site(site_url, products)
            if not variants:
                continue
            site = variants[0].site
            if count_gate(len(products), gates.setdefault(site, {}), settings):
                skipped.append(site)
                continue
            history.apply_snapshot(day, site, (Observation.of(v) for v in variants))
            for v in variants:
                latest_variants[v.key] = v
        logger.info("[%d/%d] %s %s: %d rows, %d sites%s", i + 1, len(commits), day, sha[:7], len(rows),
                    len(by_site), f", skipped truncated {skipped}" if skipped else "")

    history.save_all(settings.data_dir / "history")
    catalog = settings.data_dir / "catalog.csv"
    catalog.unlink(missing_ok=True)
    update_catalog(catalog, latest_variants.values(), history)
    status = {site: gate for site, gate in gates.items()}
    (settings.data_dir / "sites.json").write_text(json.dumps(dict(sorted(status.items())), indent=1) + "\n")
    return history
