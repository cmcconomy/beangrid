"""Store-wide promotions read from each roaster's homepage: discount codes, free-shipping thresholds,
subscription discounts, and the promo text of the theme's announcement bar.

Only what the static HTML shows is visible: codes behind email popups or applied automatically at
checkout are not. Runs once a day in the catch-up run (one paced request per store).
"""

import csv
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from beangrid.classify import fold
from beangrid.config import Settings
from beangrid.fetch import FetchError, SiteResult, get_ok, paced_map
from beangrid.normalize import site_id

logger = logging.getLogger(__name__)

# Announcement / promo bars across common Shopify themes.
BAR_SELECTOR = ", ".join([
    "[class*=announcement]", "[id*=announcement]", "[class*=promo-bar]", "[class*=promo_bar]",
    "[class*=top-bar]", "[class*=topbar]", "[class*=marquee]", "[class*=ticker]",
])
PROMO_SIGNAL_RX = re.compile(
    r"\bcodes?\b|coupon|\d\s?%|free shipping|livraison gratuite|\bsale\b|\bsoldes?\b|black friday|cyber monday|"
    r"vendredi fou|\bbogo\b|\brabais\b|\boff\b", re.I)
# "use code COFFEE10", "PROMO CODE PCR125", "code: BF2026", "le code NOEL15", "code 'COFFEE10'".
# The introducer is case-insensitive, the code itself must be upper case/digits, as stores write them.
CODE_RX = re.compile(r"(?i:\b(?:code|coupon))\s*(?:[:\-–]\s*)?[\"'“”‘’«»]?\s*([A-Z0-9][A-Z0-9_-]{3,19})\b")
NOT_CODES = {"APPLY", "CHECKOUT", "BELOW", "HERE", "REQUIRED", "NEEDED", "SENT", "EMAIL", "WILL", "ONLY", "TODAY",
             "ONLINE", "PROMO", "DISCOUNT", "COUPON", "CODE", "CODES", "WHEN", "FROM", "THIS", "THAT", "YOUR", "WITH",
             "VALID", "ENTER", "USED", "ORDER", "ORDERS", "SHOP", "SIGN", "JOIN", "PROMOTIONNEL"}
PCT_RX = re.compile(r"(?<!\d)(\d{1,2})\s?%")
CONDITIONS = [
    ("first order", re.compile(r"first (?:order|purchase)|new customers?|premi[eè]re commande|welcome", re.I)),
    ("newsletter", re.compile(r"newsletter|sign(?:ing)? up|infolettre|join our (?:list|club)", re.I)),
    # Not "SUBSCRIPTIONS EXCLUDED", and not "subscribe to our newsletter".
    ("subscription", re.compile(r"subscri(?!\w*\s*excluded)(?!be to (?:the |our )?(?:\w+ )?newsletter)|"
                                r"abonn(?!\w*\s*(?:a|à) (?:l'|notre )?infolettre)", re.I)),
    ("free shipping", re.compile(r"free shipping|livraison gratuite", re.I)),
    ("Ontario", re.compile(r"\bontario\b", re.I)),
    ("Québec", re.compile(r"\bqu[ée]bec\b", re.I)),
]
FREE_SHIP_RX = re.compile(r"(free shipping|livraison gratuite)(.{0,90}?)(?:\$\s?(\d{2,3})|(?<!\d)(\d{2,3})\s?\$)", re.I)
# Cart drawers: "Spend $60.00 more and get free shipping!"
CART_SHIP_RX = re.compile(r"spend \$\s?(\d{2,3})(?:\.\d\d)? more (?:and|to) (?:get|unlock|receive) free shipping", re.I)
REGIONS = [("Québec", r"qu[ée]bec"), ("Ontario", r"ontario"), ("BC", r"\bb\.?c\.?\b|british columbia"),
           ("Canada", r"canada")]
# wlm.arrHideLinks.push('a[href$="/products/fife-blend"],...')  (Wholesale Lock Manager app)
WLM_HIDDEN_RX = re.compile(r"""wlm\.arrHideLinks\.push\(\s*['"]a\[href\$=\\?"/products/([a-z0-9][a-z0-9-]*)\\?"\]""", re.I)
SUB_PCT_RX = re.compile(
    r"(?:subscri\w*|abonn\w*)[^%.]{0,40}?(?<!\d)(\d{1,2})\s?%|(?<!\d)(\d{1,2})\s?%[^.%]{0,40}?(?:subscri|abonn)", re.I)


