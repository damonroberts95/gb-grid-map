"""Production serving + first-run setup status. Wired from app.py with one line: `static.install(app, st)`.

Serves, in the packaged app and in any run where the web build exists:
  /basemap/gb.pmtiles   <data>/basemap/gb.pmtiles with HTTP Range support (404 until the first-run download ends)
  /                     the built web app (index.html, stats.html, queue.html, assets/, vendored fonts + sprites)
  GET /api/setup        {stage, detail, pct, ready, stages[]} for the first-run setup screen
The /api and /data routes are registered earlier in create_app and win; this must be installed LAST (the "/"
mount is a catch-all). Dev mode (Vite on :5173, proxying /api and /data here) is unaffected.

Web build location: $GRIDMAP_WEB_DIST, else the PyInstaller bundle (<bundle>/web/dist), else <repo>/web/dist.
Env: GRIDMAP_NO_BOOTSTRAP=1 skips the first-run basemap download.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import bootstrap
from ..settings import REPO_ROOT

log = logging.getLogger("gridmap.setup")

# (id, label, weight in the overall percentage)
STAGES = [("basemap", "Downloading the GB map", 25), ("fetch", "Downloading grid data", 40),
          ("normalise", "Tidying the data", 15), ("match", "Matching substations", 10),
          ("build", "Building the map layers", 10)]


def web_dist() -> Path | None:
    cands = []
    if os.environ.get("GRIDMAP_WEB_DIST"):
        cands.append(Path(os.environ["GRIDMAP_WEB_DIST"]))
    if getattr(sys, "frozen", False):
        cands.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "web" / "dist")
    cands.append(REPO_ROOT / "web" / "dist")
    return next((c for c in cands if (c / "index.html").is_file()), None)


class WebStatic(StaticFiles):
    """Hashed assets are immutable; HTML pages must always be revalidated (they name the current hashes)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                                         else "no-cache")
        return resp


class Setup:
    """Tracks first-run progress. The basemap part is ours; the rest is read from the server's refresh stage."""

    def __init__(self, st: Any):
        self.st = st
        self.basemap_done = bootstrap.basemap_path(st.data_dir).exists()
        self.detail = ""
        self.pct: int | None = None
        self.error = ""
        self.refresh_started: str | None = None

    def on_basemap(self, detail: str, pct: int | None) -> None:
        self.detail, self.pct = detail, pct
        self.st.set_stage("basemap", detail)

    def ready(self) -> bool:
        st = self.st
        return (bootstrap.basemap_path(st.data_dir).exists() and (st.public / "manifest.json").exists()
                and st.index is not None)

    def fetch_fraction(self) -> float | None:
        if not self.refresh_started:
            return None
        try:
            from ..refresh import is_core
            first_run = not (self.st.public / "manifest.json").exists()
            total = sum(1 for s in self.st.registry.values() if not s.large and (is_core(s) or not first_run))
            n = self.st.cur().execute("SELECT count(DISTINCT source_id) FROM source_run WHERE fetched_at >= ?",
                                      [self.refresh_started]).fetchone()[0]
            return min(1.0, n / total) if total else None
        except Exception:  # noqa: BLE001 - table not there yet etc.
            return None

    def status(self) -> dict:
        if self.ready():
            return {"stage": "ready", "detail": "", "pct": 100, "ready": True,
                    "stages": [{"id": i, "label": lb, "state": "done", "pct": 100} for i, lb, _ in STAGES]}
        ids = [i for i, _, _ in STAGES]
        refresh_stage = self.st.stage.get("stage")
        if self.error and not self.basemap_done:
            cur, detail = "basemap", self.error
        elif not self.basemap_done:
            cur, detail = "basemap", self.detail
        elif refresh_stage in ids:
            cur, detail = refresh_stage, self.st.stage.get("detail", "")
        else:  # idle / starting up, or the first refresh failed (detail says so)
            cur, detail = "fetch", self.st.stage.get("detail", "") or "Starting"
        k = ids.index(cur)
        frac = (self.pct or 0) / 100 if cur == "basemap" else self.fetch_fraction() if cur == "fetch" else None
        stages = []
        for j, (i, lb, _) in enumerate(STAGES):
            state = "done" if j < k else "active" if j == k else "pending"
            stages.append({"id": i, "label": lb, "state": state,
                           "pct": 100 if j < k else (round(frac * 100) if j == k and frac is not None else None)})
        pct = sum(w for _, _, w in STAGES[:k]) + STAGES[k][2] * (frac or 0)
        out = {"stage": cur, "detail": detail, "pct": round(pct), "ready": False, "stages": stages}
        if self.error and not self.basemap_done:
            out["error"] = self.error
        return out


def _wrap_scheduler(st: Any, setup: Setup) -> None:
    """Run the first-run basemap download in the scheduler thread, before the first refresh."""
    orig = st.run_scheduler

    def run() -> None:
        while not st.stop.is_set() and not setup.basemap_done:
            try:
                bootstrap.ensure_basemap(setup.on_basemap, st.data_dir)
                setup.basemap_done, setup.error = True, ""
            except Exception as e:  # noqa: BLE001 - offline etc: show it, retry
                log.warning("basemap bootstrap failed: %s", e)
                setup.error = f"{e} (will retry)"
                st.stop.wait(30)
        setup.refresh_started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig()

    st.run_scheduler = run


def install(app: FastAPI, st: Any) -> None:
    setup = Setup(st)
    app.state.setup = setup
    if st.scheduler and os.environ.get("GRIDMAP_NO_BOOTSTRAP") != "1":
        _wrap_scheduler(st, setup)
    else:
        setup.basemap_done = True

    @app.get("/api/setup")
    def setup_status():
        return JSONResponse(setup.status(), headers={"Cache-Control": "no-store"})

    @app.api_route("/basemap/gb.pmtiles", methods=["GET", "HEAD"])
    def basemap_file():
        p = bootstrap.basemap_path(st.data_dir)
        if not p.exists():
            return Response(f"gb.pmtiles not found at {p}", status_code=404, media_type="text/plain")
        # FileResponse implements Range (206 / suffix ranges / 416) and Accept-Ranges
        return FileResponse(p, media_type="application/octet-stream", headers={"Cache-Control": "no-cache"})

    dist = web_dist()
    if dist is None:
        log.info("no web build found; serving API only (dev mode: use the Vite server)")
        return
    log.info("serving web app from %s", dist)
    app.mount("/", WebStatic(directory=dist, html=True), name="web")


__all__ = ["install", "web_dist"]
