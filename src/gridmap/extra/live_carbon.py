"""Regional carbon intensity: NESO Carbon Intensity API (14 DNO regions) painted onto the DNO licence-area
polygons. Live: the API updates every half hour."""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta

SOURCES = ["carbon_regional", "neso_dno_licence_areas"]

# Carbon Intensity API regionid -> NESO licence-area polygon "ID" (verified by area name, 2026-11-12)
REGION_TO_AREA = {1: 17, 2: 18, 3: 16, 4: 15, 5: 23, 6: 13, 7: 21, 8: 14, 9: 11, 10: 10, 11: 22, 12: 20,
                  13: 12, 14: 19}

LAYERS = [
    LayerMeta(
        id="carbon_regions", title="Carbon intensity by region", group="Live", type="pmtiles", geom="polygon",
        source_layer="carbon_regions", minzoom=0, live=True,
        description="Forecast carbon intensity (gCO2 per kWh) of electricity for each of the 14 distribution "
                    "regions in the current half hour, with the generation mix. Source: NESO Carbon Intensity API.",
        color={"prop": "i", "type": "steps", "default": "#cfd6dd",
               "stops": [[0, "#f1eef6"], [50, "#d7b5d8"], [100, "#df65b0"], [150, "#dd1c77"], [200, "#980043"],
                         [300, "#4d0026"]]},
        opacity=0.6,
        popup=[["n", "Region", None], ["i", "Carbon intensity", "gCO2/kWh"], ["x", "Index", None],
               ["mix", "Generation mix", None], ["t", "Half hour from (UTC)", None]],
        legend=[["#f1eef6", "<50 gCO2/kWh"], ["#df65b0", "100-150"], ["#980043", "200-300"], ["#4d0026", "300+"]],
        attribution="Carbon Intensity API (carbonintensity.org.uk), National Energy SO, CC BY 4.0; "
                    "DNO licence areas: NESO Open Data Licence",
    )
]

@lru_cache(maxsize=1)
def _transformer() -> Any:
    from pyproj import Transformer
    return Transformer.from_crs(27700, 4326, always_xy=True)


def bng_to_wgs84(pt: Any) -> list[float]:
    lon, lat = _transformer().transform(pt[0], pt[1])
    return [round(lon, 4), round(lat, 4)]


def _ring(r: list[Any]) -> list[list[float]]:
    return [bng_to_wgs84(p) for p in r]


def reproject(geom: dict[str, Any]) -> dict[str, Any]:
    c = geom["coordinates"]
    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [_ring(r) for r in c]}
    return {"type": "MultiPolygon", "coordinates": [[_ring(r) for r in poly] for poly in c]}


def mix_text(mix: list[dict[str, Any]], n: int = 4) -> str:
    top = [m for m in sorted(mix, key=lambda m: -(m.get("perc") or 0)) if (m.get("perc") or 0) > 0][:n]
    return ", ".join(f"{m['fuel']} {m['perc']:g}%" for m in top)


def regions_from_doc(doc: dict[str, Any]) -> tuple[str | None, dict[int, dict[str, Any]]]:
    d = doc["data"][0]
    return d.get("from"), {r["regionid"]: r for r in d["regions"] if r["regionid"] in REGION_TO_AREA}


def build_features(doc: dict[str, Any], areas: dict[str, Any]) -> list[dict[str, Any]]:
    when, regs = regions_from_doc(doc)
    by_area = {f["properties"]["ID"]: f for f in areas["features"]}
    feats = []
    for rid, r in regs.items():
        f = by_area.get(REGION_TO_AREA[rid])
        if f is None:
            continue
        inten = r.get("intensity") or {}
        feats.append({"type": "Feature", "geometry": reproject(f["geometry"]),
                      "properties": {"n": r["shortname"], "i": inten.get("forecast"), "x": inten.get("index"),
                                     "mix": mix_text(r.get("generationmix") or []), "t": when}})
    return feats


def build(ctx: Ctx) -> dict[str, Path]:
    cs, asn = latest_snapshot("carbon_regional"), latest_snapshot("neso_dno_licence_areas")
    if cs is None or asn is None:
        return {}
    doc = json.loads(next(f for f in snapshot_files(cs) if f.name == "data.json").read_text())
    areas = json.loads(next(f for f in snapshot_files(asn) if f.suffix == ".geojson").read_text())
    feats = build_features(doc, areas)
    if not feats:
        return {}
    src = ctx.tmp / "live_carbon.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = ctx.tmp / "carbon_regions.pmtiles"
    subprocess.run(["tippecanoe", "-q", "-f", "-o", str(out), "-l", "carbon_regions", "-Z0", "-z9",
                    "--simplification=4", "--detect-shared-borders", str(src)], check=True)
    return {"carbon_regions": out}
