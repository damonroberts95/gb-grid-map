import json
import shutil
from pathlib import Path

import pytest
import yaml

from gridmap import extra, settings, updater
from gridmap.fetch import models
from gridmap.fetch.models import load_sources

GOOD_MOD = b"LAYERS = []\n\ndef build(ctx):\n    return {}\n"
MIN_SRC = {"id": "ovl_test", "operator": "OTHER", "portal": "http", "base_url": "https://example.org", "path": "/x",
           "category": "context", "cadence": "1d", "licence": "l", "attribution": "a"}


def bundled_yaml() -> bytes:
    return models.REGISTRY_PATH.read_bytes()


def files(**extra_files) -> dict[str, bytes]:
    return {"src/gridmap/sources.yaml": bundled_yaml(), **extra_files}


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    ov = tmp_path / "home" / "overlay"
    monkeypatch.setattr(settings, "OVERLAY_DIR", ov)
    yield ov
    extra.reset_overlay()  # drop the tmp overlay from the package path


def test_allow_list():
    ok = ["src/gridmap/sources.yaml", "src/gridmap/sources.d/new.yaml", "src/gridmap/extra/layer_x.py"]
    bad = ["src/gridmap/server/app.py", "src/gridmap/extra/__init__.py", "src/gridmap/extra/sub/x.py",
           "src/gridmap/sources.d/../server/app.py", "web/src/main.ts", "src/gridmap/extra/x.pyc",
           "src/gridmap/sources.d/x.yml", "../src/gridmap/sources.yaml", "src/gridmap/refresh.py"]
    assert all(updater.allowed(p) for p in ok)
    assert not any(updater.allowed(p) for p in bad)


def test_overlay_precedence_registry(overlay):
    base = {s.id for s in load_sources()}
    assert "ovl_test" not in base
    (overlay / "sources.d").mkdir(parents=True)
    (overlay / "sources.d" / "zz_new.yaml").write_text(yaml.dump({"sources": [MIN_SRC]}))
    assert {s.id for s in load_sources()} == base | {"ovl_test"}
    # same filename as a bundled file replaces it (bundled ni.yaml sources vanish)
    (overlay / "sources.d" / "ni.yaml").write_text(yaml.dump({"sources": []}))
    ids = {s.id for s in load_sources()}
    assert not any(i.startswith("nie_") for i in ids) and "ovl_test" in ids
    # an overlay sources.yaml replaces the bundled main registry
    (overlay / "sources.yaml").write_text(yaml.dump({"sources": [{**MIN_SRC, "id": "only_one"}]}))
    assert "only_one" in {s.id for s in load_sources()}


def test_overlay_precedence_extras(overlay):
    (overlay / "extra").mkdir(parents=True)
    (overlay / "extra" / "zz_overlay_only.py").write_bytes(GOOD_MOD)
    names = [m.__name__ for m in extra.modules()]
    assert "gridmap.extra.zz_overlay_only" in names and "gridmap.extra.dem_ev" in names
    assert Path(extra.modules()[-1].__file__).parent == overlay / "extra"
    # same name as a bundled module: overlay copy wins
    (overlay / "extra" / "dem_ev.py").write_bytes(b"LAYERS = []\nMARK = 1\n\ndef build(ctx):\n    return {}\n")
    (overlay / "VERSION.json").write_text("{}")  # signals a change: module cache is dropped
    mod = next(m for m in extra.modules() if m.__name__ == "gridmap.extra.dem_ev")
    assert getattr(mod, "MARK", None) == 1
    shutil.rmtree(overlay)
    extra.reset_overlay()
    mod = next(m for m in extra.modules() if m.__name__ == "gridmap.extra.dem_ev")
    assert not hasattr(mod, "MARK")


