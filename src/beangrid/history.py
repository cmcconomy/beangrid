"""The archive: an append-only log of price/availability *changes* per variant.

One row is written only when something about a variant changes (or it appears/disappears), so the
log stays small and git-friendly while still letting you reconstruct the price of any variant on
any day since tracking began.

data/history/<year>.csv  date,key,price,compare_at,available,listed
data/catalog.csv  key -> descriptive fields (title, size, url, category, first_seen)
"""

import csv
import logging
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from beangrid.normalize import Variant

logger = logging.getLogger(__name__)

HISTORY_FIELDS = ["date", "key", "price", "compare_at", "available", "listed"]
CATALOG_FIELDS = ["key", "site", "product_id", "title", "variant_title", "grams", "category", "url", "first_seen"]


@dataclass(frozen=True)
class Event:
    date: date
    key: str
    price: float
    compare_at: float | None
    available: bool
    listed: bool

    def same_state(self, other: "Event") -> bool:
        return (self.price, self.compare_at, self.available, self.listed) == (
            other.price, other.compare_at, other.available, other.listed,
        )


@dataclass
class VariantHistory:
    events: list[Event] = field(default_factory=list)

    @property
    def last(self) -> Event:
        return self.events[-1]

    @property
    def first_seen(self) -> date:
        return self.events[0].date

    def state_on(self, day: date) -> Event | None:
        current = None
        for e in self.events:
            if e.date > day:
                break
            current = e
        return current

    def daily_prices(self, start: date, end: date) -> list[float]:
        """Listed price for each day in [start, end]; days not listed are skipped."""
        prices = []
        idx = 0
        current: Event | None = None
        day = start
        while day <= end:
            while idx < len(self.events) and self.events[idx].date <= day:
                current = self.events[idx]
                idx += 1
            if current is not None and current.listed:
                prices.append(current.price)
            day += timedelta(days=1)
        return prices

    def change_points(self, since: date) -> list[tuple[str, float]]:
        """Compact price series for charts: the price at ``since`` plus each later price change."""
        points: list[tuple[str, float]] = []
        start = self.state_on(since)
        if start is not None and start.listed:
            points.append((since.isoformat(), start.price))
        for e in self.events:
            if e.date <= since or not e.listed:
                continue
            if not points or points[-1][1] != e.price:
                points.append((e.date.isoformat(), e.price))
        return points


