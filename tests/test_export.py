from datetime import date

import duckdb

from beangrid.export import write_parquet


def test_write_parquet_types_and_sorts_history_and_catalog(tmp_path):
    data = tmp_path / "data"
    (data / "history").mkdir(parents=True)
    (data / "history" / "2025.csv").write_text(
        "date,key,price,compare_at,available,listed\n"
        "2025-12-30,b.example:2,30,,1,1\n"
        "2025-12-01,a.example:1,24,,1,1\n"
    )
    (data / "history" / "2026.csv").write_text(
        "date,key,price,compare_at,available,listed\n"
        "2026-01-02,a.example:1,19.5,24,1,1\n"
        "2026-01-03,a.example:1,19.5,24,0,0\n"
    )
    (data / "catalog.csv").write_text(
        "key,site,product_id,title,variant_title,grams,category,url,first_seen\n"
        "a.example:1,a.example,10,\"Kenya, AA\",340g,340,coffee,https://a.example/p,2025-12-01\n"
        "b.example:2,b.example,20,Mug,Default Title,,merch,https://b.example/m,2025-12-30\n"
    )
    out = tmp_path / "site"

    written = write_parquet(data, out)

    assert sorted(p.name for p in written) == ["catalog.parquet", "history.parquet"]
    con = duckdb.connect()
    rows = con.execute(f"select * from '{out / 'history.parquet'}'").fetchall()
    assert rows == [
        (date(2025, 12, 1), "a.example:1", 24.0, None, True, True),
        (date(2026, 1, 2), "a.example:1", 19.5, 24.0, True, True),
        (date(2026, 1, 3), "a.example:1", 19.5, 24.0, False, False),
        (date(2025, 12, 30), "b.example:2", 30.0, None, True, True),
    ]
    types = dict(con.execute(f"select column_name, column_type from (describe select * from '{out / 'history.parquet'}')").fetchall())
    assert types == {"date": "DATE", "key": "VARCHAR", "price": "DOUBLE", "compare_at": "DOUBLE",
                     "available": "BOOLEAN", "listed": "BOOLEAN"}
    cat = con.execute(f"select key, title, grams, first_seen from '{out / 'catalog.parquet'}' order by key").fetchall()
    assert cat == [("a.example:1", "Kenya, AA", 340.0, date(2025, 12, 1)),
                   ("b.example:2", "Mug", None, date(2025, 12, 30))]


def test_write_parquet_without_archive_writes_nothing(tmp_path):
    assert write_parquet(tmp_path / "missing", tmp_path / "out") == []
