"""Promo extraction from store homepages. Banner texts are real ones from Nov 2026."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from beangrid.pipeline import run
from beangrid.promos import PromoInfo, extract_promos, scan_homepages, update_promos


def page(bar: str = "", body: str = "", cls: str = "announcement-bar") -> str:
    return (f"<html><head><script>var code = 'NOTME99';</script></head><body>"
            f"<div class='{cls}'><p>{bar}</p></div><main>{body}</main>"
            f"<footer>Shipping, taxes, and discount codes calculated at checkout. Have a discount code? Apply</footer>"
            f"</body></html>")


@pytest.mark.parametrize("bar, code, pct, conditions", [
    ("10% OFF FIRST ORDER USING CODE 'COFFEE10' FREE SHIPPING ON ORDERS OVER $70", "COFFEE10", 10, ["first order"]),
    ("FREE SHIPPING! ANYWHERE IN ONTARIO WITH A MINIMUM PURCHASE OF $125 OR MORE, USE PROMO CODE PCR125. "
     "(SUBSCRIPTIONS EXCLUDED)", "PCR125", None, ["free shipping", "Ontario"]),
    ("new subscribers get an extra 10% off on your first charge - this week only. use code SUBSCRIBE10 at checkout",
     "SUBSCRIBE10", 10, ["subscription"]),
    ("Black Friday: 20% off everything with code: BF2026", "BF2026", 20, []),
    ("Utilisez le code NOEL15 pour 15 % de rabais sur tout le site", "NOEL15", 15, []),
])
def test_codes_in_announcement_bars(bar, code, pct, conditions):
    info = extract_promos(page(bar))
    [c] = info.codes
    assert (c.code, c.pct) == (code, pct)
    assert set(conditions) <= set(c.conditions)
    assert bar[:20] in c.text


def test_code_in_page_body_is_found_with_its_condition():
    body = ("Subscribe to the Mikko newsletter and enjoy 10% off your next order. Use code CLUB10 at checkout "
            "to enjoy 10% off your next order.")
    [c] = extract_promos(page("Free Shipping on Orders Over $65", body)).codes
    assert (c.code, c.pct) == ("CLUB10", 10) and "newsletter" in c.conditions
    assert "subscription" not in c.conditions  # subscribing to a newsletter isn't a coffee subscription


def test_percentage_from_the_next_clause_is_not_the_codes():
    body = ('Subscribers also gain access to free shipping on additional orders with code "ADDON", '
            "and 10% off all Squall series coffees.")
    [c] = extract_promos(page(body=body)).codes  # September Coffee
    assert (c.code, c.pct) == ("ADDON", None) and {"subscription", "free shipping"} <= set(c.conditions)


def test_cart_widget_threshold_and_bogus_amounts():
    body = "${{amount}} Spend $60.00 more and get free shipping! Your Cart Subtotal $0.00 $0.00"  # Discovery
    assert extract_promos(page(body=body)).free_shipping.amount == 60


def test_percent_digits_need_a_word_boundary():
    body = ("Découvrez nos cafés d’origine unique 100 % Biologiques Abonnez-vous et économisez! "
            "Obtenez jusqu'à 15 % de rabais")  # Melk
    assert extract_promos(page(body=body)).subscription_pct == 15


@pytest.mark.parametrize("text", [
    "Shipping, taxes, and discount codes calculated at checkout",
    "Have a discount code? Apply",
    "ENTER CODE AT CHECKOUT FOR SAVINGS",
    "Use the discount code we email you",
    "Get coupon × Unlock 10% OFF YOUR ORDER Subscribe",  # popup without a visible code
    "Order special instructions Order note Apply Discount Discount code Apply",
])
def test_no_phantom_codes(text):
    assert extract_promos(page(text)).codes == []


@pytest.mark.parametrize("bar, amount, region", [
    ("FREE SHIPPING ON ORDERS $60+ FREE SHIPPING ON ORDERS $60+", 60, None),
    ("Livraison gratuite au Québec sur les commandes de 49$+", 49, "Québec"),
    ("SHIPPING VIA UPS, PUROLATOR & CANADA POST. FREE SHIPPING IN CANADA AT $65", 65, "Canada"),
    ("Free shipping Canada wide for all orders over $75!", 75, "Canada"),
    ("FREE SHIPPING on all orders over $45 CAD in Canada & $85 USD in the USA", 45, "Canada"),
    ("Free shipping over $100. Orders will be shipped on Thursdays.", 100, None),
    ("LIVRAISON GRATUITE au Canada sur les achats de 45$ et +", 45, "Canada"),
])
def test_free_shipping_threshold(bar, amount, region):
    fs = extract_promos(page(bar)).free_shipping
    assert fs is not None and (fs.amount, fs.region) == (amount, region)


def test_subscription_discount_and_banner_dedup():
    info = extract_promos(page("🚨 FREE SHIPPING on all ☕️ orders over $60 nationwide! 📦 Subscribe & SAVE 15% 📥 "
                               "🚨 FREE SHIPPING on all ☕️ orders over $60 nationwide! 📦 Subscribe & SAVE 15% 📥"))
    assert info.subscription_pct == 15
    assert info.free_shipping.amount == 60
    assert len(info.banners) == 1 and info.banners[0].count("FREE SHIPPING") == 1  # marquee repeats collapsed


def test_plain_bars_without_promos_are_ignored():
    info = extract_promos(page("Wholesale Inquiries Yonni's Doughnuts"))
    assert info == PromoInfo()


def test_pipeline_publishes_promos_and_survives_a_failed_scan(settings, monkeypatch):
    import beangrid.pipeline as pipeline
    from test_pipeline import D0, alpha, beta, results

    settings.min_sites_ok_ratio = 0.5

    async def fake_scan(sites, s, transport=None):
        assert sites[0].startswith("https://")  # stalest-first order, same as the price scrape
        return {"alpha.example": extract_promos(page("10% OFF FIRST ORDER USING CODE 'COFFEE10'"))}

    monkeypatch.setattr(pipeline, "scan_homepages", fake_scan)
    settings.scan_promos = True
    run(settings, D0, results(alpha(), beta()))
    payload = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())
    assert payload["promos"]["alpha.example"]["codes"][0]["code"] == "COFFEE10"
    assert (settings.site_out_dir / "data" / "promo_log.csv").exists()

    async def broken_scan(sites, s, transport=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "scan_homepages", broken_scan)
    run(settings, D0, results(alpha(), beta()))  # same day, scan blows up: run still publishes
    payload = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())
    assert payload["promos"]["alpha.example"]["codes"][0]["code"] == "COFFEE10"  # last known kept

    settings.scan_promos = False  # morning run: no homepage requests, last known promos published
    monkeypatch.setattr(pipeline, "scan_homepages", broken_scan)
    run(settings, D0, results(alpha(), beta()))
    payload = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())
    assert payload["promos"]["alpha.example"]["codes"][0]["code"] == "COFFEE10"


def _homepages(pages: dict[str, str]):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host not in pages:
            return httpx.Response(429)
        return httpx.Response(200, text=pages[request.url.host], headers={"content-type": "text/html"})
    return httpx.MockTransport(handle)


def test_scan_and_update_keeps_last_known_promos_and_logs_code_changes(settings, tmp_path):
    settings.max_retries = 0
    pages = {"a.example": page("10% OFF FIRST ORDER USING CODE 'COFFEE10'"),
             "b.example": page("Free shipping over $50")}
    sites = ["https://a.example", "https://b.example", "https://c.example"]  # c is rate-limited
    scanned = asyncio.run(scan_homepages(sites, settings, transport=_homepages(pages)))
    assert set(scanned) == {"a.example", "b.example"}

    path, log = tmp_path / "promos.json", tmp_path / "promo_log.csv"
    prev = {"c.example": {"url": "https://c.example", "checked": "2026-11-15", "codes": [{"code": "OLD5"}]}}
    path.write_text(json.dumps(prev))
    promos = update_promos(path, log, scanned, date(2026, 11, 16))
    assert promos["a.example"]["codes"][0]["code"] == "COFFEE10"
    assert promos["a.example"]["codes"][0]["first_seen"] == "2026-11-16"
    assert promos["c.example"]["codes"][0]["code"] == "OLD5"  # not re-scanned: kept as last known
    assert json.loads(path.read_text()) == promos

    # Next day the code is gone: logged as removed; first_seen survives while it lasts.
    pages["a.example"] = page("Free shipping over $70")
    scanned = asyncio.run(scan_homepages(sites[:1], settings, transport=_homepages(pages)))
    promos = update_promos(path, log, scanned, date(2026, 11, 17))
    assert promos["a.example"]["codes"] == []
    rows = log.read_text().splitlines()
    assert rows[0] == "date,site,event,code,pct,text"
    assert [r.split(",")[:4] for r in rows[1:]] == [["2026-11-16", "a.example", "added", "COFFEE10"],
                                                     ["2026-11-17", "a.example", "removed", "COFFEE10"]]
