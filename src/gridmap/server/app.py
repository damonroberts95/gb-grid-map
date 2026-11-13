"""FastAPI app implementing docs/api.md. One process owns the single read-write DuckDB connection.

Reads use `con.cursor()` (own connection, safe across threads). Writes (fetch log, load) happen only in the
scheduler thread, under `state.lock`. The server answers from the last good data immediately; the scheduler
runs a refresh at startup and then every 60 s (each source on its own cadence, see refresh.is_due).

    uv run uvicorn gridmap.server.app:app --host 127.0.0.1 --port 8787
Env: GRIDMAP_DATA_DIR (default <repo>/data), GRIDMAP_NO_SCHEDULER=1 (serve only; used by tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
from bisect import bisect_right
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from itertools import accumulate
from pathlib import Path

import duckdb
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import build
from .. import refresh as refresh_mod
from ..fetch.models import load_sources
from ..fetch.store import DDL
from ..match import name_norm
from ..normalise.common import DATA_DIR
from . import live, places

log = logging.getLogger("gridmap.server")
# our own modules log at INFO (sweeps, refresh stages); uvicorn's handler only covers its own loggers
if not logging.getLogger("gridmap").handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s", "%H:%M:%S"))
    logging.getLogger("gridmap").addHandler(_h)
    logging.getLogger("gridmap").setLevel(logging.INFO)
PORT = 8787
KIND_NUM = build.KIND_NUM


def _clean(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def rows(cur, sql: str, params: list | None = None) -> list[dict]:
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [{c: _clean(v) for c, v in zip(cols, r, strict=True)} for r in cur.fetchall()]


class Bus:
    """Fan-out of SSE events to connected clients; publish() may be called from any thread."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def publish(self, event: str, data: dict) -> None:
        if self.loop is None:
            return
        msg = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(q.put_nowait, msg)

    def close(self) -> None:
        for q in list(self.clients):
            q.put_nowait(None)


class Index:
    """In-memory search + fault-name index, rebuilt after each load. Search is a scan of small lists / one big
    string, well under 50 ms for 470k LV names."""

    def __init__(self, cur):
        cols = {c[0] for c in cur.execute("SELECT column_name FROM information_schema.columns "
                                           "WHERE table_name = 'site'").fetchall()}
        alias = "alias_names" if "alias_names" in cols else "NULL"
        # match on name + aliases (e.g. the NESO name of a merged GSP); return the display name only
        self.sites = [((r["name"] + " | " + (r.pop("_alias") or "")).lower(), r) for r in rows(
            cur, f"SELECT site_id AS id, name, {alias} AS _alias, operator AS op, kind, lat, lon "
                 "FROM site WHERE lat IS NOT NULL")]
        for _, r in self.sites:
            r["kind"] = KIND_NUM.get(r["kind"])
        lv = rows(cur, "SELECT asset_uid AS id, name, operator AS op, lat, lon FROM lv_site "
                       "WHERE lat IS NOT NULL AND name IS NOT NULL") if _has(cur, "lv_site") else []
        self.lv = lv
        names = [r["name"].lower() for r in lv]
        self.lv_text = "\n".join(names)
        self.lv_starts = list(accumulate([0] + [len(n) + 1 for n in names[:-1]]))  # offset of each name in lv_text
        self.faults: dict[tuple[str, str], list[dict]] = {}
        if _has(cur, "fault_level"):
            for r in rows(cur, "SELECT * FROM fault_level WHERE asset_uid IS NULL AND site_name IS NOT NULL"):
                self.faults.setdefault((r["operator"], name_norm(r["site_name"])), []).append(r)
        self.primary_by_name = {(r["op"], name_norm(r["name"])): r["id"] for _, r in self.sites if r["kind"] == 3}

    def search(self, q: str, limit: int) -> list[dict]:
        q = q.strip().lower()
        if not q:
            return []
        hits = []
        for name, r in self.sites:
            i = name.find(q)
            if i >= 0:
                hits.append((0 if i == 0 else 1 if name[i - 1] == " " else 2, r["kind"] or 9, len(name), r))
        hits.sort(key=lambda h: h[:3])
        out = [{"type": "site", "id": r["id"], "name": r["name"], "op": r["op"], "kind": r["kind"],
                "lat": r["lat"], "lon": r["lon"]} for *_, r in hits[:limit]]
        if len(q) >= 4 and len(out) < limit:
            pos = self.lv_text.find(q)
            while pos >= 0 and len(out) < limit:
                i = bisect_right(self.lv_starts, pos) - 1
                r = self.lv[i]
                out.append({"type": "lv", "id": r["id"], "name": r["name"], "op": r["op"], "kind": None,
                            "lat": r["lat"], "lon": r["lon"]})
                nxt = self.lv_starts[i + 1] if i + 1 < len(self.lv_starts) else -1
                pos = self.lv_text.find(q, nxt) if nxt >= 0 else -1
        return out


