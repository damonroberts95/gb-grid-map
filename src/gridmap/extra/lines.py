"""DNO-published overhead line routes (UKPN, NPG, ENW, SSEN) as two vector-tile layers:
EHV/HV overhead lines coloured by voltage, and LV overhead lines (the low-voltage network).
Underground cables are mostly shared-licence/gated and are not included; see docs/sources/lines.md."""

from __future__ import annotations

import io
import json
import re
import struct
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

ATTR = ("Overhead lines: UK Power Networks, Northern Powergrid, Electricity North West, SSEN Distribution open data "
        "(CC BY 4.0 / Northern Powergrid Open Data Licence v1.0)")

SOURCES = [
    "ukpn_oh_132kv", "ukpn_oh_66kv", "ukpn_oh_33kv", "ukpn_oh_hv", "ukpn_oh_lv",
    "npg_oh_ehv", "npg_oh_hv", "npg_oh_lv",
    "enw_oh_132kv", "enw_oh_33kv", "enw_oh_11kv", "enw_oh_66kv", "enw_oh_lv",
    "ssen_overhead_lines_routes",
]

# (source id, operator)
GEOJSON_HV = [
    ("ukpn_oh_132kv", "UKPN"), ("ukpn_oh_66kv", "UKPN"), ("ukpn_oh_33kv", "UKPN"), ("ukpn_oh_hv", "UKPN"),
    ("npg_oh_ehv", "NPG"), ("npg_oh_hv", "NPG"),
    ("enw_oh_132kv", "ENW"), ("enw_oh_33kv", "ENW"), ("enw_oh_11kv", "ENW"), ("enw_oh_66kv", "ENW"),
]
GEOJSON_LV = [("ukpn_oh_lv", "UKPN"), ("npg_oh_lv", "NPG"), ("enw_oh_lv", "ENW")]

LAYERS = [
    LayerMeta(
        id="dno_lines", title="Overhead lines (DNO, 6.6-132 kV)", group="Network", type="pmtiles", geom="line",
        source_layer="dno_lines", minzoom=4,
        description="Overhead lines, coloured by voltage. UKPN, NPG, ENW, SSEN. No cables. No SPEN or NGED data.",
        color={"prop": "kv", "type": "steps", "default": "#8c8c8c",
               "stops": [[0, "#cc79a7"], [30, "#009e9e"], [100, "#2c7bb6"], [250, "#7b3294"]]},
        size={"prop": "kv", "stops": [[0, 0.8], [11, 1.2], [33, 1.8], [66, 2.4], [132, 3.2]]},
        opacity=0.85,
        popup=[["op", "Operator", None], ["kv", "Voltage", "kV"], ["n", "Circuit", None]],
        legend=[["#cc79a7", "6.6-20 kV (HV)"], ["#009e9e", "33 / 66 kV"], ["#2c7bb6", "132 kV"]],
        attribution=ATTR,
    ),
    LayerMeta(
        id="lv_overhead", title="LV overhead lines (DNO)", group="Demand & LV", type="pmtiles", geom="line",
        source_layer="lv_overhead", minzoom=4,
        description="Overhead LV lines. UKPN, NPG, ENW. 2026. No cables.",
        color="#009e73", size={"prop": "w", "stops": [[0, 0.6], [2, 1.2]]}, opacity=0.8,
        popup=[["op", "Operator", None], ["kv", "Voltage", "kV"]],
        legend=[["#009e73", "LV overhead line"]],
        attribution=ATTR,
    ),
]


# --------------------------------------------------------------------------- shapefile (SSEN) reader
def _dbf_rows(dbf: io.BufferedIOBase, want: set[str]) -> Iterator[dict[str, str]]:
    head = dbf.read(32)
    nrec, hlen, rlen = struct.unpack("<IHH", head[4:12])
    desc = dbf.read(hlen - 32)
    cols, off, i = [], 1, 0
    while desc[i] != 0x0D:
        name = desc[i:i + 11].split(b"\0")[0].decode("ascii", "replace")
        ln = desc[i + 16]
        if name in want:
            cols.append((name, off, off + ln))
        off += ln
        i += 32
    for _ in range(nrec):
        rec = dbf.read(rlen)
        yield {n: rec[a:b].decode("latin-1").strip() for n, a, b in cols}


def _shp_polylines(shp: io.BufferedIOBase) -> Iterator[list[list[tuple[float, float]]] | None]:
    """Yield, per record, a list of parts (each a list of (x, y)); None for null shapes."""
    shp.read(100)
    while True:
        h = shp.read(8)
        if len(h) < 8:
            return
        (clen,) = struct.unpack(">i", h[4:8])
        body = shp.read(clen * 2)
        stype = struct.unpack("<i", body[:4])[0]
        if stype not in (3, 13, 23):
            yield None
            continue
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
        pts = struct.unpack(f"<{2 * npts}d", body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts])
        xy = list(zip(pts[0::2], pts[1::2], strict=True))
        yield [xy[parts[k]:parts[k + 1]] for k in range(nparts)]


_KV = re.compile(r"([\d.]+)\s*(k?v)", re.IGNORECASE)


