"""EirGrid transmission network (Republic of Ireland, plus the NI stations EirGrid draws): 110-400 kV overhead
lines, underground cables and interconnectors as one line layer coloured by voltage, and transmission stations
as points. From the public map of the Transmission Development Plan 2024 (ArcGIS FeatureServer)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..normalise.common import latest_snapshot
from ..tools import tool
from . import Ctx, LayerMeta
from .lines import min_zoom

SOURCES = ["eirgrid_transmission"]
ATTR = "EirGrid Transmission Development Plan 2024 map. No licence stated."

# same voltage colours as the SSEN Transmission substations layer
KV_STOPS = [[0, "#4575b4"], [200, "#762a83"], [350, "#1b1b1b"]]

LAYERS = [
    LayerMeta(
        id="eirgrid_lines", title="Transmission lines (Ireland)", group="Network", type="pmtiles", geom="line",
        source_layer="eirgrid_lines", minzoom=4,
        description="EirGrid 110-400 kV overhead lines, cables and interconnectors. TDP 2024 map.",
        color={"prop": "kv", "type": "steps", "default": "#4575b4", "stops": KV_STOPS},
        size={"prop": "kv", "stops": [[0, 1.4], [200, 2.2], [350, 3]]}, opacity=0.85,
        popup=[["n", "Circuit", None], ["kv", "Voltage", "kV"], ["t", "Type", None], ["y", "Installed", None]],
        legend=[["#4575b4", "110 kV"], ["#762a83", "220-275 kV"], ["#1b1b1b", "400 kV / interconnector"]],
        attribution=ATTR,
    ),
    LayerMeta(
        id="eirgrid_stations", title="Transmission stations (Ireland)", group="Network", type="geojson", geom="point",
        minzoom=5,
        description="EirGrid transmission stations. 'Works planned' marks committed TDP 2024 projects.",
        color={"prop": "kv", "type": "steps", "default": "#4575b4", "stops": KV_STOPS},
        size={"prop": "kv", "stops": [[0, 3.5], [200, 5], [350, 6.5]]},
        popup=[["n", "Station", None], ["kv", "Voltage", "kV"], ["no", "Station number", None], ["w", "Works planned", None]],
        legend=[["#4575b4", "110 kV"], ["#762a83", "220-275 kV"], ["#1b1b1b", "400 kV"]],
        attribution=ATTR,
    ),
]

INTERCONNECTORS = {"4": ("Celtic Interconnector", 320.0), "6": ("Greenlink", 320.0), "7": ("East-West Interconnector", 400.0)}


def kv(v) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else None


def _year(v) -> str | None:
    m = re.search(r"(\d{4})\s*$", str(v or "").strip())
    return m.group(1) if m else None


def line_features(layers: dict[str, dict]) -> list[dict]:
    """Spans of one circuit section merged into a MultiLineString (32k spans -> about a thousand features).
    Spans with committed works (41, 43) are left out of the existing layers (40, 39), so all four are read."""
    out: dict[tuple, dict] = {}
    seen: set = set()
    for lid, t in (("40", "Overhead line"), ("41", "Overhead line"), ("39", "Underground cable"),
                   ("43", "Underground cable")):
        for f in (layers.get(lid) or {}).get("features", []):
            g, p = f.get("geometry"), f.get("properties") or {}
            if not g or g.get("type") not in ("LineString", "MultiLineString") or (fid := p.get("G3E_FID")) in seen:
                continue
            if fid is not None:
                seen.add(fid)
            parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            v = kv(p.get("VOLTAGE"))
            key = (t, p.get("CSECT_ID") or f"{lid}:{p.get('OBJECTID')}", v)
            feat = out.setdefault(key, {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": []},
                                        "properties": {k: x for k, x in {
                                            "n": (p.get("CSECT_NAME") or "").strip().title() or None, "kv": v, "t": t,
                                            "y": _year(p.get("DATE_INST"))}.items() if x is not None}})
            feat["geometry"]["coordinates"] += parts
    feats = list(out.values())
    for lid, (name, v) in INTERCONNECTORS.items():
        for f in (layers.get(lid) or {}).get("features", []):
            g = f.get("geometry")
            if g and g.get("type") in ("LineString", "MultiLineString"):
                feats.append({"type": "Feature", "geometry": g,
                              "properties": {"n": name, "kv": v, "t": "Interconnector (HVDC)"}})
    for f in feats:
        f["tippecanoe"] = {"minzoom": min_zoom(f["properties"].get("kv") or 0)}
    return feats


def station_features(layers: dict[str, dict]) -> list[dict]:
    """Existing stations (layer 38) and stations with committed works (42), one point per station number."""
    seen: dict[str, dict] = {}
    for lid in ("38", "42"):
        for f in (layers.get(lid) or {}).get("features", []):
            g, p = f.get("geometry"), f.get("properties") or {}
            if not g or g.get("type") != "Point":
                continue
            no = str(p.get("ST_NUMBER") or p.get("OBJECTID"))
            name = re.sub(r"\s*\d+\s*kv$", "", (p.get("ST_NAME") or "").strip(), flags=re.IGNORECASE).title()
            v = kv(p.get("VOLTAGE"))
            cur = seen.get(no)
            if cur is None:
                lon, lat = g["coordinates"][:2]
                cur = seen[no] = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                                  "properties": {"n": name, "kv": v or 0, "no": no}}
            elif v and v > cur["properties"]["kv"]:
                cur["properties"]["kv"] = v
            if lid == "42":
                cur["properties"]["w"] = "Yes"
    return list(seen.values())


def _layers(snap: Path) -> dict[str, dict]:
    return {p.stem.removeprefix("layer_"): json.loads(p.read_text()) for p in snap.glob("layer_*.geojson")}


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("eirgrid_transmission")
    if snap is None:
        return {}
    layers = _layers(snap)
    out: dict[str, Path] = {}
    lines = line_features(layers)
    if lines:
        src = ctx.tmp / "eirgrid_lines.geojsonl"
        src.write_text("".join(json.dumps(f, separators=(",", ":")) + "\n" for f in lines))
        out["eirgrid_lines"] = ctx.tmp / "eirgrid_lines.pmtiles"
        subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out["eirgrid_lines"]), "-l", "eirgrid_lines",
                        "-Z4", "-z13", "--simplification=4", "--drop-smallest-as-needed",
                        "--extend-zooms-if-still-dropping", "--maximum-tile-bytes=150000", "--generate-ids", str(src)], check=True)
    st = station_features(layers)
    if st:
        out["eirgrid_stations"] = ctx.tmp / "eirgrid_stations.geojson"
        out["eirgrid_stations"].write_text(json.dumps({"type": "FeatureCollection", "features": st},
                                                      separators=(",", ":")))
    return out
