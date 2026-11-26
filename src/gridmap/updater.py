"""In-app updater: pulls the whole `gridmap` package + web UI from GitHub (see gridmap.payload and docs/UPDATES.md).

Every 6 h (and at start) ask GitHub for the `latest-update` release (anonymous, ETag so an unchanged release is a free
304). Its manifest.json says {sha, built_at, min_shell_version, deps_hash}.
  same sha as the running payload      -> up_to_date
  shell too old or deps_hash differs   -> needs_new_app (nothing is downloaded)
  otherwise download update-bundle.zip, check its sha256, unzip into <home>/payload/<sha>.tmp, validate it in a
  subprocess (imports the staged package, builds the FastAPI app without the scheduler, loads the registry, imports
  every extra), rename to <home>/payload/<sha> and mark it pending -> ready_to_restart.
A restart (POST /api/updates/apply) runs it. Dev checkouts (.git present) stay off unless GRIDMAP_UPDATES=1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from . import payload, settings
from .payload import UpdateError

log = logging.getLogger("gridmap.updater")
INTERVAL_S = 6 * 3600
UA = "gridmap-local/0.1"
MAX_ZIP = 300_000_000
ASSET_ZIP, ASSET_SHA, ASSET_MANIFEST = "update-bundle.zip", "update-bundle.zip.sha256", "manifest.json"


def release_url() -> str:
    return os.environ.get("GRIDMAP_UPDATE_URL") or f"https://api.github.com/repos/{settings.UPDATE_REPO}/releases/tags/latest-update"


def enabled() -> bool:
    v = os.environ.get("GRIDMAP_UPDATES")
    if v is not None:
        return v == "1"
    return not (settings.REPO_ROOT / ".git").exists()


# --- gating + validation (no network) -------------------------------------------------------------------------------

def check_manifest(m: dict) -> None:
    if not (isinstance(m, dict) and re.fullmatch(payload.SHA_RE, str(m.get("sha", "")))):
        raise UpdateError("update manifest has no valid sha")


def needs_new_app(m: dict) -> bool:
    """True when this shell cannot run the payload: shell too old, or third-party deps / native tools changed."""
    if payload.version_tuple(m.get("min_shell_version", "0")) > payload.version_tuple(payload.shell_version()):
        return True
    mine = payload.deps_hash()
    return bool(mine) and m.get("deps_hash") != mine


VALIDATE = """\
import sys
from pathlib import Path
STAGED = {staged!r}
try:
    use_payload(STAGED)  # frozen shell (packaging/launch.py): staged src in front of the bundled gridmap
except NameError:
    sys.path.insert(0, STAGED + "/src")
import gridmap
assert Path(gridmap.__file__).resolve().is_relative_to(Path(STAGED).resolve()), "staged package not first: " + gridmap.__file__
from gridmap.fetch.models import load_sources
from gridmap.server.app import create_app
from gridmap.extra import modules
create_app(Path({data!r}), scheduler=False)
assert load_sources()
for m in modules(strict=True):
    assert isinstance(m.LAYERS, list) and (not m.LAYERS or callable(m.build)), m.__name__
