"""Daily run: fetch -> validate -> normalize -> archive -> build static site data."""

import asyncio
import csv
import gzip
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from beangrid.config import Settings, load_sites
from beangrid.deals import Offer, collapse, score_offers
from beangrid.fetch import SiteResult, fetch_all
from beangrid.history import History, Observation, update_catalog
from beangrid.normalize import Variant, normalize_site, site_id

logger = logging.getLogger(__name__)

FLAT_CSV_FIELDS = [
    "key", "roaster", "site", "title", "variant_title", "category", "price", "compare_at_price", "available",
    "grams", "weight_source", "price_per_kg", "origins", "process", "roast", "decaf", "blend", "style",
    "sample", "flags", "product_type", "url", "published_at",
]


class QualityGateError(RuntimeError):
    """Too many sites failed; refuse to publish rather than overwrite good data with bad."""


@dataclass
class SiteOutcome:
    site: str
    url: str
    status: str  # ok | stale | failed
    products: list[dict[str, Any]]
    error: str | None = None
    fetched_count: int = 0
    gate: dict[str, Any] = field(default_factory=dict)


def _cache_path(settings: Settings, site: str) -> Path:
    return settings.cache_dir / f"{site}.json.gz"


def _read_cache(settings: Settings, site: str) -> tuple[date, list[dict[str, Any]]] | None:
    path = _cache_path(settings, site)
    if not path.exists():
        return None
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
        return date.fromisoformat(payload["date"]), payload["products"]
    except Exception:
        logger.warning("Unreadable cache for %s", site)
        return None


def _write_cache(settings: Settings, site: str, day: date, products: list[dict[str, Any]]) -> None:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"date": day.isoformat(), "products": products}).encode()
    _cache_path(settings, site).write_bytes(gzip.compress(data))


def load_site_status(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text()) if path.exists() else {}


def count_gate(n: int, gate: dict[str, Any], settings: Settings) -> str | None:
    """Reject a product count that collapsed versus the last good one (truncated scrape).

    A store that genuinely shrinks its catalogue would otherwise be rejected forever, so a lower
    count is accepted as the new normal once it repeats (within 20%) on consecutive runs.
    Mutates ``gate`` (last_count / pending_count / pending_runs). Returns an error or None.
    """
    last = gate.get("last_count") or 0
    if not last or n >= last * settings.min_site_ratio:
        gate.update(last_count=n, pending_count=None, pending_runs=0)
        return None
    pending = gate.get("pending_count")
    if pending and abs(n - pending) <= pending * 0.2:
        gate["pending_runs"] = (gate.get("pending_runs") or 0) + 1
    else:
        gate.update(pending_count=n, pending_runs=1)
    if gate["pending_runs"] >= settings.confirm_shrink_runs:
        logger.info("count %d confirmed as new normal (was %d)", n, last)
        gate.update(last_count=n, pending_count=None, pending_runs=0)
        return None
    return f"only {n} products vs {last} last time (truncated?)"


def validate_sites(
    results: list[SiteResult], status: dict[str, dict[str, Any]], settings: Settings, today: date
) -> list[SiteOutcome]:
    """Accept a site's scrape only if it looks complete; otherwise fall back to its last good copy."""
    outcomes = []
    for r in results:
        site = site_id(r.site_url)
        prev = status.get(site, {})
        gate = {k: prev.get(k) for k in ("last_count", "pending_count", "pending_runs")}
        error = r.error
        if r.ok and not r.products:
            error = "returned 0 products"
        elif r.ok:
            error = count_gate(len(r.products), gate, settings)
        if error is None:
            _write_cache(settings, site, today, r.products)
            outcomes.append(SiteOutcome(site, r.site_url, "ok", r.products, fetched_count=len(r.products), gate=gate))
            continue
        cached = _read_cache(settings, site)
        if cached and (today - cached[0]).days <= settings.carry_forward_days:
            logger.warning("%s: %s; carrying forward data from %s", site, error, cached[0])
            outcomes.append(SiteOutcome(site, r.site_url, "stale", cached[1], error, len(r.products), gate))
        else:
            logger.error("%s: %s; no recent cache, dropping site today", site, error)
            outcomes.append(SiteOutcome(site, r.site_url, "failed", [], error, len(r.products), gate))
    return outcomes


