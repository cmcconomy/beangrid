"""Weekly store-list maintenance: comment out dead storefronts and find new Canadian roasters.

Runs separately from the daily scrape (see `.github/workflows/canvass.yaml`) and only ever proposes
edits to `sites.txt`; the workflow turns them into a pull request for a human to review.

Discovery sources, cheapest first:
  * `candidates.txt`: roaster names or URLs a person jotted down (e.g. from a cafe shelf).
  * vendors on multi-roaster Canadian Shopify retailers (`aggregator_sites`): those shops tag each
    coffee with the roaster that made it, which is a steady feed of small roasters.
A listed store that stops serving products.json is first looked for at `shop.<domain>` and
`<name>.myshopify.com` (stores that go headless often keep Shopify on a subdomain); only if that fails, and
the daily run has been failing on it too, is it commented out.

A name is resolved by guessing storefront hosts (`<slug>.myshopify.com`, `<slug>.ca`, ...) and keeping
the first that answers `/products.json`. Shopify's `/meta.json` then supplies the store's country.
"""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from beangrid.config import Settings
from beangrid.fetch import FetchError, _get_json, fetch_site
from beangrid.normalize import normalize_site, site_id

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+")
# Trailing words that rarely appear in the domain itself ("Pilot Coffee Roasters" -> pilotcoffeeroasters, pilot).
GENERIC_SUFFIXES = (
    "coffee roasters", "coffee roastery", "coffee roasting co", "coffee roasting", "coffee company", "coffee co",
    "roasting co", "roasting company", "roasters", "roastery", "coffee", "cafe", "co", "company", "inc", "ltd",
    "micro torrefaction", "torrefacteur", "torrefaction", "brulerie",
)
NOT_ROASTERS = {"", "default", "my store", "gift card", "gift cards", "subscription", "subscriptions", "bundle"}


@dataclass
class StoreCheck:
    url: str
    alive: bool = False
    definitive: bool = False  # the failure looks permanent (404, not Shopify, DNS gone), not a bad day
    error: str | None = None
    final_url: str | None = None
    name: str = ""
    country: str | None = None
    province: str | None = None
    city: str | None = None
    myshopify: str | None = None
    domain: str | None = None  # the store's primary domain according to meta.json
    products: int = 0
    coffee_products: int = 0
    vendors: dict[str, int] = field(default_factory=dict)  # vendor -> coffee products, for aggregators


@dataclass
class SiteLine:
    """One line of sites.txt, keeping comments so the file can be rewritten faithfully."""

    raw: str
    url: str | None = None  # set for active lines and for commented-out URLs
    active: bool = False


@dataclass
class CanvassReport:
    pruned: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)
    kept_failing: list[tuple[str, str]] = field(default_factory=list)  # failing but not (yet) pruned
    moved: list[tuple[str, str]] = field(default_factory=list)  # (listed url, where it redirects now)
    relocated: list[tuple[str, str]] = field(default_factory=list)  # (listed url, replacement url)
    added: list[StoreCheck] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (candidate, reason)

    def markdown(self, today: date) -> str:
        out = [f"## Store canvass {today.isoformat()}", ""]
        if self.added:
            out += ["### New stores", "", "| Store | URL | Where | Coffee products |", "|---|---|---|---|"]
            out += [f"| {s.name} | {s.final_url} | {_where(s)} | {s.coffee_products} |" for s in self.added]
            out.append("")
        if self.relocated:
            out += ["### Replaced (store moved off the listed domain)", ""]
            out += [f"- {u} -> {to}" for u, to in self.relocated] + [""]
        if self.pruned:
            out += ["### Commented out (no Shopify endpoint)", ""]
            out += [f"- {u}: {r}" for u, r in self.pruned] + [""]
        if self.kept_failing:
            out += ["### Failing, left active for now", ""]
            out += [f"- {u}: {r}" for u, r in self.kept_failing] + [""]
        if self.moved:
            out += ["### Redirects to another host (consider updating)", ""]
            out += [f"- {u} -> {to}" for u, to in self.moved] + [""]
        if self.rejected:
            out += ["<details><summary>Candidates rejected</summary>", ""]
            out += [f"- {c}: {r}" for c, r in self.rejected] + ["", "</details>", ""]
        if len(out) == 2:
            out.append("No changes.")
        return "\n".join(out)

    @property
    def changed(self) -> bool:
        return bool(self.pruned or self.added or self.relocated)


