"""Raw snapshot store (data/raw/<source>/<UTC ts>/) and the DuckDB `source_run` log."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .models import Result, Source

REPO_ROOT = Path(__file__).resolve().parents[3]

DDL = """
CREATE TABLE IF NOT EXISTS source_run (
    source_id VARCHAR, operator VARCHAR, category VARCHAR,
    started_at VARCHAR, fetched_at VARCHAR, status VARCHAR,
    rows BIGINT, expected_rows BIGINT, bytes BIGINT, sha256 VARCHAR,
    portal_modified VARCHAR, as_of VARCHAR, url VARCHAR, snapshot_dir VARCHAR,
    note VARCHAR, licence VARCHAR, attribution VARCHAR
)
"""


def _connect_with_retry(path: Path, wait_s: float = 180.0) -> duckdb.DuckDBPyConnection:
    """DuckDB allows one writer process; parallel CLI runs queue up instead of failing."""
    import time
    deadline = time.monotonic() + wait_s
    delay = 0.5
    while True:
        try:
            return duckdb.connect(str(path))
        except duckdb.IOException as e:
            if "lock" not in str(e).lower() or time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, data_dir: Path | None = None):
        from ..settings import DATA_DIR as _DATA_DIR
        self.data_dir = Path(data_dir or _DATA_DIR)
        self.raw_dir = self.data_dir / "raw"
        self.db_path = self.data_dir / "grid.duckdb"
        self._con: duckdb.DuckDBPyConnection | None = None
        self.pending = False  # True when another process (the server) holds the DuckDB write lock

    @property
    def pending_log(self) -> Path:
        return self.raw_dir / "_pending_runs.jsonl"

    @property
    def con(self) -> duckdb.DuckDBPyConnection | None:
        """None when the DB is locked by the server: CLI runs then log to _pending_runs.jsonl,
        which the server ingests on its next refresh (see ingest_pending)."""
        if self._con is None and not self.pending:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._con = _connect_with_retry(self.db_path, wait_s=float(os.environ.get("GRIDMAP_LOCK_WAIT", "8")))
                self._con.execute(DDL)
            except duckdb.IOException as e:
                if "lock" not in str(e).lower():
                    raise
                self.pending = True
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def new_snapshot(self, source_id: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        base = self.raw_dir / source_id
        base.mkdir(parents=True, exist_ok=True)
        path, i = base / stamp, 1
        while True:
            try:
                path.mkdir()
                return path
            except FileExistsError:
                i += 1
                path = base / f"{stamp}-{i}"

    def last_ok(self, source_id: str) -> dict[str, Any] | None:
        if not self.db_path.exists() or self.con is None:
            return None
        row = self.con.execute(
            "SELECT portal_modified, rows, as_of, fetched_at, snapshot_dir, url FROM source_run "
            "WHERE source_id = ? AND status = 'ok' ORDER BY fetched_at DESC LIMIT 1", [source_id]
        ).fetchone()
        if row is None:
            return None
        return dict(zip(("portal_modified", "rows", "as_of", "fetched_at", "snapshot_dir", "url"), row, strict=True))

    def record(self, src: Source, res: Result, started_at: str) -> None:
        """Finalise a run: write meta.json for a good snapshot (drop it otherwise), log to source_run."""
        fetched_at = utcnow()
        snap = res.snapshot
        if snap is not None and res.status == "ok":
            meta = {
                "source_id": src.id, "url": res.url, "status": res.status, "rows": res.rows,
                "expected_rows": res.expected_rows, "bytes": res.bytes, "sha256": res.sha256,
                "portal_modified": res.portal_modified, "as_of": res.as_of, "fetched_at": fetched_at,
                "files": res.files, "note": res.note, "licence": src.licence, "attribution": src.attribution,
            }
            (snap / "meta.json").write_text(json.dumps(meta, indent=2))
            self.prune(src)
        elif snap is not None:
            shutil.rmtree(snap, ignore_errors=True)
            if not any((self.raw_dir / src.id).iterdir()):
                (self.raw_dir / src.id).rmdir()
            snap = None
        row = [src.id, src.operator, src.category, started_at, fetched_at, res.status, res.rows,
               res.expected_rows, res.bytes, res.sha256, res.portal_modified, res.as_of, res.url,
               str(snap) if snap else None, res.note, src.licence, src.attribution]
        if self.con is None:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            with self.pending_log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            return
        self.con.execute("INSERT INTO source_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)

    def prune(self, src: Source) -> list[str]:
        """Keep only the newest N good snapshots per source (live feeds would otherwise grow ~GB/month)."""
        keep = src.keep or (3 if src.cadence_seconds <= 3600 else 6)
        base = self.raw_dir / src.id
        good = sorted((d for d in base.iterdir() if d.is_dir() and (d / "meta.json").exists()), reverse=True)
        removed = []
        import time as _t
        for d in base.iterdir():  # abandoned partial downloads (process killed mid-fetch)
            if d.is_dir() and not (d / "meta.json").exists() and _t.time() - d.stat().st_mtime > 3600:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        for d in good[keep:]:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
        return removed

    def ingest_pending(self) -> int:
        """Move runs logged while the DB was locked into source_run. Returns rows ingested."""
        p = self.pending_log
        if not p.exists() or self.con is None:
            return 0
        tmp = p.with_suffix(".ingesting")
        p.replace(tmp)
        rows = [json.loads(line) for line in tmp.read_text().splitlines() if line.strip()]
        for r in rows:
            self.con.execute("INSERT INTO source_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
        tmp.unlink()
        return len(rows)
