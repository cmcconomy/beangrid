"""Download every product from Shopify storefronts, one isolated task per site."""

import asyncio
import contextvars
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from beangrid.config import Settings

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
# Monotonic time after which no request or retry wait is started (set by fetch_all from fetch_budget_s).
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("fetch_deadline", default=None)


@dataclass
class SiteResult:
    site_url: str
    products: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    error: str | None = None
    transient: bool = False  # failure looks temporary (rate limit, 5xx, timeout) rather than a dead store
    pages: int = 0
    final_url: str | None = None  # where products.json was actually served from, after redirects


class FetchError(Exception):
    """`transient` is True for failures worth retrying another day (rate limits, 5xx, timeouts)."""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return (await _get_json_at(client, url, params, settings))[0]


async def _get_json_at(
    client: httpx.AsyncClient, url: str, params: dict[str, Any], settings: Settings
) -> tuple[dict[str, Any], str]:
    """JSON body and the final URL it came from."""
    last_error = "unknown"
    transient = True

    async def wait(seconds: float) -> None:
        deadline = _deadline.get()
        if deadline is not None and time.monotonic() + seconds > deadline:
            raise FetchError(f"out of scrape time budget: {last_error}", transient=True)
        await asyncio.sleep(seconds)

    for attempt in range(settings.max_retries + 1):
        await wait(0)
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}: {e}"
            # DNS failure / refused connection points to a dead domain; timeouts may be a bad day.
            transient = not isinstance(e, httpx.ConnectError)
        else:
            if resp.status_code == 200:
                try:
                    return resp.json(), str(resp.url)
                except json.JSONDecodeError:
                    raise FetchError(f"non-JSON response from {resp.url} (not a Shopify store?)") from None
            last_error = f"HTTP {resp.status_code}"
            transient = True
            if resp.status_code not in RETRY_STATUSES:
                raise FetchError(f"{last_error} from {resp.url}")
            if retry_after := resp.headers.get("retry-after"):
                try:
                    pause: float | None = min(float(retry_after), settings.backoff_max_s)
                except ValueError:
                    pause = None
                if pause is not None:
                    await wait(pause)
                    continue
        if attempt < settings.max_retries:
            delay = settings.backoff_base_s * 2**attempt
            await wait(min(delay, settings.backoff_max_s) * (0.75 + random.random() / 2))
    raise FetchError(f"gave up after {settings.max_retries + 1} attempts: {last_error}", transient=transient)


async def fetch_site(site_url: str, client: httpx.AsyncClient, settings: Settings) -> SiteResult:
    result = SiteResult(site_url=site_url)
    url = f"{site_url}/products.json"
    try:
        for page in range(1, settings.max_pages + 1):
            data, final = await _get_json_at(client, url, {"limit": settings.page_limit, "page": page}, settings)
            if page == 1:
                result.final_url = final.split("/products.json", 1)[0]
            products = data.get("products") if isinstance(data, dict) else None
            if products is None:
                raise FetchError("response has no 'products' key")
            result.pages = page
            if not products:
                break
            result.products.extend(products)
            if len(products) < settings.page_limit:
                break
        else:
            logger.warning("%s: stopped at max_pages=%d", site_url, settings.max_pages)
        result.ok = True
    except Exception as e:  # one broken site must never sink the others
        result.error = str(e) or type(e).__name__
        result.transient = e.transient if isinstance(e, FetchError) else True
        logger.warning("%s failed: %s", site_url, result.error)
    return result


async def fetch_all(sites: list[str], settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> list[SiteResult]:
    sem = asyncio.Semaphore(settings.concurrency)
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        timeout=settings.timeout_s,
        follow_redirects=True,
        transport=transport,
    ) as client:

        async def one(site: str) -> SiteResult:
            async with sem:
                return await fetch_site(site, client, settings)

        # Once the budget is spent, sites still failing are reported as transient failures (and carried
        # forward by the pipeline) instead of letting a rate-limit storm run into the job timeout.
        token = _deadline.set(time.monotonic() + settings.fetch_budget_s)
        try:
            return await asyncio.gather(*(one(s) for s in sites))
        finally:
            _deadline.reset(token)
