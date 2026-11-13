"""Refresh orchestration: fetch (due sources) -> normalise (changed operators) -> match -> load DuckDB -> build.

    python -m gridmap.refresh [--force]

Live categories (outage, live_national) are only fetched; they never trigger the rebuild chain.
DuckDB is single-writer: the caller passes the one read-write connection (the server) or, from the CLI, this
module opens it itself, which fails if the server is already running.
"""

from __future__ import annotations

import argparse
import importlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from . import build, load, match
from .fetch import runner
from .fetch.models import Source, load_sources
from .fetch.store import DDL, REPO_ROOT, Store
from .normalise.__main__ import ADAPTERS
from .normalise.common import DATA_DIR, latest_snapshot

LIVE = {"outage": "outages", "live_national": "national"}  # category -> feed name (docs/api.md events)
FAIL_RETRY = (600, 3600)  # a failing source is retried after clamp(cadence, 10 min, 1 h)
NORMALISE_COOLDOWN = 900  # a failing adapter is retried after 15 min
_normalise_failed: dict[str, float] = {}

StageCb = Callable[[str, str], None]


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def is_due(src: Source, last_ok: float | None, last_any: float | None, now: float) -> bool:
    """Due when the last successful (ok/unchanged) fetch is older than the cadence. If the latest attempt failed
    (needs_key, error, empty_or_gated) retry only after clamp(cadence, 10 min, 1 h) so a dead feed is not hammered."""
    if last_ok is None and last_any is None:
        return True
    if last_any is not None and (last_ok is None or last_any > last_ok):
        return now - last_any >= min(max(src.cadence_seconds, FAIL_RETRY[0]), FAIL_RETRY[1])
    return now - last_ok >= src.cadence_seconds


def due_sources(registry: list[Source], con: duckdb.DuckDBPyConnection, now: float | None = None,
                force: bool = False) -> list[Source]:
    now = time.time() if now is None else now
    runs = {}
    for sid, ok, anyr in con.execute(
        "SELECT source_id, max(fetched_at) FILTER (WHERE status IN ('ok','unchanged')), max(fetched_at) "
        "FROM source_run GROUP BY source_id"
    ).fetchall():
        runs[sid] = (_ts(ok), _ts(anyr))
    out = []
    for s in registry:
        if s.large:
            continue  # --include-large only, never from the scheduler
        if s.id not in runs:  # no log row yet: trust a good snapshot on disk (hand-fetched / logged while locked)
            snap = latest_snapshot(s.id)
            if snap is not None:
                runs[s.id] = (snap.stat().st_mtime, snap.stat().st_mtime)
        if force or is_due(s, *runs.get(s.id, (None, None)), now):
            out.append(s)
    return out


def stale_operators(registry: list[Source], con: duckdb.DuckDBPyConnection, data_dir: Path,
                    now: float | None = None) -> list[str]:
    """Operators whose latest non-live 'ok' fetch is newer than their normalised output (or have none)."""
    now = time.time() if now is None else now
    ops = {s.id: s.operator for s in registry if s.category not in LIVE}
    latest: dict[str, float] = {}
    for sid, ts in con.execute("SELECT source_id, max(fetched_at) FROM source_run WHERE status='ok' "
                               "GROUP BY source_id").fetchall():
        op = ops.get(sid)
        if op in ADAPTERS and (t := _ts(ts)) is not None:
            latest[op] = max(latest.get(op, 0), t)
    out = []
    shared = max(_mtime(PKG / "normalise" / f) for f in ("common.py", "schema.py"))
    for op, t in latest.items():
        mt = max((f.stat().st_mtime for f in (data_dir / "norm" / op).glob("*.parquet")), default=0)
        code = max(shared, _mtime(PKG / "normalise" / f"{ADAPTERS[op]}.py"))
        if (t > mt or code > mt) and now - _normalise_failed.get(op, 0) > NORMALISE_COOLDOWN:
            out.append(op)
    return sorted(out)


