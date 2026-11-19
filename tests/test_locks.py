"""Members-only products behind the Locksmith app (only visible on the product page itself)."""

import asyncio
import json
from datetime import date

import httpx

from beangrid.locks import check_locks, is_members_only, pick_products
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

    async def fake_check(urls, path, s, today, transport=None):
        requested.append(urls)
        locks = {u: {"checked": today.isoformat(), "members_only": u.endswith("/p1")} for u in urls}
        path.write_text(json.dumps(locks))
        return locks

    monkeypatch.setattr(pipeline, "scan_homepages", fake_scan)
    monkeypatch.setattr(pipeline, "check_locks", fake_check)
    settings.scan_promos = settings.lock_check = True
    settings.lock_check_max = 50
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
    async def broken(urls, path, s, today, transport=None):
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
        return httpx.Response(200, text=pages[request.url.path])

    path = tmp_path / "locks.json"
    path.write_text(json.dumps({"https://j.example/products/old": {"checked": "2026-11-01", "members_only": True}}))
    urls = ["https://j.example/products/atlantic", "https://j.example/products/silence", "https://j.example/products/boom"]
    locks = asyncio.run(check_locks(urls, path, settings, date(2026, 11, 18), transport=httpx.MockTransport(handle)))
    assert locks["https://j.example/products/atlantic"] == {"checked": "2026-11-18", "members_only": True}
    assert locks["https://j.example/products/silence"]["members_only"] is False
    assert "https://j.example/products/boom" not in locks  # rate-limited: retried another day
    assert locks["https://j.example/products/old"]["members_only"] is True  # untouched entries kept
    assert json.loads(path.read_text()) == locks
