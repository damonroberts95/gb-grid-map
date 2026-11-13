"""Live feeds: parse the newest raw snapshot of each operator's outage feed and the national feeds.

Field mappings were written from sampled snapshots (2026-11-12). Feeds with no usable coordinates
(spen_live_outages, ukpn_ehv_outages, enw_live_neo) are skipped. A broken feed is skipped, never fatal.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

log = logging.getLogger("gridmap.live")
MAX_AGE_S = 6 * 3600  # a feed whose newest snapshot is older than this is not "current"
_cache: dict[tuple, dict] = {}


def latest_snapshot(raw_dir: Path, source_id: str, now: float | None = None) -> Path | None:
    base = raw_dir / source_id
    if not base.is_dir():
        return None
    snaps = sorted(d for d in base.iterdir() if (d / "meta.json").exists())
    if not snaps:
        return None
    ts = datetime.strptime(snaps[-1].name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp()
    return snaps[-1] if (now or time.time()) - ts <= MAX_AGE_S else None


def _s(v) -> str | None:
    v = None if v is None else str(v).strip()
    return v or None


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _latlon(v) -> tuple[float, float] | None:
    """'lat, lon' string -> (lat, lon) inside GB bounds."""
    try:
        a, b = (float(x) for x in str(v).split(","))
    except ValueError:
        return None
    return (a, b) if 49.5 <= a <= 61 and -9 <= b <= 2.5 else None


def _csv(path: Path, sep: str, cols: list[str] | None = None) -> list[dict]:
    df = pl.read_csv(path, separator=sep, encoding="utf8-lossy", infer_schema_length=0, truncate_ragged_lines=True)
    df = df.rename({c: c.strip().lower().replace(" ", "_") for c in df.columns})
    return df.select([c for c in (cols or df.columns) if c in df.columns]).to_dicts()


def _rec(op, ref, status, start, etr, cust, planned, ll) -> dict | None:
    if not ref or ll is None:
        return None
    return {"op": op, "ref": ref, "status": status, "start": _s(start), "etr": _s(etr), "cust": cust,
            "kind": "planned" if planned else "unplanned", "ll": ll}


def _ukpn_faults(d: Path) -> list[dict]:
    out = []
    for r in _csv(d / "export.csv", ";"):
        if r.get("powercuttype") == "Restored":
            continue
        planned = r.get("powercuttype") == "Planned"
        out.append(_rec("UKPN", _s(r.get("incidentreference")), _s(r.get("powercuttype")),
                        r.get("planneddate") if planned and r.get("planneddate") else r.get("creationdatetime"),
                        r.get("estimatedrestorationdate"), _int(r.get("nocustomeraffected")), planned,
                        _latlon(r.get("geopoint"))))
    return out


def _ukpn_neo(d: Path) -> list[dict]:
    cols = ["fault_id", "status", "planned", "etr", "confirmed_off", "outage_start_date", "geo_point_2d"]
    return [_rec("UKPN", _s(r["fault_id"]), _s(r["status"]), r.get("outage_start_date"), r.get("etr"),
                 _int(r.get("confirmed_off")), str(r.get("planned")).upper() == "TRUE", _latlon(r.get("geo_point_2d")))
            for r in _csv(d / "export.csv", ";", cols) if r.get("status") != "Complete"]


def _npg(d: Path) -> list[dict]:
    cols = ["reference", "powercutcategory", "natureofoutage", "loggedtime", "etrendrange", "estimatedtimetillresolution",
            "totalconfirmedpowercut", "lat", "lng"]
    seen, out = set(), []
    for r in _csv(d / "export.csv", ";", cols):  # one row per affected postcode: keep one per incident
        if r["reference"] in seen:
            continue
        seen.add(r["reference"])
        out.append(_rec("NPG", _s(r["reference"]), _s(r.get("powercutcategory")), r.get("loggedtime"),
                        r.get("etrendrange") or r.get("estimatedtimetillresolution"),
                        _int(r.get("totalconfirmedpowercut")), "Planned" in (r.get("natureofoutage") or ""),
                        _latlon(f"{r.get('lat')},{r.get('lng')}")))
    return out


def _enw(d: Path) -> list[dict]:
    return [_rec("ENW", _s(r["incident_num"]), _s(r.get("incident_status")), r.get("outage_time"),
                 r.get("estimated_restoration_time"), _int(r.get("customers_off_supply")),
                 (r.get("incident_type") or "").startswith("Planned"), _latlon(r.get("geo_point_2d")))
            for r in _csv(d / "export.csv", ";")]


def _spen_neo(d: Path) -> list[dict]:
    cols = ["fault_id", "status", "planned", "planned_outage_start_date", "date_of_reported_fault", "etr",
            "location_latitude", "location_longitude"]
    return [_rec("SPEN", _s(r["fault_id"]), _s(r["status"]),
                 r.get("planned_outage_start_date") or r.get("date_of_reported_fault"), r.get("etr"), None,
                 r.get("planned") == "1", _latlon(f"{r.get('location_latitude')},{r.get('location_longitude')}"))
            for r in _csv(d / "export.csv", ";", cols) if r.get("status") != "Completed"]


def _nged(d: Path) -> list[dict]:
    out = []
    for f in sorted(d.glob("*.csv")):
        for r in _csv(f, ","):
            ref = r.get("incident_id") or r.get("fault_id")
            out.append(_rec("NGED", _s(ref), _s(r.get("status")),
                            r.get("start_time") or r.get("planned_outage_start_date") or r.get("date_of_reported_fault"),
                            r.get("etr"), _int(r.get("confirmed_off")), str(r.get("planned")).lower() == "true",
                            _latlon(f"{r.get('location_latitude')},{r.get('location_longitude')}")))
    return out


def _ssen(d: Path) -> list[dict]:
    out = []
    for f in json.loads((d / "data.json").read_text())["faults"]:
        loc = f.get("location") or {}
        out.append(_rec("SSEN", _s(f.get("reference")), _s(f.get("jobStatus")) or _s(f.get("type")),
                        f.get("loggedAtUtc"), f.get("estimatedRestorationTimeUtc"), _int(f.get("customerCount")),
                        f.get("type") == "PSI", _latlon(f"{loc.get('latitude')},{loc.get('longitude')}")))
    return out


OUTAGE_PARSERS: dict[str, Callable[[Path], list[dict]]] = {
    "ukpn_live_faults": _ukpn_faults, "ukpn_neo_outages": _ukpn_neo, "npg_live_power_cuts": _npg,
    "enw_live_incidents": _enw, "spen_neo_outages": _spen_neo, "nged_live_power_cuts": _nged,
    "ssen_powertrack_faults": _ssen,
}


def outages(raw_dir: Path) -> dict:
    """GeoJSON FeatureCollection of current outages (cached per set of snapshot dirs)."""
    snaps = {sid: latest_snapshot(raw_dir, sid) for sid in OUTAGE_PARSERS}
    key = ("outages", tuple(sorted((k, str(v)) for k, v in snaps.items())))
    if key in _cache:
        return _cache[key]
    feats, seen = [], set()
    for sid, d in snaps.items():
        if d is None:
            continue
        try:
            recs = [r for r in OUTAGE_PARSERS[sid](d) if r]
        except Exception as e:  # noqa: BLE001 - one broken feed must not fail the endpoint
            log.warning("outage feed %s skipped: %s: %s", sid, type(e).__name__, e)
            continue
        for r in recs:
            if (r["op"], r["ref"]) in seen:
                continue
            seen.add((r["op"], r["ref"]))
            lat, lon = r.pop("ll")
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": r})
    _cache.clear()
    _cache[key] = {"type": "FeatureCollection", "features": feats}
    return _cache[key]


def _load(raw_dir: Path, sid: str):
    d = latest_snapshot(raw_dir, sid)
    if d is None:
        return None
    try:
        return json.loads((d / "data.json").read_text())
    except (OSError, ValueError):
        return None


def national(raw_dir: Path) -> dict:
    out: dict = {"fuel_mix": [], "demand_mw": None, "frequency_hz": None, "frequency_at": None,
                 "carbon": {"national": None, "regions": []}, "as_of": None}
    if (j := _load(raw_dir, "elexon_outturn_current")) is not None:
        out["fuel_mix"] = [{"fuel": r["fuelType"], "mw": r["currentUsage"]} for r in j]
    demand_at = None
    if j := _load(raw_dir, "elexon_demand_outturn"):
        last = max(j, key=lambda r: r["startTime"])
        out["demand_mw"], demand_at = last["demand"], last["startTime"]
    if j := _load(raw_dir, "elexon_frequency"):
        last = max(j["data"], key=lambda r: r["measurementTime"])
        out["frequency_hz"], out["frequency_at"] = last["frequency"], last["measurementTime"]
    if j := _load(raw_dir, "carbon_regional"):
        regions = j["data"][0]["regions"]
        for r in regions:
            if r["shortname"] == "GB":
                out["carbon"]["national"] = r["intensity"]["forecast"]
            elif r["regionid"] <= 14:
                out["carbon"]["regions"].append({"region": r["shortname"], "intensity": r["intensity"]["forecast"],
                                                 "index": r["intensity"]["index"]})
    out["as_of"] = max((t for t in (out["frequency_at"], demand_at) if t), default=None)
    return out
