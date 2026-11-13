"""Build update-bundle.zip (+ .sha256 + manifest.json) for the in-app updater. Used by CI and tests.

    uv run python scripts/make_bundle.py --sha <40 hex> --out dist/update        # build
    uv run python scripts/make_bundle.py --check dist/update/update-bundle.zip   # fail if the zip holds secrets / strays
    uv run python scripts/make_bundle.py --deps-hash                             # print the deps hash (app build)

The zip holds src/gridmap/** (.py .yaml .json), web/dist/** and manifest.json {sha, built_at, min_shell_version,
deps_hash}. Never .env*, keys.env or data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gridmap import payload


def _commit_time(root: Path, sha: str) -> str | None:
    """ISO commit time of `sha` (the launcher compares it with the bundled build's commit time)."""
    import subprocess
    r = subprocess.run(["git", "-C", str(root), "show", "-s", "--format=%cI", sha], capture_output=True, text=True,
                       check=False)
    return r.stdout.strip() or None


def build(root: Path, out: Path, sha: str, built_at: str | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for base in (root / "src" / "gridmap", root / "web" / "dist"):
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and not p.name.startswith(".DS_Store") and "__pycache__" not in rel and "/node_modules/" not in rel:
                if payload.bundle_member_ok(rel):
                    files[rel] = p
                elif payload.SECRET_RE.search(rel):
                    raise SystemExit(f"refusing to bundle secret-like file: {rel}")
                # other file types (e.g. .pyc, py.typed) are simply left out
    manifest = {"sha": sha, "built_at": built_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "min_shell_version": (root / "SHELL_VERSION").read_text().strip(), "deps_hash": payload.compute_deps_hash(root),
                "commit_time": _commit_time(root, sha)}
    zpath = out / "update-bundle.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for rel, p in files.items():
            z.write(p, rel)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (out / "update-bundle.zip.sha256").write_text(f"{hashlib.sha256(zpath.read_bytes()).hexdigest()}  update-bundle.zip\n")
    check(zpath)
    return zpath


def check(zpath: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        bad = [n for n in z.namelist() if payload.SECRET_RE.search(n) or (not n.endswith("/") and not payload.bundle_member_ok(n))]
    if bad:
        raise SystemExit(f"bundle has forbidden files: {bad[:5]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha")
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "update")
    ap.add_argument("--check", type=Path)
    ap.add_argument("--deps-hash", action="store_true")
    a = ap.parse_args()
    if a.deps_hash:
        print(payload.compute_deps_hash(ROOT))
    elif a.check:
        check(a.check)
        print("bundle clean")
    elif a.sha:
        print(build(ROOT, a.out, a.sha))
    else:
        ap.error("need --sha, --check or --deps-hash")
