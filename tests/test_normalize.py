from beangrid.normalize import normalize_product, normalize_site, site_id
from conftest import make_product


def test_site_id():
    assert site_id("https://www.Example.com/") == "example.com"
    assert site_id("beta.example") == "beta.example"


def test_normalize_coffee_product():
    p = make_product(variants=[
        {"id": 11, "title": "340g / Whole Bean", "price": "24.00", "grams": 360, "available": True,
         "compare_at_price": "30.00"},
        {"id": 12, "title": "2lb / Whole Bean", "price": "50.00", "grams": 0, "available": False,
         "compare_at_price": "50.00"},
    ])
    v1, v2 = normalize_product("https://www.alpha.example", p)
    assert v1.key == "alpha.example:11"
    assert v1.url == "https://www.alpha.example/products/p1"
    assert v1.category == "coffee" and v1.origins == ["Ethiopia"] and v1.process == ["Natural"]
    assert v1.grams == 340 and v1.price_per_kg == 70.59 and v1.compare_at_price == 30.0
    assert v1.description == "Notes of blueberry & jasmine."  # the old pipeline always left this empty
    assert v2.grams == 907.2 and v2.compare_at_price is None  # equal compare-at isn't a sale
    assert not v2.available


def test_nonsense_compare_at_dropped():
    p = make_product(variants=[{"id": 1, "title": "250g", "price": "20", "grams": 250, "available": True,
                                "compare_at_price": "999"}])
    assert normalize_product("https://a.example", p)[0].compare_at_price is None


def test_zero_price_and_empty_products_skipped():
    p = make_product(variants=[{"id": 1, "title": "250g", "price": "0.00", "grams": 250, "available": True}])
    assert normalize_product("https://a.example", p) == []
    assert normalize_product("https://a.example", {"title": "x", "variants": []}) == []


def test_site_name_and_outliers():
    products = [make_product(pid=i, title=f"Colombia Lot {i}", variants=[
        {"id": i, "title": "340g", "price": "25.00", "grams": 340, "available": True}]) for i in range(1, 8)]
    products[0]["vendor"] = "Some Farm"  # importer/farm in vendor field shouldn't rename the roaster
    # A "5lb" bag at a 340g price is a roaster data error: flag it.
    products.append(make_product(pid=99, title="Colombia Lot 99", variants=[
        {"id": 99, "title": "5lb", "price": "25.00", "grams": 340, "available": True}]))
    variants = normalize_site("https://a.example", products)
    assert {v.roaster for v in variants} == {"Test Roasters"}
    flagged = [v.variant_id for v in variants if "price_per_kg_outlier" in v.flags]
    assert flagged == [99]


def test_malformed_product_does_not_sink_site():
    good = make_product(pid=1)
    bad = make_product(pid=2)
    bad["variants"] = [{"id": "not-an-int", "title": "340g", "price": "20"}]
    variants = normalize_site("https://a.example", [bad, good])
    assert [v.product_id for v in variants] == [1]
