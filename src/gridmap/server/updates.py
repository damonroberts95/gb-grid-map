"""GET /api/updates and the background config-update thread (see gridmap.updater). Wired from app.py.

Disabled in dev checkouts (git repo) unless GRIDMAP_UPDATES=1; also never started when the scheduler is off (tests).
"""

from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from .. import updater
from ..extra import reset_overlay
from ..fetch.models import load_sources

log = logging.getLogger("gridmap.updates")


def install(app: FastAPI, st) -> None:
    def activated() -> None:
        reset_overlay()
        try:
            st.registry = {s.id: s for s in load_sources()}
        except Exception:
            log.exception("registry reload failed after update")
        # the scheduler's next tick (<= 60 s) fetches new sources and rebuilds extras (refresh.py sees VERSION.json)

    up = updater.Updater(on_activated=activated)
    app.state.updater = up

    @app.get("/api/updates")
    def get_updates():
        return up.status()

    if not (st.scheduler and updater.enabled()):
        log.info("config auto-update off (%s)", "dev checkout or no scheduler")
        return

    def loop() -> None:
        st.stop.wait(10)  # let the server finish starting
        while not st.stop.is_set():
            up.check()
            st.stop.wait(updater.INTERVAL_S)

    threading.Thread(target=loop, name="config-updater", daemon=True).start()
