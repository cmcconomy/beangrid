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
        if not r.ok:
            continue  # rate-limited or down: it stays due and is picked again next run
        verdict = is_members_only(r.products[0]["html"])
        checked += 1
        locked += bool(verdict)
        locks[r.site_url] = {"checked": today.isoformat(), "members_only": bool(verdict)}
    logger.info("locks: checked %d of %d product pages, %d members-only", checked, len(urls), locked)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(locks.items())), indent=1) + "\n")
    return locks
