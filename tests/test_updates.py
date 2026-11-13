"""Update payloads: gating, checksum, validation, rollback and bundle contents. No network (httpx MockTransport)."""
import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest

from gridmap import payload, settings, updater

ROOT = Path(__file__).resolve().parents[1]
SHA1, SHA2, SHA3 = "a" * 40, "b" * 40, "c" * 40


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


make_bundle = load("scripts/make_bundle.py", "make_bundle")
launch = load("packaging/launch.py", "launch_shell")


@pytest.fixture(scope="session")
def tree(tmp_path_factory) -> Path:
    """A minimal checkout: the real package, a fake web build, SHELL_VERSION, uv.lock, native tool list."""
    t = tmp_path_factory.mktemp("tree")
    shutil.copytree(ROOT / "src" / "gridmap", t / "src" / "gridmap", ignore=shutil.ignore_patterns("__pycache__"))
    (t / "web" / "dist").mkdir(parents=True)
    (t / "web" / "dist" / "index.html").write_text("<html></html>")
    (t / "packaging").mkdir()
    (t / "packaging" / "native_tools.txt").write_text("tippecanoe\npmtiles\n")
    shutil.copy(ROOT / "uv.lock", t / "uv.lock")
    (t / "SHELL_VERSION").write_text("1\n")
    return t


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HOME", tmp_path / "home")
    for k in ("GRIDMAP_PAYLOAD_SHA", "GRIDMAP_BUILD_SHA", "GRIDMAP_DEPS_HASH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GRIDMAP_SHELL_VERSION", "1")
    monkeypatch.setenv("GRIDMAP_DEPS_HASH", payload.compute_deps_hash(ROOT))
    monkeypatch.setattr(launch, "HOME", tmp_path / "home")
    return tmp_path / "home"


def bundle(tree: Path, tmp: Path, sha: str, extra_files: dict[str, str] | None = None) -> Path:
    t = tmp / f"tree-{sha[:4]}"
    shutil.copytree(tree, t)
    for rel, text in (extra_files or {}).items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(text)
    return make_bundle.build(t, tmp / f"out-{sha[:4]}", sha)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --- gating ---------------------------------------------------------------------------------------------------------

def test_version_gating(home, monkeypatch):
    dh = payload.deps_hash()
    assert not updater.needs_new_app({"min_shell_version": "1", "deps_hash": dh})
    assert updater.needs_new_app({"min_shell_version": "2", "deps_hash": dh})
    assert updater.needs_new_app({"min_shell_version": "1", "deps_hash": "other"})
    monkeypatch.setenv("GRIDMAP_SHELL_VERSION", "1.10")
    assert not updater.needs_new_app({"min_shell_version": "1.9", "deps_hash": dh})


def test_deps_hash_depends_on_lock_and_tools(tree, tmp_path):
    t = tmp_path / "t"
    shutil.copytree(tree, t)
    base = payload.compute_deps_hash(t)
    (t / "packaging" / "native_tools.txt").write_text("tippecanoe\npmtiles\nnewtool\n")
    assert payload.compute_deps_hash(t) != base


# --- checksum + validation ----------------------------------------------------------------------------------------

def test_sha_mismatch_rejected(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA1)
    with pytest.raises(payload.UpdateError, match="sha256"):
        updater.install(z, "0" * 64, SHA1)
    assert not (payload.root() / SHA1).exists() and payload.read_state()["pending"] is None


def test_good_bundle_installs_pending(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA1)
    updater.install(z, sha256(z), SHA1)
    assert (payload.root() / SHA1 / "src" / "gridmap" / "__init__.py").is_file()
    assert payload.read_state()["pending"] == SHA1
    assert not list(payload.root().glob("*.tmp"))