class History:
    def __init__(self, variants: dict[str, VariantHistory] | None = None):
        self.variants: dict[str, VariantHistory] = variants or {}
        self.new_events: list[Event] = []

    @property
    def start(self) -> date | None:
        firsts = [vh.first_seen for vh in self.variants.values() if vh.events]
        return min(firsts) if firsts else None

    @property
    def last_date(self) -> date | None:
        lasts = [vh.last.date for vh in self.variants.values() if vh.events]
        return max(lasts) if lasts else None

    def _add(self, event: Event) -> None:
        vh = self.variants.setdefault(event.key, VariantHistory())
        if vh.events and vh.last.same_state(event):
            return
        if vh.events and event.date < vh.last.date:
            raise ValueError(f"out-of-order event for {event.key}: {event.date} < {vh.last.date}")
        if vh.events and event.date == vh.last.date:
            vh.events[-1] = event  # re-run on the same day: latest observation wins
            self.new_events = [e for e in self.new_events if not (e.key == event.key and e.date == event.date)]
        else:
            vh.events.append(event)
        self.new_events.append(event)

    def apply_snapshot(self, day: date, site: str, observations: Iterable["Observation"]) -> None:
        """Record one successfully scraped site. Variants no longer present are marked unlisted."""
        seen = set()
        for obs in observations:
            seen.add(obs.key)
            self._add(Event(day, obs.key, obs.price, obs.compare_at, obs.available, True))
        prefix = f"{site}:"
        for key, vh in self.variants.items():
            if key.startswith(prefix) and key not in seen and vh.last.listed and vh.last.date <= day:
                last = vh.last
                self._add(Event(day, key, last.price, last.compare_at, False, False))

    # ---- persistence -------------------------------------------------------------------------

    @classmethod
    def load(cls, history_dir: Path) -> "History":
        """Read every yearly file (``<history_dir>/<year>.csv``)."""
        variants: dict[str, VariantHistory] = {}
        for path in sorted(history_dir.glob("*.csv")) if history_dir.exists() else []:
            with path.open(newline="") as f:
                for row in csv.DictReader(f):
                    e = Event(
                        date=date.fromisoformat(row["date"]),
                        key=row["key"],
                        price=float(row["price"]),
                        compare_at=float(row["compare_at"]) if row["compare_at"] else None,
                        available=row["available"] == "1",
                        listed=row["listed"] == "1",
                    )
                    variants.setdefault(e.key, VariantHistory()).events.append(e)
        for vh in variants.values():
            vh.events.sort(key=lambda e: e.date)
        return cls(variants)

    def append_new(self, history_dir: Path) -> int:
        """Append this run's events to their year's file.

        Same-day re-runs rewrite that year's file instead, so a day is never recorded twice.
        """
        if not self.new_events:
            return 0
        history_dir.mkdir(parents=True, exist_ok=True)
        by_year: dict[int, list[Event]] = {}
        for e in self.new_events:
            by_year.setdefault(e.date.year, []).append(e)
        for year, events in by_year.items():
            path = history_dir / f"{year}.csv"
            on_disk = self._max_date_on_disk(path) if path.exists() else None
            if on_disk is not None and min(e.date for e in events) <= on_disk:
                self._write_year(path, year)
                continue
            new_file = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(HISTORY_FIELDS)
                for e in sorted(events, key=lambda e: (e.date, e.key)):
                    w.writerow(_event_row(e))
        n = len({(e.key, e.date) for e in self.new_events})
        self.new_events = []
        return n

    def save_all(self, history_dir: Path) -> None:
        history_dir.mkdir(parents=True, exist_ok=True)
        for old in history_dir.glob("*.csv"):
            old.unlink()
        years = {e.date.year for vh in self.variants.values() for e in vh.events}
        for year in sorted(years):
            self._write_year(history_dir / f"{year}.csv", year)

    def _write_year(self, path: Path, year: int) -> None:
        events = sorted(
            (e for vh in self.variants.values() for e in vh.events if e.date.year == year),
            key=lambda e: (e.date, e.key),
        )
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HISTORY_FIELDS)
            for e in events:
                w.writerow(_event_row(e))

    @staticmethod
    def _max_date_on_disk(path: Path) -> date | None:
        last_line = ""
        with path.open() as f:
            for line in f:
                if line.strip():
                    last_line = line
        try:
            return date.fromisoformat(last_line.split(",", 1)[0])
        except ValueError:
            return None

    # ---- analytics ---------------------------------------------------------------------------

    def price_stats(self, key: str, today: date) -> dict[str, float | str | bool | None]:
        vh = self.variants.get(key)
        if vh is None or not vh.events:
            return {}
        prior30 = vh.daily_prices(today - timedelta(days=30), today - timedelta(days=1))
        window90 = vh.daily_prices(today - timedelta(days=90), today)
        back_in_stock = False
        avail_events = [e for e in vh.events if e.listed]
        for prev, cur in zip(avail_events, avail_events[1:]):
            if cur.available and not prev.available and (today - cur.date).days <= 7 and (cur.date - prev.date).days >= 3:
                back_in_stock = True
        sale_since = None
        for e in reversed(vh.events):
            if not e.listed or e.compare_at is None:
                break
            sale_since = e.date
        return {
            "sale_days": (today - sale_since).days if sale_since else None,
            "ref_price_30d": statistics.median(prior30) if len(prior30) >= 7 else None,
            "min_90d": min(window90) if window90 else None,
            "max_90d": max(window90) if window90 else None,
            "first_seen": vh.first_seen.isoformat(),
            "back_in_stock": back_in_stock,
        }


@dataclass(frozen=True)
class Observation:
    key: str
    price: float
    compare_at: float | None
    available: bool

    @classmethod
    def of(cls, v: Variant) -> "Observation":
        return cls(v.key, v.price, v.compare_at_price, v.available)


def _fmt(x: float | None) -> str:
    if x is None:
        return ""
    return f"{x:.2f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def _event_row(e: Event) -> list[str]:
    return [e.date.isoformat(), e.key, _fmt(e.price), _fmt(e.compare_at), "1" if e.available else "0", "1" if e.listed else "0"]


def update_catalog(path: Path, variants: Iterable[Variant], history: History) -> None:
    rows: dict[str, dict[str, str]] = {}
    if path.exists():
        with path.open(newline="") as f:
            rows = {r["key"]: r for r in csv.DictReader(f)}
    for v in variants:
        vh = history.variants.get(v.key)
        rows[v.key] = {
            "key": v.key,
            "site": v.site,
            "product_id": str(v.product_id),
            "title": v.title,
            "variant_title": v.variant_title,
            "grams": _fmt(v.grams),
            "category": v.category,
            "url": v.url,
            "first_seen": rows.get(v.key, {}).get("first_seen") or (vh.first_seen.isoformat() if vh else ""),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        for key in sorted(rows):
            w.writerow({k: rows[key].get(k, "") for k in CATALOG_FIELDS})
