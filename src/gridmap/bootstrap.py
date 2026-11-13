"""First-run bootstrap: things the app needs before the normal refresh can run (currently the GB basemap).

The basemap is a Protomaps extract of the GB bounding box, made by the bundled `pmtiles` binary straight from the
public daily planet build (range requests; only the GB tiles are downloaded, about 1.5 GB).
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .settings import DATA_DIR
from .tools import tool

log = logging.getLogger("gridmap.bootstrap")

BUILD_URL = "https://build.protomaps.com/{date}.pmtiles"
BBOX = "-8.7,49.8,1.9,60.9"
MAXZOOM = 14
ProgressCb = Callable[[str, int | None], None]  # (detail, pct 0-100 or None)
_PCT = re.compile(r"(\d{1,3})\s*%")


def basemap_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / "basemap" / "gb.pmtiles"


def resolve_build_url(days: int = 7, client: httpx.Client | None = None) -> str:
    """Newest daily Protomaps build that exists (HEAD, today back `days` days)."""
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        today = datetime.now(UTC).date()
        for back in range(days + 1):
            url = BUILD_URL.format(date=(today - timedelta(days=back)).strftime("%Y%m%d"))
            try:
                if client.head(url).status_code == 200:
                    return url
            except httpx.HTTPError as e:
                log.info("HEAD %s failed: %s", url, type(e).__name__)
        raise RuntimeError(f"no Protomaps build found in the last {days} days (offline?)")
    finally:
        if own:
            client.close()


def ensure_basemap(on_progress: ProgressCb | None = None, data_dir: Path | None = None) -> bool:
    """Download the GB basemap if missing. Returns True when it was downloaded now. Raises on failure."""
    say = on_progress or (lambda detail, pct: None)
    out = basemap_path(data_dir)
    if out.exists() and out.stat().st_size > 0:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    say("Finding the latest map build", 0)
    url = resolve_build_url()
    part = out.with_name(out.name + ".part")
    part.unlink(missing_ok=True)
    say("Downloading the GB map (about 1.5 GB)", 0)
    log.info("extracting basemap from %s", url)
    proc = subprocess.Popen([tool("pmtiles"), "extract", url, str(part), f"--bbox={BBOX}", f"--maxzoom={MAXZOOM}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    tail = ""
    last = -1
    assert proc.stdout is not None
    while chunk := proc.stdout.read(256):
        tail = (tail + chunk.decode("utf-8", "replace"))[-400:]
        hits = _PCT.findall(tail)
        if hits and int(hits[-1]) != last and "fetching chunks" in tail:
            last = min(100, int(hits[-1]))
            say("Downloading the GB map (about 1.5 GB)", last)
    if proc.wait() != 0 or not part.exists():
        part.unlink(missing_ok=True)
        msg = re.sub(r"\s+", " ", tail).strip()[-200:]
        raise RuntimeError(f"pmtiles extract failed: {msg}")
    part.replace(out)
    say("Map downloaded", 100)
    return True