def test_apply_good_and_version(overlay):
    from datetime import UTC, datetime
    ok = updater.apply("a" * 40, files(**{"src/gridmap/sources.d/zz.yaml": yaml.dump({"sources": [MIN_SRC]}).encode(),
                                          "src/gridmap/extra/zz_ok.py": GOOD_MOD,
                                          "src/gridmap/server/app.py": b"evil = 1\n"}),
                       now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert ok
    assert (overlay / "extra" / "zz_ok.py").exists() and not (overlay / "server").exists()
    v = json.loads((overlay / "VERSION.json").read_text())
    assert v["sha"] == "a" * 40 and v["fetched_at"] == "2026-01-02T03:04:05Z"
    assert not [p for p in overlay.parent.iterdir() if p != overlay]  # staging dirs cleaned up


@pytest.mark.parametrize("name,content,msg", [
    ("src/gridmap/sources.d/bad.yaml", b"sources: [unclosed\n  - {", "invalid YAML"),
    ("src/gridmap/sources.d/bad.yaml", yaml.dump({"sources": [{**MIN_SRC, "bogus_key": 1}]}).encode(), "registry rejected"),
    ("src/gridmap/extra/zz_syntax.py", b"def build(:\n", "does not compile"),
    ("src/gridmap/extra/zz_boom.py", b"raise RuntimeError('boom at import')\n", "failed to import"),
])
def test_validation_rejects(overlay, name, content, msg):
    updater.apply("b" * 40, files(**{"src/gridmap/extra/zz_ok.py": GOOD_MOD}))  # a good overlay is already active
    before = (overlay / "VERSION.json").read_text()
    with pytest.raises(updater.UpdateError, match=msg):
        updater.apply("c" * 40, files(**{name: content}))
    assert (overlay / "VERSION.json").read_text() == before  # untouched
    assert (overlay / "extra" / "zz_ok.py").exists()


def test_disallowed_only_is_refused(overlay):
    with pytest.raises(updater.UpdateError):
        updater.apply("d" * 40, {"src/gridmap/server/app.py": b"x = 1\n"})
    assert not overlay.exists()


def test_dev_mode_default(monkeypatch):
    monkeypatch.delenv("GRIDMAP_UPDATES", raising=False)
    assert updater.enabled() is not (settings.REPO_ROOT / ".git").exists()
    monkeypatch.setenv("GRIDMAP_UPDATES", "1")
    assert updater.enabled()
    monkeypatch.setenv("GRIDMAP_UPDATES", "0")
    assert not updater.enabled()


def test_check_flow_with_mock_github(overlay):
    import httpx
    sha = "e" * 40
    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        hits.append(u)
        if u.endswith("/commits/main"):
            if req.headers.get("if-none-match") == '"t1"':
                return httpx.Response(304)
            return httpx.Response(200, text=sha, headers={"etag": '"t1"'})
        if "/contents/src/gridmap/sources.d" in u:
            return httpx.Response(200, json=[{"type": "file", "name": "zz.yaml"}, {"type": "dir", "name": "sub"}])
        if "/contents/src/gridmap/extra" in u:
            return httpx.Response(200, json=[{"type": "file", "name": "zz_ok.py"}, {"type": "file", "name": "__init__.py"}])
        if u.startswith(f"https://raw.githubusercontent.com/o/r/{sha}/"):
            p = u.split(f"{sha}/", 1)[1]
            return httpx.Response(200, content={"src/gridmap/sources.yaml": bundled_yaml(),
                                                "src/gridmap/sources.d/zz.yaml": yaml.dump({"sources": [MIN_SRC]}).encode(),
                                                "src/gridmap/extra/zz_ok.py": GOOD_MOD}[p])
        return httpx.Response(404)

    done = []
    up = updater.Updater("o/r", "main", httpx.Client(transport=httpx.MockTransport(handler)), on_activated=lambda: done.append(1))
    assert up.check() and done == [1]
    assert up.status()["current_sha"] == sha and up.last_error is None
    assert not any("__init__" in h for h in hits)
    assert "ovl_test" in {s.id for s in load_sources()}
    assert not up.check()  # 304: nothing to do
    assert up.last_check