def parse_kv(v: Any) -> float | None:
    """'11kV' / '415V' / 66000.0 (volts) / '6.6kV' -> kV. None when unknown (never 0)."""
    if v is None:
        return None
    s = str(v).strip()
    if m := _KV.fullmatch(s):
        x = float(m[1])
        return x if m[2].lower() == "kv" else round(x / 1000, 3)
    try:
        x = float(s)
    except ValueError:
        return None
    if x <= 0:
        return None
    return round(x / 1000, 3) if x >= 200 else x  # bare numbers: volts when large, else kV


def _round_coords(g: dict) -> dict:
    def r(c: Any) -> Any:
        return [round(c[0], 5), round(c[1], 5)] if c and isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def _geojson_feats(path: Path, op: str, lv: bool) -> Iterator[dict]:
    for f in json.loads(path.read_text())["features"]:
        g = f.get("geometry")
        if not g or g["type"] not in ("LineString", "MultiLineString"):
            continue
        p = f.get("properties") or {}
        kv = parse_kv(p.get("voltage"))
        if lv:
            props = {"op": op, "kv": kv if kv is not None else 0.4}
        else:
            props = {"op": op, "kv": kv if kv is not None else 0}
            if n := p.get("circuit_name"):
                props["n"] = n[:60]
        yield {"type": "Feature", "geometry": _round_coords(g), "properties": props}


def read_shp_zip(z: Path, crs: int, want: set[str]) -> Iterator[tuple[dict[str, str], list[list[list[float]]]]]:
    """Polyline shapefile inside a zip -> (dbf row subset, parts as [[lon, lat], ...] in WGS84)."""
    from pyproj import Transformer

    tr = Transformer.from_crs(crs, 4326, always_xy=True)
    with zipfile.ZipFile(z) as zf:
        base = next(n[:-4] for n in zf.namelist() if n.lower().endswith(".dbf"))
        with zf.open(base + ".dbf") as dbf, zf.open(base + ".shp") as shp:
            for row, geom in zip(_dbf_rows(dbf, want), _shp_polylines(shp), strict=False):
                if not geom:
                    continue
                parts = []
                for part in geom:
                    xs, ys = tr.transform([p[0] for p in part], [p[1] for p in part])
                    parts.append([[round(x, 5), round(y, 5)] for x, y in zip(xs, ys, strict=True)])
                yield row, parts


def line_geom(parts: list[list[list[float]]]) -> dict:
    return ({"type": "LineString", "coordinates": parts[0]} if len(parts) == 1
            else {"type": "MultiLineString", "coordinates": parts})


def _ssen_feats(snap: Path) -> Iterator[tuple[dict, bool]]:
    """SSEN route shapefiles (BNG). Yields (feature, is_lv). Only existing, overhead segments."""
    for z in sorted(f for f in snapshot_files(snap) if f.suffix == ".zip"):
        for row, parts in read_shp_zip(z, 27700, {"status", "nominal_vo", "mounting"}):
            if "overhead" not in row["mounting"].lower() or (row["status"] and "exist" not in row["status"].lower()):
                continue
            kv = parse_kv(row["nominal_vo"])
            yield ({"type": "Feature", "geometry": line_geom(parts),
                    "properties": {"op": "SSEN", "kv": kv if kv is not None else 0}}, kv is not None and kv < 1)


def _tile(feats_path: Path, out: Path, layer: str, minz: int, maxz: int) -> None:
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", layer, f"-Z{minz}", f"-z{maxz}",
                    "--drop-densest-as-needed", "--drop-smallest-as-needed", "--simplification=4",
                    "--extend-zooms-if-still-dropping", "--maximum-tile-bytes=150000", str(feats_path)], check=True)


def build(ctx: Ctx) -> dict[str, Path]:
    hv_p, lv_p = ctx.tmp / "lines_hv.geojsonl", ctx.tmp / "lines_lv.geojsonl"
    counts = {"hv": 0, "lv": 0}
    with hv_p.open("w") as hv, lv_p.open("w") as lv:
        for lst, is_lv in ((GEOJSON_HV, False), (GEOJSON_LV, True)):
            for sid, op in lst:
                snap = latest_snapshot(sid)
                if snap is None:
                    continue
                f = next((x for x in snapshot_files(snap) if x.suffix == ".geojson"), None)
                if f is None:
                    continue
                for feat in _geojson_feats(f, op, is_lv):
                    (lv if is_lv else hv).write(json.dumps(feat, separators=(",", ":")) + "\n")
                    counts["lv" if is_lv else "hv"] += 1
        snap = latest_snapshot("ssen_overhead_lines_routes")
        if snap is not None:
            for feat, is_lv in _ssen_feats(snap):
                (lv if is_lv else hv).write(json.dumps(feat, separators=(",", ":")) + "\n")
                counts["lv" if is_lv else "hv"] += 1
    out: dict[str, Path] = {}
    if counts["hv"]:
        out["dno_lines"] = ctx.tmp / "dno_lines.pmtiles"
        _tile(hv_p, out["dno_lines"], "dno_lines", 4, 13)
    if counts["lv"]:
        out["lv_overhead"] = ctx.tmp / "lv_overhead.pmtiles"
        _tile(lv_p, out["lv_overhead"], "lv_overhead", 4, 15)
    return out
