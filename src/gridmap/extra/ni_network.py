"""Northern Ireland (NIE Networks) substations and secondary (LV) transformers.

The NIE Opendatasoft portal reports data_visible=false for every dataset anonymously: exports are header-only
until a free account key is supplied in NIE_API_KEY. Without a snapshot this module builds nothing (no layers).
Column names below come from the live export headers (2026-11-12); row values are unverified until a key exists.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["nie_sites_grid_bulk_primary", "nie_secondary_transformers"]
ATTR = "Contains data from the NIE Networks open data portal, licensed under CC BY 4.0."

LAYERS = [
    LayerMeta(
        id="ni_sites", title="NI substations (NIE Networks)", group="Network", type="geojson", geom="point",
        description="Northern Ireland grid, bulk and primary substations. NIE Networks. No headroom.",
        color={"prop": "k", "type": "categorical", "default": "#6a51a3",
               "stops": [["GSP", "#3f007d"], ["BSP", "#6a51a3"], ["Primary", "#9e9ac8"]]},
        size={"prop": "k", "stops": [["Primary", 3], ["BSP", 5], ["GSP", 7]]},
        popup=[["n", "Site", None], ["k", "Level", None], ["v", "Voltage", "kV"], ["a", "Area", None]],
        legend=[["#3f007d", "Grid supply point"], ["#6a51a3", "Bulk supply point"], ["#9e9ac8", "Primary"]],
        attribution=ATTR,
    ),
    LayerMeta(
        id="ni_secondary", title="NI secondary substations (LV)", group="Demand & LV", type="pmtiles",
        geom="point", source_layer="ni_secondary", minzoom=4,
        description="Northern Ireland secondary transformers. Size is rating. NIE Networks. No loading.",
        color="#2b8cbe", size={"prop": "r", "stops": [[0, 2], [500, 3], [1000, 5]]},
        popup=[["n", "Site", None], ["c", "Transformers", None], ["r", "Installed rating", "kVA"]],
        legend=[["#2b8cbe", "Secondary transformer site"]],
        attribution=ATTR,
    ),
]

LEVELS = {"grid supply": "GSP", "bulk supply": "BSP", "primary": "Primary"}


@lru_cache(maxsize=1)
def _irish_grid() -> Any:
    from pyproj import Transformer
    return Transformer.from_crs(29903, 4326, always_xy=True)


def _num(v: Any) -> float | None:
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f


def ni_coords(geopoint: Any, x: Any, y: Any) -> tuple[float, float] | None:
    """WGS84 (lat, lon) inside Northern Ireland, from 'lat, lon' geopoint else Irish Grid x/y."""
    cands: list[tuple[float, float]] = []
    if isinstance(geopoint, str) and "," in geopoint:
        a, b = (_num(p) for p in geopoint.split(",", 1))
        if a is not None and b is not None:
            cands.append((a, b))
    fx, fy = _num(x), _num(y)
    if fx is not None and fy is not None and fx > 0 and fy > 0:
        lon, lat = _irish_grid().transform(fx, fy)
        cands.append((lat, lon))
    for lat, lon in cands:
        if 54.0 <= lat <= 55.5 and -8.3 <= lon <= -5.3:
            return round(lat, 5), round(lon, 5)
    return None


def level_of(site_type: Any, dataset: Any) -> str:
    s = f"{site_type or ''} {dataset or ''}".lower()
    for key, v in LEVELS.items():
        if key in s:
            return v
    return "Primary" if "primary" in s else "Other"


def _read(source_id: str) -> pl.DataFrame | None:
    snap = latest_snapshot(source_id)
    if snap is None:
        return None
    f = next((p for p in snapshot_files(snap) if p.suffix == ".csv"), None)
    if f is None:
        return None
    df = pl.read_csv(f, separator=";", encoding="utf8-lossy", infer_schema=False)
    df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})
    return df if df.height else None


def site_features(df: pl.DataFrame) -> list[dict[str, Any]]:
    feats, seen = [], set()
    geo = "site_geopoint" if "site_geopoint" in df.columns else None
    for r in df.iter_rows(named=True):
        c = ni_coords(r.get(geo) if geo else None, r.get("site_longitude_x_irish_grid"),
                      r.get("site_latitude_y_irish_grid"))
        key = r.get("site_functional_location") or r.get("site_name")
        if c is None or key in seen:
            continue
        seen.add(key)
        kv = _num(str(r.get("site_voltage") or "").lower().replace("kv", ""))
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c[1], c[0]]},
                      "properties": {"n": r.get("site_name"), "k": level_of(r.get("site_type"), r.get("dataset")),
                                     "v": kv, "a": r.get("license_area")}})
    return feats


def secondary_features(df: pl.DataFrame) -> list[dict[str, Any]]:
    """One point per site: transformer count and installed kVA (units converted; unknown stays null)."""
    sites: dict[Any, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        c = ni_coords(r.get("site_geopoint"), r.get("site_longitude_x_irish_grid"),
                      r.get("site_latitude_y_irish_grid"))
        if c is None:
            continue
        key = r.get("site_functional_location") or r.get("site_name") or c
        s = sites.setdefault(key, {"c": c, "n": r.get("site_name"), "k": 0, "kva": None})
        s["k"] += 1
        v = _num(r.get("nominal_rating"))
        unit = str(r.get("nominal_rating_unit_of_measurement") or "").lower()
        if v is not None:
            mult = {"kva": 1.0, "mva": 1000.0, "va": 0.001}.get(unit)
            if mult is not None:
                s["kva"] = (s["kva"] or 0.0) + v * mult
    return [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [s["c"][1], s["c"][0]]},
             "properties": {"n": s["n"], "c": s["k"], "r": round(s["kva"]) if s["kva"] is not None else None}}
            for s in sites.values()]


def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    df = _read("nie_sites_grid_bulk_primary")
    if df is not None:
        feats = site_features(df)
        if feats:
            p = ctx.tmp / "ni_sites.geojson"
            p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
            out["ni_sites"] = p
    df = _read("nie_secondary_transformers")
    if df is not None:
        feats = secondary_features(df)
        if feats:
            src = ctx.tmp / "ni_secondary.geojson"
            src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
            pm = ctx.tmp / "ni_secondary.pmtiles"
            subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(pm), "-l", "ni_secondary", "-Z4", "-z14",
                            "-B8", "--drop-densest-as-needed", "--maximum-tile-bytes=150000", str(src)], check=True)
            out["ni_secondary"] = pm
    return out
