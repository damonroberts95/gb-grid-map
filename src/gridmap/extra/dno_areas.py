"""DNO licence areas: which operator runs the network where. Distinct colour per operator, bold borders,
light tint and a name label at country scale. On by default (core orientation layer)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pyproj import Transformer

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["neso_dno_licence_areas"]

# Okabe-Ito based, distinct from headroom RAG (green/amber/red) and the violet outage colour.
# Keep in sync with web/src/layers.ts OP_COLOURS.
OP_COLOURS = {"UKPN": "#0072B2", "NPG": "#E69F00", "ENW": "#CC79A7", "SPEN": "#56B4E9",
              "NGED": "#996633", "SSEN": "#009E73"}  # NIE (#999933) has no NESO area
OP_NAME = {"ENWL": "ENW"}  # NESO file says ENWL; we use ENW everywhere else

LAYERS = [
    LayerMeta(
        id="dno_areas", title="Operator areas", group="Areas", type="pmtiles", geom="polygon",
        source_layer="dno_areas", minzoom=0, default_on=True,
        description="The 14 distribution areas and their operators. NESO, May 2024.",
        color={"prop": "op", "type": "categorical", "stops": [[k, v] for k, v in OP_COLOURS.items()],
               "default": "#888888"},
        size=2.2, opacity=0.25,
        popup=[["n", "Licence area", None], ["opn", "Operator", None], ["gsp_group", "GSP group", None]],
        legend=[[v, k] for k, v in OP_COLOURS.items()],
        attribution="DNO licence areas: NESO (NESO Open Data Licence)",
        label="lbl", label_maxzoom=8.5,
    )
]


def _ring(coords, tf):
    return [list(tf.transform(x, y)) for x, y, *_ in coords]


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("neso_dno_licence_areas")
    if snap is None:
        return {}
    fc = json.loads(next(f for f in snapshot_files(snap) if f.suffix == ".geojson").read_text())
    tf = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        op = OP_NAME.get(p["DNO"], p["DNO"])
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        coords = [[_ring(r, tf) for r in poly] for poly in polys]
        props = {"op": op, "opn": p.get("DNO_Full"), "n": p.get("Area"), "gsp_group": p.get("Name")}
        feats.append({"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": coords},
                      "properties": props})
        # label point: centroid of the largest polygon's outer ring (vertex mean is fine at this scale)
        outer = max((c[0] for c in coords), key=len)
        cx, cy = sum(x for x, _ in outer) / len(outer), sum(y for _, y in outer) / len(outer)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [cx, cy]},
                      "properties": {**props, "lbl": f"{op}\n{p.get('Area')}"}})
    src = ctx.tmp / "dno_areas.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = ctx.tmp / "dno_areas.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "dno_areas", "-Z0", "-z10", "-r1",
                    "--simplification=6", "--detect-shared-borders", "--no-tiny-polygon-reduction", str(src)],
                   check=True)
    return {"dno_areas": out}
