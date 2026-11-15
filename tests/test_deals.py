from datetime import date, timedelta

from beangrid.deals import collapse, score_offers
from beangrid.history import History, Observation
from beangrid.normalize import normalize_site
from conftest import make_product

TODAY = date(2026, 6, 1)


def _variants():
    products = [
        make_product(pid=1, title="Kenya AA", variants=[
            {"id": 11, "title": "340g / Whole Bean", "price": "20.00", "grams": 340, "available": True},
            {"id": 12, "title": "340g / Espresso", "price": "20.00", "grams": 340, "available": True},
            {"id": 13, "title": "1kg / Whole Bean", "price": "50.00", "grams": 1000, "available": True},
        ]),
        make_product(pid=2, title="Brazil Cerrado", variants=[
            {"id": 21, "title": "340g", "price": "17.00", "grams": 340, "available": True, "compare_at_price": "20.00"}]),
        make_product(pid=3, title="Colombia Huila", variants=[
            {"id": 31, "title": "340g", "price": "30.00", "grams": 340, "available": True}]),
        make_product(pid=4, title="Guatemala Sample", variants=[
            {"id": 41, "title": "40g", "price": "3.00", "grams": 40, "available": True}]),
        make_product(pid=5, title="Vanilla Syrup", product_type="Syrup", variants=[
            {"id": 51, "title": "750ml", "price": "12.00", "grams": 900, "available": True}]),
    ]
    return normalize_site("https://www.shop.example", products)


def test_collapse_merges_grinds_and_drops_non_coffee():
    offers = collapse(_variants())
    titles = sorted((o.title, o.size) for o in offers)
    assert titles == [("Brazil Cerrado", "340 g"), ("Colombia Huila", "340 g"), ("Guatemala Sample", "40 g"),
                      ("Kenya AA", "1 kg"), ("Kenya AA", "340 g")]
    kenya = next(o for o in offers if o.title == "Kenya AA" and o.grams == 340)
    assert kenya.options == ["340g / Espresso", "340g / Whole Bean"]
    assert kenya.id == "shop.example:11"  # whole bean preferred as representative


def test_scoring_sale_drop_and_value():
    variants = _variants()
    h = History()
    start = TODAY - timedelta(days=40)
    h.apply_snapshot(start, "shop.example", [Observation.of(v) for v in variants])
    # Colombia was $36 for the last month and dropped to $30 two days ago.
    h.variants["shop.example:31"].events[0] = h.variants["shop.example:31"].events[0].__class__(
        start, "shop.example:31", 36.0, None, True, True)
    h.apply_snapshot(TODAY - timedelta(days=2), "shop.example", [Observation.of(v) for v in variants])

    offers = collapse(variants)
    score_offers(offers, h, TODAY)
    by = {(o.title, o.grams): o for o in offers}

    brazil = by[("Brazil Cerrado", 340)]
    assert brazil.sale_pct == 0.15 and brazil.value_pct == 0.0
    colombia = by[("Colombia Huila", 340)]
    assert colombia.drop_pct == round(1 - 30 / 36, 3) and colombia.ref_price == 36.0 and colombia.low_90d
    assert colombia.history[-1] == ((TODAY - timedelta(days=2)).isoformat(), 30.0)
    sample = by[("Guatemala Sample", 40)]
    assert sample.sample and sample.deal_score is None  # samples never rank as deals
    assert brazil.deal_score > by[("Kenya AA", 340)].deal_score
    assert by[("Kenya AA", 1000)].bucket == "large"


def test_new_arrival_needs_established_roaster():
    variants = _variants()
    h = History()
    old = [Observation.of(v) for v in variants if v.product_id != 3]
    h.apply_snapshot(TODAY - timedelta(days=60), "shop.example", old)
    h.apply_snapshot(TODAY - timedelta(days=2), "shop.example", [Observation.of(v) for v in variants])
    offers = collapse(variants)
    score_offers(offers, h, TODAY)
    assert {o.title for o in offers if o.is_new} == {"Colombia Huila"}


def test_long_running_sale_is_not_a_deal():
    variants = _variants()
    h = History()
    h.apply_snapshot(TODAY - timedelta(days=90), "shop.example", [Observation.of(v) for v in variants])
    offers = collapse(variants)
    score_offers(offers, h, TODAY)
    brazil = next(o for o in offers if o.title == "Brazil Cerrado")
    assert brazil.sale_pct == 0.15 and brazil.sale_days == 90 and brazil.perma_sale
    assert brazil.deal_score == round(40 * (1 - brazil.value_pct), 1)  # value only, no discount credit


def test_wholesale_listings_are_flagged_and_left_out_of_value_ranking():
    products = [
        make_product(pid=1, title="Kenya AA", variants=[
            {"id": 11, "title": "340g", "price": "20.00", "grams": 340, "available": True},
            # A retail product that also sells a cafe size through a "Wholesale" option.
            {"id": 12, "title": "Wholesale 2.27kg", "price": "60.00", "grams": 2270, "available": True}]),
        make_product(pid=2, title="Cafe Blend", product_type="b2b coffee", variants=[
            {"id": 21, "title": "2.27kg", "price": "55.00", "grams": 2270, "available": True}]),
        make_product(pid=3, title="Polaris Espresso - W", variants=[
            {"id": 31, "title": "340g", "price": "9.00", "grams": 340, "available": True}]),
        make_product(pid=4, title="Colombia Huila", variants=[
            {"id": 41, "title": "340g", "price": "30.00", "grams": 340, "available": True}]),
    ]
    variants = normalize_site("https://www.shop.example", products)
    assert {v.key: v.wholesale for v in variants} == {
        "shop.example:11": False, "shop.example:12": True, "shop.example:21": True, "shop.example:31": True,
        "shop.example:41": False}
    assert all(v.category == "coffee" for v in variants)  # still beans, just flagged

    offers = collapse(variants)
    score_offers(offers, History(), TODAY)
    by_id = {o.id: o for o in offers}
    for wid in ("shop.example:12", "shop.example:21", "shop.example:31"):
        assert by_id[wid].wholesale and by_id[wid].value_pct is None and by_id[wid].deal_score is None
    # The cheap wholesale 340g doesn't push retail bags up the value scale: Kenya is cheapest retail.
    assert by_id["shop.example:11"].value_pct == 0.0 and by_id["shop.example:41"].value_pct == 1.0