PKG = Path(__file__).resolve().parent


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def code_newer_than(outputs: Path, *modules: str) -> bool:
    """True if any of our own pipeline modules (file or package dir) changed after `outputs` was written."""
    def newest(p: Path) -> float:
        return max((_mtime(f) for f in p.rglob("*.py")), default=0.0) if p.is_dir() else _mtime(p)
    return max(newest(PKG / m) for m in modules) > _mtime(outputs)


def _has_live_extras() -> bool:
    from .extra import modules
    return any(m.live for mod in modules() for m in mod.LAYERS)


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name])
                .fetchone()[0])


def refresh(*, force: bool = False, on_stage: StageCb | None = None, con: duckdb.DuckDBPyConnection | None = None,
            lock: threading.Lock | None = None, data_dir: Path | None = None,
            registry: list[Source] | None = None) -> dict:
    """One pass. Returns {fetched, live, normalised, loaded, built}; `live` lists feeds whose data refreshed."""
    say = on_stage or (lambda stage, detail: None)
    data_dir = data_dir or DATA_DIR
    lock = lock or threading.Lock()
    own = con is None
    if own:
        data_dir.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(data_dir / "grid.duckdb"))
    load_dotenv(REPO_ROOT / ".env")  # keys stay in os.environ; never printed
    registry = registry if registry is not None else load_sources()
    res: dict = {"fetched": {}, "live": [], "normalised": [], "loaded": False, "built": []}
    try:
        with lock:
            con.execute(DDL)
            st = Store(data_dir)
            st._con = con.cursor()
            if (n := st.ingest_pending()):
                say("fetch", f"ingested {n} runs logged by CLI fetches")
            due = due_sources(registry, con, force=force)
        cat = {s.id: s.category for s in registry}

        if due:
            say("fetch", f"{len(due)} sources due")
            store = Store(data_dir)
            store._con = con.cursor()
            with lock:
                summary = runner.run([s.id for s in due], force=force, store=store, registry=registry)
            res["fetched"] = summary["counts"]
            res["live"] = sorted({LIVE[cat[r["source"]]] for r in summary["runs"]
                                  if r["status"] == "ok" and cat[r["source"]] in LIVE})

        stale = stale_operators(registry, con, data_dir)
        for op in stale:
            say("normalise", op)
            try:
                importlib.import_module(f"gridmap.normalise.{ADAPTERS[op]}").run()
                res["normalised"].append(op)
                _normalise_failed.pop(op, None)
            except Exception as e:  # noqa: BLE001 - one adapter failing must not stop the others
                _normalise_failed[op] = time.time()
                say("normalise", f"{op} FAILED: {type(e).__name__}: {e}")

        derived_missing = not (data_dir / "derived" / "site.parquet").exists()
        chain = (bool(res["normalised"]) or derived_missing
                 or code_newer_than(data_dir / "derived" / "site.parquet", "match.py"))
        if chain:
            say("match", "matching sites")
            match.run()
        if chain or not _has_table(con, "site"):
            say("match", "loading DuckDB")
            with lock:
                if res["normalised"]:
                    load.load_all(con, data_dir / "norm")
                load.load_derived(con, data_dir / "derived")
            res["loaded"] = True
        fetched_static = due and any(r for r in summary["runs"]
                                     if r["status"] == "ok" and cat[r["source"]] not in LIVE)
        if (chain or fetched_static or not (data_dir / "public" / "manifest.json").exists()
                or code_newer_than(data_dir / "public" / "manifest.json", "build.py", "extra")):
            say("build", "building map files")
            out = build.build_all(data_dir)
            res["built"] = out["changed"]
            for mod, err in out.get("extra_errors", {}).items():
                say("build", f"{mod} FAILED: {err}")
        elif res["live"] and _has_live_extras():
            res["built"] = build.build_all(data_dir, only_live=True)["changed"]
    finally:
        say("idle", "")
        if own:
            con.close()
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="fetch every non-large source regardless of cadence")
    args = ap.parse_args()
    try:
        out = refresh(force=args.force, on_stage=lambda s, d: print(f"[{s}] {d}".rstrip(), flush=True))
    except duckdb.IOException as e:
        print(f"cannot open grid.duckdb (is the server running? it owns the write lock): {e}")
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