def _where(s: StoreCheck) -> str:
    return ", ".join(x for x in (s.city, s.province, s.country) if x)


# ---------------------------------------------------------------- sites.txt


def parse_sites(text: str) -> list[SiteLine]:
    lines = []
    for raw in text.splitlines():
        body = raw.split("#", 1)[0].strip()
        if body:
            lines.append(SiteLine(raw, body.rstrip("/"), active=True))
        elif m := URL_RE.search(raw):
            lines.append(SiteLine(raw, m.group(0).rstrip("/")))
        else:
            lines.append(SiteLine(raw))
    return lines


def render_sites(
    lines: list[SiteLine],
    pruned: dict[str, str],
    added: list[StoreCheck],
    today: date,
    relocated: dict[str, str] | None = None,
) -> str:
    relocated = relocated or {}
    out = []
    for ln in lines:
        if ln.active and ln.url in relocated:
            out.append(f"{relocated[ln.url]}  # moved from {ln.url} (canvass {today.isoformat()})")
        elif ln.active and ln.url in pruned:
            out.append(f"# {ln.url}  # dead {today.isoformat()}: {pruned[ln.url]}")
        else:
            out.append(ln.raw)
    for s in added:
        out.append(f"{s.final_url}  # {s.name}, {_where(s)} (canvass {today.isoformat()})")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- probing


def _origin(url: str) -> str:
    u = httpx.URL(url)
    return f"{u.scheme}://{u.host}"


async def check_store(
    url: str, client: httpx.AsyncClient, settings: Settings, pages: int | None = None
) -> StoreCheck:
    """Is `url` a live Shopify storefront, where is it, and how much coffee does it sell?"""
    check = StoreCheck(url=url)
    one_page = settings.model_copy(update={"max_pages": pages or settings.canvass_pages})
    result = await fetch_site(url, client, one_page)
    if not result.ok:
        check.error, check.definitive = result.error, not result.transient
        return check
    check.alive = True
    check.final_url = result.final_url or url
    try:
        meta = await _get_json(client, f"{_origin(check.final_url)}/meta.json", {}, settings)
    except FetchError as e:
        logger.info("%s: no meta.json (%s)", url, e)
        meta = {}
    check.name = (meta.get("name") or "").strip()
    check.country, check.province, check.city = meta.get("country"), meta.get("province"), meta.get("city")
    check.myshopify = meta.get("myshopify_domain")
    check.domain = meta.get("domain")
    check.products = len(result.products)
    variants = normalize_site(check.final_url, result.products)
    coffee_ids = {v.product_id for v in variants if v.category == "coffee"}
    check.coffee_products = len(coffee_ids)
    if not check.name and variants:
        check.name = variants[0].roaster
    for p in result.products:
        if p.get("id") in coffee_ids:
            vendor = (p.get("vendor") or "").strip()
            check.vendors[vendor] = check.vendors.get(vendor, 0) + 1
    return check


async def _gather_checks(
    urls: list[str], client: httpx.AsyncClient, settings: Settings, pages: int | None = None
) -> list[StoreCheck]:
    sem = asyncio.Semaphore(settings.concurrency)

    async def one(u: str) -> StoreCheck:
        async with sem:
            return await check_store(u, client, settings, pages)

    return list(await asyncio.gather(*(one(u) for u in urls)))


# ---------------------------------------------------------------- names -> storefronts


def _fold(name: str) -> str:
    """'Café Saint-Henri & Co.' -> 'cafe saint henri co'"""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def name_key(name: str) -> str:
    """Comparable core of a roaster name: 'Pilot Coffee Roasters' and 'pilot' both -> 'pilot'."""
    s = _fold(name)
    changed = True
    while changed:
        changed = False
        for suffix in GENERIC_SUFFIXES:
            if s.endswith(" " + suffix):
                s, changed = s[: -len(suffix) - 1], True
    for prefix in ("the ", "cafe ", "brulerie "):
        s = s.removeprefix(prefix)
    return s.replace(" and ", " ").replace(" ", "")


