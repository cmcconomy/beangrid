import csv
import json
from datetime import date, timedelta

import pytest

from beangrid.fetch import SiteResult
from beangrid.pipeline import QualityGateError, run
from conftest import make_product

D0 = date(2026, 3, 1)


def coffee(pid, price="24.00", title=None):
    return make_product(pid=pid, title=title or f"Colombia Lot {pid}", variants=[
        {"id": pid * 10, "title": "340g / Whole Bean", "price": price, "grams": 360, "available": True}])


def alpha(n=10, price="24.00"):
    return [coffee(i, price) for i in range(1, n + 1)] + [
        make_product(pid=500, title="Logo Mug", product_type="Mugs",
                     variants=[{"id": 5000, "title": "Default Title", "price": "18", "grams": 400, "available": True}])]


def beta(n=4):
    return [coffee(100 + i, "30.00", f"Kenya Lot {i}") for i in range(n)]


def results(a, b, a_ok=True, b_ok=True):
    return [
        SiteResult("https://alpha.example", a if a_ok else [], ok=a_ok, error=None if a_ok else "HTTP 503"),
        SiteResult("https://www.beta.example", b if b_ok else [], ok=b_ok, error=None if b_ok else "HTTP 503"),
    ]


def offers(settings):
    return json.loads((settings.site_out_dir / "data" / "offers.json").read_text())


def test_end_to_end(settings):
    settings.min_sites_ok_ratio = 0.5  # only two sites in this test
    summary = run(settings, D0, results(alpha(), beta()))
    assert summary["sites_ok"] == 2 and summary["coffee_offers"] == 14

    payload = offers(settings)
    assert {o["roaster"] for o in payload["offers"]} == {"Test Roasters"}
    assert payload["category_counts"]["drinkware"] == 1
    assert payload["history_years"] == [2026]
    assert (settings.site_out_dir / "index.html").exists()
    rows = list(csv.DictReader((settings.site_out_dir / "beangrid.csv").open()))
    assert len(rows) == 15 and {r["category"] for r in rows} == {"coffee", "drinkware"}
    assert (settings.site_out_dir / "data" / "history" / "2026.csv").exists()

    # Day 2: beta's store is down -> carried forward from cache, nothing marked delisted.
    run(settings, D0 + timedelta(days=1), results(alpha(), beta(), b_ok=False))
    payload = offers(settings)
    kenya = [o for o in payload["offers"] if o["title"].startswith("Kenya")]
    assert len(kenya) == 4 and all(o["stale"] for o in kenya)
    assert payload["sites"]["beta.example"]["status"] == "stale"
    hist = list(csv.DictReader((settings.data_dir / "history" / "2026.csv").open()))
    assert not [r for r in hist if r["listed"] == "0"]

    # Day 3: alpha comes back truncated (2 of 11 products) -> treated as failed, not as 9 delistings.
    run(settings, D0 + timedelta(days=2), results(alpha(1), beta()))
    hist = list(csv.DictReader((settings.data_dir / "history" / "2026.csv").open()))
    assert not [r for r in hist if r["listed"] == "0"]
    assert json.loads((settings.data_dir / "sites.json").read_text())["alpha.example"]["status"] == "stale"


def test_price_drop_surfaces_as_deal(settings):
    for i in range(35):
        run(settings, D0 + timedelta(days=i), results(alpha(price="30.00"), beta()))
    run(settings, D0 + timedelta(days=35), results(alpha(price="24.00"), beta()))
    payload = offers(settings)
    dropped = [o for o in payload["offers"] if o.get("drop_pct")]
    assert len(dropped) == 10 and dropped[0]["ref_price"] == 30.0
    assert all(o["low_90d"] for o in dropped)
    hist = list(csv.DictReader((settings.data_dir / "history" / "2026.csv").open()))
    assert len(hist) == 15 + 10  # first sighting of each variant + one price change each, nothing daily


def test_quality_gate_blocks_publish(settings):
    run(settings, D0, results(alpha(), beta()))
    before = (settings.data_dir / "history" / "2026.csv").read_text()
    with pytest.raises(QualityGateError):
        run(settings, D0 + timedelta(days=1), results(alpha(), beta(), a_ok=False, b_ok=False))
    assert (settings.data_dir / "history" / "2026.csv").read_text() == before


def test_same_day_rerun_is_idempotent(settings):
    run(settings, D0, results(alpha(), beta()))
    first = (settings.data_dir / "history" / "2026.csv").read_text()
    run(settings, D0, results(alpha(), beta()))
    assert (settings.data_dir / "history" / "2026.csv").read_text() == first


def test_count_gate_accepts_a_confirmed_shrink(settings):
    from beangrid.pipeline import count_gate

    gate: dict = {}
    assert count_gate(100, gate, settings) is None
    assert count_gate(30, gate, settings)  # one-off collapse: rejected
    assert count_gate(95, gate, settings) is None  # recovered
    assert count_gate(30, gate, settings)
    assert count_gate(32, gate, settings) is None  # same lower count again: new normal
    assert gate["last_count"] == 32
    assert count_gate(30, gate, settings) is None


def test_store_that_shrinks_recovers_after_confirmation(settings):
    settings.min_sites_ok_ratio = 0.5
    run(settings, D0, results(alpha(10), beta()))
    run(settings, D0 + timedelta(days=1), results(alpha(2), beta()))
    status = json.loads((settings.data_dir / "sites.json").read_text())["alpha.example"]
    assert status["status"] == "stale"
    run(settings, D0 + timedelta(days=2), results(alpha(2), beta()))
    status = json.loads((settings.data_dir / "sites.json").read_text())["alpha.example"]
    assert status["status"] == "ok" and status["last_count"] == 3
    hist = list(csv.DictReader((settings.data_dir / "history" / "2026.csv").open()))
    assert len([r for r in hist if r["listed"] == "0"]) == 8  # the 8 dropped coffees are now delisted
