"""Members-only products behind the Locksmith app (only visible on the product page itself)."""

import asyncio
import json
from datetime import date

import httpx

from beangrid.locks import check_locks, inspect_page, is_members_only, pick_products
from beangrid.promos import extract_promos

LOCKSMITH = '<script type="application/vnd.locksmith+json" data-locksmith>{json}</script>'
# Trimmed from justuscoffee.com, Nov 2026.
LOCKED = {"version": "v385", "locked": True, "scope": "product", "access_granted": False, "access_denied": True,
          "requires_customer": True, "hide_resource": True, "hide_links_to_resource": True, "hide_price": False,
          "transparent": False, "locks": {"all": [760653, 783883], "opened": [760653]}}
OPEN = {"version": "v385", "locked": True, "scope": "product", "access_granted": True, "access_denied": False,
        "requires_customer": False, "hide_resource": False, "hide_links_to_resource": False, "hide_price": False,
        "transparent": True, "locks": {"all": [760653], "opened": [760653]}}


def product_page(state: dict | None) -> str:
    lock = LOCKSMITH.format(json=json.dumps(state)) if state else ""
    return f"<html><head>{lock}</head><body><h1>Coffee</h1></body></html>"


def test_members_only_from_locksmith_state():
    assert is_members_only(product_page(LOCKED)) is True  # Atlantic Blend
    assert is_members_only(product_page(OPEN)) is False  # Breaking the Silence: store-wide lock, but open
    assert is_members_only(product_page(None)) is None  # no Locksmith on the page: unknown, not "public"
    assert is_members_only("<script data-locksmith>{not json</script>") is None


def test_homepage_scan_notes_stores_running_locksmith():
    assert extract_promos(product_page(OPEN)).locksmith is True
    assert extract_promos(product_page(None)).locksmith is False


# Wholesale Lock Manager (Bows, Nov 2026): every page lists the locked products whose links it hides.
WLM_HOME = """<html><head><script type='text/javascript' src='/apps/wlm/js/wlm-1.1.js?v=2.0'></script>
<script type='text/javascript'> wlm.arrHideLinks.push('a[href$="/products/bella-aurora-habit"],a[href*="/products/bella-aurora-habit/"],a[href*="/products/bella-aurora-habit?"],a[href*="/products/bella-aurora-habit#"]'); </script>
<script type='text/javascript'> wlm.arrHideLinks.push('a[href$="/products/fife-blend"],a[href*="/products/fife-blend/"]'); </script>
</head><body><div class='announcement-bar'>Free shipping within Canada on orders over $60</div></body></html>"""


def test_wholesale_lock_manager_hidden_products_come_from_the_homepage():
    info = extract_promos(WLM_HOME)
    assert info.hidden_products == ["bella-aurora-habit", "fife-blend"]
    assert info.free_shipping.amount == 60  # the rest of the scan is unaffected
    assert extract_promos(product_page(None)).hidden_products == []


def test_pipeline_flags_wlm_hidden_products_without_extra_requests(settings, monkeypatch):
    import beangrid.pipeline as pipeline
    from beangrid.pipeline import run
    from test_pipeline import D0, alpha, beta, results

    settings.min_sites_ok_ratio = 0.5

    async def fake_scan(sites, s, transport=None):
        home = WLM_HOME.replace("bella-aurora-habit", "p1").replace("fife-blend", "p2")
        return {"alpha.example": extract_promos(home)}

    async def no_checks(*a, **k):
        raise AssertionError("WLM needs no product-page requests")

    monkeypatch.setattr(pipeline, "scan_homepages", fake_scan)
    monkeypatch.setattr(pipeline, "check_locks", no_checks)
    settings.scan_promos = True
    run(settings, D0, results(alpha(), beta()))
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    hidden = {o["url"].rsplit("/", 1)[1] for o in offers if o["members_only"]}
    assert hidden == {"p1", "p2"}


