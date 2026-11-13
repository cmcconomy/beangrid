import asyncio
from datetime import date

import httpx
import pytest

from beangrid.canvass import canvass, guess_urls, name_key, parse_sites, render_sites
from beangrid.config import load_sites
from conftest import make_product

TODAY = date(2026, 11, 13)


def beans(n: int, vendor: str = "Test Roasters", start: int = 1) -> list[dict]:
    titles = ["Ethiopia Guji Natural", "Colombia Huila Washed", "Kenya Nyeri AA", "Guatemala Huehuetenango",
              "Peru Cajamarca", "Rwanda Nyamasheke"]
    out = []
    for i in range(n):
        p = make_product(pid=start + i, title=titles[i % len(titles)] + f" Lot {start + i}")
        p["vendor"] = vendor
        out.append(p)
    return out


def store(name: str, country: str = "CA", products: list[dict] | None = None, province: str = "Ontario") -> dict:
    return {
        "meta": {"name": name, "country": country, "province": province, "city": "Somewhere",
                 "myshopify_domain": name_key(name) + ".myshopify.com"},
        "products": beans(4) if products is None else products,
    }


STORES = {
    "alpha.example": store("Alpha Roasters"),
    "new.example": store("Moved Coffee"),
    "agg.example": store("Coffee Shelf", products=beans(3, "Maple Roast Co") + beans(2, "Alpha Roasters", 10)
                         + beans(1, "Coffee Shelf", 20)),
    "mapleroast.ca": store("Maple Roast Co", province="Nova Scotia"),
    "northernbean.ca": store("Northern Bean Roasters", province="Manitoba"),
    "usroaster.example": store("Yankee Beans", country="US"),
    "tea.example": store("Tea Only", products=[make_product(pid=1, title="Earl Grey", product_type="Tea")]),
}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "old.example":
        return httpx.Response(301, headers={"location": f"https://new.example{path}?{request.url.query.decode()}"})
    if host in ("gone.example", "recent.example"):
        return httpx.Response(404)
    if host == "flaky.example":
        return httpx.Response(503)
    if host == "squarespace.example":
        return httpx.Response(200, text="<html>not shopify</html>")
    if host not in STORES:
        raise httpx.ConnectError("nodename nor servname provided", request=request)
    s = STORES[host]
    if path == "/meta.json":
        return httpx.Response(200, json=s["meta"])
    if path == "/products.json":
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json={"products": s["products"] if page == 1 else []})
    return httpx.Response(404)


@pytest.fixture
def canvass_settings(settings, tmp_path):
    settings.sites_file.write_text(
        "# header comment\n"
        "https://alpha.example\n"
        "https://gone.example\n"
        "https://recent.example\n"
        "https://flaky.example\n"
        "https://squarespace.example  # switched platforms?\n"
        "https://old.example\n"
        "# https://retired.example  # dead 2025-01-01: HTTP 404\n"
    )
    settings.candidates_file = tmp_path / "candidates.txt"
    settings.candidates_file.write_text(
        "# names or urls\nNorthern Bean Roasters\nhttps://usroaster.example\ntea.example\nNowhere Coffee\n"
        "Alpha Roasters  # already listed\n"
    )
    settings.aggregator_sites = ["https://agg.example"]
    settings.max_retries = 0
    settings.timeout_s = 1.0
    return settings


STATUS = {
    "alpha.example": {"roaster": "Alpha Roasters", "last_ok": "2026-11-12"},
    "gone.example": {"roaster": "Gone", "last_ok": "2026-10-01"},
    "recent.example": {"roaster": "Recent", "last_ok": "2026-11-12"},
    "flaky.example": {"roaster": "Flaky", "last_ok": "2026-09-01"},
    "squarespace.example": {"status": "failed", "last_ok": None},  # never scraped successfully
}


def run_canvass(settings, **kw):
    return asyncio.run(canvass(settings, TODAY, STATUS, transport=httpx.MockTransport(handler), **kw))


def test_prunes_only_permanent_and_persistent_failures(canvass_settings):
    report, text = run_canvass(canvass_settings, discover=False)

    pruned = dict(report.pruned)
    assert set(pruned) == {"https://gone.example", "https://squarespace.example"}
    assert "404" in pruned["https://gone.example"]
    assert "non-JSON" in pruned["https://squarespace.example"]
    kept = dict(report.kept_failing)
    assert "last scraped OK 2026-11-12" in kept["https://recent.example"]
    assert "transient" in kept["https://flaky.example"]
    assert report.moved == [("https://old.example", "https://new.example")]
    assert not report.added

    lines = text.splitlines()
    assert "# https://gone.example  # dead 2026-11-13: HTTP 404 from https://gone.example/products.json?limit=250&page=1" in lines
    assert "https://alpha.example" in lines and "https://recent.example" in lines
    assert "# https://retired.example  # dead 2025-01-01: HTTP 404" in lines  # untouched
    # The rewritten file still parses, minus the pruned stores.
    path = canvass_settings.sites_file
    path.write_text(text)
    assert load_sites(path) == ["https://alpha.example", "https://recent.example", "https://flaky.example",
                                "https://old.example"]


def test_no_prune_without_daily_run_evidence(canvass_settings):
    # Backfilled sites.json has only the shrink-gate fields; a site missing from it has no record at all.
    status = {"gone.example": {"last_count": 40, "pending_count": None, "pending_runs": 0}}
    report, text = asyncio.run(canvass(canvass_settings, TODAY, status, transport=httpx.MockTransport(handler),
                                       discover=False))
    assert not report.pruned
    kept = dict(report.kept_failing)
    assert "no daily-run status yet" in kept["https://gone.example"]
    assert "no daily-run status yet" in kept["https://squarespace.example"]
    assert "https://gone.example" in text.splitlines()


