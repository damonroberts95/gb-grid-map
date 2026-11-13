"""SSEN NeRDA API client (verified 2026-11-12, see docs/sources/nerda.md and docs/AI_GOTCHAS.md).

Auth is a GET with a JSON body {"username", "apiKey"} (no auth header), so the generic fetcher cannot drive it:
the static snapshot is fetched here (`fetch_static`, called weekly by the server's NeRDA thread, or by hand with
`uv run python -I -m gridmap.extra.nerda_client --fetch-static`). Never log or print the credentials.

Also: polite rate limiting (<= NERDA_RPS, default 2 req/s, shared by all threads), retries, a compact index of the
133 MB static file, the ApiNerdaAfter parser, and API-key rotation (`--rotate`, key lasts 90 days).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..normalise.common import DATA_DIR

log = logging.getLogger("gridmap.nerda")
LAYERS: list = []  # helper module: no layers (the extras framework skips it)
SOURCES: list[str] = []
BASE = "https://nerda-prod-apis-v2.azurewebsites.net/api/"
SOURCE_ID = "ssen_nerda_static"
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
KEY_LIFETIME_DAYS, KEY_WARN_DAY = 90, 80
STATIC_MAX_AGE_DAYS = 7
KEEP_SNAPSHOTS = 2
UA = "gridmap-local/0.1"
RETRY_STATUS = {429, 500, 502, 503, 504}


def load_env(path: Path = ENV_PATH) -> None:
    from dotenv import load_dotenv
    load_dotenv(path)


def credentials() -> dict[str, str] | None:
    load_env()
    u, k = os.environ.get("NERDA_USERNAME"), os.environ.get("NERDA_API_KEY")
    return {"username": u, "apiKey": k} if u and k else None


class RateLimiter:
    """Thread-safe: at most `rps` calls per second across all threads."""

    def __init__(self, rps: float):
        self.gap = 1.0 / max(rps, 0.01)
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


_limiter: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(float(os.environ.get("NERDA_RPS", "2")))
    return _limiter


class NerdaError(Exception):
    pass


def _request(method: str, endpoint: str, params: dict | None = None, *, retries: int = 3, timeout: float = 60,
             backoff: float = 1.0) -> httpx.Response:
    creds = credentials()
    if creds is None:
        raise NerdaError("NERDA_USERNAME / NERDA_API_KEY not set")
    last: Exception | None = None
    for attempt in range(retries + 1):
        limiter().wait()
        try:
            r = httpx.request(method, BASE + endpoint, params=params, content=json.dumps(creds),
                              headers={"Content-Type": "application/json", "User-Agent": UA}, timeout=timeout)
            if r.status_code not in RETRY_STATUS:
                if r.status_code >= 400:
                    raise NerdaError(f"HTTP {r.status_code} for {endpoint}")
                return r
            last = NerdaError(f"HTTP {r.status_code} for {endpoint}")
        except httpx.TransportError as e:
            last = NerdaError(f"{type(e).__name__} for {endpoint}")
        time.sleep(backoff * 2 ** attempt)
    raise last or NerdaError(endpoint)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_after(measurement: str, after: datetime) -> dict:
    return _request("GET", "ApiNerdaAfter", {"measurement": measurement, "after": iso(after)}).json()


# ---- parsing -------------------------------------------------------------------------------------------------

def parse_after(doc: dict) -> dict[str, dict]:
    """ApiNerdaAfter reply -> {channel: {"v": latest value, "ts": ISO ts}} (channel = alias suffix after the feeder,
    e.g. 'i1', 'i1_max', 'v2_min'). Channels with an empty history are dropped."""
    out: dict[str, dict] = {}
    for a in doc.get("AnalogValues") or []:
        hist = [h for h in a.get("value_history") or [] if h.get("value") is not None]
        if not hist:
            continue
        last = max(hist, key=lambda h: h.get("__ts") or "")
        chan = (a.get("aliasName") or "").rsplit(".", 1)[-1]
        out[chan] = {"v": last["value"], "ts": last.get("__ts")}
    return out


def latest_value(doc: dict, prefer_max: bool = True) -> tuple[float, str] | None:
    """One representative reading from a reply: the largest latest value across the phase channels
    (i1/i2/i3 and their _max), neutral 'in' excluded for currents; for other measurements any channel."""
    ch = parse_after(doc)
    phase = {k: v for k, v in ch.items() if re.fullmatch(r"i[123](_max)?", k)} or {
        k: v for k, v in ch.items() if not k.startswith("in")} or ch
    if not phase:
        return None
    best = max(phase.values(), key=lambda x: x["v"]) if prefer_max else max(phase.values(), key=lambda x: x["ts"] or "")
    newest = max(x["ts"] or "" for x in phase.values())
    return float(best["v"]), newest


def dedupe_measurements(lines: list[dict]) -> list[dict]:
    """Static `lines[]`: a line can be listed twice, measurement ids repeat within a line and one id can appear under
    two types. Lines unique on name (uuid when unnamed), measurements unique on (id, type); order kept."""
    out: dict[str, dict] = {}
    for ln in lines:
        key = ln.get("line_name") or ln.get("nerda_line_uuid") or ""
        row = out.setdefault(key, {"name": ln.get("line_name"), "feeder": ln.get("feeder_name"),
                                   "uuid": ln.get("nerda_line_uuid"), "limit": ln.get("limit"), "m": []})
        seen = {(m["id"], m["type"]) for m in row["m"]}
        for m in ln.get("measurements") or []:
            k = (m.get("nerda_measurement_id"), m.get("measurementType"))
            if k in seen or not k[0]:
                continue
            seen.add(k)
            mult = "" if m.get("unitMultiplier") in (None, "none") else m["unitMultiplier"]
            sym = "" if m.get("unitSymbol") in (None, "none") else m["unitSymbol"]
            row["m"].append({"id": k[0], "type": k[1], "unit": mult + sym})
    return list(out.values())


NRN_RE = re.compile(r"^(\d+)_(\d+)_(\d+)_(\d+)$")


BSP_NAME = re.compile(r"\b(GRID|NGTE|BSP|GSP|SGT)\b", re.IGNORECASE)


def infer_kind(nrn: str, name: str | None = None, lines: list[dict] | None = None) -> str:
    """NRN '<primary>_<hv feeder>_<secondary>_<lv feeder>' (4010_001_040_02 = primary 4010, HV feeder 001, secondary
    040, LV feeder 02). Site-level NRNs: 4 digits only = primary substation (BSP when named like a grid/NGTE
    in-feed); full pattern with secondary > 0, or whose lines carry a secondary group = secondary; '' / '----' =
    other (monitoring points). Verified against the 2026-11-12 static file (3,814 secondary, 755 four-digit)."""
    if re.fullmatch(r"\d{4}", nrn or ""):
        return "bsp" if name and BSP_NAME.search(name) else "primary"
    m = NRN_RE.match(nrn or "")
    if not m:
        return "other"
    return "secondary" if int(m.group(3)) > 0 or any(_sec_line(ln) for ln in lines or []) else "other"


def _sec_line(ln: dict) -> re.Match | None:
    m = NRN_RE.match(ln.get("name") or "")
    return m if m and int(m.group(3)) > 0 else None


def lv_feeders(lines: list[dict]) -> list[dict]:
    """LV feeder lines of a secondary: names '<primary>_<hv>_<secondary>_NN' with secondary > 0 and NN >= 1."""
    return [ln for ln in lines if (m := _sec_line(ln)) and int(m.group(4)) >= 1]


def line_current_id(line: dict) -> str | None:
    return next((m["id"] for m in line["m"] if m["type"] == "LineCurrent"), None)


# ---- static snapshot -----------------------------------------------------------------------------------------

def raw_dir() -> Path:
    return DATA_DIR / "raw" / SOURCE_ID


def latest_static() -> Path | None:
    from ..normalise.common import latest_snapshot
    s = latest_snapshot(SOURCE_ID)
    return s / "static.json" if s else None


def static_age_days() -> float | None:
    s = latest_static()
    if s is None:
        return None
    meta = json.loads((s.parent / "meta.json").read_text())
    t = datetime.strptime(meta["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (datetime.now(UTC) - t).total_seconds() / 86400


def fetch_static(force: bool = False) -> Path | None:
    """Stream ApiNerdaStatic (all SEPD substations, ~133 MB, ~60 s) to raw/ssen_nerda_static/<ts>/static.json with
    meta.json (same shape as other snapshots); keep the newest KEEP_SNAPSHOTS. Skips if fresher than a week."""
    age = static_age_days()
    if not force and age is not None and age < STATIC_MAX_AGE_DAYS:
        return None
    creds = credentials()
    if creds is None:
        log.warning("nerda static: no credentials")
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snap = raw_dir() / stamp
    snap.mkdir(parents=True, exist_ok=True)
    dest = snap / "static.json"
    h, size, rows, tail = hashlib.sha256(), 0, 0, b""
    try:
        limiter().wait()
        with httpx.stream("GET", BASE + "ApiNerdaStatic", content=json.dumps(creds), timeout=httpx.Timeout(30, read=300),
                          headers={"Content-Type": "application/json", "User-Agent": UA}) as r:
            if r.status_code != 200:
                raise NerdaError(f"HTTP {r.status_code} for ApiNerdaStatic")
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
                    buf = tail + chunk
                    rows += buf.count(b'"sds_nrn"')
                    tail = buf[-8:]
        with dest.open('rb') as f:
            if f.read(1) != b'[':
                raise NerdaError('not a JSON list')
    except Exception as e:  # noqa: BLE001
        import shutil
        shutil.rmtree(snap, ignore_errors=True)
        log.warning("nerda static fetch failed: %s", type(e).__name__)
        return None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    (snap / "meta.json").write_text(json.dumps({
        "source_id": SOURCE_ID, "url": BASE + "ApiNerdaStatic", "status": "ok", "rows": rows, "expected_rows": None,
        "bytes": size, "sha256": h.hexdigest(), "portal_modified": None, "as_of": now, "fetched_at": now,
        "files": ["static.json"], "note": "fetched by extra.nerda_client (GET with JSON body)",
        "licence": "SSEN NeRDA terms and conditions (internal use)",
        "attribution": "Contains data from SSEN NeRDA (Network Rich Data Access)"}, indent=2))
    good = sorted((d for d in raw_dir().iterdir() if (d / "meta.json").exists()), reverse=True)
    import shutil
    for d in good[KEEP_SNAPSHOTS:]:
        shutil.rmtree(d, ignore_errors=True)
    log.info("nerda static: %d substations, %.0f MB", rows, size / 1e6)
    return dest


def index_path() -> Path:
    return DATA_DIR / "derived" / "nerda_index.json"


def load_index() -> dict | None:
    """Compact index of the newest static snapshot ({"snapshot", "sites": [...]}), rebuilt when the snapshot changes.
    The 133 MB file is parsed once per snapshot (~1 GB transient), never per request."""
    s = latest_static()
    if s is None:
        return None
    p = index_path()
    if p.exists():
        try:
            idx = json.loads(p.read_text())
            if idx.get("snapshot") == s.parent.name:
                return idx
        except ValueError:
            pass
    idx = build_index(json.loads(s.read_bytes()), s.parent.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, json.dumps(idx, separators=(",", ":")))
    return idx


def build_index(static: list[dict], snapshot: str = "") -> dict:
    sites = []
    for s in static:
        try:
            lat, lon = float(s.get("latitude")), float(s.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
        nrn = s.get("sds_nrn") or ""
        lines = dedupe_measurements(s.get("lines") or [])
        sites.append({"nrn": nrn, "n": s.get("sds_site_id"), "lat": lat, "lon": lon, "k": infer_kind(nrn, s.get("sds_site_id"), lines),
                      "nt": len(s.get("transformers") or []), "ns": len(s.get("switches") or []), "lines": lines})
    return {"snapshot": snapshot, "sites": sites}


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---- key expiry and rotation -----------------------------------------------------------------------------------

def key_info_path() -> Path:
    return DATA_DIR / "derived" / "nerda_key.json"


def key_age_days(today: datetime | None = None) -> float | None:
    """Days since the current key was created. The API does not report this, so it is the first time we saw this
    key (fingerprint in data/derived/nerda_key.json; resets when the key changes). An upper bound on freshness:
    a key issued earlier than first-seen expires earlier than first-seen + 90 d."""
    creds = credentials()
    if creds is None:
        return None
    fp = hashlib.sha256(creds["apiKey"].encode()).hexdigest()[:12]
    p, now = key_info_path(), today or datetime.now(UTC)
    info: dict[str, Any] = {}
    if p.exists():
        try:
            info = json.loads(p.read_text())
        except ValueError:
            info = {}
    if info.get("fp") != fp:
        info = {"fp": fp, "first_seen": now.isoformat()}
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(p, json.dumps(info))
    return (now - datetime.fromisoformat(info["first_seen"])).total_seconds() / 86400


def warn_if_key_old() -> None:
    age = key_age_days()
    if age is not None and age >= KEY_WARN_DAY:
        log.warning("NeRDA API key is ~%.0f days old (lifetime %d): run `uv run python -I -m gridmap.extra.nerda_client "
                    "--rotate`", age, KEY_LIFETIME_DAYS)


def rewrite_env(path: Path, name: str, value: str) -> None:
    """Replace NAME=... in a .env file in place (atomic, other lines kept verbatim); append when missing."""
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    out, done = [], False
    for ln in lines:
        if re.match(rf"\s*(export\s+)?{re.escape(name)}\s*=", ln):
            out.append(f"{name}={value}\n")
            done = True
        else:
            out.append(ln)
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f"{name}={value}\n")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(out))
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def extract_key(reply: httpx.Response) -> str | None:
    """GenerateApiKey reply: JSON with the key under apiKey/api_key/key, or a bare string."""
    try:
        d = reply.json()
    except ValueError:
        d = reply.text.strip()
    if isinstance(d, str):
        return d.strip('" \n') or None
    if isinstance(d, dict):
        for k, v in d.items():
            if k.lower().replace("_", "") in ("apikey", "key", "newapikey") and isinstance(v, str) and v:
                return v
    return None


def rotate(env_path: Path = ENV_PATH) -> bool:
    """POST GenerateApiKey (same body), write the new key to NERDA_API_KEY in .env. The old key stops working,
    so the file is only rewritten once a key was extracted from the reply. Never prints the key."""
    reply = _request("POST", "GenerateApiKey", retries=0)
    key = extract_key(reply)
    if not key:
        log.error("GenerateApiKey replied %d but no key could be extracted; .env left unchanged, reply NOT saved",
                  reply.status_code)
        return False
    rewrite_env(env_path, "NERDA_API_KEY", key)
    os.environ["NERDA_API_KEY"] = key
    key_age_days()  # records first-seen for the new key
    log.info("NeRDA API key rotated and written to %s", env_path.name)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description="SSEN NeRDA client")
    ap.add_argument("--rotate", action="store_true", help="rotate the API key and rewrite NERDA_API_KEY in .env")
    ap.add_argument("--fetch-static", action="store_true", help="fetch the static snapshot now (even if fresh)")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.rotate:
        return 0 if rotate() else 1
    if a.fetch_static:
        return 0 if fetch_static(force=True) else 1
    age = key_age_days()
    print(f"key age (since first seen): {age if age is None else round(age, 1)} d; static age: {static_age_days()} d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

