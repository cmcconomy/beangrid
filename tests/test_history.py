import csv
from datetime import date, timedelta

from beangrid.history import History, Observation

D0 = date(2026, 1, 1)


def obs(key, price, available=True, compare_at=None):
    return Observation(key, price, compare_at, available)


def test_only_changes_are_recorded(tmp_path):
    h = History()
    for i in range(5):
        h.apply_snapshot(D0 + timedelta(days=i), "a.example", [obs("a.example:1", 20.0)])
    h.apply_snapshot(D0 + timedelta(days=5), "a.example", [obs("a.example:1", 18.0)])
    path = tmp_path / "history"
    assert h.append_new(path) == 2
    rows = list(csv.DictReader((path / "2026.csv").open()))
    assert [(r["date"], r["price"]) for r in rows] == [("2026-01-01", "20"), ("2026-01-06", "18")]


def test_delisting_and_other_sites_untouched():
    h = History()
    h.apply_snapshot(D0, "a.example", [obs("a.example:1", 20.0), obs("a.example:2", 30.0)])
    h.apply_snapshot(D0, "b.example", [obs("b.example:1", 10.0)])
    h.apply_snapshot(D0 + timedelta(days=1), "a.example", [obs("a.example:1", 20.0)])
    gone = h.variants["a.example:2"].last
    assert (gone.listed, gone.available, gone.price) == (False, False, 30.0)
    assert h.variants["b.example:1"].last.listed  # b not scraped today: not marked delisted


def test_round_trip_and_same_day_rerun_is_idempotent(tmp_path):
    path = tmp_path / "history"
    h = History()
    h.apply_snapshot(D0, "a.example", [obs("a.example:1", 20.0, compare_at=25.0)])
    h.append_new(path)

    h2 = History.load(path)
    h2.apply_snapshot(D0 + timedelta(days=1), "a.example", [obs("a.example:1", 19.0)])
    h2.append_new(path)
    # Re-run the same day with a different observation: replaces rather than duplicates.
    h3 = History.load(path)
    h3.apply_snapshot(D0 + timedelta(days=1), "a.example", [obs("a.example:1", 18.5)])
    h3.append_new(path)

    final = History.load(path).variants["a.example:1"].events
    assert [(e.date, e.price, e.compare_at) for e in final] == [(D0, 20.0, 25.0), (D0 + timedelta(days=1), 18.5, None)]


def test_price_stats():
    h = History()
    today = D0 + timedelta(days=60)
    h.apply_snapshot(D0, "a.example", [obs("a.example:1", 30.0, available=False)])
    h.apply_snapshot(D0 + timedelta(days=10), "a.example", [obs("a.example:1", 30.0)])
    h.apply_snapshot(today - timedelta(days=2), "a.example", [obs("a.example:1", 24.0)])
    s = h.price_stats("a.example:1", today)
    assert s["ref_price_30d"] == 30.0
    assert s["min_90d"] == 24.0 and s["max_90d"] == 30.0
    assert s["first_seen"] == "2026-01-01"
    assert s["back_in_stock"] is False  # restocked 50 days ago, not recently


def test_change_points():
    h = History()
    h.apply_snapshot(D0, "a.example", [obs("a.example:1", 30.0)])
    h.apply_snapshot(D0 + timedelta(days=5), "a.example", [obs("a.example:1", 30.0, available=False)])
    h.apply_snapshot(D0 + timedelta(days=9), "a.example", [obs("a.example:1", 27.0)])
    assert h.variants["a.example:1"].change_points(D0 + timedelta(days=2)) == [
        ("2026-01-03", 30.0), ("2026-01-10", 27.0)]


def test_history_is_split_by_year(tmp_path):
    h = History()
    h.apply_snapshot(date(2025, 12, 31), "a.example", [obs("a.example:1", 20.0)])
    h.apply_snapshot(date(2026, 1, 1), "a.example", [obs("a.example:1", 18.0)])
    h.append_new(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2025.csv", "2026.csv"]
    assert [e.price for e in History.load(tmp_path).variants["a.example:1"].events] == [20.0, 18.0]