def test_staged_payload_that_fails_import_rejected(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA1, {"src/gridmap/extra/zz_broken.py": "raise RuntimeError('boom')\n"})
    with pytest.raises(payload.UpdateError, match="failed its check"):
        updater.install(z, sha256(z), SHA1)
    assert not (payload.root() / SHA1).exists() and not list(payload.root().glob("*.tmp"))
    assert payload.read_state()["pending"] is None


def test_unsafe_zip_rejected(home, tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../evil.py", "x")
    with pytest.raises(payload.UpdateError, match="not allowed"):
        payload.extract_bundle(z, tmp_path / "out")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("src/gridmap/keys.env", "KEY=1")
    with pytest.raises(payload.UpdateError, match="not allowed"):
        payload.extract_bundle(z, tmp_path / "out")


# --- the Updater against a fake release ---------------------------------------------------------------------------

def fake_github(z: Path, zip_sha: str | None = None, manifest_over: dict | None = None):
    man = {**json.loads(zipfile.ZipFile(z).read("manifest.json")), **(manifest_over or {})}
    sums = (zip_sha or sha256(z)) + "  update-bundle.zip\n"
    base = "https://dl.test/"

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if u.endswith("/latest-update"):
            if req.headers.get("if-none-match") == '"e1"':
                return httpx.Response(304)
            names = ["manifest.json", "update-bundle.zip", "update-bundle.zip.sha256"]
            return httpx.Response(200, headers={"etag": '"e1"'},
                                  json={"assets": [{"name": n, "browser_download_url": base + n} for n in names]})
        if u == base + "manifest.json":
            return httpx.Response(200, json=man)
        if u == base + "update-bundle.zip":
            return httpx.Response(200, content=z.read_bytes())
        if u == base + "update-bundle.zip.sha256":
            return httpx.Response(200, text=sums)
        return httpx.Response(404)

    return updater.Updater(client=httpx.Client(transport=httpx.MockTransport(handler)), url="https://api.test/latest-update")


def test_updater_downloads_validates_and_marks_ready(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA2)
    up = fake_github(z)
    assert up.check() is True
    s = up.status()
    assert s["state"] == "ready_to_restart" and s["available_sha"] == SHA2 and s["last_error"] is None
    assert payload.read_state()["pending"] == SHA2
    assert up.check() is False  # 304


def test_updater_needs_new_app(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA2)
    up = fake_github(z, manifest_over={"min_shell_version": "9"})
    assert up.check() is False
    assert up.status()["state"] == "needs_new_app"
    assert not payload.root().exists() or not (payload.root() / SHA2).exists()


def test_updater_rejects_bad_checksum(home, tree, tmp_path):
    z = bundle(tree, tmp_path, SHA2)
    up = fake_github(z, zip_sha="f" * 64)
    assert up.check() is False
    assert up.status()["state"] == "error" and "sha256" in up.status()["last_error"]
    assert not (payload.root() / SHA2).exists()


def test_up_to_date_and_known_bad_skipped(home, tree, tmp_path, monkeypatch):
    z = bundle(tree, tmp_path, SHA2)
    monkeypatch.setenv("GRIDMAP_PAYLOAD_SHA", SHA2)
    up = fake_github(z)
    assert up.check() is False and up.status()["state"] == "up_to_date"
    monkeypatch.delenv("GRIDMAP_PAYLOAD_SHA")
    payload.write_state({"good": [], "pending": None, "trying": False, "bad": [SHA2]})
    up = fake_github(z)
    assert up.check() is False and up.status()["state"] == "up_to_date" and "skipped" in up.status()["last_error"]


# --- shell: selection, rollback, pruning -------------------------------------------------------------------------

def make_payload(sha: str):
    d = payload.root() / sha / "src" / "gridmap"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("")


def test_rollback_picks_previous_good(home):
    make_payload(SHA1)
    make_payload(SHA2)
    payload.write_state({"good": [SHA1], "pending": SHA2, "trying": False, "bad": []})
    assert launch.select() == SHA2  # first launch tries the pending payload
    assert payload.read_state()["trying"] is True
    assert launch.select() == SHA1  # it never became healthy: marked bad, previous good wins
    st = payload.read_state()
    assert st["bad"] == [SHA2] and st["pending"] is None
    assert launch.select() == SHA1


def test_rollback_to_bundled_when_nothing_good(home):
    make_payload(SHA2)
    payload.write_state({"good": [], "pending": SHA2, "trying": True, "bad": []})
    assert launch.select() is None


def test_missing_pending_dir_falls_back(home):
    make_payload(SHA1)
    payload.write_state({"good": [SHA1], "pending": SHA3, "trying": False, "bad": []})
    assert launch.select() == SHA1


def test_confirm_promotes_and_keeps_two_good(home):
    for s in (SHA1, SHA2, SHA3):
        make_payload(s)
    payload.write_state({"good": [SHA2, SHA1], "pending": SHA3, "trying": True, "bad": []})
    assert payload.confirm(SHA3)
    st = payload.read_state()
    assert st["good"] == [SHA3, SHA2] and st["pending"] is None
    assert not (payload.root() / SHA1).exists() and (payload.root() / SHA2).exists()


def test_import_failure_marks_bad(home):
    make_payload(SHA1)
    payload.write_state({"good": [SHA1], "pending": None, "trying": False, "bad": []})
    launch.mark_bad(SHA1)
    assert launch.select() is None


# --- bundle contents -------------------------------------------------------------------------------------------

def test_bundle_has_expected_files_and_manifest(tree, tmp_path):
    z = bundle(tree, tmp_path, SHA1)
    names = zipfile.ZipFile(z).namelist()
    assert "manifest.json" in names and "web/dist/index.html" in names and "src/gridmap/sources.yaml" in names
    assert all(n == "manifest.json" or n.startswith(("src/gridmap/", "web/dist/")) for n in names)
    m = json.loads(zipfile.ZipFile(z).read("manifest.json"))
    assert set(m) == {"sha", "built_at", "min_shell_version", "deps_hash", "commit_time"} and m["min_shell_version"] == "1"
    assert m["deps_hash"] == payload.compute_deps_hash(tree)


def test_bundle_excludes_secrets(tree, tmp_path):
    clean = bundle(tree, tmp_path, SHA1, {"data/x.csv": "a", ".env": "KEY=1", "packaging/keys.env": "KEY=1"})
    names = zipfile.ZipFile(clean).namelist()
    assert not any(payload.SECRET_RE.search(n) for n in names) and not any(n.startswith("data/") for n in names)
    with pytest.raises(SystemExit):  # a secret-like file inside the package is refused outright
        bundle(tree, tmp_path, SHA2, {"src/gridmap/keys.env": "KEY=1"})
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr(".env.local", "KEY=1")
    with pytest.raises(SystemExit):
        make_bundle.check(bad)


def test_older_payload_never_beats_newer_bundle(tmp_path, monkeypatch):
    import json as _j
    home = tmp_path / "home"
    monkeypatch.setattr(launch, "HOME", home)
    for sha, t in (("old", "2026-11-13T10:00:00+00:00"), ("new", "2026-11-13T12:00:00+00:00")):
        d = home / "payload" / sha
        (d / "src" / "gridmap").mkdir(parents=True)
        (d / "src" / "gridmap" / "__init__.py").write_text("")
        (d / "manifest.json").write_text(_j.dumps({"sha": sha, "commit_time": t}))
    monkeypatch.setattr(launch, "read_text",
                        lambda n: _j.dumps({"sha": "bundle", "commit_time": "2026-11-13T11:00:00+00:00"}))
    assert not launch.newer_than_bundle("old")   # older code than the app: use the bundled copy
    assert launch.newer_than_bundle("new")
    monkeypatch.setattr(launch, "read_text", lambda n: _j.dumps({"sha": "new"}))
    assert not launch.newer_than_bundle("new")   # identical code