def write_flat_csv(path: Path, variants: list[Variant]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FLAT_CSV_FIELDS)
        w.writeheader()
        for v in sorted(variants, key=lambda v: (v.site, v.title, v.variant_title)):
            d = v.model_dump()
            d["key"] = v.key
            for k in ("origins", "process", "flags"):
                d[k] = ";".join(d[k])
            w.writerow({k: d.get(k) for k in FLAT_CSV_FIELDS})


def write_offers_csv(path: Path, offers: list[Offer]) -> None:
    fields = [
        "roaster", "title", "size", "grams", "price", "ppk", "compare_at", "sale_pct", "drop_pct", "deal_score",
        "available", "origins", "process", "roast", "decaf", "blend", "style", "suspect", "flags", "url",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for o in sorted(offers, key=lambda o: (o.roaster, o.title, o.grams or 0)):
            d = o.model_dump()
            for k in ("origins", "process", "flags"):
                d[k] = ";".join(d[k])
            w.writerow({k: d.get(k) for k in fields})


def build_site(
    settings: Settings,
    offers: list[Offer],
    variants: list[Variant],
    site_status: dict[str, dict[str, Any]],
    today: date,
) -> None:
    out = settings.site_out_dir
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(settings.site_src_dir, out)
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for v in variants:
        counts[v.category] = counts.get(v.category, 0) + 1
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "category_counts": counts,
        "history_years": sorted(int(f.stem) for f in (settings.data_dir / "history").glob("*.csv")),
        "sites": site_status,
        "offers": [o.model_dump(exclude_none=True, exclude={"image", "weight_source"}) for o in offers],
    }
    (data_dir / "offers.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    write_offers_csv(data_dir / "coffee.csv", offers)
    write_flat_csv(out / "beangrid.csv", variants)  # every product, all categories; legacy URL
    if (settings.data_dir / "catalog.csv").exists():
        shutil.copy(settings.data_dir / "catalog.csv", data_dir / "catalog.csv")
    if (settings.data_dir / "history").exists():
        shutil.copytree(settings.data_dir / "history", data_dir / "history")


def run(settings: Settings, today: date | None = None, results: list[SiteResult] | None = None) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    sites = load_sites(settings.sites_file)
    status_path = settings.data_dir / "sites.json"
    prev_status = load_site_status(status_path)

    if results is None:
        results = asyncio.run(fetch_all(sites, settings))
    outcomes = validate_sites(results, prev_status, settings, today)

    ok = [o for o in outcomes if o.status == "ok"]
    if len(ok) < len(outcomes) * settings.min_sites_ok_ratio:
        raise QualityGateError(f"only {len(ok)}/{len(outcomes)} sites scraped cleanly; not publishing")

    history = History.load(settings.data_dir / "history")
    if (last := history.last_date) and last > today:
        raise RuntimeError(f"history already has data for {last}, after {today}")

    all_variants: list[Variant] = []
    new_status: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        variants = normalize_site(o.url, o.products)
        all_variants.extend(variants)
        if o.status == "ok":
            history.apply_snapshot(today, o.site, (Observation.of(v) for v in variants))
        prev = prev_status.get(o.site, {})
        new_status[o.site] = {
            "url": o.url,
            "roaster": variants[0].roaster if variants else prev.get("roaster", o.site),
            "status": o.status,
            "error": o.error,
            "last_ok": today.isoformat() if o.status == "ok" else prev.get("last_ok"),
            "coffee_variants": sum(1 for v in variants if v.category == "coffee"),
            **o.gate,
        }

    written = history.append_new(settings.data_dir / "history")
    update_catalog(settings.data_dir / "catalog.csv", all_variants, history)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(dict(sorted(new_status.items())), indent=1) + "\n")

    stale = {o.site for o in outcomes if o.status == "stale"}
    offers = collapse(all_variants, stale)
    score_offers(offers, history, today)
    build_site(settings, offers, all_variants, new_status, today)

    summary = {
        "sites_ok": len(ok),
        "sites_stale": len(stale),
        "sites_failed": sum(1 for o in outcomes if o.status == "failed"),
        "variants": len(all_variants),
        "coffee_offers": len(offers),
        "history_events": written,
    }
    logger.info("Run summary: %s", summary)
    return summary