# Just Us! (Spark B2B app): the public size menu omits the wholesale sizes that products.json still lists.
ITALIAN_PAGE = """<html><head>{lock}</head><body><h1>Italian</h1>
<div data-spark="b2c-only"><product-variants data-main-product-variants data-type="select">
  <select id="product-weight-main" name="options[Weight]"><option value="12 oz" selected>12 oz</option><option value="2 lb">2 lb</option></select>
  <select id="product-grind-main" name="options[Grind]"><option value="Whole Bean">Whole Bean</option><option value="Ground">Ground</option></select>
  <script type="application/json">{{"id": 46344747024546, "title": "12 oz / Whole Bean"}}</script>
</product-variants></div>
<product-form><form action="/cart/add"></form></product-form></body></html>""".format(lock=LOCKSMITH.format(json=json.dumps(OPEN)))


# Traffic (custom div dropdown, Nov 2026): the 5 kg Moree bag is in products.json but not in the public menu.
TRAFFIC_PAGE = """<html><body><h1>Minute Papillon DECAF</h1><p>Regular price $22.00</p>
<div class="dropdown">Size: 300g <div data-value="1175288610882">300g</div><div data-value="1175288643650">2lb</div>
<div data-value="2750971805762">5lb</div></div><button>Add to Cart</button>
<script>var meta = {"variants":[{"title":"5kg Moree Reusable Bag"}]};</script></body></html>"""
TRAFFIC_OPTIONS = [["300g", "2lb", "5lb", "5kg Moree Reusable Bag"]]


def test_any_theme_options_missing_from_the_visible_page_are_not_offered():
    page = inspect_page(TRAFFIC_PAGE, TRAFFIC_OPTIONS)
    assert page["visible_options"] == ["2lb", "300g", "5lb"]  # the script's copy of "5kg Moree" doesn't count


def test_options_rendered_only_by_javascript_restrict_nothing():
    js_only = "<html><body><h1>Coffee</h1><div id='picker'></div><script>render(['250g','1kg'])</script></body></html>"
    assert inspect_page(js_only, [["250g", "1kg"], ["Whole Bean", "Ground"]])["visible_options"] is None
    # One option group visible, the other not rendered: only the visible group can restrict.
    partial = "<html><body><select><option>250g</option></select><script>x=['Whole Bean','Ground','1kg']</script></body></html>"
    assert inspect_page(partial, [["250g", "1kg"], ["Whole Bean", "Ground"]])["visible_options"] == [
        "250g", "Ground", "Whole Bean"]


def test_page_inspection_records_the_publicly_offered_options():
    page = inspect_page(ITALIAN_PAGE)
    assert page == {"members_only": False, "visible_options": ["12 oz", "2 lb", "Ground", "Whole Bean"]}
    assert inspect_page(product_page(LOCKED))["members_only"] is True
    assert inspect_page(product_page(None)) == {"members_only": None, "visible_options": None}  # no picker: unknown


def test_variants_outside_the_public_menu_are_members_only(settings, monkeypatch):
    import beangrid.pipeline as pipeline
    from beangrid.pipeline import run
    from conftest import make_product
    from test_pipeline import D0, beta

    settings.min_sites_ok_ratio = 0.5
    italian = make_product(pid=7, title="Italian Guatemala Dark Roast", handle="italian", variants=[
        {"id": 1, "title": "12 oz / Whole Bean", "price": "18.20", "grams": 0, "available": True},
        {"id": 2, "title": "2 lb / Whole Bean", "price": "42.86", "grams": 0, "available": True},
        {"id": 3, "title": "5 lb / Whole Bean", "price": "70.00", "grams": 0, "available": True},
        {"id": 4, "title": "6 x 2 lb case / Ground", "price": "109.20", "grams": 0, "available": True}])
    url = "https://alpha.example/products/italian"
    (settings.data_dir).mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "locks.json").write_text(json.dumps({url: {"checked": "2026-03-01", "members_only": False,
                                                                     "visible_options": ["12 oz", "2 lb", "Whole Bean", "Ground"]}}))
    from beangrid.fetch import SiteResult
    res = [SiteResult("https://alpha.example", [italian], ok=True), SiteResult("https://www.beta.example", beta(), ok=True)]
    run(settings, D0, res)
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    by_size = {o["size"]: o["members_only"] for o in offers if o["url"] == url}
    assert by_size == {"340 g": False, "907 g": False, "2.27 kg": True, "5.44 kg": True}


