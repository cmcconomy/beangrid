"""Parquet copies of the archive, published with the site for analysis (DuckDB, pandas, the browser).

The committed CSVs stay the source of truth: they are append-only text, which git stores cheaply.
Parquet is rebuilt from them on every run and never committed.
"""

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

HISTORY_COLUMNS = {
    "date": "DATE", "key": "VARCHAR", "price": "DOUBLE", "compare_at": "DOUBLE",
    "available": "BOOLEAN", "listed": "BOOLEAN",
}
CATALOG_COLUMNS = {
    "key": "VARCHAR", "site": "VARCHAR", "product_id": "BIGINT", "title": "VARCHAR", "variant_title": "VARCHAR",
    "grams": "DOUBLE", "category": "VARCHAR", "url": "VARCHAR", "first_seen": "DATE",
}


def _columns(spec: dict[str, str]) -> str:
    return "{" + ", ".join(f"'{k}': '{v}'" for k, v in spec.items()) + "}"


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def write_parquet(data_dir: Path, out_dir: Path) -> list[Path]:
    """Write history.parquet (all years, sorted by key then date) and catalog.parquet into out_dir."""
    jobs = []
    history_files = sorted((data_dir / "history").glob("*.csv")) if (data_dir / "history").exists() else []
    if history_files:
        files = "[" + ", ".join(f"'{_sql_path(p)}'" for p in history_files) + "]"
        jobs.append(("history.parquet", files, HISTORY_COLUMNS, "key, date"))
    if (data_dir / "catalog.csv").exists():
        jobs.append(("catalog.parquet", f"'{_sql_path(data_dir / 'catalog.csv')}'", CATALOG_COLUMNS, "key"))
    if not jobs:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with duckdb.connect() as con:
        for name, source, columns, order in jobs:
            target = out_dir / name
            con.execute(
                f"COPY (SELECT * FROM read_csv({source}, header = true, columns = {_columns(columns)}) "
                f"ORDER BY {order}) TO '{_sql_path(target)}' (FORMAT parquet, COMPRESSION zstd)"
            )
            written.append(target)
            logger.info("Wrote %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return written