DNOS = ("UKPN", "ENW", "NPG", "NGED", "SSEN", "SPEN")


def _pct(n, d) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def tec_by_site(data_dir: Path) -> dict[str, dict]:
    """NESO TEC register (Direct Connection rows only, so embedded projects already in a DNO ECR are not counted
    twice) totalled per matched site: {site_id: {site, built, pipeline, scoping, projects}}. Reuses the name
    matcher of the extras `tec` layer (gen_planning.match_site). {} when the register is absent."""
    import polars as pl

    from ..extra import gen_planning as gp
    from ..normalise.common import parse_num
    base = data_dir / "raw" / "neso_tec_register"
    snaps = sorted((d for d in base.iterdir() if d.is_dir()), reverse=True) if base.exists() else []
    csv = next((f for d in snaps for f in sorted(d.glob("*.csv"))), None)
    if csv is None:
        return {}
    idx = gp._target_sites(data_dir)
    d = pl.read_csv(csv, infer_schema=False, encoding="utf8-lossy")
    d = d.rename({c: c.lstrip("\ufeff").strip() for c in d.columns})
    out: dict[str, dict] = {}
    for r in d.iter_rows(named=True):
        if (r.get("Agreement Type") or "").strip() != "Direct Connection":
            continue
        site = gp.match_site(r["Connection Site"] or "", r["HOST TO"], idx)
        if site is None:
            continue
        mw = parse_num(r["Cumulative Total Capacity (MW)"]) or 0.0
        t = out.setdefault(site["site_id"], {"site": site, "built": 0.0, "pipeline": 0.0, "scoping": 0.0,
                                             "projects": 0})
        key = gp.TEC_STATUS.get((r["Project Status"] or "").strip())
        if key == "b":
            t["built"] += mw
        elif key == "sc":
            t["scoping"] += mw
        elif key:
            t["pipeline"] += mw
        t["projects"] += 1
    return out


def _has(cur, table: str) -> bool:
    return bool(cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0])


class State:
    def __init__(self, data_dir: Path, scheduler: bool):
        self.data_dir = data_dir
        self.public = data_dir / "public"
        self.raw = data_dir / "raw"
        self.scheduler = scheduler
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.bus = Bus()
        self.refreshing = False
        self.stage = {"stage": "idle", "detail": ""}
        self.index: Index | None = None
        self.con: duckdb.DuckDBPyConnection | None = None
        self.registry = {s.id: s for s in load_sources()}

    def cur(self):
        return self.con.cursor()

    def manifest(self) -> dict:
        m = build.read_manifest(self.public)
        return {**m, "refreshing": self.refreshing}

    def rebuild_index(self) -> None:
        try:
            self.index = Index(self.cur())
        except duckdb.Error as e:  # tables not loaded yet
            log.info("search index not built yet: %s", e)

    def set_stage(self, stage: str, detail: str = "") -> None:
        self.stage = {"stage": stage, "detail": detail}
        self.bus.publish("status", self.stage)

    def refresh_once(self, core_only: bool = False) -> None:
        before = build.read_manifest(self.public).get("version")
        self.refreshing = True
        self.set_stage("fetch", "starting")
        try:
            res = refresh_mod.refresh(on_stage=self.set_stage, con=self.con, lock=self.lock, data_dir=self.data_dir,
                                      core_only=core_only)
        except Exception as e:
            log.exception("refresh failed")
            self.refreshing = False
            self.set_stage("idle", f"refresh failed: {type(e).__name__}")
            return
        if res["loaded"] or self.index is None:
            self.rebuild_index()
        self.refreshing = False
        m = self.manifest()
        if m.get("version") != before:
            self.bus.publish("manifest", m)
        for feed in res["live"]:
            self.bus.publish("live", {"feed": feed})
        self.set_stage("idle", "")

    def run_scheduler(self) -> None:
        self.rebuild_index()
        if not (self.public / "manifest.json").exists():
            # first run: core data only, so the map appears in minutes; the loop below fetches the rest
            self.refresh_once(core_only=True)
        while not self.stop.is_set():
            self.refresh_once()
            self.stop.wait(60)