def test_pick_new_products_first_then_stale_ones_within_the_cap():
    urls = [f"https://s.example/products/p{i}" for i in range(6)]
    locks = {
        urls[0]: {"checked": "2026-11-18", "members_only": False},  # fresh
        urls[1]: {"checked": "2026-11-01", "members_only": True},  # stale (17 days)
        urls[2]: {"checked": "2026-11-10", "members_only": False},  # stale (8 days)
    }
    picked = pick_products(urls, locks, date(2026, 11, 18), max_checks=4, recheck_days=7)
    assert picked == [urls[3], urls[4], urls[5], urls[1]]  # never checked, then the stalest


def test_pipeline_flags_members_only_offers_and_the_switch_stops_requests(settings, monkeypatch):
    import beangrid.pipeline as pipeline
    from beangrid.pipeline import run
    from test_pipeline import D0, alpha, beta, results

    settings.min_sites_ok_ratio = 0.5
    requested: list[list[str]] = []

    async def fake_scan(sites, s, transport=None):  # alpha's homepage loads Locksmith, beta's doesn't
        info = extract_promos(product_page(OPEN))
        info.url = "https://alpha.example"
        return {"alpha.example": info, "beta.example": extract_promos(product_page(None))}

    async def fake_check(urls, path, s, today, transport=None, **kw):
        requested.append(urls)
        locks = {u: {"checked": today.isoformat(), "members_only": u.endswith("/p1")} for u in urls}
        path.write_text(json.dumps(locks))
        return locks

    monkeypatch.setattr(pipeline, "scan_homepages", fake_scan)
    monkeypatch.setattr(pipeline, "check_locks", fake_check)
    settings.scan_promos = settings.lock_check = True
    settings.lock_check_max = 50
    settings.lock_promoted_per_bucket = 0  # this test is about lock-app stores only
    run(settings, D0, results(alpha(), beta()))
    assert requested and all(u.startswith("https://alpha.example/products/") for u in requested[0])
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    p1 = [o for o in offers if o["url"].endswith("/p1")]
    assert p1 and all(o["members_only"] and o.get("deal_score") is None for o in p1)
    assert not any(o["members_only"] for o in offers if not o["url"].endswith("/p1"))

    # Switched off: no page requests at all, but yesterday's verdicts still apply.
    requested.clear()
    settings.lock_check = False
    run(settings, D0, results(alpha(), beta()))
    assert requested == []
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    assert all(o["members_only"] for o in offers if o["url"].endswith("/p1"))

    # A crashing check never fails the run.
    async def broken(urls, path, s, today, transport=None, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "check_locks", broken)
    settings.lock_check = True
    (settings.data_dir / "locks.json").unlink()
    run(settings, D0, results(alpha(), beta()))


def test_check_locks_updates_state_and_never_fails(tmp_path, settings):
    settings.max_retries = 0
    pages = {"/products/atlantic": product_page(LOCKED), "/products/silence": product_page(OPEN)}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/products/boom":
            return httpx.Response(429)
        if request.url.path == "/products/gone":  # listed in products.json, but the page is a 404
            return httpx.Response(404)
        return httpx.Response(200, text=pages[request.url.path])

    path = tmp_path / "locks.json"
    path.write_text(json.dumps({"https://j.example/products/old": {"checked": "2026-11-01", "members_only": True}}))
    urls = ["https://j.example/products/atlantic", "https://j.example/products/silence", "https://j.example/products/boom",
            "https://j.example/products/gone"]
    locks = asyncio.run(check_locks(urls, path, settings, date(2026, 11, 18), transport=httpx.MockTransport(handle)))
    assert locks["https://j.example/products/atlantic"] == {"checked": "2026-11-18", "members_only": True,
                                                            "visible_options": None}
    assert locks["https://j.example/products/silence"]["members_only"] is False
    assert "https://j.example/products/boom" not in locks  # rate-limited: retried another day
    assert locks["https://j.example/products/gone"]["members_only"] is True  # no public page: not buyable
    assert locks["https://j.example/products/old"]["members_only"] is True  # untouched entries kept
    assert json.loads(path.read_text()) == locks