"""


def validate(staged: Path) -> None:
    """Raise UpdateError unless the staged payload imports and starts cleanly. Runs in a throw-away subprocess:
    in the frozen app sys.executable is the shell, whose launcher runs `-c <code>` instead of opening the GUI."""
    if not (staged / "web" / "dist" / "index.html").is_file():
        raise UpdateError("staged payload has no web UI")
    with tempfile.TemporaryDirectory(prefix="gridmap-validate-") as data:
        env = {**os.environ, "GRIDMAP_DATA_DIR": data, "GRIDMAP_NO_EXTRAS": "1", "GRIDMAP_NO_SCHEDULER": "1",
               "GRIDMAP_NO_BOOTSTRAP": "1", "GRIDMAP_UPDATES": "0"}
        env.pop("GRIDMAP_PAYLOAD_DIR", None)
        try:
            r = subprocess.run([sys.executable, "-I", "-c", VALIDATE.format(staged=str(staged), data=data)], check=False,
                               capture_output=True, text=True, timeout=180, env=env)
        except subprocess.TimeoutExpired as e:
            raise UpdateError("staged payload check timed out") from e
    if r.returncode:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1][:300]
        raise UpdateError(f"staged payload failed its check: {tail}")


def install(zip_path: Path, expect_sha256: str, sha: str) -> None:
    """Verify, unzip, validate and mark pending. Raises UpdateError and leaves nothing behind on failure."""
    got = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if got != expect_sha256.lower():
        raise UpdateError("download does not match its sha256; discarded")
    root = payload.root()
    tmp, final = root / f"{sha}.tmp", root / sha
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        m = payload.extract_bundle(zip_path, tmp)
        if m.get("sha") != sha:
            raise UpdateError("bundle manifest sha does not match the release")
        validate(tmp)
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    payload.mark_pending(sha)


# --- network ----------------------------------------------------------------------------------------------------------

class Updater:
    def __init__(self, client: httpx.Client | None = None, url: str | None = None):
        self.client = client or httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=True)
        self.url = url or release_url()
        self.state = "up_to_date"
        self.available_sha: str | None = None
        self.last_check: str | None = None
        self.last_error: str | None = None
        self._etag: str | None = None
        self._lock = threading.Lock()
        pend = payload.pending_sha()
        if pend and pend != payload.active_sha():
            self.state, self.available_sha = "ready_to_restart", pend

    def status(self) -> dict[str, Any]:
        return {"shell_version": payload.shell_version(), "active_sha": payload.active_sha(),
                "available_sha": self.available_sha, "state": self.state, "last_check": self.last_check,
                "last_error": self.last_error, "enabled": enabled()}

    def _get(self, url: str, **kw) -> httpx.Response:
        r = self.client.get(url, **kw)
        if r.status_code in (403, 429):
            raise UpdateError(f"GitHub rate limit (HTTP {r.status_code}); will retry later")
        if r.status_code not in (200, 304):
            raise UpdateError(f"HTTP {r.status_code} from {r.url.host}")
        return r

    def _release(self) -> dict[str, str] | None:
        """{asset name: download url}, or None when unchanged since the last ETag."""
        h = {"Accept": "application/vnd.github+json"}
        if self._etag:
            h["If-None-Match"] = self._etag
        r = self._get(self.url, headers=h)
        if r.status_code == 304:
            return None
        self._etag = r.headers.get("etag")
        return {a["name"]: a["browser_download_url"] for a in r.json().get("assets", [])}

    def check(self) -> bool:
        """One pass. Returns True when a new payload was staged. Never raises."""
        with self._lock:
            self.last_check = payload.now_iso()
            try:
                assets = self._release()
                if assets is None:
                    return False
                if ASSET_MANIFEST not in assets:
                    raise UpdateError("release has no manifest.json")
                m = self._get(assets[ASSET_MANIFEST]).json()
                check_manifest(m)
                sha = m["sha"]
                self.last_error = None
                st = payload.read_state()
                if sha == payload.active_sha() or payload.older_than_bundle(m):
                    # older_than_bundle: a local build ahead of GitHub. Staging it would only make the launcher
                    # discard it after a restart, and the "Update ready" banner would come back every check.
                    self.state, self.available_sha = "up_to_date", None
                    return False
                if needs_new_app(m):
                    self.state, self.available_sha = "needs_new_app", sha
                    return False
                if sha == st["pending"] and (payload.root() / sha).is_dir():
                    self.state, self.available_sha = "ready_to_restart", sha
                    return False
                if sha in st["bad"]:
                    self.state, self.available_sha = "up_to_date", None
                    self.last_error = f"update {sha[:7]} did not work on this Mac and was skipped"
                    return False
                if ASSET_ZIP not in assets or ASSET_SHA not in assets:
                    raise UpdateError("release is missing its bundle")
                self.state, self.available_sha = "downloading", sha
                want = self._get(assets[ASSET_SHA]).text.split()[0]
                with tempfile.TemporaryDirectory(prefix="gridmap-dl-") as d:
                    z = Path(d) / ASSET_ZIP
                    with self.client.stream("GET", assets[ASSET_ZIP]) as r:
                        if r.status_code != 200:
                            raise UpdateError(f"HTTP {r.status_code} downloading the bundle")
                        n = 0
                        with z.open("wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                n += len(chunk)
                                if n > MAX_ZIP:
                                    raise UpdateError("bundle is too large")
                                f.write(chunk)
                    install(z, want, sha)
                self.state = "ready_to_restart"
                log.info("update staged: %s", sha[:10])
                return True
            except (UpdateError, httpx.HTTPError, OSError, ValueError, KeyError, IndexError) as e:
                self.last_error = str(e) if isinstance(e, UpdateError) else f"{type(e).__name__}: {e}"
                if self.state == "downloading":
                    self.state, self.available_sha = "error", None
                elif self.state != "ready_to_restart":
                    self.state = "error"
                self._etag = None  # look again next time
                log.warning("update skipped: %s", self.last_error)
                return False


def restart() -> None:
    """Re-run the shell (packaging/launch.py picks the newest payload). Does not return."""
    cmd = json.loads(os.environ.get("GRIDMAP_SHELL_CMD", "null") or "null") or [sys.executable, *sys.argv]
    log.info("restarting: %s", cmd[0])
    logging.shutdown()
    os.execv(cmd[0], cmd)
