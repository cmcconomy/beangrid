"""Collapse variants into shoppable offers and score how good a deal each one is."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from beangrid.history import History
from beangrid.normalize import Variant

SUSPECT_FLAGS = {"implausible_price_per_kg", "price_per_kg_outlier", "no_weight"}
GRIND_PREFERENCE = ("whole bean", "en grain", "grains", "beans")
SPARKLINE_DAYS = 120
# A "sale" running longer than this is the real price: shown, but not scored as a deal.
PERMA_SALE_DAYS = 60


class Offer(BaseModel):
    id: str
    site: str
    roaster: str
    title: str
    size: str
    grams: float | None
    price: float
    ppk: float | None
    compare_at: float | None
    available: bool
    url: str
    image: str | None
    options: list[str]
    origins: list[str]
    process: list[str]
    roast: str | None
    decaf: bool
    half_caf: bool
    blend: bool
    style: str | None
    sample: bool
    flags: list[str]
    suspect: bool
    weight_source: str
    bucket: str | None
    desc: str
    stale: bool = False

    sale_pct: float | None = None
    sale_days: int | None = None
    perma_sale: bool = False
    drop_pct: float | None = None
    ref_price: float | None = None
    low_90d: bool = False
    is_new: bool = False
    back_in_stock: bool = False
    value_pct: float | None = None
    deal_score: float | None = None
    first_seen: str | None = None
    history: list[tuple[str, float]] = []


def size_bucket(grams: float | None) -> str | None:
    if grams is None:
        return None
    if grams < 150:
        return "sample"
    if grams < 600:
        return "bag"
    if grams < 1500:
        return "large"
    return "bulk"


def format_size(grams: float | None) -> str:
    if grams is None:
        return "?"
    if grams >= 1000:
        return f"{grams / 1000:.2f}".rstrip("0").rstrip(".") + " kg"
    return f"{grams:.0f} g"


def _rep_rank(v: Variant) -> tuple[int, int]:
    label = v.variant_title.lower()
    return (0 if v.available else 1, 0 if any(p in label for p in GRIND_PREFERENCE) else 1)


def collapse(variants: list[Variant], stale_sites: set[str] | None = None) -> list[Offer]:
    """One offer per (product, size, price): grind options of the same bag are merged."""
    stale_sites = stale_sites or set()
    groups: dict[tuple[str, int, float | None, float], list[Variant]] = defaultdict(list)
    for v in variants:
        if v.category != "coffee":
            continue
        groups[(v.site, v.product_id, round(v.grams) if v.grams else None, v.price)].append(v)

    offers = []
    for members in groups.values():
        members.sort(key=_rep_rank)
        rep = members[0]
        flags = sorted({f for m in members for f in m.flags})
        options = sorted({m.variant_title for m in members if m.variant_title})
        offers.append(
            Offer(
                id=rep.key,
                site=rep.site,
                roaster=rep.roaster,
                title=rep.title,
                size=format_size(rep.grams),
                grams=rep.grams,
                price=rep.price,
                ppk=rep.price_per_kg,
                compare_at=rep.compare_at_price,
                available=any(m.available for m in members),
                url=rep.url,
                image=rep.image,
                options=options,
                origins=rep.origins,
                process=rep.process,
                roast=rep.roast,
                decaf=rep.decaf,
                half_caf=rep.half_caf,
                blend=rep.blend,
                style=rep.style,
                sample=rep.sample,
                flags=flags,
                suspect=bool(SUSPECT_FLAGS & set(rep.flags)),
                weight_source=rep.weight_source,
                bucket=size_bucket(rep.grams),
                desc=rep.description[:300],
                stale=rep.site in stale_sites,
            )
        )
    return offers


def _eligible(o: Offer) -> bool:
    return o.available and not o.suspect and not o.sample and o.ppk is not None


def _percentile_ranks(values: list[float]) -> list[float]:
    """0 = cheapest, 1 = most expensive (ties share the average rank)."""
    if len(values) == 1:
        return [0.5]
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 / (len(values) - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def score_offers(offers: list[Offer], history: History, today: date) -> None:
    hist_start = history.start
    site_first_seen: dict[str, date] = {}
    for key, vh in history.variants.items():
        if vh.events:
            site = key.split(":", 1)[0]
            site_first_seen[site] = min(site_first_seen.get(site, vh.first_seen), vh.first_seen)

    since = today - timedelta(days=SPARKLINE_DAYS)
    for o in offers:
        stats: dict[str, Any] = history.price_stats(o.id, today)
        vh = history.variants.get(o.id)
        if vh:
            o.history = vh.change_points(since)
        o.first_seen = stats.get("first_seen")
        if o.compare_at:
            o.sale_pct = round(1 - o.price / o.compare_at, 3)
            o.sale_days = stats.get("sale_days")
            o.perma_sale = bool(o.sale_days and o.sale_days > PERMA_SALE_DAYS)
        ref = stats.get("ref_price_30d")
        if ref and o.price < ref * 0.95:
            o.drop_pct = round(1 - o.price / ref, 3)
            o.ref_price = ref
        min90, max90 = stats.get("min_90d"), stats.get("max_90d")
        o.low_90d = bool(min90 is not None and max90 is not None and o.price <= min90 < max90)
        o.back_in_stock = bool(stats.get("back_in_stock"))
        if o.first_seen and hist_start:
            first = date.fromisoformat(o.first_seen)
            site_first = site_first_seen.get(o.site, first)
            o.is_new = (
                (today - first).days <= 7
                and (first - hist_start).days > 3
                and (first - site_first).days > 3  # a newly added roaster isn't "new coffee"
            )

    by_bucket: dict[str, list[Offer]] = defaultdict(list)
    for o in offers:
        if _eligible(o) and o.bucket:
            by_bucket[o.bucket].append(o)
    for group in by_bucket.values():
        for o, pct in zip(group, _percentile_ranks([o.ppk for o in group])):
            o.value_pct = round(pct, 3)

    for o in offers:
        if not _eligible(o) or o.value_pct is None:
            continue
        discount = max(0 if o.perma_sale else (o.sale_pct or 0), o.drop_pct or 0)
        score = 60 * min(discount / 0.25, 1) + 40 * (1 - o.value_pct)
        if o.low_90d and o.drop_pct:
            score += 5
        o.deal_score = round(min(score, 100), 1)
