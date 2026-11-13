"""API key management for the Keys page: GET /api/keys, PUT/DELETE /api/keys/{id}, POST /api/keys/{id}/test,
GET /api/keys/guide (the plain-English steps from keys_catalog).

Security model (this writes a secrets file, so it is deliberately strict):
- only loopback clients (127.0.0.1 / ::1) are served; anyone else gets 403;
- the Host header must be a loopback name (blocks DNS rebinding) and, when an Origin header is present, its host must
  be loopback too (blocks other websites driving your browser; the Vite dev proxy at localhost:5173 is allowed);
- full values are never returned, logged or put in error messages: only a mask ("••••abcd", last 4 chars, and only
  when the value is at least 12 chars long) is exposed.
The file is KEYS_FILE (settings): rewritten atomically (temp file + rename), other lines kept, chmod 600. Values are
also loaded into os.environ so the next refresh uses them without a restart.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .. import keys_catalog as kc
from .. import settings
from ..fetch.models import load_sources

log = logging.getLogger("gridmap.keys")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
UA = "gridmap-local/0.1"
BAD_CHARS = set(" \t\r\n\"'\\#$`")
MAX_LEN = 4096
_lock = threading.Lock()
_tests: dict[str, dict] = {}  # provider id -> {ok, at, detail} (memory only)


def mask(value: str | None) -> str:
    """'••••abcd' for values of 12+ chars; nothing otherwise (a short value would leak too much of itself)."""
    return f"••••{value[-4:]}" if value and len(value) >= 12 else ""


def _host(h: str) -> str:
    h = h.strip()
    return h[1:h.index("]")] if h.startswith("[") else h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def guard(request: Request) -> None:
    """403 unless this is a same-machine request from the app's own pages."""
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        raise HTTPException(403, "keys can only be managed from this computer")
    if _host(request.headers.get("host", "")) not in LOOPBACK:
        raise HTTPException(403, "unexpected Host header")
    origin = request.headers.get("origin")
    if origin is not None and (urlsplit(origin).hostname or "") not in LOOPBACK:
        raise HTTPException(403, "cross-origin request refused")


# --- .env file -------------------------------------------------------------------------------------------------

def _name_of(line: str) -> str | None:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    return s.removeprefix("export ").split("=", 1)[0].strip()


