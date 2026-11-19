"""Members-only products: listed in products.json like any other, but locked by the Locksmith app.

Some stores (Just Us!, Rampage, Structure, Nemesis, Traffic in Nov 2026) use Locksmith to restrict
products to logged-in wholesale or member accounts. products.json doesn't show it; each product page
embeds the visitor's lock state as JSON. So, for stores whose homepage loads Locksmith, product pages
are checked a few dozen at a time (new products first, then anything not checked for a week) and the
verdicts kept in data/locks.json, which stays in force when checking is switched off.

Switch: BEANGRID_LOCK_CHECK (set by the workflow for the 14:23 run unless the repo variable
BEANGRID_LOCK_CHECK is "off").
"""

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from beangrid.config import Settings
from beangrid.fetch import FetchError, SiteResult, get_ok, paced_map

logger = logging.getLogger(__name__)

LOCKSMITH_RX = re.compile(r'<script[^>]*data-locksmith[^>]*>(.*?)</script>', re.S)


def is_members_only(html: str) -> bool | None:
    """True if an anonymous visitor is locked out, False if Locksmith lets them in, None if unknown."""
    m = LOCKSMITH_RX.search(html)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(state, dict) or "access_denied" not in state:
        return None
    return bool(state.get("access_denied"))


# Variant pickers across common themes; their options are what the public can actually choose.
PICKER_TAGS = ("product-variants", "variant-selects", "variant-radios", "variant-picker")


def visible_options(html: str) -> list[str] | None:
    """Option values the product page offers to an anonymous visitor, or None if there's no recognisable picker.

    B2B apps (e.g. Spark at Just Us!) render the public menu without wholesale sizes ("6 x 2 lb case") that
    products.json still lists.
    """
    soup = BeautifulSoup(html, "html.parser")
    values: set[str] = set()
    for picker in soup.find_all(PICKER_TAGS):
        values |= {o.get_text(strip=True) for o in picker.find_all("option") if o.get_text(strip=True)}
        values |= {i.get("value", "").strip() for i in picker.find_all("input") if i.get("type") == "radio"}
    values.discard("")
    return sorted(values) if values else None


def inspect_page(html: str) -> dict[str, Any]:
    return {"members_only": is_members_only(html), "visible_options": visible_options(html)}


def pick_products(urls: list[str], locks: dict[str, Any], today: date, max_checks: int, recheck_days: int) -> list[str]:
    """Never-checked products first, then the longest-unchecked beyond recheck_days, up to max_checks."""
    new = [u for u in urls if u not in locks]
    due = sorted((u for u in urls if u in locks and (today - date.fromisoformat(locks[u]["checked"])).days >= recheck_days),
                 key=lambda u: locks[u]["checked"])
    return (new + due)[:max_checks]


async def _fetch_page(url: str, client: httpx.AsyncClient, settings: Settings) -> SiteResult:
    result = SiteResult(site_url=url)
    try:
        resp = await get_ok(client, url, {}, settings)
        result.products, result.ok = [{"html": resp.text}], True
    except FetchError as e:
        result.error, result.transient = str(e), e.transient
    except Exception as e:  # one broken page must never sink the check
        result.error, result.transient = str(e) or type(e).__name__, True
    return result


def load_locks(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


async def check_locks(
    urls: list[str], path: Path, settings: Settings, today: date, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """Fetch these product pages, record their lock state in `path`, and return all known lock states."""
    locks = load_locks(path)
    results = await paced_map(urls, _fetch_page, settings, transport, settings.lock_budget_s,
                              accept="text/html,application/xhtml+xml")
    checked = locked = 0
    for r in results:
        if not r.ok and not r.transient and "HTTP 404" in (r.error or ""):
            # Listed in products.json but the page is "not found": hidden from the public storefront.
            page = {"members_only": True, "visible_options": None}
        elif not r.ok:
            continue  # rate-limited or down: it stays due and is picked again next run
        else:
            page = inspect_page(r.products[0]["html"])
        checked += 1
        locked += bool(page["members_only"])
        locks[r.site_url] = {"checked": today.isoformat(), "members_only": bool(page["members_only"]),
                             "visible_options": page["visible_options"]}
    logger.info("locks: checked %d of %d product pages, %d members-only", checked, len(urls), locked)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(locks.items())), indent=1) + "\n")
    return locks
