import asyncio

import httpx

from beangrid.fetch import fetch_all


def _handler(pages: dict[str, list[list[dict]]], calls: list[str]):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        host = request.url.host
        if host == "flaky.example" and calls.count(str(request.url)) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if host == "html.example":
            return httpx.Response(200, text="<html>maintenance</html>")
        if host == "old.example":
            return httpx.Response(301, headers={"location": "https://new.example/products.json?limit=2&page=1"})
        if host not in pages:
            return httpx.Response(404)
        page = int(request.url.params["page"])
        site_pages = pages[host]
        return httpx.Response(200, json={"products": site_pages[page - 1] if page <= len(site_pages) else []})
    return handle


def test_fetch_paginates_retries_and_isolates_failures(settings):
    settings.page_limit = 2
    full = [[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}], [{"id": 5}]]
    pages = {"good.example": full, "flaky.example": [[{"id": 9}]], "new.example": [[{"id": 7}]]}
    calls: list[str] = []
    sites = ["https://good.example", "https://flaky.example", "https://html.example", "https://gone.example",
             "https://old.example"]
    results = asyncio.run(fetch_all(sites, settings, transport=httpx.MockTransport(_handler(pages, calls))))
    by = {r.site_url: r for r in results}

    assert by["https://good.example"].ok and len(by["https://good.example"].products) == 5
    assert by["https://good.example"].pages == 3  # stopped on the short page, no extra request
    assert all("limit=2" in c for c in calls)
    assert by["https://flaky.example"].ok  # 429 retried
    assert not by["https://html.example"].ok and "non-JSON" in by["https://html.example"].error
    assert not by["https://gone.example"].ok and "404" in by["https://gone.example"].error
    assert by["https://old.example"].ok and by["https://old.example"].products == [{"id": 7}]  # redirect followed
