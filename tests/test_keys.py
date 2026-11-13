import os
import re
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gridmap import keys_catalog as kc
from gridmap import settings
from gridmap.fetch.models import load_sources
from gridmap.server import keys


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# my keys\nOTHER=keep-me\nUKPN_API_KEY=oldoldoldold1234\n")
    monkeypatch.setattr(settings, "KEYS_FILE", f)
    for k in kc.ALL_ENVS:
        monkeypatch.delenv(k, raising=False)
    keys._tests.clear()
    return f


def client(env_file, host="127.0.0.1"):
    app = FastAPI()
    keys.install(app)
    return TestClient(app, client=(host, 50000), base_url="http://127.0.0.1:8787")


def test_catalog_covers_registry():
    used = {s.auth_env for s in load_sources() if s.auth_env}
    assert used <= set(kc.ALL_ENVS), used - set(kc.ALL_ENVS)
    assert {"NERDA_USERNAME", "NERDA_API_KEY", "OCM_API_KEY", "NIE_API_KEY", "SSENT_API_KEY"} <= set(kc.ALL_ENVS)
    assert len(kc.ALL_ENVS) == len(set(kc.ALL_ENVS))
    for p in kc.PROVIDERS:
        assert p.steps and p.unlocks and p.test["kind"] in keys.TESTS


def test_keys_md_in_sync():
    from pathlib import Path
    assert (Path(settings.REPO_ROOT) / "docs" / "KEYS.md").read_text() == kc.markdown()


def test_mask_never_leaks():
    assert keys.mask("abcdefghijkl") == "••••ijkl"
    assert keys.mask("short") == "" and keys.mask("") == "" and keys.mask(None) == ""
    assert keys.mask("x" * 11) == ""


def test_listing_hides_values(env_file):
    c = client(env_file)
    assert c.put("/api/keys/NPG_API_KEY", json={"value": "SECRETVALUE-abcd1234"}).status_code == 200
    r = c.get("/api/keys")
    assert r.status_code == 200
    assert "SECRETVALUE" not in r.text
    npg = next(k for k in r.json() if k["id"] == "NPG_API_KEY")
    assert npg["set"] and npg["masked"] == "••••1234" and npg["last_test"] is None


def test_put_writes_file(env_file):
    c = client(env_file)
    r = c.put("/api/keys/UKPN_API_KEY", json={"value": "  newvalue-0123456789  "})
    assert r.status_code == 200 and "newvalue" not in r.text
    lines = env_file.read_text().splitlines()
    assert lines == ["# my keys", "OTHER=keep-me", "UKPN_API_KEY=newvalue-0123456789"]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert os.environ["UKPN_API_KEY"] == "newvalue-0123456789"
    c.put("/api/keys/OCM_API_KEY", json={"value": "ocmvalue-0123456789"})
    assert env_file.read_text().splitlines()[-1] == "OCM_API_KEY=ocmvalue-0123456789"
    assert list(env_file.parent.glob(".env.tmp-*")) == []
    assert c.delete("/api/keys/UKPN_API_KEY").status_code == 200
    assert "UKPN" not in env_file.read_text() and "OTHER=keep-me" in env_file.read_text()
    assert "UKPN_API_KEY" not in os.environ


@pytest.mark.parametrize("bad", ["", "   ", "a b", "x\ny", 'q"q', "a#b", "$(x)", "z" * 5000])
def test_put_rejects_bad_values(env_file, bad):
    c = client(env_file)
    assert c.put("/api/keys/NPG_API_KEY", json={"value": bad}).status_code == 400
    assert "NPG" not in env_file.read_text()


def test_unknown_key(env_file):
    assert client(env_file).put("/api/keys/NOPE", json={"value": "abcdefghijkl"}).status_code == 404


def test_non_localhost_rejected(env_file):
    c = client(env_file, host="203.0.113.9")
    assert c.get("/api/keys").status_code == 403
    assert c.put("/api/keys/NPG_API_KEY", json={"value": "abcdefghijkl"}).status_code == 403
    assert c.post("/api/keys/NPG_API_KEY/test").status_code == 403
    assert c.delete("/api/keys/UKPN_API_KEY").status_code == 403
    assert "UKPN_API_KEY=oldoldoldold1234" in env_file.read_text()


def test_cross_origin_and_rebinding_rejected(env_file):
    c = client(env_file)
    r = c.put("/api/keys/NPG_API_KEY", json={"value": "abcdefghijkl"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.put("/api/keys/NPG_API_KEY", json={"value": "abcdefghijkl"}, headers={"Host": "evil.example"})
    assert r.status_code == 403
    ok = c.put("/api/keys/NPG_API_KEY", json={"value": "abcdefghijkl"}, headers={"Origin": "http://localhost:5173"})
    assert ok.status_code == 200


def test_test_endpoint_unset_and_scrubbed(env_file, monkeypatch):
    c = client(env_file)
    r = c.post("/api/keys/NGED_API_KEY/test")
    assert r.json()["ok"] is False and "No key" in r.json()["detail"]
    monkeypatch.setitem(keys.TESTS, "ckan", lambda p, e, k: (False, f"boom {k}"))
    c.put("/api/keys/NGED_API_KEY", json={"value": "tokentokentoken1"})
    r = c.post("/api/keys/NGED_API_KEY/test").json()
    assert "tokentokentoken1" not in str(r) and "[key]" in r["detail"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["at"])
    assert next(k for k in c.get("/api/keys").json() if k["id"] == "NGED_API_KEY")["last_test"]["ok"] is False