def test_discovers_canadian_roasters_from_candidates_and_aggregator_vendors(canvass_settings):
    report, text = run_canvass(canvass_settings, prune=False)

    added = {s.final_url: s for s in report.added}
    assert set(added) == {"https://northernbean.ca", "https://mapleroast.ca"}
    assert added["https://mapleroast.ca"].province == "Nova Scotia"
    assert added["https://northernbean.ca"].coffee_products == 4

    rejected = dict(report.rejected)
    assert "in US" in rejected["https://usroaster.example"]
    assert "0 coffee products" in rejected["https://tea.example"]
    assert rejected["Nowhere Coffee"] == "no Shopify storefront found"
    assert "Alpha Roasters" not in rejected  # known roaster: never even resolved
    assert not any("Coffee Shelf" in c for c in rejected)  # the aggregator's own label

    canvass_settings.sites_file.write_text(text)
    sites = load_sites(canvass_settings.sites_file)
    assert sites[-2:] == ["https://northernbean.ca", "https://mapleroast.ca"]
    assert "https://mapleroast.ca  # Maple Roast Co, Somewhere, Nova Scotia, CA (canvass 2026-11-13)" in text

    md = report.markdown(TODAY)
    assert "| Maple Roast Co | https://mapleroast.ca |" in md


def test_known_store_by_myshopify_domain_is_not_re_added(canvass_settings, monkeypatch):
    # Listed under its myshopify host; the candidate is the same shop's custom domain.
    monkeypatch.setitem(STORES, "alpha.myshopify.example", STORES["alpha.example"])
    canvass_settings.sites_file.write_text("https://alpha.myshopify.example\n")
    canvass_settings.candidates_file.write_text("https://alpha.example\n")
    canvass_settings.aggregator_sites = []
    report, _ = run_canvass(canvass_settings)
    assert not report.added
    assert "already listed" in dict(report.rejected)["https://alpha.example"]


def test_candidate_whose_primary_domain_is_listed_is_not_re_added(canvass_settings, monkeypatch):
    # e.g. de-mello-coffee.myshopify.com, whose meta.json domain is the listed hellodemello.com.
    dupe = store("ALPHA ROASTERS ONLINE")
    dupe["meta"]["domain"] = "alpha.example"
    monkeypatch.setitem(STORES, "alpha-roasters.example", dupe)
    canvass_settings.candidates_file.write_text("https://alpha-roasters.example\n")
    canvass_settings.aggregator_sites = []
    report, _ = asyncio.run(canvass(canvass_settings, TODAY, {}, transport=httpx.MockTransport(handler), prune=False))
    assert not report.added
    assert "already listed" in dict(report.rejected)["https://alpha-roasters.example"]


def test_headless_store_is_replaced_by_its_shopify_subdomain(canvass_settings, monkeypatch):
    # headless.example went headless (HTML at /products.json) but kept Shopify at shop.headless.example.
    # decoy.example died, and decoy.myshopify.example... is an unrelated shop that must not be adopted.
    moved = store("Headless Roasters")
    moved["meta"]["domain"] = "shop.headless.example"
    imposter = store("Decoy UK", country="GB")
    imposter["meta"]["domain"] = "www.decoy.co.uk"
    monkeypatch.setitem(STORES, "shop.headless.example", moved)
    monkeypatch.setitem(STORES, "decoy.myshopify.com", imposter)
    monkeypatch.setitem(STATUS, "decoy.example", {"last_ok": "2026-01-01"})

    def routed(request: httpx.Request) -> httpx.Response:
        if request.url.host in ("headless.example", "decoy.example"):
            return httpx.Response(200, text="<html>headless</html>")
        return handler(request)

    canvass_settings.sites_file.write_text("https://headless.example\nhttps://decoy.example\n")
    report, text = asyncio.run(canvass(canvass_settings, TODAY, STATUS, transport=httpx.MockTransport(routed),
                                       discover=False))
    assert report.relocated == [("https://headless.example", "https://shop.headless.example")]
    assert [u for u, _ in report.pruned] == ["https://decoy.example"]
    assert text.splitlines()[0] == "https://shop.headless.example  # moved from https://headless.example (canvass 2026-11-13)"
    canvass_settings.sites_file.write_text(text)
    assert load_sites(canvass_settings.sites_file) == ["https://shop.headless.example"]


@pytest.mark.parametrize("name, key", [
    ("Pilot Coffee Roasters", "pilot"),
    ("Café Saint-Henri", "sainthenri"),
    ("Brûleries FARO Roasting House", "bruleriesfaroroastinghouse"),
    ("Phil & Sebastian Coffee Roasters", "philsebastian"),
    ("The Angry Roaster Coffee Co.", "angryroaster"),
    ("Thom Bargen coffee", "thombargen"),
])
def test_name_key(name, key):
    assert name_key(name) == key


def test_guess_urls_covers_common_domain_shapes():
    urls = guess_urls("Pilot Coffee Roasters")
    for u in ("https://pilotcoffeeroasters.com", "https://pilot.ca", "https://pilotcoffee.com",
              "https://pilotcoffeeroasters.myshopify.com", "https://pilot.coffee"):
        assert u in urls
    assert len(urls) == len(set(urls)) <= 13


def test_parse_and_render_preserve_unchanged_file():
    text = "# c\nhttps://a.example/\n\n# https://b.example  # dead\nhttps://c.example  # note\n"
    lines = parse_sites(text)
    assert [(ln.url, ln.active) for ln in lines] == [
        (None, False), ("https://a.example", True), (None, False), ("https://b.example", False),
        ("https://c.example", True)]
    assert render_sites(lines, {}, [], TODAY) == text