def write_env(path: Path, name: str, value: str | None) -> None:
    """Set (or, with value=None, remove) NAME in the .env file; atomic, other lines untouched, mode 600."""
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for ln in lines:
        if _name_of(ln) == name:
            if value is not None and not done:
                out.append(f"{name}={value}")
            done = True
            continue
        out.append(ln)
    if value is not None and not done:
        out.append(f"{name}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(out) + ("\n" if out else ""))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- test calls ------------------------------------------------------------------------------------------------

def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[key]")
    return text


def _test_ods(p: kc.Provider, env: str, key: str) -> tuple[bool, str]:
    base = p.test["base_url"]
    h = {"Authorization": f"Apikey {key}", "User-Agent": UA}
    r = httpx.get(f"{base}/api/explore/v2.1/catalog/datasets", params={"limit": 1}, headers=h, timeout=20)
    if r.status_code in (401, 403):
        return False, f"The portal rejected this key (HTTP {r.status_code}). Check you copied the whole key."
    if r.status_code != 200:
        return False, f"The portal answered HTTP {r.status_code}; try again later."
    gated = [s for s in load_sources() if s.auth_env == env and s.gated and s.dataset][:3]
    if not gated:
        return True, "The portal accepted the key."
    ok = 0
    for s in gated:
        rr = httpx.get(f"{base}/api/explore/v2.1/catalog/datasets/{s.dataset}/records", params={"limit": 1},
                       headers=h, timeout=20)
        ok += rr.status_code == 200
    if ok:
        return True, f"The portal accepted the key; {ok} of {len(gated)} locked datasets checked are readable."
    return False, (f"The key is valid but none of the {len(gated)} locked datasets checked is readable yet. "
                   "Open a dataset on the portal and request access, then test again.")


def _test_ckan(p: kc.Provider, env: str, key: str) -> tuple[bool, str]:
    r = httpx.get(f"{p.test['base_url']}/api/3/action/dashboard_activity_list", params={"limit": 1},
                  headers={"Authorization": key, "User-Agent": UA}, timeout=20)
    if r.status_code == 200:
        return True, "The portal accepted the token."
    if r.status_code in (401, 403):
        return False, "The portal does not recognise this token. Use the one from the API Tokens tab."
    return False, f"The portal answered HTTP {r.status_code}; try again later."


def _test_nerda(p: kc.Provider, env: str, key: str) -> tuple[bool, str]:
    user, apikey = os.environ.get("NERDA_USERNAME"), os.environ.get("NERDA_API_KEY")
    if not (user and apikey):
        return False, "Both the username and the API key are needed."
    import json
    r = httpx.request("GET", p.test["base_url"] + "ApiNerdaAfter", params={"measurement": "0", "after": "2026-01-01T00:00:00.000Z"},
                      content=json.dumps({"username": user, "apiKey": apikey}),
                      headers={"Content-Type": "application/json", "User-Agent": UA}, timeout=30)
    if r.status_code == 200:
        return True, "NeRDA accepted the username and key."
    if r.status_code in (401, 403):
        return False, "NeRDA rejected the username or key (keys expire after 90 days)."
    return False, f"NeRDA answered HTTP {r.status_code}; try again later."


def _test_ocm(p: kc.Provider, env: str, key: str) -> tuple[bool, str]:
    r = httpx.get(p.test["base_url"], params={"maxresults": 1}, headers={"X-API-Key": key, "User-Agent": UA}, timeout=30)
    if r.status_code == 200:
        return True, "Open Charge Map accepted the key."
    if r.status_code in (401, 403):
        return False, "Open Charge Map rejected this key."
    return False, f"Open Charge Map answered HTTP {r.status_code}; try again later."


TESTS = {"ods": _test_ods, "ckan": _test_ckan, "nerda": _test_nerda, "ocm": _test_ocm}


def run_test(env: str) -> dict:
    p = kc.BY_ENV[env]
    secrets = [os.environ.get(k.env, "") for k in p.keys]
    if not os.environ.get(env):
        res = {"ok": False, "detail": "No key set yet."}
    else:
        try:
            ok, detail = TESTS[p.test["kind"]](p, env, os.environ[env])
            res = {"ok": ok, "detail": detail}
        except httpx.HTTPError as e:
            res = {"ok": False, "detail": f"Could not reach the service ({type(e).__name__}). Check your internet connection."}
        except Exception as e:  # noqa: BLE001 - never leak a key through an exception message
            log.warning("key test for %s failed: %s", p.id, type(e).__name__)
            res = {"ok": False, "detail": f"Test failed unexpectedly ({type(e).__name__})."}
    res = {"ok": res["ok"], "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "detail": _scrub(res["detail"], secrets)}
    _tests[p.id] = res
    return res


def listing() -> list[dict]:
    return [{"id": k.env, "provider": p.id, "provider_name": p.name, "label": k.label, "set": bool(os.environ.get(k.env)),
             "masked": mask(os.environ.get(k.env)), "last_test": _tests.get(p.id)}
            for p in kc.PROVIDERS for k in p.keys]


class Body(BaseModel):
    value: str


def install(app: FastAPI, st=None) -> None:
    settings.load_keys()

    def known(id: str) -> str:
        if id not in kc.BY_ENV:
            raise HTTPException(404, "unknown key")
        return id

    @app.get("/api/keys")
    def get_keys(request: Request):
        guard(request)
        return listing()

    @app.get("/api/keys/guide")
    def get_guide(request: Request):
        guard(request)
        return kc.to_json()

    @app.put("/api/keys/{id}")
    def put_key(id: str, body: Body, request: Request):
        guard(request)
        known(id)
        v = body.value.strip()
        if not v or len(v) > MAX_LEN or BAD_CHARS & set(v) or any(ord(c) < 32 for c in v):
            raise HTTPException(400, "that does not look like a key: it must be one unbroken piece of text without spaces or quotes")
        with _lock:
            write_env(settings.KEYS_FILE, id, v)
            os.environ[id] = v
        _tests.pop(kc.BY_ENV[id].id, None)
        log.info("key %s saved", id)
        return {"id": id, "set": True, "masked": mask(v)}

    @app.delete("/api/keys/{id}")
    def delete_key(id: str, request: Request):
        guard(request)
        known(id)
        with _lock:
            write_env(settings.KEYS_FILE, id, None)
            os.environ.pop(id, None)
        _tests.pop(kc.BY_ENV[id].id, None)
        log.info("key %s removed", id)
        return {"id": id, "set": False, "masked": ""}

    @app.post("/api/keys/{id}/test")
    def test_key(id: str, request: Request):
        guard(request)
        known(id)
        return run_test(id)