class CachedStatic(StaticFiles):
    """Hash-named files are immutable; the manifest must always be revalidated. Range is handled by FileResponse."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = ("no-cache" if path == "manifest.json"
                                         else "public, max-age=31536000, immutable")
        return resp


def create_app(data_dir: Path | None = None, scheduler: bool | None = None) -> FastAPI:
    data_dir = Path(data_dir or DATA_DIR)
    if scheduler is None:
        scheduler = os.environ.get("GRIDMAP_NO_SCHEDULER") != "1"
    st = State(data_dir, scheduler)
    st.public.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st.bus.loop = asyncio.get_running_loop()
        # GRIDMAP_DB lets the server run on a snapshot copy (with GRIDMAP_NO_SCHEDULER=1) while batch jobs hold the lock
        st.con = duckdb.connect(os.environ.get("GRIDMAP_DB") or str(data_dir / "grid.duckdb"))
        st.con.execute(DDL)
        th = None
        if st.scheduler:
            th = threading.Thread(target=st.run_scheduler, name="scheduler", daemon=True)
            th.start()
        else:
            await asyncio.to_thread(st.rebuild_index)
        yield
        st.stop.set()
        st.bus.close()
        if th:
            th.join(timeout=5)

    app = FastAPI(title="gridmap", lifespan=lifespan)
    app.state.st = st
    from . import nerda  # GET /api/nerda/{nrn} + background LV sweep (server/nerda.py)
    nerda.install(app, st)
    from . import (  # API-key setup page + config auto-update (server/keys.py, updates.py)
        keys,
        updates,
    )
    keys.install(app, st)
    updates.install(app, st)

    @app.exception_handler(duckdb.CatalogException)
    async def _no_data(request: Request, exc: duckdb.CatalogException):
        return JSONResponse({"error": "data not loaded yet"}, status_code=503)

    def one(sql: str, params: list) -> dict | None:
        r = rows(st.cur(), sql, params)
        return r[0] if r else None

    @app.get("/api/manifest")
    def manifest():
        return st.manifest()

    @app.get("/api/events")
    async def events():
        q: asyncio.Queue = asyncio.Queue()
        st.bus.clients.add(q)

        async def gen():
            try:
                yield f"event: manifest\ndata: {json.dumps(st.manifest(), separators=(',', ':'))}\n\n"
                yield f"event: status\ndata: {json.dumps(st.stage, separators=(',', ':'))}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), 15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if msg is None:
                        return
                    yield msg
            finally:
                st.bus.clients.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def ancestors(site_id: str | None, depth: int = 4) -> list[dict]:
        """Feed chain upwards from site_id (inclusive), nearest first: Primary -> BSP -> GSP."""
        out, seen = [], set()
        while site_id and site_id not in seen and len(out) < depth:
            seen.add(site_id)
            r = one("SELECT site_id, name, kind, lat, lon, parent_site_id FROM site WHERE site_id = ?", [site_id])
            if r is None:
                break
            out.append({k: r[k] for k in ("site_id", "name", "kind", "lat", "lon")})
            site_id = r["parent_site_id"]
        return out

    @app.get("/api/site/{site_id:path}")
    def site_detail(site_id: str):
        cur = st.cur()
        site = one("""SELECT s.*, c.gen_headroom_mw, c.demand_headroom_mw, c.demand_headroom_mva, c.firm_capacity_mva,
                             c.gen_rag_native, c.demand_rag_native, c.upstream_constraint, c.basis,
                             c.source_id AS capacity_source_id, c.as_of AS capacity_as_of,
                             f.fl_3ph_break_ka, f.fl_3ph_make_ka, f.fl_1ph_break_ka, f.rating_break_ka,
                             f.rating_make_ka, f.break_headroom_ka, f.make_headroom_ka,
                             q.queued_mw, q.connected_mw, q.other_mw, q.queued_projects, q.projects
                      FROM site s LEFT JOIN site_capacity c USING (site_id) LEFT JOIN site_fault f USING (site_id)
                      LEFT JOIN site_queue q USING (site_id) WHERE s.site_id = ?""", [site_id])
        if site is None:
            return JSONResponse({"error": "unknown site"}, status_code=404)
        parent = one("SELECT site_id, name, kind FROM site WHERE site_id = ?", [site["parent_site_id"]])
        chain = ancestors(site["parent_site_id"])
        children = rows(cur, """SELECT s.site_id, s.name, s.kind, s.lat, s.lon, c.gen_headroom_mw AS g,
                                       coalesce(c.demand_headroom_mw, c.demand_headroom_mva) AS d
                                FROM site s LEFT JOIN site_capacity c USING (site_id)
                                WHERE s.parent_site_id = ? ORDER BY s.name""", [site_id])
        assets = rows(cur, """SELECT a.asset_uid, a.name, a.kind, a.kv_hv, a.kv_lv, a.source_id, a.as_of
                              FROM asset a JOIN asset_site m USING (asset_uid) WHERE m.site_id = ?
                              ORDER BY a.kv_lv DESC NULLS LAST, a.name""", [site_id])
        uids = [a["asset_uid"] for a in assets]
        caps = {r["asset_uid"]: r for r in rows(cur, "SELECT * FROM capacity WHERE list_contains(?, asset_uid)", [uids])}
        for a in assets:
            c = caps.get(a["asset_uid"])
            a["capacity"] = {k: v for k, v in c.items() if k != "asset_uid"} if c else None
        faults = rows(cur, "SELECT * FROM fault_level WHERE list_contains(?, asset_uid) ORDER BY kv DESC", [uids])
        if st.index:
            key = (site["operator"], site_id.split(":", 1)[-1].split("~")[0])
            faults += st.index.faults.get(key, [])
        proj = rows(cur, """SELECT p.project_uid, p.operator, p.site_name, p.status, p.technology, p.export_mw,
                                   p.import_mw, p.storage_mw, p.connection_date, p.source_id
                            FROM queue_site qs JOIN queue_project p USING (project_uid) WHERE qs.site_id = ?
                            ORDER BY p.export_mw DESC NULLS LAST""", [site_id])
        by_status: dict[str, float] = {}
        by_tech: dict[str, float] = {}
        for p in proj:
            mw = p["export_mw"] or 0
            by_status[p["status"] or "other"] = by_status.get(p["status"] or "other", 0) + mw
            by_tech[p["technology"] or "unknown"] = by_tech.get(p["technology"] or "unknown", 0) + mw
        src_ids = {x for x in [site.get("capacity_source_id"), *[a["source_id"] for a in assets],
                               *[f.get("source_id") for f in faults], *[p["source_id"] for p in proj]] if x}
        sources = rows(cur, """SELECT source_id, operator, licence, attribution, as_of, fetched_at FROM (
                                 SELECT *, row_number() OVER (PARTITION BY source_id ORDER BY fetched_at DESC) rn
                                 FROM source_run WHERE status IN ('ok','unchanged') AND list_contains(?, source_id))
                               WHERE rn = 1 ORDER BY source_id""", [sorted(src_ids)])
        return {"site": site, "parent": parent, "chain": chain, "children": children, "assets": assets, "faults": faults,
                "queue": {"by_status": by_status, "by_tech": by_tech, "projects": proj[:50]}, "sources": sources}

    @app.get("/api/lv/{asset_uid:path}")
    def lv_detail(asset_uid: str):
        r = one("SELECT * FROM lv_site WHERE asset_uid = ?", [asset_uid])
        if r is None:
            return JSONResponse({"error": "unknown lv site"}, status_code=404)
        try:
            r["native_ids"] = json.loads(r["native_ids"]) if r.get("native_ids") else None
        except ValueError:
            pass
        up, how = None, None
        if st.index and r.get("upstream_primary"):
            up = st.index.primary_by_name.get((r["operator"], name_norm(r["upstream_primary"])))
            how = "published" if up else None
        if up is None and r.get("lat") is not None:
            # operators rarely publish secondary->primary links: fall back to the nearest primary (flagged inferred)
            near = one("""SELECT site_id FROM site WHERE operator = ? AND kind = 'PRIMARY' AND lat IS NOT NULL
                          AND abs(lat - ?) < 0.15 AND abs(lon - ?) < 0.25
                          ORDER BY (lat - ?)^2 + ((lon - ?) * cos(radians(?)))^2 LIMIT 1""",
                       [r["operator"], r["lat"], r["lon"], r["lat"], r["lon"], r["lat"]])
            if near:
                up, how = near["site_id"], "nearest"
        chain = ancestors(up)
        return {**r, "upstream_site_id": up, "upstream_how": how, "chain": chain}

    @app.get("/api/search")
    def search(q: str = "", limit: int = Query(20, ge=1, le=100)):
        hits = st.index.search(q, limit) if st.index else []
        pidx = places.get(st.data_dir / "derived" / "places.parquet")
        pl = pidx.search(q, limit) if pidx else []
        hits = hits[:limit - min(len(pl), 5)]  # substations first, but always leave room for a few places
        return hits + pl[:limit - len(hits)]

    @app.get("/api/sources")
    def sources():
        latest = {r["source_id"]: r for r in rows(st.cur(), """
            SELECT source_id, status, rows, as_of, fetched_at, portal_modified FROM (
              SELECT *, row_number() OVER (PARTITION BY source_id ORDER BY fetched_at DESC) rn FROM source_run)
            WHERE rn = 1""")}
        out = []
        for s in st.registry.values():
            r = latest.get(s.id, {})
            out.append({"id": s.id, "operator": s.operator, "category": s.category, "status": r.get("status"),
                        "rows": r.get("rows"), "as_of": r.get("as_of") or r.get("portal_modified"),
                        "fetched_at": r.get("fetched_at"), "cadence": s.cadence, "licence": s.licence,
                        "attribution": s.attribution})
        return out

    @app.get("/api/live/outages")
    def outages():
        return live.outages(st.raw)

    @app.get("/api/live/national")
    def national():
        return live.national(st.raw)


    cache: dict[str, tuple] = {}

    def versioned(key: str, fn):
        """Memoise fn() until the manifest version changes (i.e. after a data rebuild)."""
        ver = build.read_manifest(st.public).get("version")
        hit = cache.get(key)
        if hit and hit[0] == ver:
            return hit[1]
        val = fn()
        cache[key] = (ver, val)
        return val

    def compute_stats() -> dict:
        cur = st.cur()
        cov = rows(cur, """
            WITH cap AS (SELECT asset_uid, bool_or(gen_headroom_mw IS NOT NULL) g,
                                bool_or(demand_headroom_mw IS NOT NULL OR demand_headroom_mva IS NOT NULL) d,
                                bool_or(upstream_constraint IS NOT NULL) k,
                                bool_or(gen_rag_native IS NOT NULL OR demand_rag_native IS NOT NULL) r
                         FROM capacity GROUP BY 1),
                 flt AS (SELECT asset_uid, true f, bool_or(rb IS NOT NULL) sw FROM (
                           SELECT m.asset_uid, sf.rating_break_ka rb FROM asset_site m JOIN site_fault sf USING (site_id)
                           UNION ALL SELECT asset_uid, rating_break_ka FROM fault_level WHERE asset_uid IS NOT NULL)
                         GROUP BY 1)
            SELECT a.operator, count(*) busbars, count(a.lat) coords,
                   count(*) FILTER (WHERE cap.g) gen, count(*) FILTER (WHERE cap.d) demand,
                   count(*) FILTER (WHERE flt.f) fault, count(*) FILTER (WHERE flt.sw) switchgear,
                   count(*) FILTER (WHERE cap.k) constraint_data, count(*) FILTER (WHERE cap.r) rag
            FROM asset a LEFT JOIN cap USING (asset_uid) LEFT JOIN flt USING (asset_uid)
            GROUP BY 1 ORDER BY 2 DESC""")
        site_cov = {r["operator"]: r for r in rows(cur, """
            SELECT s.operator, count(*) sites, count(s.lat) coords,
                   count(c.gen_headroom_mw) gen,
                   count(*) FILTER (WHERE c.demand_headroom_mw IS NOT NULL OR c.demand_headroom_mva IS NOT NULL) demand,
                   count(f.site_id) fault
            FROM site s LEFT JOIN site_capacity c USING (site_id) LEFT JOIN site_fault f USING (site_id)
            GROUP BY 1""")}
        asset_ops = {r["operator"] for r in cov}

        def table_row(r: dict) -> dict:
            n, sc = r["busbars"], site_cov.get(r["operator"], {})
            return {"operator": r["operator"], "busbars": n, "sites": sc.get("sites", 0),
                    "coords_pct": _pct(r["coords"], n), "gen_pct": _pct(r["gen"], n),
                    "demand_pct": _pct(r["demand"], n), "fault_pct": _pct(r["fault"], n),
                    "switchgear_pct": _pct(r["switchgear"], n), "constraint_pct": _pct(r["constraint_data"], n),
                    "rag_pct": _pct(r["rag"], n), "site_coords_pct": _pct(sc.get("coords", 0), sc.get("sites", 0)),
                    "site_gen_pct": _pct(sc.get("gen", 0), sc.get("sites", 0)),
                    "site_demand_pct": _pct(sc.get("demand", 0), sc.get("sites", 0)),
                    "site_fault_pct": _pct(sc.get("fault", 0), sc.get("sites", 0))}

        dno = [table_row(r) for r in cov if r["operator"] in DNOS]
        to = [table_row(r) for r in cov if r["operator"] not in DNOS]
        tot = {k: sum(r[k] for r in cov) for k in cov[0] if k != "operator"} if cov else {}
        n = tot.get("busbars", 0)
        headline = {"sites": sum(r["sites"] for r in site_cov.values()), "busbars": n,
                    "coords_pct": _pct(tot.get("coords", 0), n), "gen_pct": _pct(tot.get("gen", 0), n),
                    "demand_pct": _pct(tot.get("demand", 0), n), "fault_pct": _pct(tot.get("fault", 0), n),
                    "switchgear_pct": _pct(tot.get("switchgear", 0), n),
                    "constraint_pct": _pct(tot.get("constraint_data", 0), n), "rag_pct": _pct(tot.get("rag", 0), n)}
        coord_quality = rows(cur, "SELECT coalesce(coord_quality, 'unknown') quality, count(*) n FROM asset "
                                  "GROUP BY 1 ORDER BY 2 DESC")
        gen_src = rows(cur, "SELECT source_id, count(*) n FROM capacity WHERE gen_headroom_mw IS NOT NULL "
                            "GROUP BY 1 ORDER BY 2 DESC")
        dem_src = rows(cur, "SELECT source_id, count(*) n FROM capacity WHERE demand_headroom_mw IS NOT NULL "
                            "OR demand_headroom_mva IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        lv = {"sites": 0, "coords_pct": None, "utilisation_pct": None, "by_operator": []}
        if _has(cur, "lv_site"):
            by = rows(cur, "SELECT operator, count(*) sites, count(lat) coords, count(utilisation_pct) util "
                           "FROM lv_site GROUP BY 1 ORDER BY 2 DESC")
            ls, lc, lu = (sum(r[k] for r in by) for k in ("sites", "coords", "util"))
            lv = {"sites": ls, "coords_pct": _pct(lc, ls), "utilisation_pct": _pct(lu, ls),
                  "by_operator": [{"operator": r["operator"], "sites": r["sites"], "coords_pct": _pct(r["coords"], r["sites"]),
                                   "utilisation_pct": _pct(r["util"], r["sites"])} for r in by]}
            headline["coords_pct_incl_lv"] = _pct(tot.get("coords", 0) + lc, n + ls)
        runs = rows(cur, """SELECT source_id, operator, category, status, rows, as_of, fetched_at FROM (
                              SELECT *, row_number() OVER (PARTITION BY source_id ORDER BY fetched_at DESC) rn
                              FROM source_run) WHERE rn = 1 ORDER BY operator, source_id""")
        fresh: dict[str, dict] = {}
        for r in runs:
            f = fresh.setdefault(r["operator"], {"operator": r["operator"], "sources": 0, "ok": 0,
                                                 "fetched_at": None, "as_of": None})
            f["sources"] += 1
            f["ok"] += r["status"] in ("ok", "unchanged")
            f["fetched_at"] = max(filter(None, [f["fetched_at"], r["fetched_at"]]), default=None)
            f["as_of"] = max(filter(None, [f["as_of"], r["as_of"]]), default=None)
        return {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "headline": headline, "dno": dno, "to": to,
                "note": "Busbars = asset rows (one per operator-published substation/voltage record), excluding LV. "
                        "Fault/switchgear are linked per asset via its matched site.",
                "coord_quality": coord_quality,
                "headroom_sources": {"generation": gen_src, "demand": dem_src}, "lv": lv,
                "freshness": {"by_operator": sorted(fresh.values(), key=lambda f: f["operator"] or ""), "sources": runs},
                "transmission_assets_held": sorted(asset_ops - set(DNOS))}

    @app.get("/api/stats")
    def stats():
        return versioned("stats", compute_stats)

    def compute_queue_sites() -> list[dict]:
        cur = st.cur()
        out: dict[str, dict] = {}
        for r in rows(cur, """SELECT s.site_id, s.name, s.operator, s.kind, s.kv_max, s.lat, s.lon, q.queued_mw,
                                     q.connected_mw, q.other_mw, q.projects
                              FROM site_queue q JOIN site s USING (site_id)"""):
            out[r["site_id"]] = {"site_id": r["site_id"], "name": r["name"], "op": r["operator"], "kind": r["kind"],
                                 "kv": r["kv_max"], "lat": r["lat"], "lon": r["lon"],
                                 "queued_mw": r["queued_mw"] or 0.0, "connected_mw": r["connected_mw"] or 0.0,
                                 "scoping_mw": r["other_mw"] or 0.0, "tec_queued_mw": 0.0, "tec_built_mw": 0.0,
                                 "projects": int(r["projects"] or 0)}
        tec = tec_by_site(st.data_dir)
        kv = dict(cur.execute("SELECT site_id, kv_max FROM site").fetchall()) if tec else {}
        for sid, t in tec.items():
            s = t["site"]
            o = out.setdefault(sid, {"site_id": sid, "name": s["name"], "op": s["operator"], "kind": s["kind"],
                                     "kv": kv.get(sid), "lat": s["lat"], "lon": s["lon"], "queued_mw": 0.0,
                                     "connected_mw": 0.0, "scoping_mw": 0.0, "tec_queued_mw": 0.0,
                                     "tec_built_mw": 0.0, "projects": 0})
            o["tec_queued_mw"] += t["pipeline"]
            o["tec_built_mw"] += t["built"]
            o["scoping_mw"] += t["scoping"]
            o["projects"] += t["projects"]
        return list(out.values())

    @app.get("/api/queue")
    def queue(op: str = "", limit: int = Query(100, ge=1, le=1000), scoping: bool = False):
        """Substations ranked by queued MW. queued = DNO accepted+offered + TEC (direct connection) consented /
        under construction; scoping MW (DNO 'other' + TEC Scoping) is excluded from the ranking unless scoping=true."""
        sites = versioned("queue_sites", compute_queue_sites)
        ranked = []
        for s in sites:
            if op and s["op"] != op:
                continue
            q = s["queued_mw"] + s["tec_queued_mw"] + (s["scoping_mw"] if scoping else 0.0)
            c = s["connected_mw"] + s["tec_built_mw"]
            if q > 0:
                ranked.append({**s, "total_queued_mw": round(q, 1), "total_connected_mw": round(c, 1)})
        ranked.sort(key=lambda s: -s["total_queued_mw"])

        def tot(lst):
            return {"queued_gw": round(sum(s["total_queued_mw"] for s in lst) / 1000, 2),
                    "connected_gw": round(sum(s["total_connected_mw"] for s in lst) / 1000, 2),
                    "projects": sum(s["projects"] for s in lst), "sites": len(lst)}

        return {"op": op or None, "limit": limit, "scoping": scoping, "totals": tot(ranked[:limit]),
                "totals_all": tot(ranked), "operators": sorted({s["op"] for s in sites if s["op"]}),
                "tec_included": any(s["tec_queued_mw"] or s["tec_built_mw"] for s in sites),
                "sites": [{k: (round(v, 1) if isinstance(v, float) and k.endswith("_mw") else v)
                           for k, v in s.items()} for s in ranked[:limit]]}

    app.mount("/data", CachedStatic(directory=st.public), name="data")
    from . import (
        static,  # production web build + /basemap + /api/setup; must stay LAST (its "/" mount is a catch-all)
    )
    static.install(app, st)
    return app


app = create_app()
