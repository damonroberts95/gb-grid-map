"""Live feeds: parse the newest raw snapshot of each operator's outage feed and the national feeds.

Field mappings were written from sampled snapshots (2026-11-12). Feeds with no usable coordinates
(spen_live_outages, ukpn_ehv_outages, enw_live_neo) are skipped. The NIE open data faults give postcode sectors only (fallback);
NIE Powercheck gives exact points and is preferred when current; they are
placed at the sector centre taken from NIE's own secondary transformer postcodes. A broken feed is skipped, never fatal.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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


_NI_SECTORS: dict[str, dict[str, tuple[float, float]]] = {}
_SECTOR = re.compile(r"^(BT\d{1,2}) ?(\d)")


def _ni_sectors(raw_dir: Path) -> dict[str, tuple[float, float]]:
    """'BT514' -> mean (lat, lon) of NIE secondary transformers with a postcode in that sector. Any snapshot age."""
    base = raw_dir / "nie_secondary_transformers"
    snaps = sorted(d for d in base.iterdir() if (d / "export.csv").exists()) if base.is_dir() else []
    if not snaps:
        return {}
    key = str(snaps[-1])
    if key not in _NI_SECTORS:
        df = pl.read_csv(snaps[-1] / "export.csv", separator=";", infer_schema_length=0,
                         columns=["site_postcode", "site_geopoint"])
        acc: dict[str, list[tuple[float, float]]] = {}
        for pc, gp in df.iter_rows():
            m = _SECTOR.match((pc or "").strip().upper())
            if m and (ll := _latlon(gp)):
                acc.setdefault(m[1] + m[2], []).append(ll)
        _NI_SECTORS.clear()
        _NI_SECTORS[key] = {k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)) for k, v in acc.items()}
    return _NI_SECTORS[key]


def _nie_time(v, now: datetime | None = None) -> str | None:
    """'8:17 PM, 24 Nov' or '5:00 PM, 25 Nov 2026' (UK local time) -> ISO 8601. Anything else -> None."""
    v = _s(v)
    if not v:
        return None
    tz = ZoneInfo("Europe/London")
    now = now or datetime.now(tz)
    for fmt, has_year in (("%I:%M %p, %d %b %Y", True), ("%I:%M %p, %d %b", False)):
        try:
            t = datetime.strptime(v if has_year else f"{v} {now.year}", fmt + ("" if has_year else " %Y"))
        except ValueError:
            continue
        if not has_year and t > now.replace(tzinfo=None) + timedelta(days=180):
            t = t.replace(year=t.year - 1)  # "24 Dec" read on 2 Jan
        return t.replace(tzinfo=tz).isoformat()
    return None


def _nie(d: Path) -> list[dict]:
    sectors = _ni_sectors(d.parent.parent)
    out = []
    for r in _csv(d / "export.csv", ";"):
        pts = [sectors[k] for s in (r.get("postcode") or "").split(";")
               if (m := _SECTOR.match(s.strip().upper())) and (k := m[1] + m[2]) in sectors]
        ll = (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts)) if pts else None
        rec = _rec("NIE", _s(r.get("outageid")), _s(r.get("outagetype")), _nie_time(r.get("starttime")),
                   _nie_time(r.get("estrestorefulldatetime")), _int(r.get("numcustaffected")),
                   r.get("outagetype") == "Planned", ll)
        if rec:
            rec["area"] = _s(r.get("postcode"))  # approximate: placed at the postcode sector centre
        out.append(rec)
    return out


_ITM = None


def _irish_grid(v) -> tuple[float, float] | None:
    """'E,N' (Irish Grid, EPSG:29903) -> (lat, lon)."""
    global _ITM
    try:
        e, n = (float(x) for x in str(v).split(","))
    except ValueError:
        return None
    if _ITM is None:
        from pyproj import Transformer
        _ITM = Transformer.from_crs(29903, 4326, always_xy=True)
    lon, lat = _ITM.transform(e, n)
    return _latlon(f"{lat},{lon}")


def _nie_powercheck(d: Path) -> list[dict]:
    out = []
    for f in json.loads((d / "data.json").read_text()).get("outageMessage") or []:
        rec = _rec("NIE", _s(f.get("outageId")), _s(f.get("outageType")), _nie_time(f.get("startTime")),
                   _nie_time(f.get("estRestoreFullDateTime")), _int(f.get("numCustAffected")),
                   f.get("outageType") == "Planned", _irish_grid((f.get("point") or {}).get("coordinates")))
        if rec:
            rec["area"] = _s(f.get("fullPostCodes") or f.get("postCode"))
        out.append(rec)
    return out


_ITM2157 = None


def _ie_point(v) -> tuple[float, float] | None:
    """ESB PowerCheck point: 'lat,lon' (either order) or ITM 'x,y' (EPSG:2157) -> (lat, lon) on the island of Ireland."""
    global _ITM2157
    try:
        a, b = (float(x) for x in str(v).replace(";", ",").split(",")[:2])
    except ValueError:
        return None
    ok = lambda la, lo: 51.2 <= la <= 55.5 and -10.8 <= lo <= -5.3
    if abs(a) > 1000 and abs(b) > 1000:
        if _ITM2157 is None:
            from pyproj import Transformer
            _ITM2157 = Transformer.from_crs(2157, 4326, always_xy=True)
        lo, la = _ITM2157.transform(a, b)
        return (round(la, 6), round(lo, 6)) if ok(la, lo) else None
    for la, lo in ((a, b), (b, a)):
        if ok(la, lo):
            return la, lo
    return None


def _ie_time(v) -> str | None:
    """ESB times: ISO 8601, or 'dd/mm/yyyy HH:MM' (Irish local time) -> ISO 8601."""
    v = _s(v)
    if not v:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=ZoneInfo("Europe/Dublin")).isoformat()
        except ValueError:
            pass
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=ZoneInfo("Europe/Dublin"))).isoformat()


def _esb_powercheck(d: Path) -> list[dict]:
    """ESB Networks PowerCheck outages. Field names follow the PowerCheck JSON (camelCase, outageMessage list);
    a point is 'lat,lon' or ITM. Restored outages are skipped."""
    doc = json.loads((d / "data.json").read_text())
    items = doc if isinstance(doc, list) else (doc.get("outageMessage") or doc.get("outages") or [])
    out = []
    for f in items:
        if not isinstance(f, dict) or str(f.get("outageType") or "").lower() == "restored":
            continue
        pt = (f.get("point") or {}).get("coordinates") if isinstance(f.get("point"), dict) else f.get("point")
        ll = _ie_point(pt) or (_ie_point(f"{f.get('latitude')},{f.get('longitude')}") if f.get("latitude") else None)
        typ = _s(f.get("outageType"))
        rec = _rec("ESB", _s(f.get("outageId") or f.get("id")), typ, _ie_time(f.get("startTime")),
                   _ie_time(f.get("estRestoreTime") or f.get("estimatedRestoreTime")),
                   _int(f.get("numCustAffected")), (typ or "").lower() == "planned", ll)
        if rec:
            rec["area"] = _s(f.get("location"))
        out.append(rec)
    return out


OUTAGE_PARSERS: dict[str, Callable[[Path], list[dict]]] = {
    "ukpn_live_faults": _ukpn_faults, "ukpn_neo_outages": _ukpn_neo, "npg_live_power_cuts": _npg,
    "enw_live_incidents": _enw, "spen_neo_outages": _spen_neo, "nged_live_power_cuts": _nged,
    "ssen_powertrack_faults": _ssen, "nie_network_faults": _nie,
    "nie_powercheck_faults": _nie_powercheck, "esb_powercheck_outages": _esb_powercheck,
}


def outages(raw_dir: Path) -> dict:
    """GeoJSON FeatureCollection of current outages (cached per set of snapshot dirs)."""
    snaps = {sid: latest_snapshot(raw_dir, sid) for sid in OUTAGE_PARSERS}
    if snaps.get("nie_powercheck_faults"):  # exact points and live: the postcode-sector fallback is only for when it is down
        snaps["nie_network_faults"] = None
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


def _eirgrid_last(raw_dir: Path, sid: str, field: str) -> tuple[float, str] | None:
    """Newest non-null (value, ISO time) of an EirGrid dashboard field. The API now and then answers with empty
    Rows or only future null slots, so look back over the kept snapshots."""
    base = raw_dir / sid
    snaps = sorted((d for d in base.iterdir() if (d / "meta.json").exists()), reverse=True) if base.is_dir() else []
    for d in snaps:
        ts = datetime.strptime(d.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp()
        if time.time() - ts > MAX_AGE_S:
            break
        try:
            rows = json.loads((d / "data.json").read_text()).get("Rows") or []
        except (OSError, ValueError):
            continue
        pts = []
        for r in rows:
            if r.get("FieldName") == field and r.get("Value") is not None:
                t = datetime.strptime(r["EffectiveTime"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/London"))
                pts.append((t, r["Value"]))
        if pts:
            t, v = max(pts, key=lambda p: p[0])
            return v, t.isoformat()
    return None


NI_FIELDS = {"demand_mw": ("eirgrid_ni_demand", "SYSTEM_DEMAND"), "wind_mw": ("eirgrid_ni_wind", "WIND_ACTUAL"),
             "solar_mw": ("eirgrid_ni_solar", "SOLAR_ACTUAL"), "moyle_mw": ("eirgrid_ni_interconnection", "INTER_MOYLE"),
             "net_import_mw": ("eirgrid_ni_interconnection", "INTER_NET_NI"), "co2_g_kwh": ("eirgrid_ni_co2", "CO2_INTENSITY")}


ROI_FIELDS = {"demand_mw": ("eirgrid_roi_demand", "SYSTEM_DEMAND"), "wind_mw": ("eirgrid_roi_wind", "WIND_ACTUAL"),
              "solar_mw": ("eirgrid_roi_solar", "SOLAR_ACTUAL"), "ewic_mw": ("eirgrid_roi_interconnection", "INTER_EWIC"),
              "greenlink_mw": ("eirgrid_roi_interconnection", "INTER_GRNLK"),
              "net_import_mw": ("eirgrid_roi_interconnection", "INTER_NET_ROI"),
              "co2_g_kwh": ("eirgrid_roi_co2", "CO2_INTENSITY")}


def _region_block(raw_dir: Path, fields: dict[str, tuple[str, str]]) -> dict | None:
    """One region's national figures (EirGrid dashboard). Import into the region is positive. None when nothing is current."""
    got = {k: r for k, (sid, f) in fields.items() if (r := _eirgrid_last(raw_dir, sid, f))}
    if not got:
        return None
    return {**{k: r[0] for k, r in got.items()}, "as_of": max(r[1] for r in got.values())}


def _ni_block(raw_dir: Path) -> dict | None:
    return _region_block(raw_dir, NI_FIELDS)


def national(raw_dir: Path) -> dict:
    out: dict = {"fuel_mix": [], "demand_mw": None, "frequency_hz": None, "frequency_at": None,
                 "carbon": {"national": None, "regions": []}, "as_of": None, "ni": None, "roi": None}
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
    out["ni"] = _ni_block(raw_dir)
    out["roi"] = _region_block(raw_dir, ROI_FIELDS)
    out["as_of"] = max((t for t in (out["frequency_at"], demand_at) if t), default=None)
    return out