def guess_urls(name: str) -> list[str]:
    folded = _fold(name).replace(" and ", " ")
    full = folded.replace(" ", "")
    core = name_key(name)
    slugs: list[str] = []
    for s in (full, core, f"{core}coffee", f"{core}coffeeroasters", folded.replace(" ", "-"), f"{core}roasters"):
        if s and len(s) > 2 and s not in slugs:
            slugs.append(s)
    urls = []
    for s in slugs[:4]:
        urls += [f"https://{s}.ca", f"https://{s}.com", f"https://{s}.myshopify.com"]
    if core and len(core) > 2:
        urls.append(f"https://{core}.coffee")
    return list(dict.fromkeys(urls))


def _is_roaster_name(vendor: str, aggregator: StoreCheck) -> bool:
    v = _fold(vendor)
    return v not in NOT_ROASTERS and name_key(vendor) != name_key(aggregator.name) and not v.isdigit()


async def resolve_name(name: str, client: httpx.AsyncClient, settings: Settings) -> StoreCheck | None:
    """First guessed host that serves products.json (bounded: DNS misses fail fast)."""
    quick = settings.model_copy(update={"max_retries": 1, "timeout_s": 15.0})
    for url in guess_urls(name):
        check = await check_store(url, client, quick)
        if check.alive:
            return check
    return None


# ---------------------------------------------------------------- the run


def read_candidates(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def relocation_urls(url: str) -> list[str]:
    host = site_id(url)
    base = host.split(".")[0]
    return list(dict.fromkeys([
        f"https://shop.{host}", f"https://{base}.myshopify.com", f"https://{base.replace('-', '')}.myshopify.com"]))


async def find_relocation(url: str, client: httpx.AsyncClient, settings: Settings) -> str | None:
    """Where a listed store's Shopify storefront went, if it is still under the same domain."""
    host = site_id(url)
    quick = settings.model_copy(update={"max_retries": 1, "timeout_s": 15.0})
    for guess in relocation_urls(url):
        check = await check_store(guess, client, quick)
        # A guessed myshopify name can belong to an unrelated shop; trust it only if meta.json says
        # the store's own primary domain is (a subdomain of) the listed one.
        primary = site_id(check.domain) if check.domain else ""
        if check.alive and (primary == host or primary.endswith("." + host)):
            return f"https://{check.domain}"
    return None


def _days_since(day: str | None, today: date) -> int | None:
    return (today - date.fromisoformat(day)).days if day else None


def decide_prune(
    check: StoreCheck, status: dict[str, Any] | None, settings: Settings, today: date
) -> tuple[bool, str]:
    """Prune only failures that look permanent *and* that the daily run has also been seeing for a while."""
    reason = check.error or "unknown error"
    if not check.definitive:
        return False, f"{reason} (transient)"
    # The daily run's own record must corroborate it; no record (e.g. backfilled status) means no prune.
    if not status:
        return False, f"{reason} (no daily-run status yet)"
    last_ok = status.get("last_ok")
    if last_ok is None:
        if status.get("status") == "failed":
            return True, reason
        return False, f"{reason} (no daily-run status yet)"
    if (_days_since(last_ok, today) or 0) < settings.prune_after_days:
        return False, f"{reason} (last scraped OK {last_ok})"
    return True, reason


async def canvass(
    settings: Settings,
    today: date,
    site_status: dict[str, dict[str, Any]],
    transport: httpx.AsyncBaseTransport | None = None,
    prune: bool = True,
    discover: bool = True,
) -> tuple[CanvassReport, str]:
    """Return the report and the new sites.txt text."""
    lines = parse_sites(settings.sites_file.read_text())
    active = [ln.url for ln in lines if ln.active and ln.url]
    report = CanvassReport()

    async with httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        timeout=settings.timeout_s,
        follow_redirects=True,
        transport=transport,
    ) as client:
        known_hosts = {site_id(ln.url) for ln in lines if ln.url}
        known_names = {name_key(s.get("roaster", "")) for s in site_status.values()} - {""}
        pruned: dict[str, str] = {}
        relocated: dict[str, str] = {}

        if prune:
            for check in await _gather_checks(active, client, settings):
                if check.alive:
                    known_names.add(name_key(check.name))
                    if check.myshopify:
                        known_hosts.add(site_id(check.myshopify))
                    if check.final_url and site_id(check.final_url) != site_id(check.url):
                        report.moved.append((check.url, check.final_url))
                        known_hosts.add(site_id(check.final_url))
                    continue
                if check.definitive and (new := await find_relocation(check.url, client, settings)):
                    relocated[check.url] = new
                    report.relocated.append((check.url, new))
                    known_hosts.add(site_id(new))
                    continue
                do_prune, reason = decide_prune(check, site_status.get(site_id(check.url)), settings, today)
                if do_prune:
                    pruned[check.url] = reason
                    report.pruned.append((check.url, reason))
                else:
                    report.kept_failing.append((check.url, reason))

        if discover:
            await _discover(settings, client, report, known_hosts, known_names - {""})

    return report, render_sites(lines, pruned, report.added, today, relocated)


