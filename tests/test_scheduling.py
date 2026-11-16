"""Making the most of Shopify's rate limit: pacing, circuit breaker, stalest-first rotation, partial runs."""

import asyncio
import csv
import json
import time
from datetime import date, timedelta

import httpx
import pytest

from beangrid.fetch import SiteResult, fetch_all
from beangrid.pipeline import QualityGateError, SiteOutcome, check_gate, order_sites, plan_sites, run
from test_pipeline import D0, alpha, beta, results


def _store(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"products": [{"id": 1}] if request.url.params.get("page") == "1" else []})


def test_requests_are_paced_across_workers(settings):
    settings.request_interval_s = 0.05
    settings.concurrency = 3
    starts: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return _store(request)

    sites = [f"https://s{i}.example" for i in range(6)]
    asyncio.run(fetch_all(sites, settings, transport=httpx.MockTransport(handle)))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 6 and min(gaps) >= 0.045  # one (short) page each, never two at once


def test_circuit_breaker_defers_the_rest_once_rate_limited(settings):
    settings.breaker_after = 3
    settings.concurrency = 1
    settings.max_retries = 0
    # ok, 429, 429, ok (resets the streak), 429, 429, 429 -> trip; s7..s9 are never requested.
    limited = {1, 2, 4, 5, 6}
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(429) if int(request.url.host[1]) in limited else _store(request)

    sites = [f"https://s{i}.example" for i in range(10)]
    out = asyncio.run(fetch_all(sites, settings, transport=httpx.MockTransport(handle)))
    assert [r.ok for r in out[:7]] == [True, False, False, True, False, False, False]
    assert all(not r.ok and r.transient and "deferred" in r.error for r in out[7:])
    assert not {"s7.example", "s8.example", "s9.example"} & set(calls)


def test_dead_stores_do_not_trip_the_breaker(settings):
    settings.breaker_after = 2
    settings.concurrency = 1

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.host.startswith("dead") else _store(request)

    sites = ["https://dead1.example", "https://dead2.example", "https://dead3.example", "https://live.example"]
    out = asyncio.run(fetch_all(sites, settings, transport=httpx.MockTransport(handle)))
    assert out[-1].ok  # 404s are permanent failures, not rate limiting


def test_canvass_only_rechecks_stores_the_daily_run_could_not_scrape(settings, tmp_path):
    from beangrid.canvass import canvass

    settings.sites_file.write_text("https://alpha.example\nhttps://gone.example\nhttps://new.example\n")
    settings.candidates_file = tmp_path / "none.txt"
    settings.aggregator_sites = []
    status = {"alpha.example": {"status": "ok", "last_ok": "2026-11-15"},
              "gone.example": {"status": "failed", "last_ok": "2026-10-01"}}  # new.example: not scraped yet
    hosts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(404)

    report, _ = asyncio.run(canvass(settings, date(2026, 11, 16), status, transport=httpx.MockTransport(handle),
                                    discover=False))
    assert "alpha.example" not in hosts and {"gone.example", "new.example"} <= set(hosts)
    assert [u for u, _ in report.pruned] == ["https://gone.example"]


def test_stalest_sites_go_first_and_ties_rotate_daily():
    sites = [f"https://s{i}.example" for i in range(6)]
    status = {
        "s0.example": {"last_ok": "2026-03-01"}, "s1.example": {"last_ok": "2026-02-27"},
        "s2.example": {"last_ok": "2026-03-01"}, "s3.example": {},  # never scraped OK
        "s4.example": {"last_ok": "2026-03-01"},  # s5: not in status at all (newly added)
    }
    day1 = order_sites(sites, status, date(2026, 3, 2))
    assert set(day1[:2]) == {"https://s3.example", "https://s5.example"} and day1[2] == "https://s1.example"
    # Equally fresh stores don't keep the same position: otherwise the same ones lose every block.
    tails = {tuple(order_sites(sites, status, date(2026, 3, 2) + timedelta(days=d))[3:]) for d in range(3)}
    assert len(tails) == 3


def _outcomes(ok: int, stale: int, failed: int) -> list[SiteOutcome]:
    return ([SiteOutcome(f"o{i}", "", "ok", []) for i in range(ok)]
            + [SiteOutcome(f"s{i}", "", "stale", []) for i in range(stale)]
            + [SiteOutcome(f"f{i}", "", "failed", []) for i in range(failed)])


def test_partial_run_publishes_with_carried_forward_stores(settings):
    check_gate(_outcomes(ok=30, stale=50, failed=6), settings)  # mostly carried forward: still publish
    with pytest.raises(QualityGateError, match="usable"):
        check_gate(_outcomes(ok=30, stale=10, failed=46), settings)  # too much missing
    with pytest.raises(QualityGateError, match="fresh"):
        check_gate(_outcomes(ok=5, stale=81, failed=0), settings)  # nothing new: don't churn the site


def test_second_run_of_the_day_reuses_stores_already_scraped(settings):
    settings.min_sites_ok_ratio = 0.5
    run(settings, D0, results(alpha(), beta(), b_ok=False))  # beta failed this morning (no cache yet)
    status = json.loads((settings.data_dir / "sites.json").read_text())
    sites = ["https://alpha.example", "https://www.beta.example"]
    plan = plan_sites(sites, status, settings, D0)
    assert plan.to_fetch == ["https://www.beta.example"]
    [reused] = plan.reused
    assert reused.site_url == "https://alpha.example" and reused.ok and len(reused.products) == 11
    # Next day everything is due again, stalest (never-OK beta) first.
    assert plan_sites(sites, status, settings, D0 + timedelta(days=1)).to_fetch == sites[::-1]


def test_rerun_with_reused_store_keeps_its_history(settings):
    settings.min_sites_ok_ratio = 0.5
    run(settings, D0, results(alpha(), beta(), b_ok=False))
    path = settings.data_dir / "history" / "2026.csv"
    before = [r for r in csv.DictReader(path.open()) if r["key"].startswith("alpha")]
    status = json.loads((settings.data_dir / "sites.json").read_text())
    plan = plan_sites(["https://alpha.example", "https://www.beta.example"], status, settings, D0)
    run(settings, D0, plan.reused + [SiteResult("https://www.beta.example", beta(), ok=True)])
    after = list(csv.DictReader(path.open()))
    assert [r for r in after if r["key"].startswith("alpha")] == before
    assert len([r for r in after if r["key"].startswith("www.beta") or r["key"].startswith("beta")]) == 4
    assert json.loads((settings.data_dir / "sites.json").read_text())["alpha.example"]["status"] == "ok"
