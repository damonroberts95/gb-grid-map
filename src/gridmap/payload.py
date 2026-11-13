"""Payload store: the updatable part of the app (python package + built web UI), kept in <home>/payload/<sha>/.

Shell vs payload
  shell    the frozen .app: Python, third-party wheels, native tools, pywebview and packaging/launch.py.
           Changing any of that needs a new .app. The shell version is the SHELL_VERSION file; deps_hash covers
           uv.lock plus the native tool list.
  payload  src/gridmap/** and web/dist/** from a GitHub release (see docs/UPDATES.md). packaging/launch.py puts the
           newest healthy payload's src/ in front of the bundled gridmap before importing gridmap.desktop.

Layout:  <home>/payload/<sha>/{src/gridmap, web/dist, manifest.json}
         <home>/payload/current.json  {"good": [sha, ...] newest first, "pending": sha|null, "trying": bool, "bad": [sha]}
packaging/launch.py reads and writes current.json with the same rules (see select() there); keep them in step.

State machine: the updater stages a bundle, validates it and marks it `pending`. The next launch runs it with
`trying` set. Once the server answers /api/manifest the app calls confirm() and it becomes `good`. If the app dies or
stays unhealthy for 90 s, the next launch finds `trying` still set, marks the sha `bad` and falls back to the newest
good payload, or the bundled copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from . import settings

SHA_RE = re.compile(r"[0-9a-f]{7,40}")
KEEP_GOOD = 2
HEALTH_S = 90
ALLOWED_SUFFIX = {".py", ".yaml", ".json"}  # inside src/gridmap/
MAX_FILES = 5000
MAX_BYTES = 400_000_000
SECRET_RE = re.compile(r"(^|/)(\.env[^/]*|keys\.env)$")


class UpdateError(Exception):
    pass


def root() -> Path:
    return settings.HOME / "payload"


# --- identity of the running shell ---------------------------------------------------------------------------------

def shell_version() -> str:
    v = os.environ.get("GRIDMAP_SHELL_VERSION")
    if v is None:
        try:
            v = (settings.REPO_ROOT / "SHELL_VERSION").read_text()
        except OSError:
            v = "0"
    return v.strip() or "0"


def version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except ValueError as e:
        raise UpdateError(f"bad version {v!r}") from e


def compute_deps_hash(repo: Path) -> str:
    """sha256 of uv.lock plus the native tool list (packaging/native_tools.txt). Same value in CI and in the app build."""
    h = hashlib.sha256()
    h.update((repo / "uv.lock").read_bytes())
    h.update(b"\0")
    tools = repo / "packaging" / "native_tools.txt"
    h.update("\n".join(sorted(t.strip() for t in tools.read_text().splitlines() if t.strip())).encode()
             if tools.exists() else b"")
    return h.hexdigest()


def deps_hash() -> str | None:
    v = os.environ.get("GRIDMAP_DEPS_HASH")
    if v:
        return v
    try:  # dev checkout
        return compute_deps_hash(settings.REPO_ROOT)
    except OSError:
        return None


def active_sha() -> str | None:
    """Payload sha we are running from, else the commit the .app was built from (None in a dev checkout)."""
    return os.environ.get("GRIDMAP_PAYLOAD_SHA") or os.environ.get("GRIDMAP_BUILD_SHA") or None


# --- current.json ---------------------------------------------------------------------------------------------------

def read_state() -> dict:
    try:
        st = json.loads((root() / "current.json").read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("good", [])
    st.setdefault("pending", None)
    st.setdefault("trying", False)
    st.setdefault("bad", [])
    return st


def write_state(st: dict) -> None:
    root().mkdir(parents=True, exist_ok=True)
    tmp = root() / "current.json.tmp"
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(root() / "current.json")


def prune(st: dict) -> None:
    """Keep the last KEEP_GOOD good payloads and the pending one; delete every other directory."""
    import shutil
    st["good"] = st["good"][:KEEP_GOOD]
    keep = set(st["good"]) | ({st["pending"]} if st["pending"] else set())
    for d in root().iterdir() if root().exists() else []:
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def mark_pending(sha: str) -> None:
    st = read_state()
    old = st["pending"]
    st["pending"], st["trying"] = sha, False
    if sha in st["bad"]:
        st["bad"].remove(sha)
    write_state(st)
    if old and old != sha and old not in st["good"]:
        import shutil
        shutil.rmtree(root() / old, ignore_errors=True)


def confirm(sha: str) -> bool:
    """The pending payload answered healthily: promote it to good and drop old ones. True if it was promoted."""
    st = read_state()
    if st["pending"] != sha:
        return False
    st["good"] = [sha] + [g for g in st["good"] if g != sha]
    st["pending"], st["trying"] = None, False
    write_state(st)
    prune(st)
    write_state(st)
    return True


def pending_sha() -> str | None:
    st = read_state()
    return st["pending"] if st["pending"] and (root() / st["pending"] / "src").is_dir() else None


# --- bundles ----------------------------------------------------------------------------------------------------------

def bundle_member_ok(name: str) -> bool:
    """Allow-list for files in update-bundle.zip: package code/config, the built web app, manifest.json. No secrets."""
    if name.startswith("/") or ".." in name.split("/") or "\\" in name or SECRET_RE.search(name):
        return False
    if name == "manifest.json":
        return True
    if name.startswith("src/gridmap/"):
        return Path(name).suffix in ALLOWED_SUFFIX and "__pycache__" not in name
    return name.startswith("web/dist/")


def extract_bundle(zip_path: Path, dest: Path) -> dict:
    """Unzip into dest (must not exist), refusing anything outside the allow-list. Returns manifest.json."""
    with zipfile.ZipFile(zip_path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        bad = [i.filename for i in infos if not bundle_member_ok(i.filename)]
        if bad:
            raise UpdateError(f"bundle has files that are not allowed: {bad[0]}")
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_BYTES:
            raise UpdateError("bundle is too large")
        names = {i.filename for i in infos}
        if "manifest.json" not in names or "src/gridmap/__init__.py" not in names or "web/dist/index.html" not in names:
            raise UpdateError("bundle is incomplete")
        for i in infos:
            out = dest / i.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(i))
    return json.loads((dest / "manifest.json").read_text())


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
