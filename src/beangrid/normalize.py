"""Turn raw Shopify product JSON into flat, typed variant records."""

import logging
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

from beangrid.classify import classify, is_sample
from beangrid.weights import parse_weight, resolve_weight

logger = logging.getLogger(__name__)

# Competition geshas really do reach ~$2,000/kg; anything cheaper than ~$15/kg is a data error.
PLAUSIBLE_PRICE_PER_KG = (15.0, 3000.0)


class Variant(BaseModel):
    key: str  # "<site>:<variant id>", stable across days
    site: str
    roaster: str = ""
    product_id: int
    variant_id: int
    title: str
    variant_title: str
    url: str
    image: str | None = None
    product_type: str = ""
    tags: list[str] = []
    published_at: str | None = None
    description: str = ""

    price: float
    compare_at_price: float | None = None
    available: bool

    grams: float | None = None
    weight_source: str = "none"
    shipping_grams: float | None = None
    price_per_kg: float | None = None

    category: str
    category_reason: str = ""
    origins: list[str] = []
    process: list[str] = []
    roast: str | None = None
    decaf: bool = False
    half_caf: bool = False
    blend: bool = False
    style: str | None = None
    sample: bool = False
    flags: list[str] = []


def site_id(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.")


def _float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def html_to_text(html: str | None, limit: int = 600) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _variant_label(v: dict[str, Any]) -> str:
    title = v.get("title") or ""
    return "" if title == "Default Title" else title


def normalize_product(site_url: str, product: dict[str, Any]) -> list[Variant]:
    site = site_id(site_url)
    title = (product.get("title") or "").strip()
    variants = product.get("variants") or []
    if not title or not variants:
        return []

    options = product.get("options") or []
    option_names = [o.get("name", "") for o in options if isinstance(o, dict)]
    option_values = [str(val) for o in options if isinstance(o, dict) for val in o.get("values", [])]
    variant_labels = [_variant_label(v) for v in variants]
    has_size_variants = any(parse_weight(lbl) for lbl in variant_labels)
    has_text_weight = has_size_variants or parse_weight(title) is not None

    tags = _tags(product.get("tags"))
    cls = classify(
        title=title,
        product_type=product.get("product_type") or "",
        tags=tags,
        option_names=option_names,
        option_values=option_values,
        has_weight=has_text_weight,
    )
    images = product.get("images") or []
    product_image = images[0].get("src") if images and isinstance(images[0], dict) else None
    url = f"{site_url.rstrip('/')}/products/{product.get('handle', '')}"
    description = html_to_text(product.get("body_html")) if cls.category == "coffee" else ""

    out = []
    for v, label in zip(variants, variant_labels):
        price = _float(v.get("price"))
        if price is None or price <= 0:
            continue
        shipping = _float(v.get("grams"))
        weight = resolve_weight(label, title, shipping, has_size_variants)
        flags = list(weight.flags)

        compare_at = _float(v.get("compare_at_price"))
        if compare_at is not None and not (price < compare_at <= price * 3):
            compare_at = None  # unset, equal, or nonsense compare-at prices

        ppk = round(price / weight.grams * 1000, 2) if weight.grams else None
        if ppk is not None and cls.category == "coffee" and not (
            PLAUSIBLE_PRICE_PER_KG[0] <= ppk <= PLAUSIBLE_PRICE_PER_KG[1]
        ):
            flags.append("implausible_price_per_kg")

        featured = v.get("featured_image")
        image = featured.get("src") if isinstance(featured, dict) else product_image
        out.append(
            Variant(
                key=f"{site}:{v.get('id')}",
                site=site,
                roaster=(product.get("vendor") or "").strip(),
                product_id=int(product.get("id") or 0),
                variant_id=int(v.get("id") or 0),
                title=title,
                variant_title=label,
                url=url,
                image=image,
                product_type=product.get("product_type") or "",
                tags=tags,
                published_at=product.get("published_at"),
                description=description,
                price=price,
                compare_at_price=compare_at,
                available=bool(v.get("available")),
                grams=weight.grams,
                weight_source=weight.source,
                shipping_grams=shipping,
                price_per_kg=ppk,
                category=cls.category,
                category_reason=cls.reason,
                origins=cls.origins,
                process=cls.process,
                roast=cls.roast,
                decaf=cls.decaf,
                half_caf=cls.half_caf,
                blend=cls.blend,
                style=cls.style,
                sample=is_sample(label, weight.grams),
                flags=flags,
            )
        )
    return out


def normalize_site(site_url: str, products: list[dict[str, Any]]) -> list[Variant]:
    variants: list[Variant] = []
    for product in products:
        try:
            variants.extend(normalize_product(site_url, product))
        except Exception:
            logger.exception("Skipping malformed product %s on %s", product.get("id"), site_url)
    apply_site_name(variants)
    flag_site_outliers(variants)
    return variants


def apply_site_name(variants: list[Variant]) -> None:
    """Shopify 'vendor' is sometimes the farm or importer; use the site's dominant vendor as roaster."""
    counts: dict[str, int] = {}
    for v in variants:
        if v.category == "coffee" and v.roaster:
            counts[v.roaster] = counts.get(v.roaster, 0) + 1
    if not counts:
        for v in variants:
            if v.roaster:
                counts[v.roaster] = counts.get(v.roaster, 0) + 1
    name = max(counts, key=counts.get) if counts else (variants[0].site if variants else "")
    for v in variants:
        v.roaster = name


def flag_site_outliers(variants: list[Variant]) -> None:
    """Flag coffee far *cheaper* per kg than the roaster's own norm: almost always a wrong size.

    Unusually expensive coffee (geshas, competition lots) is real, and harmless to deal-hunting,
    so only the low side is flagged.
    """
    ppks = sorted(
        v.price_per_kg for v in variants
        if v.category == "coffee" and v.price_per_kg and not v.sample and "implausible_price_per_kg" not in v.flags
    )
    if len(ppks) < 6:
        return
    median = ppks[len(ppks) // 2]
    for v in variants:
        if v.category != "coffee" or not v.price_per_kg or v.sample:
            continue
        if v.price_per_kg < median * 0.4:
            v.flags.append("price_per_kg_outlier")
