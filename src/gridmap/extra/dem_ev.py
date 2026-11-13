"""Public EV charge points from Open Charge Map (CC BY 4.0).

Fetched weekly by the scheduler (source `ocm_gb_poi`, portal `ocm`, fetch/ocm.py; needs OCM_API_KEY). `build`
below is pure: it only reads that snapshot (pois.json).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..normalise.common import best_coords, latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["ocm_gb_poi"]
API = "https://api.openchargemap.io/v3"
USER_AGENT = "gridmap-local/0.1"
CAP = 5000
GB_BOX = (49.8, -8.7, 60.9, 1.8)  # lat_min, lon_min, lat_max, lon_max
ATTRIBUTION = ("Data from Open Charge Map and its contributors, licensed under CC BY 4.0 "
               "(some records originate from the National Chargepoint Registry, Open Government Licence v2.0)")
_DROP_STATUS = {100, 150, 200, 210}  # not operational, planned, removed, duplicate

LAYERS = [
    LayerMeta(
        id="ev_chargers", title="EV charge points", group="Demand & LV", type="pmtiles", geom="point",
        source_layer="ev_chargers", minzoom=8,
        description="Public EV charge points. Colour is fastest connector. Open Charge Map.",
        color={"prop": "kw", "type": "steps", "default": "#999999",
               "stops": [[0, "#56B4E9"], [7.01, "#0072B2"], [22.01, "#CC79A7"], [50.01, "#882255"], [150.01, "#1b1b3a"]]},
        size={"prop": "kw", "stops": [[0, 2.5], [22.01, 3.5], [50.01, 5], [150.01, 7]]},
        popup=[["nm", "Site", None], ["op", "Operator", None], ["kw", "Fastest connector", "kW"],
               ["c", "Connectors", None], ["use", "Access", None]],
        legend=[["#56B4E9", "7 kW or less"], ["#0072B2", "7-22 kW"], ["#CC79A7", "22-50 kW"],
                ["#882255", "50-150 kW"], ["#1b1b3a", "over 150 kW"], ["#999999", "power not stated"]],
        attribution=ATTRIBUTION,
    )
]


# ---------- build (pure) ----------

def _in_gb(lat: float, lon: float) -> bool:
    """best_coords already bounds to the GB box; also drop Ireland / Northern Ireland, which OCM files under GB."""
    if lon < -5.9 and 51.3 < lat < 55.2:
        return False
    return not (lon < -5.2 and 54.0 < lat < 55.2)


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("ocm_gb_poi")
    if snap is None:
        return {}
    doc = json.loads(next(p for p in snapshot_files(snap) if p.name == "pois.json").read_text())
    ops, use = doc["operators"], doc["usage"]
    feats = []
    for p in doc["pois"]:
        if p["st"] in _DROP_STATUS:
            continue
        la, lo, _ = best_coords(p["lat"], p["lon"])
        if la is None or not _in_gb(la, lo):
            continue
        props = {"nm": p["nm"], "op": ops.get(str(p["op"])) if p["op"] not in (None, 44) else None,  # 44 = unknown
                 "kw": p["kw"], "c": p["c"], "use": use.get(str(p["use"])) if p["use"] else None}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 5), round(la, 5)]},
                      "properties": {k: v for k, v in props.items() if v is not None}})
    if not feats:
        return {}
    src = ctx.tmp / "dem_ev_chargers.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / "dem_ev_chargers.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "ev_chargers", "-Z8", "-z14",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)
    return {"ev_chargers": out}
