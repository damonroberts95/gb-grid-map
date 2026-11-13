"""OpenStreetMap power infrastructure for Great Britain: overhead lines, underground/submarine cables and
high-voltage substations. Source: Geofabrik GB extract, filtered with osmium-tool, tiled with tippecanoe."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta
from ._osm import export_geojsonseq, pbf_in

SOURCES = ["osm_gb_pbf"]
ATTR = "© OpenStreetMap contributors (ODbL)"

BAND_STOPS = [[0, "#8c8c8c"], [1, "#cc79a7"], [30, "#009e9e"], [100, "#2c7bb6"], [250, "#7b3294"]]

LAYERS = [
    LayerMeta(
        id="power_lines", title="Power lines (OpenStreetMap)", group="Network", type="pmtiles", geom="line",
        source_layer="power_lines", minzoom=5,
        description="Overhead lines, coloured by voltage. OpenStreetMap. Grey has no voltage.",
        color={"prop": "kv", "type": "steps", "default": "#8c8c8c", "stops": BAND_STOPS},
        size={"prop": "kv", "stops": [[0, 0.7], [1, 0.9], [30, 1.5], [100, 2.2], [250, 3]]},
        opacity=0.85,
        popup=[["n", "Name or ref", None], ["kv", "Voltage", "kV"], ["op", "Operator", None], ["cc", "Circuits", None]],
        legend=[["#7b3294", "400 / 275 kV"], ["#2c7bb6", "132 kV"], ["#009e9e", "66 / 33 kV"],
                ["#cc79a7", "11 kV and below"], ["#8c8c8c", "Voltage not mapped"]],
        attribution=ATTR,
    ),
    LayerMeta(
        id="power_cables", title="Underground cables (OpenStreetMap)", group="Network", type="pmtiles", geom="line",
        source_layer="power_cables", minzoom=8,
        description="Underground and subsea cables. OpenStreetMap. Far from complete.",
        color="#8c510a", size={"prop": "kv", "stops": [[0, 1], [30, 1.4], [100, 2], [250, 2.8]]}, opacity=0.85,
        popup=[["n", "Name or ref", None], ["kv", "Voltage", "kV"], ["op", "Operator", None], ["cc", "Circuits", None]],
        legend=[["#8c510a", "Underground / submarine cable"]],
        attribution=ATTR,
    ),
    LayerMeta(
        id="power_substations", title="Grid substations 132 kV+ (OpenStreetMap)", group="Network", type="geojson",
        geom="point", minzoom=6,
        description="Substations of 132 kV and above. OpenStreetMap.",
        color={"prop": "kv", "type": "steps", "default": "#2c7bb6",
               "stops": [[0, "#2c7bb6"], [250, "#7b3294"]]},
        size={"prop": "kv", "stops": [[0, 3.5], [250, 5.5]]},
        popup=[["n", "Name", None], ["kv", "Highest voltage", "kV"], ["op", "Operator", None]],
        legend=[["#7b3294", "275 / 400 kV"], ["#2c7bb6", "132 kV"]],
        attribution=ATTR,
    ),
]


def max_kv(v: str | None) -> float:
    """OSM `voltage` ('400000', '275000;132000', '33 kV') -> highest value in kV; 0 when absent/unparseable."""
    best = 0.0
    for tok in re.split(r"[;,/]", v or ""):
        m = re.fullmatch(r"\s*([\d.]+)\s*(kv|v)?\s*", tok, re.IGNORECASE)
        if not m:
            continue
        x = float(m[1])
        x = x if (m[2] or "").lower() == "kv" else x / 1000
        if 0 < x < 1500:
            best = max(best, round(x, 2))
    return best


def _min_zoom(kv: float) -> int:
    return 5 if kv >= 100 else 8 if kv >= 30 else 10


def seq_features(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """GeoJSONSeq lines from `osmium export` -> ('line'|'cable', feature). Keeps few, short properties."""
    for raw in lines:
        raw = raw.strip().lstrip("\x1e")
        if not raw:
            continue
        f = json.loads(raw)
        g, p = f.get("geometry"), f.get("properties") or {}
        if not g or g["type"] not in ("LineString", "MultiLineString"):
            continue
        kind = "cable" if p.get("power") == "cable" else "line" if p.get("power") in ("line", "minor_line") else None
        if kind is None:
            continue
        kv = max_kv(p.get("voltage"))
        props: dict = {"kv": kv}
        if n := (p.get("name") or p.get("ref")):
            props["n"] = n[:50]
        if o := p.get("operator"):
            props["op"] = o[:40]
        if c := p.get("circuits"):
            props["cc"] = c[:6]
        if p.get("power") == "cable" and p.get("location") not in (None, "underground", "underwater", "seabed"):
            continue  # cables mapped overhead/overground are really lines drawn as cable; skip
        feat = {"type": "Feature", "geometry": {"type": g["type"], "coordinates": _round(g["coordinates"])},
                "properties": props, "tippecanoe": {"minzoom": _min_zoom(kv)}}
        yield kind, feat


def _round(c):
    return [round(c[0], 5), round(c[1], 5)] if c and isinstance(c[0], (int, float)) else [_round(x) for x in c]


def _centroid(g: dict) -> list[float] | None:
    if g["type"] == "Point":
        return [round(g["coordinates"][0], 5), round(g["coordinates"][1], 5)]
    ring = g["coordinates"][0] if g["type"] == "Polygon" else (g["coordinates"][0][0] if g["type"] == "MultiPolygon" else None)
    if not ring:
        return None
    return [round(sum(p[0] for p in ring) / len(ring), 5), round(sum(p[1] for p in ring) / len(ring), 5)]


def substation_features(lines: Iterable[str], min_kv: float = 132) -> Iterator[dict]:
    for raw in lines:
        raw = raw.strip().lstrip("\x1e")
        if not raw:
            continue
        f = json.loads(raw)
        p = f.get("properties") or {}
        if p.get("power") != "substation" or not f.get("geometry"):
            continue
        kv = max_kv(p.get("voltage"))
        if kv < min_kv:
            continue
        pt = _centroid(f["geometry"])
        if pt is None:
            continue
        props: dict = {"kv": kv, "n": (p.get("name") or p.get("ref") or "")[:50]}
        if o := p.get("operator"):
            props["op"] = o[:40]
        yield {"type": "Feature", "geometry": {"type": "Point", "coordinates": pt}, "properties": props}


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("osm_gb_pbf")
    if snap is None:
        return {}
    pbf = pbf_in(snapshot_files(snap))
    t = ctx.tmp
    # one pyosmium pass: power lines/cables as LineStrings, substations as Points/MultiPolygons
    export_geojsonseq(pbf, t / "osmp.geojsonseq", {"power": {"line", "minor_line", "cable", "substation"}},
                      nodes=True, ways=True, areas=True)

    out: dict[str, Path] = {}
    paths = {"line": t / "osmp_lines.geojsonl", "cable": t / "osmp_cables.geojsonl"}
    counts = {"line": 0, "cable": 0}
    with paths["line"].open("w") as fl, paths["cable"].open("w") as fc:
        files = {"line": fl, "cable": fc}
        with (t / "osmp.geojsonseq").open() as src:
            for kind, feat in seq_features(src):
                files[kind].write(json.dumps(feat, separators=(",", ":")) + "\n")
                counts[kind] += 1
    for kind, lid, minz in (("line", "power_lines", 5), ("cable", "power_cables", 8)):
        if counts[kind]:
            out[lid] = t / f"{lid}.pmtiles"
            subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out[lid]), "-l", lid, f"-Z{minz}", "-z13",
                            "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(paths[kind])],
                           check=True)
    with (t / "osmp.geojsonseq").open() as src:
        feats = list(substation_features(src))
    if feats:
        out["power_substations"] = t / "power_substations.geojson"
        out["power_substations"].write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                       separators=(",", ":")))
    return out
