"""Live solar (Sheffield Solar PV_Live): current PV generation for each of the 14 distribution regions, painted on
the DNO licence-area polygons and coloured by share of installed PV capacity. PV_Live has no bulk GSP endpoint
(one request per GSP, ~330), so the region (PES) level is the finest we can poll politely every 15 min."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta
from .live_carbon import REGION_TO_AREA, reproject

SOURCES = ["neso_dno_licence_areas"] + [f"pvlive_pes_{i}" for i in range(10, 24)]

# PV_Live pes_id -> (Carbon Intensity regionid, display name). pes_list (2026-11-12) letters are the GSP group codes
# (_A Eastern ... _P North Scotland); region ids follow the same MPAS group, verified against the licence-area names.
PES_REGION = {10: (10, "East of England"), 11: (9, "East Midlands"), 12: (13, "London"), 13: (6, "Merseyside & N Wales"),
              14: (8, "West Midlands"), 15: (4, "North East England"), 16: (3, "North West England"),
              17: (1, "North Scotland"), 18: (2, "South Scotland"), 19: (14, "South East England"),
              20: (12, "Southern England"), 21: (7, "South Wales"), 22: (11, "South West England"),
              23: (5, "Yorkshire")}

STOPS = [[0, "#f2f0e6"], [2, "#fee391"], [10, "#fec44f"], [25, "#fe9929"], [45, "#d95f0e"], [65, "#993404"]]

LAYERS = [
    LayerMeta(
        id="live_pv", title="Solar generation now (PV_Live)", group="Live", type="pmtiles", geom="polygon",
        source_layer="live_pv", minzoom=0, live=True, default_on=False,
        description="Solar output now as % of installed capacity, by region. Sheffield Solar PV_Live. Early values "
                    "can change.",
        color={"prop": "pct", "type": "steps", "default": "#cfd6dd", "stops": STOPS},
        opacity=0.6,
        popup=[["n", "Region", None], ["mw", "PV output", "MW"], ["cap", "Installed PV", "MWp"],
               ["pct", "Of installed capacity", "%"], ["t", "Data as of (UTC)", None]],
        legend=[["#f2f0e6", "<2% of capacity"], ["#fec44f", "10-25%"], ["#d95f0e", "45-65%"], ["#993404", "65%+"]],
        attribution="PV_Live solar generation estimates, Sheffield Solar, University of Sheffield (api.pvlive.uk); "
                    "DNO licence areas: NESO Open Data Licence",
    )
]


def hhmm(ts: str | None) -> str | None:
    """'2026-11-12T16:30:00Z' -> '16:30'."""
    return ts[11:16] if ts and len(ts) >= 16 else None


def region_values(docs: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """{pes_id: parsed PV_Live document} -> {pes_id: {mw, cap, pct, t}} (skips empty / malformed documents)."""
    out: dict[int, dict[str, Any]] = {}
    for pes, doc in docs.items():
        try:
            meta, row = doc["meta"], doc["data"][0]
            r = dict(zip(meta, row, strict=True))
            mw, cap = r["generation_mw"], r["installedcapacity_mwp"]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if mw is None or not cap:
            continue
        out[pes] = {"mw": round(mw, 1), "cap": round(cap), "pct": round(100 * mw / cap, 1),
                    "t": hhmm(r["datetime_gmt"])}
    return out


def build_features(values: dict[int, dict[str, Any]], areas: dict[str, Any]) -> list[dict[str, Any]]:
    by_area = {f["properties"]["ID"]: f for f in areas["features"]}
    feats = []
    for pes, v in values.items():
        rid, name = PES_REGION[pes]
        f = by_area.get(REGION_TO_AREA[rid])
        if f is None:
            continue
        feats.append({"type": "Feature", "geometry": reproject(f["geometry"]), "properties": {"n": name, **v}})
    return feats


def load_docs() -> dict[int, dict[str, Any]]:
    docs = {}
    for pes in PES_REGION:
        snap = latest_snapshot(f"pvlive_pes_{pes}")
        f = next((x for x in snapshot_files(snap) if x.name == "data.json"), None) if snap else None
        if f is not None:
            docs[pes] = json.loads(f.read_text())
    return docs


def build(ctx: Ctx) -> dict[str, Path]:
    asn = latest_snapshot("neso_dno_licence_areas")
    docs = load_docs()
    if asn is None or not docs:
        return {}
    areas = json.loads(next(f for f in snapshot_files(asn) if f.suffix == ".geojson").read_text())
    feats = build_features(region_values(docs), areas)
    if not feats:
        return {}
    src = ctx.tmp / "live_pv.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = ctx.tmp / "live_pv.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "live_pv", "-Z0", "-z9",
                    "--simplification=4", "--detect-shared-borders", str(src)], check=True)
    return {"live_pv": out}

