"""Shared helpers for the LV extras (lv_monitored, lv_smart, lv_headroom_forecast). Not a layer module:
LAYERS is empty so the extras framework skips it. NB: edits here do not change the other modules' input hash,
so touch (or edit) the module you want rebuilt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import polars as pl

from ..normalise.common import best_coords, latest_snapshot, snapshot_files
from ..tools import tool

LAYERS: list = []
SOURCES: list[str] = []


def read_csv_snapshot(source_id: str) -> tuple[pl.DataFrame, dict] | None:
    """Latest ok snapshot of an Opendatasoft CSV export (';' delimiter, BOM) as all-text, plus its meta."""
    snap = latest_snapshot(source_id)
    if snap is None:
        return None
    f = next(p for p in snapshot_files(snap) if p.suffix == ".csv")
    df = pl.read_csv(f, separator=";", infer_schema_length=0, encoding="utf8-lossy")
    df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})
    return df, json.loads((snap / "meta.json").read_text())


def latlon(geopoint: str | None) -> tuple[float | None, float | None]:
    """Opendatasoft CSV geopoints are 'lat, lon'. Validated via common.best_coords (GB bounds, swap fix)."""
    if not geopoint or "," not in geopoint:
        return None, None
    a, b = geopoint.split(",", 1)
    la, lo, _ = best_coords(lat=a.strip(), lon=b.strip())
    return la, lo


def num(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def to_pmtiles(features: list[dict], tmp: Path, name: str, minzoom: int = 9, maxzoom: int = 14) -> Path:
    """Points -> pmtiles with tippecanoe. Dense areas are thinned at low zoom, never at maxzoom's tile budget."""
    src = tmp / f"{name}.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    out = tmp / f"{name}.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", name, f"-Z{minzoom}", f"-z{maxzoom}",
                    "--drop-densest-as-needed", "--maximum-tile-bytes=450000", str(src)], check=True)
    return out


def nearest_uids(points: list[tuple[float, float]], lv: pl.DataFrame, max_m: float = 40.0) -> list[str | None]:
    """asset_uid of the nearest lv_site row (cols asset_uid, lat, lon) within max_m metres of each (lat, lon), else None.
    Used where a monitor list has no id that matches the open lv_site table (NPG, SSEN NeRDA)."""
    import math
    cell = 0.002
    grid: dict[tuple[int, int], list[tuple[float, float, str]]] = {}
    for uid, la, lo in lv.select("asset_uid", "lat", "lon").iter_rows():
        grid.setdefault((int(la / cell), int(lo / cell)), []).append((la, lo, uid))
    out: list[str | None] = []
    for la, lo in points:
        gi, gj = int(la / cell), int(lo / cell)
        best, bd = None, max_m
        for i in (gi - 1, gi, gi + 1):
            for j in (gj - 1, gj, gj + 1):
                for a, b, uid in grid.get((i, j), ()):
                    d = math.hypot((a - la) * 111_000, (b - lo) * 111_000 * math.cos(math.radians(la)))
                    if d <= bd:
                        best, bd = uid, d
        out.append(best)
    return out


def point(lat: float, lon: float, props: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {k: v for k, v in props.items() if v is not None}}


# ---- mount type (ground / pole) for the lv tiles ----

POLE_MAX_KVA = 100   # inference when the operator publishes no mounting: <= 100 kVA is pole,
GROUND_MIN_KVA = 200  # > 200 kVA is ground, in between is left unknown


def mount_exprs() -> tuple[pl.Expr, pl.Expr]:
    """(published, inferred) mount-type expressions over lv_site columns native_ids + transformer_kva: 'g' / 'p' / null.
    Published: ENW native_ids.mount, UKPN .design (PMT/GMT), NPG .site_type, NGED/SSEN .type. SPEN publishes none."""
    def j(k: str) -> pl.Expr:
        return pl.col("native_ids").str.json_path_match(f"$.{k}").str.to_lowercase()
    pub = (
        pl.when(j("mount").str.starts_with("pole") | (j("design") == "pmt") | j("site_type").str.starts_with("pole")
                | j("type").str.starts_with("pole mtd") | j("type").str.starts_with("pole mounted")).then(pl.lit("p"))
        .when(j("mount").str.starts_with("ground") | (j("design") == "gmt") | j("site_type").str.starts_with("ground")
              | j("type").str.starts_with("grd mtd") | j("type").str.starts_with("ground mounted")
              | j("type").str.starts_with("buried") | j("type").str.starts_with("pillar")
              | j("type").str.contains("pad mounted")).then(pl.lit("g"))
    )
    kva = pl.col("transformer_kva")
    inf = pl.when(kva <= POLE_MAX_KVA).then(pl.lit("p")).when(kva > GROUND_MIN_KVA).then(pl.lit("g"))
    return pub, inf


def with_mount(df: pl.DataFrame) -> pl.DataFrame:
    """Add mt ('g'|'p'|null) and mti (1 when inferred from kVA rather than published, else null)."""
    pub, inf = mount_exprs()
    if "native_ids" not in df.columns:
        df = df.with_columns(pl.lit(None, pl.Utf8).alias("native_ids"))
    return (df.with_columns(pub.alias("_pub"), inf.alias("_inf"))
            .with_columns(pl.coalesce("_pub", "_inf").alias("mt"),
                          pl.when(pl.col("_pub").is_null() & pl.col("_inf").is_not_null()).then(1).alias("mti"))
            .drop("_pub", "_inf"))
