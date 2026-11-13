"""Shared helpers for the offshore extras (offshore_wind, offshore_cables). No layers of its own, so the extras
framework skips it. NB: edits here do not change the other modules' input hash; touch them to rebuild."""

from __future__ import annotations

import io
import json
import struct
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from .lines import _dbf_rows

LAYERS: list = []
SOURCES: list[str] = []

# Colour-blind-safe (Okabe-Ito) status palette shared by the offshore layers. Not the headroom red/amber/green.
STATUS_COLORS = [
    ["Operational", "#0072B2"], ["Under construction", "#E69F00"], ["Consented", "#CC79A7"],
    ["In planning", "#56B4E9"], ["Lease / option round", "#7f7f7f"], ["Decommissioned", "#b9b9b9"],
]
STATUS_SPEC = {"prop": "s", "type": "categorical", "default": "#7f7f7f", "stops": STATUS_COLORS}

_STATUS = {
    "active/in operation": "Operational", "operational": "Operational", "in operation": "Operational",
    "under construction": "Under construction",
    "consented": "Consented", "government support on offer": "Consented",
    "in planning": "In planning", "pre-planning application": "In planning", "pre planning": "In planning",
    "inactive/decommissioned": "Decommissioned", "decommissioned": "Decommissioned",
}


def norm_status(v: Any) -> str:
    return _STATUS.get(str(v or "").strip().lower(), "In planning")


def read_geojson(source_id: str) -> list[dict]:
    """Features of the latest json snapshot of an http source ([] when never fetched)."""
    snap = latest_snapshot(source_id)
    if snap is None:
        return []
    f = next((p for p in snapshot_files(snap) if p.suffix == ".json"), None)
    return json.loads(f.read_text())["features"] if f else []


def round_geom(g: dict, nd: int = 5) -> dict:
    def r(c: Any) -> Any:
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def _rings(g: dict) -> Iterator[list[list[float]]]:
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
    for poly in polys:
        yield from poly


def pip(pt: tuple[float, float], g: dict) -> bool:
    """Point in (Multi)Polygon, even-odd over all rings (so holes work)."""
    x, y = pt
    inside = False
    for ring in _rings(g):
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def rep_point(g: dict) -> tuple[float, float]:
    """A point inside the largest outer ring: bbox centre if it is inside, else the first vertex nudged to the
    ring's mean (good enough for site matching; not a true pole of inaccessibility)."""
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    outer = max((p[0] for p in polys), key=len)
    xs, ys = [c[0] for c in outer], [c[1] for c in outer]
    cand = [((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), (sum(xs) / len(xs), sum(ys) / len(ys))]
    for c in cand:
        if pip(c, g):
            return c
    return (outer[0][0], outer[0][1])


def _signed_area(ring: list[tuple[float, float]]) -> float:
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
               for i in range(len(ring))) / 2


def _shp_polygons(shp: io.BufferedIOBase) -> Iterator[list[list[tuple[float, float]]] | None]:
    shp.read(100)
    while True:
        h = shp.read(8)
        if len(h) < 8:
            return
        (clen,) = struct.unpack(">i", h[4:8])
        body = shp.read(clen * 2)
        if struct.unpack("<i", body[:4])[0] not in (5, 15, 25):
            yield None
            continue
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
        pts = struct.unpack(f"<{2 * npts}d", body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts])
        xy = list(zip(pts[0::2], pts[1::2], strict=True))
        yield [xy[parts[k]:parts[k + 1]] for k in range(nparts)]


def read_polygon_zip(z: Path, crs: int, want: set[str]) -> Iterator[tuple[dict[str, str], dict]]:
    """Polygon shapefile in a zip -> (dbf row subset, WGS84 MultiPolygon geometry). Shapefile outer rings are
    clockwise and holes anticlockwise; each hole is attached to the last outer ring."""
    from pyproj import Transformer

    tr = Transformer.from_crs(crs, 4326, always_xy=True)
    with zipfile.ZipFile(z) as zf:
        base = next(n[:-4] for n in zf.namelist() if n.lower().endswith(".dbf"))
        with zf.open(base + ".dbf") as dbf, zf.open(base + ".shp") as shp:
            for row, rings in zip(_dbf_rows(dbf, want), _shp_polygons(shp), strict=False):
                if not rings:
                    continue
                polys: list[list[list[list[float]]]] = []
                for ring in rings:
                    xs, ys = tr.transform([p[0] for p in ring], [p[1] for p in ring])
                    ll = [[round(x, 5), round(y, 5)] for x, y in zip(xs, ys, strict=True)]
                    if _signed_area(ring) < 0 or not polys:  # clockwise in projected space = outer
                        polys.append([ll])
                    else:
                        polys[-1].append(ll)
                yield row, {"type": "MultiPolygon", "coordinates": polys}


def tile(feats: list[dict], out: Path, layer: str, minz: int, maxz: int, extra: list[str] | None = None) -> Path:
    src = out.with_suffix(".geojsonl")
    src.write_text("\n".join(json.dumps(f, separators=(",", ":")) for f in feats))
    subprocess.run(["tippecanoe", "-q", "-f", "-o", str(out), "-l", layer, f"-Z{minz}", f"-z{maxz}",
                    *(extra or []), str(src)], check=True)
    return out
