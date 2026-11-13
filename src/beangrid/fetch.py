"""Download every product from Shopify storefronts, one isolated task per site."""

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from beangrid.config import Settings

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class SiteResult:
    site_url: str
    products: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    error: str | None = None
    pages: int = 0


class FetchError(Exception):
    pass


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any], settings: Settings) -> dict[str, Any]:
    last_error = "unknown"
    for attempt in range(settings.max_retries + 1):
        try:
            resp = await client.get(url, params=params)
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    raise FetchError(f"non-JSON response from {resp.url} (not a Shopify store?)") from None
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUSES:
                raise FetchError(f"{last_error} from {resp.url}")
            if retry_after := resp.headers.get("retry-after"):
                try:
                    await asyncio.sleep(min(float(retry_after), settings.backoff_max_s))
                    continue
                except ValueError:
                    pass
        if attempt < settings.max_retries:
            delay = settings.backoff_base_s * 2**attempt
            await asyncio.sleep(min(delay, settings.backoff_max_s) * (0.75 + random.random() / 2))
    raise FetchError(f"gave up after {settings.max_retries + 1} attempts: {last_error}")


async def fetch_site(site_url: str, client: httpx.AsyncClient, settings: Settings) -> SiteResult:
    result = SiteResult(site_url=site_url)
    url = f"{site_url}/products.json"
    try:
        for page in range(1, settings.max_pages + 1):
            data = await _get_json(client, url, {"limit": settings.page_limit, "page": page}, settings)
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

        return await asyncio.gather(*(one(s) for s in sites))
