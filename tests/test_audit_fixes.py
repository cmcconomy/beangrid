"""Fixes from the Nov 2026 audit of the cheapest listing per store (real listings)."""

import json

import pytest

from beangrid.classify import classify, variant_category
from beangrid.normalize import normalize_product
from conftest import make_product


@pytest.mark.parametrize("label, category", [
    ("French Caramel / Soft Pods", "pods"),  # Reunion
    ("Arrow Espresso / Soft Pods", "pods"),
    ("1 lb / Keurig / K-Cup", "pods"),  # Down East
    ("Compostable pods / 18-pack", "pods"),  # Balzac's
    ("Whole Bean / Capsules + Instant", "pods"),  # Fernwood
    ("Decaf / 42 x 2.5 oz Portion Packs", "portion_packs"),  # Reunion
    ("3 oz Portion Pack / Ground", "portion_packs"),  # Just Us!
    ("Whole Beans / Green Beans / 1 Lb", "green_coffee"),
    ("2 lb / Whole Bean", None),
    ("Espresso Pod Grind / 340g", None),  # a grind setting, not pods
    ("Original Dark Roast / 2 lb", None),
])
def test_variant_formats_that_are_not_beans(label, category):
    assert variant_category(label) == category


def test_pod_variants_leave_the_bean_variants_alone():
    p = make_product(title="Authentic Donut Shop Blend", variants=[
        {"id": 1, "title": "Original / 2 lb", "price": "36.00", "grams": 0, "available": True},
        {"id": 2, "title": "Original / Soft Pods", "price": "12.00", "grams": 500, "available": True},
        {"id": 3, "title": "Original / 42 x 2.5 oz Portion Packs", "price": "60.00", "grams": 0, "available": True}])
    cats = {v.variant_id: v.category for v in normalize_product("https://reunioncoffeeroasters.com", p)}
    assert cats == {1: "coffee", 2: "pods", 3: "portion_packs"}


@pytest.mark.parametrize("title, ptype, tags", [
    ("Dragon's Tears - Maple & Coffee", "Boutique", ["coffee", "maple", "sweet"]),  # meringues
    ("Caramel Coffee Toffee - Fleur de Sel", "Boutique", ["coffee"]),
    ("Bodum Pavina 12oz Double Parois", "Boutique", ["coffee"]),
    ("Épcies à Barbecue et café avec La pince", "Collaboration", []),  # 94Celcius BBQ rub (sic)
    ("Coffee BBQ Seasoning", "", ["coffee"]),
])
def test_non_coffee_goods(title, ptype, tags):
    assert classify(title, ptype, tags, has_weight=True).category != "coffee"


def test_promoted_listings_get_their_pages_checked_on_any_store(settings, monkeypatch):
    """Coffee Wallas' 'LoLa Blend' was the store's cheapest listing but its page is a 404."""
    import beangrid.pipeline as pipeline
    from beangrid.pipeline import run
    from test_pipeline import D0, alpha, beta, results

    settings.min_sites_ok_ratio = 0.5
    settings.lock_check = True
    settings.lock_promoted_per_bucket = 2
    checked: list[str] = []

    async def fake_check(urls, path, s, today, transport=None, **kw):
        checked.extend(urls)
        locks = {u: {"checked": today.isoformat(), "members_only": u.endswith("/p1"), "visible_options": None}
                 for u in urls}
        path.write_text(json.dumps(locks))
        return locks

    monkeypatch.setattr(pipeline, "check_locks", fake_check)
    run(settings, D0, results(alpha(), beta()))  # neither store uses a lock app
    # Only the cheapest two per size bucket: alpha's $24 bags (p1, p10 by URL) beat beta's $30 ones.
    assert checked == ["https://alpha.example/products/p1", "https://alpha.example/products/p10"]
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    assert all(o["members_only"] for o in offers if o["url"].endswith("/p1"))


def test_promoted_ranking_skips_listings_already_known_to_be_hidden():
    from beangrid.normalize import normalize_site
    from beangrid.pipeline import promoted_urls
    from test_pipeline import alpha

    vs = normalize_site("https://alpha.example", alpha())
    known = {"https://alpha.example/products/p1": {"checked": "2026-03-01", "members_only": True}}
    assert promoted_urls(vs, 2) == ["https://alpha.example/products/p1", "https://alpha.example/products/p10"]
    assert promoted_urls(vs, 2, known) == ["https://alpha.example/products/p10", "https://alpha.example/products/p2"]
