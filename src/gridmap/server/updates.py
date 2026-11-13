"""GET /api/updates, POST /api/updates/apply and the background update thread (see gridmap.updater). Wired from app.py.

Disabled in dev checkouts (git repo) unless GRIDMAP_UPDATES=1; the thread also needs the scheduler on (tests turn it off) unless GRIDMAP_UPDATES=1.
"""

from __future__ import annotations

import logging
import os
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .. import updater

log = logging.getLogger("gridmap.updates")


def install(app: FastAPI, st) -> None:
    up = updater.Updater()
    app.state.updater = up

    @app.get("/api/updates")
    def get_updates():
        return JSONResponse(up.status(), headers={"Cache-Control": "no-store"})

    @app.post("/api/updates/apply")
    def apply_update():
        if up.state != "ready_to_restart":
            return JSONResponse({"error": "no update is ready"}, status_code=409)
        threading.Timer(0.5, updater.restart).start()  # let this response go out first
        return {"restarting": True}

    if not (updater.enabled() and (st.scheduler or os.environ.get("GRIDMAP_UPDATES") == "1")):
        log.info("auto-update off (%s)", "dev checkout or no scheduler")
        return

    def loop() -> None:
        st.stop.wait(10)  # let the server finish starting
        while not st.stop.is_set():
            up.check()
            st.stop.wait(updater.INTERVAL_S)

    threading.Thread(target=loop, name="updater", daemon=True).start()
