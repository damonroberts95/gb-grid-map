"""Constraints and curtailment: where generation is being held back, and the areas managed by active network
management (ANM).

- `curtailment` points: UKPN GSP-level curtailment events + constraint breaches (2023-26, public rows) and NPG
  site-specific curtailment events (2023-26), aggregated per scheme and sized by energy curtailed.
- `anm_zones` polygons: SSEN Orkney and Isle of Wight ANM areas (the primary supply areas they cover; SSEN publishes
  no ANM boundary), plus the UKPN and NPG GSP areas where curtailment is recorded.
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta

SOURCES = ["ukpn_curtailment_events", "ukpn_constraint_breaches", "npg_curtailment_events", "ukpn_gsp_areas",
           "ssen_anm_orkney", "ssen_anm_iow", "ssen_primary_supply_areas"]

OP_COLORS = [["UKPN", "#6a3d9a"], ["NPG", "#1f9ec9"], ["SSEN", "#b15928"]]

LAYERS = [
    LayerMeta(
        id="curtailment", title="Generation curtailment", group="Live", type="geojson", geom="point",
        minzoom=5, opacity=0.8,
        description="Grid supply points where distributed generation has been curtailed, sized by estimated energy "
                    "lost (MWh) since 2023. UKPN publishes its GSP-level events and constraint breaches; Northern "
                    "Powergrid publishes site-specific events (outage-related). Public rows to Nov 2026 (UKPN) / Mar 2026 (NPG); "
                    "the two operators measure energy lost differently, so compare within an operator.",
        color={"prop": "op", "type": "categorical", "default": "#555555", "stops": OP_COLORS[:2]},
        size={"prop": "e", "stops": [[0, 4], [100, 6], [1000, 9], [10000, 13], [100000, 18]]},
        popup=[["n", "Grid supply point / scheme", None], ["op", "Operator", None], ["ev", "Curtailment events", None],
               ["e", "Energy curtailed", "MWh"], ["h", "Event hours", "h"], ["b", "Constraint breaches", None],
               ["r", "Main reason", None], ["p", "Period", None]],
        legend=[["#6a3d9a", "UKPN"], ["#1f9ec9", "Northern Powergrid"]],
        attribution="UK Power Networks and Northern Powergrid open data (CC BY 4.0; NPG Open Data Licence v1.0)",
    ),
    LayerMeta(
        id="anm_zones", title="ANM and curtailment areas", group="Areas", type="pmtiles", geom="polygon",
        source_layer="anm_zones", minzoom=5, opacity=0.7,
        description="Areas where generation is managed by active network management or has been curtailed: SSEN's "
                    "Orkney and Isle of Wight ANM schemes (drawn from the primary supply areas they cover) and the "
                    "UKPN / Northern Powergrid GSP areas with curtailment events.",
        color={"prop": "op", "type": "categorical", "default": "#555555", "stops": OP_COLORS},
        popup=[["n", "Area", None], ["op", "Operator", None], ["k", "Type", None], ["ev", "Curtailment events", None],
               ["e", "Energy curtailed", "MWh"], ["g", "Mean generation (2025)", "MW"],
               ["a", "Mean ANM-managed output (2025)", "MW"], ["dm", "Mean demand (2025)", "MW"]],
        legend=[["#6a3d9a", "UKPN GSP area"], ["#1f9ec9", "NPG GSP area"], ["#b15928", "SSEN ANM scheme"]],
        attribution="SSEN Distribution, UK Power Networks and Northern Powergrid open data (CC BY 4.0; NPG Open Data "
                    "Licence v1.0)",
        inputs=["derived/site.parquet"],
    ),
]

# UKPN scheme names -> GSP area name(s) in ukpn_gsp_areas
UKPN_ALIAS = {"WALPOLE-E": ["WALPOLE"], "WALPOLE-W": ["WALPOLE"], "PELHAM & WYMONDLEY": ["PELHAM"],
              "ELSTREE-GIS": ["ELSTREE"]}


def _csv(source_id: str, delim: str = ";") -> list[dict]:
    snap = latest_snapshot(source_id)
    if snap is None:
        return []
    csv.field_size_limit(10**9)
    f = next(p for p in snapshot_files(snap) if p.suffix == ".csv")
    return list(csv.DictReader(io.StringIO(f.read_text(encoding="utf-8-sig")), delimiter=delim))


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _round(c: Any, nd: int = 4) -> Any:
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round(x, nd) for x in c]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower().replace("&", "and"))


def _centre(geom: dict) -> tuple[float, float]:
    c = geom["coordinates"]
    while isinstance(c[0][0], list):
        c = c[0]
    xs, ys = [p[0] for p in c], [p[1] for p in c]
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def ukpn_schemes() -> dict[str, dict]:
    """Per UKPN GSP area name: events, hours, MWh lost, constraint events, breaches, period."""
    out: dict[str, dict] = defaultdict(lambda: {"ev": 0, "h": 0.0, "e": 0.0, "con": 0, "b": 0, "t": [], "src": set()})
    for r in _csv("ukpn_curtailment_events"):
        for gsp in UKPN_ALIAS.get(r["scheme"], [r["scheme"].replace("-", " ")]):
            o = out[gsp]
            o["ev"] += 1
            o["h"] += _f(r["duration_hours"])
            o["e"] += _f(r["estimated_energy_lost_kwh"]) / 1000 / len(UKPN_ALIAS.get(r["scheme"], [0]))
            o["con"] += r["driver"] == "Constraint"
            o["t"].append(r["start_time_utc"][:10])
            o["src"].add(r["scheme"])
    for r in _csv("ukpn_constraint_breaches"):
        for gsp in UKPN_ALIAS.get(r["scheme"], [r["scheme"].replace("-", " ")]):
            out[gsp]["b"] += 1
    return out


def _gsp_polys() -> dict[str, dict]:
    snap = latest_snapshot("ukpn_gsp_areas")
    if snap is None:
        return {}
    out: dict[str, dict] = {}
    for ft in json.loads(snapshot_files(snap)[0].read_text())["features"]:
        out[_norm(re.sub(r"\s*\(.*\)$", "", ft["properties"]["grid_supply_point"]))] = ft
    return out


def _orkney_stats() -> dict[str, float] | None:
    snap = latest_snapshot("ssen_anm_orkney")
    if snap is None:
        return None
    f = next((p for p in snapshot_files(snap) if "2025" in p.name), None)
    if f is None:
        return None
    rows = list(csv.reader(io.StringIO(f.read_text(encoding="utf-8-sig"))))[3:]
    anm, gen, dem = [], [], []
    for r in rows:
        if len(r) >= 8 and r[1] and r[4] and r[7]:
            try:
                anm.append(float(r[1])), gen.append(float(r[4])), dem.append(float(r[7]))
            except ValueError:
                continue
    if not anm:
        return None
    m = lambda x: round(sum(x) / len(x), 2)
    return {"a": m(anm), "g": m(gen), "dm": m(dem)}


def curtailment_points(data_dir: Path) -> list[dict]:
    import polars as pl

    from ..match import NameIndex

    sp = data_dir / "derived" / "site.parquet"
    idx = NameIndex(pl.read_parquet(sp)) if sp.exists() else None
    polys = _gsp_polys()
    feats: list[dict] = []
    # UKPN: aggregated per GSP area; location = resolved GSP site, else the area centre
    for gsp, o in sorted(ukpn_schemes().items()):
        poly = polys.get(_norm(gsp))
        lat = lon = None
        if idx:
            sid = idx.resolve("UKPN", gsp.title(), ("GSP",))
            site = next((s for lst in idx.by.values() for s in lst if s["site_id"] == sid), None) if sid else None
            if site and site["lat"] is not None:
                lat, lon = site["lat"], site["lon"]
        if lat is None and poly:
            lat, lon = _centre(poly["geometry"])
        if lat is None:
            continue
        n_con = o["con"]
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                      "properties": {"n": gsp.title(), "op": "UKPN", "ev": o["ev"], "e": round(o["e"], 1),
                                     "h": round(o["h"]), "b": o["b"] or None,
                                     "r": "Network constraint" if n_con * 2 > o["ev"] else "Non-constraint (e.g. outage)",
                                     "p": f"{min(o['t'])} to {max(o['t'])}"}})
    # NPG: aggregated per scheme, point from the dataset's own geo_point_2d
    npg: dict[str, dict] = {}
    for r in _csv("npg_curtailment_events"):
        o = npg.setdefault(r["scheme_name"], {"ev": 0, "h": 0.0, "e": 0.0, "t": [], "why": defaultdict(int), "pt": r["geo_point_2d"]})
        o["ev"] += 1
        o["h"] += _f(r["duration_hours"])
        o["e"] += _f(r["outage_related_curtailment_total_energy_reduction_mwh"])
        o["t"].append(r["start_time_utc"][:10])
        o["why"][r["reason_for_curtailment"]] += 1
    for name, o in sorted(npg.items()):
        try:
            lat, lon = (float(x) for x in o["pt"].split(","))
        except ValueError:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                      "properties": {"n": name, "op": "NPG", "ev": o["ev"], "e": round(o["e"], 1), "h": round(o["h"]),
                                     "r": max(o["why"], key=o["why"].get), "p": f"{min(o['t'])} to {max(o['t'])}"}})
    return feats


def anm_polygons() -> list[dict]:
    feats: list[dict] = []
    polys, sch = _gsp_polys(), ukpn_schemes()
    for gsp, o in sorted(sch.items()):
        for key in [_norm(gsp)] + (["wymondley"] if gsp == "PELHAM" else []):
            ft = polys.get(key)
            if not ft:
                continue
            name = (key if key == "wymondley" else gsp).title() + " GSP area"
            feats.append({"type": "Feature",
                          "geometry": {"type": ft["geometry"]["type"], "coordinates": _round(ft["geometry"]["coordinates"])},
                          "properties": {"n": name, "op": "UKPN", "k": "GSP curtailment area",
                                         "ev": o["ev"], "e": round(o["e"], 1)}})
    seen: set[str] = set()
    for r in _csv("npg_curtailment_events"):
        if r["scheme_name"] in seen or not r["geo_shape"]:
            continue
        seen.add(r["scheme_name"])
        g = json.loads(r["geo_shape"])
        feats.append({"type": "Feature", "geometry": {"type": g["type"], "coordinates": _round(g["coordinates"])},
                      "properties": {"n": r["scheme_name"] + " GSP area", "op": "NPG", "k": "GSP curtailment area"}})
    snap = latest_snapshot("ssen_primary_supply_areas")
    if snap:
        ork, iow = [], []
        for f in snapshot_files(snap):
            if f.suffix != ".geojson":
                continue
            for ft in json.loads(f.read_text())["features"]:
                if not ft["geometry"]:
                    continue
                la, lo = _centre(ft["geometry"])
                if "shepd" in f.name and la > 58.6 and -3.5 < lo < -2.2:
                    ork.append(ft["geometry"])
                elif "sepd" in f.name and 50.55 < la < 50.78 and -1.6 < lo < -1.05:
                    iow.append(ft["geometry"])
        stats = _orkney_stats() or {}
        for name, geoms, extra in (("Orkney ANM", ork, stats), ("Isle of Wight ANM", iow, {})):
            if not geoms:
                continue
            coords = [poly for g in geoms for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])]
            feats.append({"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": _round(coords)},
                          "properties": {"n": name, "op": "SSEN", "k": "ANM scheme (area approximated by primary supply areas)", **extra}})
    return feats


def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    pts = curtailment_points(ctx.data_dir)
    if pts:
        p = ctx.tmp / "area_curtailment.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": pts}, separators=(",", ":")))
        out["curtailment"] = p
    polys = anm_polygons()
    if polys:
        print(f"area_constraints: curtailment points={len(pts)} anm polygons={len(polys)}")
        src = ctx.tmp / "area_anm.geojson"
        src.write_text(json.dumps({"type": "FeatureCollection", "features": polys}, separators=(",", ":")))
        pm = ctx.tmp / "area_anm.pmtiles"
        subprocess.run(["tippecanoe", "-q", "-f", "-o", str(pm), "-l", "anm_zones", "-Z5", "-z12",
                        "--simplification=4", "--drop-densest-as-needed", str(src)], check=True)
        out["anm_zones"] = pm
    return out