async def _discover(
    settings: Settings,
    client: httpx.AsyncClient,
    report: CanvassReport,
    known_hosts: set[str],
    known_names: set[str],
) -> None:
    candidates = read_candidates(settings.candidates_file)
    urls = [c for c in candidates if "://" in c or "." in c.split()[0]]
    names = [c for c in candidates if c not in urls]

    for agg in await _gather_checks(settings.aggregator_sites, client, settings, settings.aggregator_pages):
        if not agg.alive:
            logger.warning("aggregator %s unavailable: %s", agg.url, agg.error)
            continue
        known_hosts.add(site_id(agg.final_url or agg.url))
        names += [v for v in sorted(agg.vendors, key=lambda v: -agg.vendors[v]) if _is_roaster_name(v, agg)]

    seen: set[str] = set()
    todo_names = []
    for n in names:
        k = name_key(n)
        if k and k not in known_names and k not in seen:
            seen.add(k)
            todo_names.append(n)
    if len(todo_names) > settings.canvass_max_names:
        logger.info("resolving %d of %d candidate names", settings.canvass_max_names, len(todo_names))
        todo_names = todo_names[: settings.canvass_max_names]

    checks: list[tuple[str, StoreCheck | None]] = []
    for u in urls:
        u = u if "://" in u else f"https://{u}"
        checks.append((u, (await _gather_checks([u.rstrip("/")], client, settings))[0]))
    sem = asyncio.Semaphore(settings.concurrency)

    async def resolve(n: str) -> tuple[str, StoreCheck | None]:
        async with sem:
            return n, await resolve_name(n, client, settings)

    checks += await asyncio.gather(*(resolve(n) for n in todo_names))

    for cand, check in checks:
        reason = _reject_reason(check, settings, known_hosts, known_names)
        if reason:
            report.rejected.append((cand, reason))
            continue
        assert check is not None
        known_hosts.update(site_id(h) for h in (check.final_url, check.myshopify, check.domain) if h)
        known_names.add(name_key(check.name))
        check.final_url = _origin(check.final_url or check.url)
        report.added.append(check)


def _reject_reason(
    check: StoreCheck | None, settings: Settings, known_hosts: set[str], known_names: set[str]
) -> str | None:
    if check is None:
        return "no Shopify storefront found"
    if not check.alive:
        return check.error or "not a Shopify storefront"
    hosts = {site_id(h) for h in (check.url, check.final_url, check.myshopify, check.domain) if h}
    if hosts & known_hosts or name_key(check.name) in known_names:
        return f"already listed ({check.final_url})"
    if check.country not in settings.canvass_countries:
        return f"{check.final_url} is in {check.country or 'unknown country'}"
    if check.coffee_products < settings.canvass_min_coffee:
        return f"{check.final_url} has {check.coffee_products} coffee products"
    return None
