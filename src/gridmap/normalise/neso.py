"""NESO: GSP list (FES regional breakdown) -> asset rows of kind GSP, operator NESO.

Gives every DNO a shared set of transmission-interface parents with coordinates.
"""

from __future__ import annotations

import polars as pl

from .common import (
    add_coords,
    as_of,
    ids_json,
    latest_snapshot,
    snapshot_files,
    snapshot_meta,
    write_table,
)


def run() -> dict[str, int]:
    snap = latest_snapshot("neso_fes_gsp_info")
    if snap is None:
        return {}
    meta = snapshot_meta(snap)
    df = pl.read_csv(snapshot_files(snap)[0], infer_schema=False, encoding="utf8-lossy")
    df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})
    df = add_coords(df, "Latitude", "Longitude")
    out = df.select(
        ("NESO:" + pl.col("GSP ID").str.strip_chars()).alias("asset_uid"),
        pl.lit("NESO").alias("operator"),
        pl.col("GSP Group").alias("licence_area"),
        pl.col("Name").str.strip_chars().alias("name"),
        pl.lit("GSP").alias("kind"),
        "lat", "lon", "coord_quality",
        pl.struct("GSP ID", "Minor FLOP").map_elements(
            lambda r: ids_json(gsp_id=r["GSP ID"], minor_flop=r["Minor FLOP"]), return_dtype=pl.Utf8
        ).alias("native_ids"),
        pl.lit("neso_fes_gsp_info").alias("source_id"),
        pl.lit(as_of(meta)).alias("as_of"),
    ).unique("asset_uid", keep="first")
    assert out.height > 300, f"NESO GSP list unexpectedly small: {out.height}"
    write_table("NESO", "asset", out)
    return {"asset": out.height}
