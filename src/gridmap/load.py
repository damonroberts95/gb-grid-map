"""Load data/norm/<OP>/<table>.parquet into DuckDB canonical tables (full replace per table).

Single writer: call only from the server/scheduler process or the refresh CLI, never concurrently.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .normalise.common import DATA_DIR
from .normalise.schema import TABLES


def load_all(con: duckdb.DuckDBPyConnection, norm_dir: Path | None = None) -> dict[str, int]:
    norm_dir = norm_dir or DATA_DIR / "norm"
    counts: dict[str, int] = {}
    for table in TABLES:
        files = sorted(norm_dir.glob(f"*/{table}.parquet"))
        if not files:
            continue
        paths = [str(f) for f in files]
        # Swap atomically: build into a staging table, then replace in one transaction.
        con.execute("BEGIN")
        con.execute(f"CREATE OR REPLACE TABLE {table}__stage AS SELECT * FROM read_parquet(?, union_by_name=true)",
                    [paths])
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"ALTER TABLE {table}__stage RENAME TO {table}")
        con.execute("COMMIT")
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def load_derived(con: duckdb.DuckDBPyConnection, derived_dir: Path | None = None) -> dict[str, int]:
    """Load data/derived/*.parquet (match output) as tables named after the file; same atomic swap as load_all."""
    derived_dir = derived_dir or DATA_DIR / "derived"
    counts: dict[str, int] = {}
    for f in sorted(derived_dir.glob("*.parquet")):
        t = f.stem
        con.execute("BEGIN")
        con.execute(f"CREATE OR REPLACE TABLE {t}__stage AS SELECT * FROM read_parquet(?)", [str(f)])
        con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute(f"ALTER TABLE {t}__stage RENAME TO {t}")
        con.execute("COMMIT")
        counts[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    return counts


if __name__ == "__main__":
    c = duckdb.connect(str(DATA_DIR / "grid.duckdb"))
    for t, n in load_all(c).items():
        print(f"{t:14} {n:,}")
