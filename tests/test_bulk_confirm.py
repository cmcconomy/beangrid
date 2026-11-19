"""Sizes over 5 lb are suspect until the store's public page shows them (Italian's '6 x 2 lb case', Traffic's
'5kg Moree Reusable Bag', display shippers were all real variants hidden from the public)."""

import json

from beangrid.fetch import SiteResult
from beangrid.pipeline import bulk_confirmed, run
from conftest import make_product
from test_pipeline import D0, beta

URL = "https://alpha.example/products/minutepapillon"


def _alpha():
    return make_product(pid=7, title="Minute Papillon Decaf Colombia", handle="minutepapillon", variants=[
        {"id": 1, "title": "300g", "price": "22.00", "grams": 0, "available": True},
        {"id": 2, "title": "5lb", "price": "120.00", "grams": 0, "available": True},
        {"id": 3, "title": "5kg Moree Reusable Bag", "price": "165.00", "grams": 0, "available": True}])


def _offers(settings):
    offers = json.loads((settings.site_out_dir / "data" / "offers.json").read_text())["offers"]
    return {o["size"]: o for o in offers if o["url"] == URL}


def _run(settings, locks):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "locks.json").write_text(json.dumps(locks))
    run(settings, D0, [SiteResult("https://alpha.example", [_alpha()], ok=True),
                       SiteResult("https://www.beta.example", beta(), ok=True)])
    return _offers(settings)


def test_big_sizes_are_suspect_until_the_public_page_confirms_them(settings):
    settings.min_sites_ok_ratio = 0.5
    by_size = _run(settings, {})  # never checked
    assert not by_size["300 g"]["suspect"] and not by_size["2.27 kg"]["suspect"]  # 5 lb itself is a normal size
    assert by_size["5 kg"]["suspect"] and "unconfirmed_bulk_size" in by_size["5 kg"]["flags"]

    # Page checked, and the 5 kg bag isn't in the public menu: members-only (hidden), not merely suspect.
    by_size = _run(settings, {URL: {"checked": "2026-03-01", "members_only": False,
                                    "visible_options": ["300g", "5lb"]}})
    assert by_size["5 kg"]["members_only"]

    # Page checked and the 5 kg bag is offered: confirmed, no longer suspect.
    by_size = _run(settings, {URL: {"checked": "2026-03-01", "members_only": False,
                                    "visible_options": ["300g", "5lb", "5kg Moree Reusable Bag"]}})
    assert not by_size["5 kg"]["suspect"] and not by_size["5 kg"]["members_only"]


def test_bulk_confirmation_rules():
    from beangrid.normalize import normalize_site

    v = [x for x in normalize_site("https://alpha.example", [_alpha()]) if x.variant_id == 3][0]
    assert not bulk_confirmed(v, {})
    assert not bulk_confirmed(v, {URL: {"members_only": False, "visible_options": None}})  # page unreadable
    assert bulk_confirmed(v, {URL: {"members_only": False, "visible_options": ["5kg Moree Reusable Bag", "300g"]}})


def test_unconfirmed_bulk_products_are_queued_for_page_checks(settings, monkeypatch):
    import beangrid.pipeline as pipeline

    settings.min_sites_ok_ratio = 0.5
    settings.lock_check = True
    settings.lock_promoted_per_bucket = 0
    checked: list[str] = []

    async def fake_check(urls, path, s, today, transport=None, **kw):
        checked.extend(urls)
        return {}

    monkeypatch.setattr(pipeline, "check_locks", fake_check)
    run(settings, D0, [SiteResult("https://alpha.example", [_alpha()], ok=True),
                       SiteResult("https://www.beta.example", beta(), ok=True)])
    assert checked == [URL]  # neither store uses a lock app; the 5 kg bag alone earns the check
