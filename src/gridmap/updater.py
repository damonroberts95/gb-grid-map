"""Config auto-update from GitHub: pulls `src/gridmap/sources.yaml`, `src/gridmap/sources.d/*.yaml` and
`src/gridmap/extra/*.py` (data-source registry + layer modules) from the public repo, never app/server/web code.

How it decides
  1. every 6 h (and at server start) ask the GitHub API for the branch head sha (anonymous, ETag / If-None-Match, so
     an unchanged repo is a free 304). Same sha as overlay/VERSION.json -> nothing to do.
  2. list the two directories at that sha, download only allow-listed files (see `allowed`) from raw.githubusercontent.com.
  3. stage them in a temp dir next to OVERLAY_DIR and validate: YAML parses, `load_sources()` succeeds with the staged
     overlay applied, every .py compiles, and importing all extras with the staged overlay works in a throw-away
     subprocess (a module that raises on import, or that needs a newer app, is rejected before it can hurt the app).
  4. atomic swap into OVERLAY_DIR and write overlay/VERSION.json {sha, fetched_at}. The map picks it up on the next
     refresh tick (<= 60 s): new sources fetch and extras rebuild (refresh.py compares VERSION.json's mtime).

Overlay rule (one rule, everywhere): a file in OVERLAY_DIR replaces the bundled file of the same name; bundled files
without an overlay twin stay. Implemented in fetch.models.load_sources and extra.modules(). A file deleted upstream
therefore keeps its bundled copy; deletions only take effect with a new app build.

Dev mode: from a git checkout (REPO_ROOT/.git exists) updating is OFF so it never fights local edits; set
GRIDMAP_UPDATES=1 to force on, GRIDMAP_UPDATES=0 to force off.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import settings
from .fetch.models import load_sources

log = logging.getLogger("gridmap.updater")
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "gridmap-local/0.1"
INTERVAL_S = 6 * 3600
MAX_FILE = 1_000_000
MAX_FILES = 200
_NAME = r"[A-Za-z0-9][A-Za-z0-9_\-]*"
PATTERNS = (re.compile(r"src/gridmap/sources\.yaml"),
            re.compile(rf"src/gridmap/sources\.d/{_NAME}\.yaml"),
            re.compile(rf"src/gridmap/extra/(?!__init__\.py){_NAME}\.py"))
LIST_DIRS = ("src/gridmap/sources.d", "src/gridmap/extra")


class UpdateError(Exception):
    pass


def allowed(path: str) -> bool:
    """Only the registry, the per-extra registries and extra layer modules (not extra/__init__.py, the framework)."""
    return ".." not in path and any(p.fullmatch(path) for p in PATTERNS)


def enabled() -> bool:
    v = os.environ.get("GRIDMAP_UPDATES")
    if v is not None:
        return v == "1"
    return not (settings.REPO_ROOT / ".git").exists()


def _rel(path: str) -> str:
    return path.removeprefix("src/gridmap/")


# --- validation + activation (no network) ------------------------------------------------------------------------

IMPORT_CHECK = (
    "from gridmap.extra import modules\n"
    "for m in modules():\n"
    "    assert isinstance(m.LAYERS, list) and (not m.LAYERS or callable(m.build)), m.__name__\n"
)


def validate(staged: Path) -> None:
    """Raise UpdateError unless the staged overlay is safe to activate."""
    for f in sorted(staged.rglob("*.yaml")):
        try:
            doc = yaml.safe_load(f.read_text())
        except yaml.YAMLError as e:
            raise UpdateError(f"{f.name}: invalid YAML ({type(e).__name__})") from e
        if f.name == "sources.yaml" and not (isinstance(doc, dict) and isinstance(doc.get("sources"), list)):
            raise UpdateError("sources.yaml: no `sources` list")
    try:
        load_sources(overlay=staged)
    except Exception as e:
        raise UpdateError(f"registry rejected: {type(e).__name__}: {e}") from e
    for f in sorted(staged.rglob("*.py")):
        try:
            compile(f.read_text(), str(f), "exec")
        except (SyntaxError, ValueError) as e:
            raise UpdateError(f"{f.name}: does not compile ({type(e).__name__}: {e})") from e
    try:
        r = subprocess.run([sys.executable, "-I", "-c", IMPORT_CHECK], check=False, capture_output=True, text=True, timeout=180,
                           env={**os.environ, "GRIDMAP_OVERLAY_DIR": str(staged), "GRIDMAP_NO_EXTRAS": "1"})
    except subprocess.TimeoutExpired as e:
        raise UpdateError("extras import check timed out") from e
    if r.returncode:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1][:300]
        raise UpdateError(f"extras failed to import: {tail}")


def apply(sha: str, files: dict[str, bytes], overlay: Path | None = None, now: datetime | None = None) -> bool:
    """Stage `files` ({repo path: bytes}; anything not allow-listed is dropped), validate, and swap into the
    overlay dir. Returns True when activated; raises UpdateError (overlay untouched) when invalid."""
    overlay = overlay or settings.OVERLAY_DIR
    files = {p: b for p, b in files.items() if allowed(p)}
    if "src/gridmap/sources.yaml" not in files and not any(p.startswith("src/gridmap/") for p in files):
        raise UpdateError("nothing to apply")
    overlay.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{overlay.name}.new-", dir=overlay.parent))
    old = overlay.parent / f".{overlay.name}.old"
    try:
        for p, b in files.items():
            dest = staged / _rel(p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b)
        validate(staged)
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (staged / "VERSION.json").write_text(json.dumps({"sha": sha, "fetched_at": stamp, "files": sorted(files)}, indent=1))
        shutil.rmtree(old, ignore_errors=True)
        if overlay.exists():
            overlay.rename(old)
        staged.rename(overlay)
        shutil.rmtree(old, ignore_errors=True)
        return True
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def current_version(overlay: Path | None = None) -> dict:
    try:
        return json.loads(((overlay or settings.OVERLAY_DIR) / "VERSION.json").read_text())
    except (OSError, ValueError):
        return {}


# --- network ----------------------------------------------------------------------------------------------------

class Updater:
    def __init__(self, repo: str | None = None, branch: str | None = None, client: httpx.Client | None = None,
                 on_activated: Callable[[], None] | None = None):
        self.repo = repo or settings.UPDATE_REPO
        self.branch = branch or settings.UPDATE_BRANCH
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
        self.on_activated = on_activated
        self.last_check: str | None = None
        self.last_error: str | None = None
        self._etag: str | None = None
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        v = current_version()
        return {"enabled": enabled(), "current_sha": v.get("sha"), "fetched_at": v.get("fetched_at"),
                "last_check": self.last_check, "last_error": self.last_error}

    def _head(self) -> str | None:
        """Branch head sha, or None when unchanged since the last ETag."""
        h = {"Accept": "application/vnd.github.sha"}
        if self._etag:
            h["If-None-Match"] = self._etag
        r = self.client.get(f"{API}/repos/{self.repo}/commits/{self.branch}", headers=h)
        if r.status_code == 304:
            return None
        if r.status_code in (403, 429):
            raise UpdateError(f"GitHub rate limit (HTTP {r.status_code}); will retry later")
        if r.status_code != 200:
            raise UpdateError(f"GitHub answered HTTP {r.status_code} for the branch head")
        sha = r.text.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise UpdateError("unexpected branch head reply")
        self._etag = r.headers.get("etag")
        return sha

    def _list(self, directory: str, sha: str) -> list[str]:
        r = self.client.get(f"{API}/repos/{self.repo}/contents/{directory}", params={"ref": sha})
        if r.status_code != 200:
            raise UpdateError(f"GitHub answered HTTP {r.status_code} listing {directory}")
        return [f"{directory}/{e['name']}" for e in r.json() if e.get("type") == "file"]

    def fetch_files(self, sha: str) -> dict[str, bytes]:
        paths = ["src/gridmap/sources.yaml"] + [p for d in LIST_DIRS for p in self._list(d, sha)]
        paths = [p for p in paths if allowed(p)]
        if len(paths) > MAX_FILES:
            raise UpdateError("too many files")
        out = {}
        for p in paths:
            r = self.client.get(f"{RAW}/{self.repo}/{sha}/{p}")
            if r.status_code != 200 or len(r.content) > MAX_FILE:
                raise UpdateError(f"could not fetch {p} (HTTP {r.status_code})")
            out[p] = r.content
        return out

    def check(self) -> bool:
        """One update pass. Returns True if a new overlay was activated. Never raises."""
        with self._lock:
            self.last_check = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                sha = self._head()
                if sha is None or (sha == current_version().get("sha") and settings.OVERLAY_DIR.exists()):
                    self.last_error = None
                    return False
                apply(sha, self.fetch_files(sha))
            except (UpdateError, httpx.HTTPError, OSError, ValueError) as e:
                self.last_error = f"{type(e).__name__}: {e}" if not isinstance(e, UpdateError) else str(e)
                self._etag = None  # re-check fully next time
                log.warning("config update skipped: %s", self.last_error)
                return False
            self.last_error = None
            log.info("config update activated: %s @ %s", self.repo, sha[:10])
            if self.on_activated:
                self.on_activated()
            return True
