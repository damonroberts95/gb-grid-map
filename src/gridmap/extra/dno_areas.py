"""DNO licence areas: which operator runs the network where. Distinct colour per operator, bold borders,
light tint and a name label at country scale. On by default (core orientation layer)."""

from __future__ import annotations

import itertools
import json
import subprocess
from pathlib import Path

from pyproj import Transformer

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["neso_dno_licence_areas", "ons_ni_outline", "tailte_roi_provinces"]

# Okabe-Ito based, distinct from headroom RAG (green/amber/red) and the violet outage colour.
# Keep in sync with web/src/layers.ts OP_COLOURS.
OP_COLOURS = {"UKPN": "#0072B2", "NPG": "#E69F00", "ENW": "#CC79A7", "SPEN": "#56B4E9",
              "NGED": "#996633", "SSEN": "#009E73", "NIE": "#999933",  # NIE area = NI outline (ONS)
              "ESB": "#882255"}  # ESB area = Republic of Ireland (Tailte Éireann provinces, merged)
OP_NAME = {"ENWL": "ENW"}  # NESO file says ENWL; we use ENW everywhere else

LAYERS = [
    LayerMeta(
        id="dno_areas", title="Operator areas", group="Areas", type="pmtiles", geom="polygon",
        source_layer="dno_areas", minzoom=0, default_on=True,
        description="The distribution areas and their operators. NESO, May 2024. Northern Ireland: ONS outline. Ireland: Tailte Éireann.",
        color={"prop": "op", "type": "categorical", "stops": [[k, v] for k, v in OP_COLOURS.items()],
               "default": "#888888"},
        size=2.2, opacity=0.25,
        popup=[["n", "Licence area", None], ["opn", "Operator", None], ["gsp_group", "GSP group", None]],
        legend=[[v, k] for k, v in OP_COLOURS.items()],
        attribution="DNO licence areas: NESO (NESO Open Data Licence). NI outline: ONS (OGL v3), contains OS data (c) Crown copyright. "
                    "Ireland outline: contains Tailte Éireann data (CC BY 4.0)",
        label="lbl", label_maxzoom=8.5,
    )
]


def _ring(coords, tf):
    return [list(tf.transform(x, y)) for x, y, *_ in coords]


def ni_area() -> list[dict]:
    """NIE's licence area is all of Northern Ireland: the ONS country outline (already WGS84)."""
    snap = latest_snapshot("ons_ni_outline")
    if snap is None:
        return []
    feats = []
    for f in json.loads((snap / "data.json").read_text()).get("features", []):
        g = f["geometry"]
        coords = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        props = {"op": "NIE", "opn": "NIE Networks", "n": "Northern Ireland", "gsp_group": None}
        feats.append({"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": coords}, "properties": props})
        outer = max((c[0] for c in coords), key=len)
        cx, cy = sum(x for x, *_ in outer) / len(outer), sum(y for _, y, *_ in outer) / len(outer)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [cx, cy]},
                      "properties": {**props, "lbl": "NIE\nNorthern Ireland"}})
    return feats


def _area(ring: list) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0, *_), (x1, y1, *_) in itertools.pairwise(ring)) / 2


def _inside(pt, ring) -> bool:
    x, y = pt[0], pt[1]
    hit = False
    for (x0, y0, *_), (x1, y1, *_) in itertools.pairwise(ring):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def dissolve(polys: list[list[list]]) -> list[list[list]] | None:
    """Merge polygons that share borders (same vertices, as in one generalised boundary set) into one
    MultiPolygon: drop every edge used twice, chain the rest back into rings, put holes into their outer ring.
    None when the edges do not close into rings (borders not shared exactly): the caller keeps the parts."""
    key = lambda p: (round(p[0], 6), round(p[1], 6))
    count: dict[frozenset, int] = {}
    edges = []
    for poly in polys:
        for ring in poly:
            for a, b in itertools.pairwise(ring):
                ka, kb = key(a), key(b)
                if ka == kb:
                    continue
                edges.append((ka, kb))
                count[frozenset((ka, kb))] = count.get(frozenset((ka, kb)), 0) + 1
    nxt: dict[tuple, list[tuple]] = {}
    for ka, kb in edges:
        if count[frozenset((ka, kb))] == 1:
            nxt.setdefault(ka, []).append(kb)
    rings = []
    while nxt:
        start = next(iter(nxt))
        ring, cur = [start], start
        while True:
            outs = nxt.get(cur)
            if not outs:
                return None
            prev, cur = cur, outs.pop()
            if not outs:
                del nxt[prev]
            ring.append(cur)
            if cur == start:
                break
        if len(ring) >= 4:
            rings.append([list(p) for p in ring])
    sign = 1 if _area(max((p[0] for p in polys), key=lambda r: abs(_area(r)))) > 0 else -1
    outers = [r for r in rings if _area(r) * sign > 0]
    holes = [r for r in rings if _area(r) * sign <= 0]
    out = [[o] for o in outers]
    for hl in holes:
        home = next((p for p in sorted(out, key=lambda p: abs(_area(p[0]))) if _inside(hl[0], p[0])), None)
        if home is not None:
            home.append(hl)
    return out


def roi_area() -> list[dict]:
    """ESB Networks' area is the Republic of Ireland: the four Tailte Éireann provinces merged into one outline."""
    snap = latest_snapshot("tailte_roi_provinces")
    if snap is None:
        return []
    polys = []
    for f in json.loads((snap / "data.json").read_text()).get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") in ("Polygon", "MultiPolygon"):
            polys += g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    if not polys:
        return []
    coords = dissolve(polys) or polys
    props = {"op": "ESB", "opn": "ESB Networks", "n": "Ireland", "gsp_group": None}
    outer = max((c[0] for c in coords), key=len)
    cx, cy = sum(x for x, *_ in outer) / len(outer), sum(y for _, y, *_ in outer) / len(outer)
    return [{"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": coords}, "properties": props},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [cx, cy]},
             "properties": {**props, "lbl": "ESB\nIreland"}}]


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
    feats += ni_area() + roi_area()
    src = ctx.tmp / "dno_areas.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = ctx.tmp / "dno_areas.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "dno_areas", "-Z0", "-z10", "-r1",
                    "--simplification=6", "--detect-shared-borders", "--no-tiny-polygon-reduction", str(src)],
                   check=True)
    return {"dno_areas": out}
