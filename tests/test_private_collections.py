"""Private collections: Just Us! keeps its cafe/company blends ("Blomidon Blend", "Waldorf Special Blend",
"Kinden's Bakery and Café Blend"...) in a 'custom-coffee-blend' collection; they're locked to the customer."""

import asyncio

import httpx

from beangrid.promos import is_private_collection, scan_homepages
from test_promos import page


def test_which_collections_are_private():
    # Real collections, Nov 2026.
    assert is_private_collection("custom-coffee-blend", "Custom Coffee Blend")  # Just Us! cafe blends
    assert is_private_collection("reserve-privee", "Réserve privée")  # Brûlerie du Quai (Locksmith-locked)
    assert is_private_collection("white-label", "White-label")  # Traffic
    assert is_private_collection("wholesale-private", "Wholesale Private")  # Moving
    assert is_private_collection("custom-coffee", "CUSTOM COFFEE")  # De Mello
    assert not is_private_collection("wholesale", "Sacs de café")  # Cafe Pista: every public coffee
    assert not is_private_collection("microlots", "Custom Coffee")  # Virgin Hill: public microlots
    assert not is_private_collection("non-wholesale-special-requests", "Non-Wholesale - Special Requests")
    assert not is_private_collection("customer-favourites", "Customer Favourites")
    assert not is_private_collection("coffee", "Coffee")
    assert not is_private_collection("sobeys-assortment", "Sobeys Assortment")


def test_private_collections_mark_their_products_hidden(settings):
    settings.max_retries = 0
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text=page("Free Shipping on orders over $59"))
        if request.url.path == "/collections.json":
            return httpx.Response(200, json={"collections": [
                {"handle": "coffee", "title": "Coffee"},
                {"handle": "custom-coffee-blend", "title": "Custom Coffee Blend"}]})
        if request.url.path == "/collections/custom-coffee-blend/products.json":
            return httpx.Response(200, json={"products": [{"handle": "waldorf-special-blend"}, {"handle": "blomidon-blend"}]})
        return httpx.Response(404)

    scanned = asyncio.run(scan_homepages(["https://j.example"], settings, transport=httpx.MockTransport(handle)))
    info = scanned["j.example"]
    assert info.hidden_products == ["blomidon-blend", "waldorf-special-blend"]
    assert info.private_collections == ["custom-coffee-blend"]
    assert info.free_shipping.amount == 59
    assert "/collections/coffee/products.json" not in calls  # only private-looking collections are read


def test_collection_lookup_failing_never_loses_the_homepage(settings):
    settings.max_retries = 0

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=page("Free shipping over $50"))
        return httpx.Response(429)

    scanned = asyncio.run(scan_homepages(["https://k.example"], settings, transport=httpx.MockTransport(handle)))
    info = scanned["k.example"]
    assert info.free_shipping.amount == 50 and info.hidden_products == [] and info.private_collections == []
