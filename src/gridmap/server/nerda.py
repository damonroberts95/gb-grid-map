"""SSEN NeRDA in the server: GET /api/nerda/{nrn_or_line_uuid} (live readings on demand) and the background sweep
that keeps data/derived/nerda_latest.parquet (latest LV feeder current per secondary) fresh for the nerda_sites layer.

Wired from app.py with one line: `nerda.install(app, st)`. All network traffic goes through
extra.nerda_client's shared rate limiter (<= 2 req/s in total, NERDA_RPS to change); an on-demand request pauses the
sweep while it runs. Never logs credentials.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..extra import nerda_client as nc

log = logging.getLogger("gridmap.nerda")

CACHE_S = 180
AFTER_ONDEMAND = timedelta(minutes=15)
AFTER_SWEEP = timedelta(minutes=20)
WANT_TYPES = {"LineCurrent", "PhaseVoltage", "SinglePhaseActivePower", "SinglePhaseReactivePower",
              "SinglePhaseApparentPower", "ThreePhaseActivePower", "ThreePhaseReactivePower", "PowerFactor",
              "LineCurrentTHD", "PhaseVoltageTHD", "LineCurrentFrequency"}
MAX_LINES = 12          # per request: a primary can have 30 lines x 9 measurements
FETCH_WORKERS = 4       # on-demand concurrency (the shared limiter still caps the rate)
SWEEP_WORKERS = 10      # sweep concurrency: request latency is ~2-4 s, so 2 req/s needs several in flight
SAVE_EVERY_S = 600      # sweep: persist + only_live build at least this often within a cycle
LATEST_COLS = {"nrn": str, "i_max": float, "t": str, "nf": int, "fetched": str}

# channel prefix -> (family, unit, how to summarise across phases)
FAMILIES = [
    (re.compile(r"^i[123n](_max|_min)?$"), "LineCurrent", "A"),
    (re.compile(r"^v[123](_max|_min)?$"), "PhaseVoltage", "V"),
    (re.compile(r"^p[123]?(_max|_min)?$"), "ActivePower", "kW"),
    (re.compile(r"^q[123]?(_max|_min)?$"), "ReactivePower", "kVAr"),
    (re.compile(r"^s[123]?(_max|_min)?$"), "ApparentPower", "kVA"),
    (re.compile(r"^pf[123]?$"), "PowerFactor", ""),
    (re.compile(r"^[iv][123]_thd$"), "THD", "%"),
    (re.compile(r"^f[123]$"), "Frequency", "Hz"),
]


def family(channel: str) -> tuple[str, str] | None:
    for rx, fam, unit in FAMILIES:
        if rx.match(channel):
            return fam, unit
    return None


def summarise(channels: dict[str, dict]) -> dict[str, dict]:
    """{channel: {v, ts}} -> {family: {"v": headline, "unit", "ts", "ch": {channel: value}}}. Headline = max phase
    current (neutral excluded), mean voltage/PF/frequency, sum of per-phase powers, max THD. Plain channels are
    preferred over _max/_min aggregates; those are the fallback."""
    out: dict[str, dict] = {}
    by_fam: dict[str, dict[str, dict]] = {}
    for ch, rec in channels.items():
        f = family(ch)
        if f:
            by_fam.setdefault(f[0], {})[ch] = {**rec, "unit": f[1]}
    for fam, chs in by_fam.items():
        plain = {c: r for c, r in chs.items() if not c.endswith(("_max", "_min"))}
        use = plain or chs
        if fam == "LineCurrent":
            use = {c: r for c, r in use.items() if not c.startswith("in")} or use
        vals = [r["v"] for r in use.values()]
        if fam in ("LineCurrent", "THD"):
            head = max(vals)
        elif fam in ("ActivePower", "ReactivePower", "ApparentPower"):
            head = sum(vals) if not any(c in use for c in ("p", "q", "s")) else max(vals)
        else:
            head = sum(vals) / len(vals)
        out[fam] = {"v": round(head, 3), "unit": next(iter(use.values()))["unit"],
                    "ts": max(r["ts"] or "" for r in use.values()) or None,
                    "ch": {c: round(r["v"], 3) for c, r in sorted(chs.items())}}
    return out


def resolve(idx: dict, ident: str) -> dict | None:
    """{"site": site dict, "lines": [line dicts]} for an NRN, or a single line for a line uuid / line name."""
    ident = ident.strip()
    for s in idx["sites"]:
        if s["nrn"] == ident and ident:
            return {"site": s, "lines": s["lines"]}
    for s in idx["sites"]:
        for ln in s["lines"]:
            if ident in (ln["uuid"], ln["name"]):
                return {"site": s, "lines": [ln]}
    return None


# ---- on-demand ----------------------------------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_ondemand = threading.Event()
_ondemand_n = 0
_ondemand_lock = threading.Lock()


def _fetch_channels(mid: str, window: timedelta) -> dict[str, dict]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(mid)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    ch = nc.parse_after(nc.get_after(mid, datetime.now(UTC) - window))
    with _cache_lock:
        _cache[mid] = (now, ch)
        for k in [k for k, v in _cache.items() if now - v[0] > CACHE_S * 4]:
            _cache.pop(k, None)
    return ch


def readings(ident: str) -> dict | None:
    """Latest reading per line and measurement family, or None for an unknown id."""
    global _ondemand_n
    idx = nc.load_index()
    if idx is None:
        return None
    hit = resolve(idx, ident)
    if hit is None:
        return None
    lines = hit["lines"][:MAX_LINES]
    jobs = [(ln["name"], m["id"]) for ln in lines for m in ln["m"] if m["type"] in WANT_TYPES]
    with _ondemand_lock:
        _ondemand_n += 1
        _ondemand.set()
    try:
        ids = sorted({mid for _, mid in jobs})
        with ThreadPoolExecutor(FETCH_WORKERS) as ex:
            results = dict(zip(ids, ex.map(_safe_fetch, ids), strict=True))
    finally:
        with _ondemand_lock:
            _ondemand_n -= 1
            if _ondemand_n == 0:
                _ondemand.clear()
    out_lines = []
    for ln in lines:
        merged: dict[str, dict] = {}
        for m in ln["m"]:
            if m["type"] in WANT_TYPES:
                for c, rec in (results.get(m["id"]) or {}).items():
                    if c not in merged or (rec["ts"] or "") > (merged[c]["ts"] or ""):
                        merged[c] = rec
        out_lines.append({"line": ln["name"], "uuid": ln["uuid"], "feeder": ln["feeder"], "limit": ln["limit"],
                          "readings": summarise(merged)})
    s = hit["site"]
    return {"nrn": s["nrn"], "name": s["n"], "kind": s["k"], "window_min": int(AFTER_ONDEMAND.total_seconds() / 60),
            "truncated": len(hit["lines"]) > MAX_LINES, "lines": out_lines,
            "fetched": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _safe_fetch(mid: str) -> dict[str, dict]:
    try:
        return _fetch_channels(mid, AFTER_ONDEMAND)
    except nc.NerdaError as e:
        log.warning("nerda on-demand fetch failed: %s", e)
        return {}


# ---- sweep ---------------------------------------------------------------------------------------------------------

def latest_path(data_dir: Path) -> Path:
    return data_dir / "derived" / "nerda_latest.parquet"


def sweep_targets(idx: dict) -> list[tuple[str, list[str]]]:
    """[(nrn, [LineCurrent measurement id per LV feeder])] for LV-monitored secondaries."""
    out = []
    for s in idx["sites"]:
        if s["k"] != "secondary":
            continue
        ids = [i for ln in nc.lv_feeders(s["lines"]) if (i := nc.line_current_id(ln))]
        if ids:
            out.append((s["nrn"], ids))
    return out


def feeder_current(doc: dict) -> tuple[float, str] | None:
    return nc.latest_value(doc)


def sweep_site(ids: list[str]) -> tuple[float, str, int] | None:
    """Max LV feeder current (A), newest timestamp and feeders read; None when nothing came back."""
    vals = []
    for mid in ids:
        while _ondemand.is_set():
            time.sleep(0.5)
        r = feeder_current(nc.get_after(mid, datetime.now(UTC) - AFTER_SWEEP))
        if r:
            vals.append(r)
    if not vals:
        return None
    return max(v for v, _ in vals), max(t for _, t in vals), len(vals)


def save_latest(path: Path, old: dict[str, dict], new: dict[str, dict]) -> dict[str, dict]:
    import polars as pl
    merged = {**old, **new}
    rows = [{"nrn": k, **v} for k, v in merged.items()]
    df = pl.DataFrame(rows, schema={k: pl.Float64 if v is float else pl.Int64 if v is int else pl.Utf8
                                    for k, v in LATEST_COLS.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.write_parquet(tmp)
    tmp.replace(path)
    return merged


def load_latest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    import polars as pl
    return {r["nrn"]: r for r in pl.read_parquet(path).iter_rows(named=True)}


class Sweeper:
    def __init__(self, st: Any):
        self.st = st
        self.path = latest_path(st.data_dir)

    def publish(self) -> None:
        """only_live build so the layer picks up the new parquet; skipped while a refresh is building."""
        from .. import build
        for _ in range(120):
            if not self.st.refreshing:
                break
            self.st.stop.wait(5)
        try:
            before = build.read_manifest(self.st.public).get("version")
            out = build.build_all(self.st.data_dir, only_live=True)
            m = self.st.manifest()
            if m.get("version") != before:
                self.st.bus.publish("manifest", m)
            self.st.bus.publish("live", {"feed": "nerda"})
            log.info("nerda sweep: map rebuilt (%s)", ",".join(out["changed"]) or "no change")
        except Exception:
            log.exception("nerda sweep: build failed")

    def cycle(self, idx: dict) -> None:
        targets = sweep_targets(idx)
        latest = load_latest(self.path)
        t0 = last_save = time.monotonic()
        new: dict[str, dict] = {}
        feeders = errors = reqs = 0
        auth_fail = 0
        with ThreadPoolExecutor(SWEEP_WORKERS) as ex:
            for i in range(0, len(targets), 40):  # chunks, so a stop request does not wait for the whole cycle
                chunk = targets[i:i + 40]
                for (nrn, ids), res in zip(chunk, ex.map(self._site, [t[1] for t in chunk]), strict=True):
                    reqs += len(ids)
                    if isinstance(res, Exception):
                        errors += 1
                        auth_fail = auth_fail + 1 if "HTTP 40" in str(res) else 0
                        if auth_fail >= 10:
                            log.error("nerda sweep: repeated auth failures, aborting cycle (key expired? --rotate)")
                            return
                        continue
                    auth_fail = 0
                    if res:
                        i_max, t, nf = res
                        new[nrn] = {"i_max": round(i_max, 1), "t": t, "nf": nf,
                                    "fetched": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
                        feeders += nf
                if self.st.stop.is_set():
                    return
                if time.monotonic() - last_save > SAVE_EVERY_S and new:
                    latest = save_latest(self.path, latest, new)
                    new = {}
                    last_save = time.monotonic()
                    self.publish()
        if new:
            latest = save_latest(self.path, latest, new)
        dt = time.monotonic() - t0
        log.info("nerda sweep: cycle done in %.1f min: %d sites, %d requests (%.2f req/s), %d feeders with data, "
                 "%d site errors", dt / 60, len(targets), reqs, reqs / max(dt, 1), feeders, errors)
        self.publish()

    @staticmethod
    def _site(ids: list[str]) -> Any:
        try:
            return sweep_site(ids)
        except Exception as e:  # noqa: BLE001 - returned, counted by the caller
            return e

    def run(self) -> None:
        self.st.stop.wait(20)  # let the server settle first
        while not self.st.stop.is_set():
            try:
                if nc.credentials() is None:
                    log.info("nerda: no credentials, sweep disabled")
                    return
                nc.warn_if_key_old()
                age = nc.static_age_days()
                if age is None or age >= nc.STATIC_MAX_AGE_DAYS:
                    nc.fetch_static(force=True)
                idx = nc.load_index()
                if idx is None:
                    self.st.stop.wait(1800)
                    continue
                self.cycle(idx)
            except Exception:
                log.exception("nerda sweep failed")
                self.st.stop.wait(600)
            self.st.stop.wait(60)


def install(app: Any, st: Any) -> None:
    """Register GET /api/nerda/{ident} and start the sweep thread (daemon; stops with st.stop)."""
    from fastapi.responses import JSONResponse

    @app.get("/api/nerda/{ident}")
    def nerda_readings(ident: str):
        if nc.credentials() is None:
            return JSONResponse({"error": "NeRDA credentials not configured"}, status_code=503)
        try:
            res = readings(ident)
        except nc.NerdaError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        if res is None:
            return JSONResponse({"error": "unknown NRN / line, or static data not fetched yet"}, status_code=404)
        return res

    if st.scheduler:
        threading.Thread(target=Sweeper(st).run, name="nerda-sweep", daemon=True).start()
