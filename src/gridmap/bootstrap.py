"""First-run bootstrap: things the app needs before the normal refresh can run (currently the basemap).

The basemap is a Protomaps extract of the GB + Ireland bounding box, made by the bundled `pmtiles` binary straight
from the public daily planet build (range requests; only the tiles inside BBOX are downloaded, about 1.7 GB).
The bbox used is written next to the file (gb.pmtiles.bbox). When BBOX changes, an existing install re-extracts
once in the background (upgrade_basemap) and swaps the new file in; the old map keeps working meanwhile.
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
BBOX = "-11.0,49.8,1.9,60.9"  # GB + Ireland (was -8.7: GB only, until 2026-11-27)
MAXZOOM = 14
ProgressCb = Callable[[str, int | None], None]  # (detail, pct 0-100 or None)
_PCT = re.compile(r"(\d{1,3})\s*%")


def basemap_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / "basemap" / "gb.pmtiles"


def bbox_marker(data_dir: Path | None = None) -> Path:
    return basemap_path(data_dir).with_name("gb.pmtiles.bbox")


def basemap_outdated(data_dir: Path | None = None) -> bool:
    """True when a basemap exists but was extracted with another bbox (or before the marker existed)."""
    if not basemap_path(data_dir).exists():
        return False
    m = bbox_marker(data_dir)
    return not m.exists() or m.read_text().strip() != BBOX


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


def ensure_basemap(on_progress: ProgressCb | None = None, data_dir: Path | None = None, force: bool = False) -> bool:
    """Download the basemap if missing (or always, with force). Returns True when it was downloaded now.
    Raises on failure. Extracts to a .part file and swaps it in, so a current map is never broken."""
    say = on_progress or (lambda detail, pct: None)
    out = basemap_path(data_dir)
    if not force and out.exists() and out.stat().st_size > 0:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    say("Finding the latest map build", 0)
    url = resolve_build_url()
    part = out.with_name(out.name + ".part")
    part.unlink(missing_ok=True)
    say("Downloading the map (about 1.7 GB)", 0)
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
            say("Downloading the map (about 1.7 GB)", last)
    if proc.wait() != 0 or not part.exists():
        part.unlink(missing_ok=True)
        msg = re.sub(r"\s+", " ", tail).strip()[-200:]
        raise RuntimeError(f"pmtiles extract failed: {msg}")
    part.replace(out)
    bbox_marker(data_dir).write_text(BBOX)
    say("Map downloaded", 100)
    return True


def upgrade_basemap(data_dir: Path | None = None) -> bool:
    """Re-extract an outdated basemap (bbox changed). Quiet; the old file serves until the swap. True if replaced."""
    if not basemap_outdated(data_dir):
        return False
    log.info("basemap bbox changed to %s: re-extracting in the background", BBOX)
    return ensure_basemap(None, data_dir, force=True)
