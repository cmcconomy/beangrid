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


def test_green_bean_options_are_not_roasted_coffee():
    # Dark City sells "Green Beans" as a Roast option: $13.60/lb unroasted showed up as the cheapest roasted bag.
    p = make_product(title="Brazil Fazenda Cachoeira Peaberry", variants=[
        {"id": 1, "title": "Whole Beans / Light / 0.5 Lb", "price": "13.50", "grams": 0, "available": True},
        {"id": 2, "title": "Whole Beans / Light / 1 Lb", "price": "24.00", "grams": 0, "available": True},
        {"id": 3, "title": "Whole Beans / Green Beans / 1 Lb", "price": "13.60", "grams": 0, "available": True},
        {"id": 4, "title": "Café vert / 1 kg", "price": "20.00", "grams": 0, "available": True},
        {"id": 5, "title": "Unroasted / 5 lb", "price": "50.00", "grams": 0, "available": True},
    ])
    cats = {v.variant_id: v.category for v in normalize_product("https://darkcitycoffee.com", p)}
    assert cats == {1: "coffee", 2: "coffee", 3: "green_coffee", 4: "green_coffee", 5: "green_coffee"}


def test_roasted_lots_sold_by_green_weight_stay_coffee():
    # Virgin Hill: you buy a 600 g lot of green beans and they roast it for you.
    p = make_product(title="Ethiopia Sidamo", variants=[
        {"id": 1, "title": "1 lot - 600g green beans / Roasted black / Beans", "price": "30", "grams": 0,
         "available": True}])
    assert normalize_product("https://virginhillcoffee.com", p)[0].category == "coffee"


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