class PromoCode(BaseModel):
    code: str
    pct: int | None = None
    conditions: list[str] = []
    text: str = ""


class FreeShipping(BaseModel):
    amount: int
    region: str | None = None
    text: str = ""


class PromoInfo(BaseModel):
    url: str = ""
    banners: list[str] = []
    codes: list[PromoCode] = []
    free_shipping: FreeShipping | None = None
    subscription_pct: int | None = None
    locksmith: bool = False  # the store uses the Locksmith access app: some products may be members-only
    # Wholesale Lock Manager lists the products it hides on every page: handles of members-only products.
    hidden_products: list[str] = []
    # Store collections that hold private/customer products (e.g. Just Us! "custom-coffee-blend").
    private_collections: list[str] = []


def _clean(text: str) -> str:
    """Collapse whitespace and marquee repetition ("A B A B" -> "A B")."""
    words = text.split()
    n = len(words)
    for period in range(1, n // 2 + 1):
        if n % period == 0 and words == words[:period] * (n // period):
            words = words[:period]
            break
    return " ".join(words)


def _context(text: str, start: int, end: int, before: int = 120, after: int = 80) -> str:
    """The text around a match, trimmed to whole words."""
    lo, hi = max(0, start - before), min(len(text), end + after)
    if lo > 0 and (cut := text.find(" ", lo, start)) != -1:
        lo = cut + 1
    if hi < len(text) and (cut := text.rfind(" ", end, hi)) != -1:
        hi = cut
    return ("…" if lo > 0 else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def extract_promos(html: str) -> PromoInfo:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template"]):  # (html itself is kept for the locksmith check)
        t.decompose()
    bars: list[str] = []
    for el in soup.select(BAR_SELECTOR):
        txt = _clean(el.get_text(" "))
        if 4 < len(txt) < 400 and not any(txt in b for b in bars):
            bars = [b for b in bars if b not in txt] + [txt]
    banners = [b for b in bars if PROMO_SIGNAL_RX.search(b)]
    body = _clean(soup.get_text(" "))

    info = PromoInfo(banners=banners[:3], locksmith="data-locksmith" in html,
                     hidden_products=sorted(set(WLM_HIDDEN_RX.findall(html))))
    seen: set[str] = set()
    for source in [*banners, body]:
        for m in CODE_RX.finditer(source):
            code = m.group(1)
            if code in seen or code in NOT_CODES or not re.search(r"[A-Z]", code):
                continue
            seen.add(code)
            if source is body:  # a whole page: keep only the window around the code
                ctx = _context(body, m.start(), m.end())
            else:  # a banner is short: all of it is context
                ctx = source
            pos = ctx.find(code)
            # The percentage nearest the code is the one it gives ("10% off ... code X", "code X for 15%"),
            # but not one from the next clause: 'with code "ADDON", and 10% off all Squall coffees'.
            pcts = [(abs(p.start() - pos), int(p.group(1))) for p in PCT_RX.finditer(ctx)
                    if p.start() < pos or not re.search(r",|\band\b|\bet\b", ctx[pos + len(code): p.start()])]
            info.codes.append(PromoCode(
                code=code,
                pct=min(pcts)[1] if pcts else None,
                conditions=[name for name, rx in CONDITIONS if rx.search(ctx)],
                text=ctx[:300],
            ))

    for source in [*banners, body]:
        for m in FREE_SHIP_RX.finditer(source):
            amount = int(m.group(3) or m.group(4))
            if amount < 10 or re.search(r"subtotal|your cart", m.group(2), re.I):
                continue  # "$0.00" from a cart drawer, not a threshold
            window = source[m.start(): m.end() + 15]
            region = next((name for name, rx in REGIONS if re.search(rx, window, re.I)), None)
            info.free_shipping = FreeShipping(amount=amount, region=region,
                                              text=_context(source, m.start(), m.end(), 0, 40)[:200])
            break
        if not info.free_shipping and (m := CART_SHIP_RX.search(source)):
            info.free_shipping = FreeShipping(amount=int(m.group(1)), text=m.group(0))
        if info.free_shipping:
            break
    for source in [*banners, body]:
        if m := SUB_PCT_RX.search(source):
            info.subscription_pct = int(m.group(1) or m.group(2))
            break
    return info


# Collections that hold products made for particular customers: a cafe's house blend (Just Us!
# "custom-coffee-blend": "Blomidon Blend", "Waldorf Special Blend"...), a private reserve, white label.
# Matched on the collection *handle* only: titles are marketing ("Custom Coffee" = Virgin Hill's public
# microlots). Plain "wholesale" collections are left out: they are often the whole catalogue as offered to
# wholesale accounts (Cafe Pista's has every public coffee), not products the public can't buy.
PRIVATE_COLLECTION_RX = re.compile(r"(?<![a-z])(?:custom|private|privees?|white ?label)(?![a-z])")


def is_private_collection(handle: str, title: str = "") -> bool:
    h = fold(handle.replace("-", " "))
    return bool(PRIVATE_COLLECTION_RX.search(h)) and not re.search(r"(?<![a-z])non(?![a-z])", h)


async def _private_products(site: str, client: httpx.AsyncClient, settings: Settings) -> dict[str, list[str]]:
    """Handles of products in the store's private-looking collections ({collection: [handles]}). Best effort."""
    out: dict[str, list[str]] = {}
    try:
        cols = (await get_ok(client, f"{site}/collections.json", {"limit": 250}, settings)).json().get("collections", [])
        for col in cols:
            handle = col.get("handle") or ""
            if not handle or not is_private_collection(handle, col.get("title") or ""):
                continue
            resp = await get_ok(client, f"{site}/collections/{handle}/products.json", {"limit": 250}, settings)
            out[handle] = sorted({p["handle"] for p in resp.json().get("products", []) if p.get("handle")})
    except Exception as e:  # noqa: BLE001 - optional extra; the homepage result stands on its own
        logger.info("promos: %s collections skipped (%s)", site, e)
    return out


async def _fetch_homepage(site: str, client: httpx.AsyncClient, settings: Settings) -> SiteResult:
    result = SiteResult(site_url=site)
    try:
        resp = await get_ok(client, site + "/", {}, settings)
        result.products = [{"html": resp.text}]  # SiteResult is the paced_map currency; one "product": the page
        result.ok = True
        result.products[0]["private"] = await _private_products(site, client, settings)
    except FetchError as e:
        result.error, result.transient = str(e), e.transient
    except Exception as e:  # one broken page must never sink the scan
        result.error, result.transient = str(e) or type(e).__name__, True
    return result


async def scan_homepages(
    sites: list[str], settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, PromoInfo]:
    """Promo info per site_id for the homepages that could be read."""
    results = await paced_map(sites, _fetch_homepage, settings, transport, settings.promo_budget_s,
                              accept="text/html,application/xhtml+xml")
    out = {}
    for r in results:
        if not r.ok:
            logger.info("promos: %s skipped (%s)", r.site_url, r.error)
            continue
        info = extract_promos(r.products[0]["html"])
        info.url = r.site_url
        private = r.products[0].get("private") or {}
        info.private_collections = sorted(private)
        info.hidden_products = sorted(set(info.hidden_products) | {h for hs in private.values() for h in hs})
        out[site_id(r.site_url)] = info
    logger.info("promos: read %d of %d homepages, %d with codes", len(out), len(sites),
                sum(1 for i in out.values() if i.codes))
    return out


def load_promos(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def update_promos(path: Path, log_path: Path, scanned: dict[str, PromoInfo], today: date) -> dict[str, Any]:
    """Merge today's scan into promos.json (unscanned stores keep their last known promos) and log code changes."""
    promos = load_promos(path)
    log_rows = []
    for site, info in sorted(scanned.items()):
        prev_codes = {c["code"]: c for c in promos.get(site, {}).get("codes", [])}
        entry = {"checked": today.isoformat(), **info.model_dump()}
        for c in entry["codes"]:
            c["first_seen"] = prev_codes.get(c["code"], {}).get("first_seen") or today.isoformat()
            if c["code"] not in prev_codes:
                log_rows.append([today.isoformat(), site, "added", c["code"], c["pct"] or "", c["text"]])
        now = {c["code"] for c in entry["codes"]}
        for code, c in prev_codes.items():
            if code not in now:
                log_rows.append([today.isoformat(), site, "removed", code, c.get("pct") or "", ""])
        promos[site] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(promos.items())), indent=1, ensure_ascii=False) + "\n")
    if log_rows:
        new_file = not log_path.exists()
        with log_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["date", "site", "event", "code", "pct", "text"])
            w.writerows(log_rows)
    return promos
