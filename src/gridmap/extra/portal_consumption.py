"""DESNZ postcode-level domestic electricity consumption (all domestic meters, 2024) as points coloured by mean
kWh per meter. Postcode units from zoom 12, postcode sectors (aggregated) below that. Located with OS Code-Point
Open. Useful as a proxy for local LV demand and for spotting electric-heating areas."""

from __future__ import annotations

import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import polars as pl
from pyproj import Transformer

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta

SOURCES = ["desnz_postcode_elec", "os_codepoint"]

STOPS = [[0, "#ffffcc"], [1500, "#a1dab4"], [2500, "#41b6c4"], [3500, "#2c7fb8"], [5000, "#253494"]]
LEGEND = [["#ffffcc", "under 1,500 kWh"], ["#a1dab4", "1,500 to 2,500"], ["#41b6c4", "2,500 to 3,500"],
          ["#2c7fb8", "3,500 to 5,000"], ["#253494", "over 5,000 kWh/yr"]]

LAYERS = [
    LayerMeta(
        id="elec_postcode_kwh", title="Home electricity use by postcode (2024)", group="Demand & LV",
        type="pmtiles", geom="point", source_layer="elec_postcode_kwh", minzoom=6,
        description="Average yearly electricity used per domestic meter in 2024, for each postcode (zoom in far) or "
                    "postcode sector (zoomed out). High averages often mean electric heating or Economy 7 "
                    "storage heaters. Only postcodes with at "
                    "least 5 meters are published.",
        color={"prop": "kwh", "type": "steps", "default": "#cccccc", "stops": STOPS},
        size={"prop": "m", "stops": [[5, 1.5], [30, 2.5], [150, 3.5], [3000, 7]]}, opacity=0.8,
        popup=[["pc", "Postcode", None], ["kwh", "Mean per meter", "kWh/yr"],
               ["m", "Domestic meters", None]],
        legend=LEGEND,
        attribution=("Contains public sector information from DESNZ Postcode level electricity statistics 2024, "
                     "licensed under the Open Government Licence v3.0. Contains OS data (c) Crown copyright and "
                     "database right (Code-Point Open).")),
]

_SECTOR = re.compile(r"^(.+?)\d[A-Z]{2}$")


def read_consumption(path: Path) -> pl.DataFrame:
    """DESNZ CSV -> one row per postcode unit: key (no spaces), pc, m, total, kwh, med. Drops the per-outcode
    'All postcodes' summary rows."""
    df = pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
    df = df.filter(pl.col("Postcode") != "All postcodes")
    return df.select(
        pl.col("Postcode").alias("pc"),
        pl.col("Postcode").str.replace_all(" ", "").str.to_uppercase().alias("key"),
        pl.col("Num_meters").cast(pl.Int64, strict=False).alias("m"),
        pl.col("Total_cons_kwh").cast(pl.Float64, strict=False).alias("total"),
        pl.col("Mean_cons_kwh").cast(pl.Float64, strict=False).alias("kwh"),
        pl.col("Median_cons_kwh").cast(pl.Float64, strict=False).alias("med"),
    ).drop_nulls(["m", "kwh"])


def read_codepoint(zpath: Path, areas: set[str]) -> pl.DataFrame:
    """Code-Point Open zip -> key, e, n for the postcode areas listed."""
    frames = []
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            m = re.fullmatch(r"(?:.*/)?CSV/([a-z]{1,2})\.csv", name)
            if m and m[1].upper() in areas:
                d = pl.read_csv(io.BytesIO(z.read(name)), has_header=False, infer_schema=False)
                frames.append(d.select(pl.col(d.columns[0]).alias("pc0"), pl.col(d.columns[2]).alias("e"),
                                       pl.col(d.columns[3]).alias("n")))
    if not frames:
        return pl.DataFrame(schema={"key": pl.Utf8, "e": pl.Float64, "n": pl.Float64})
    return (pl.concat(frames).with_columns(pl.col("pc0").str.replace_all(" ", "").str.to_uppercase().alias("key"))
            .select("key", pl.col("e").cast(pl.Float64, strict=False), pl.col("n").cast(pl.Float64, strict=False))
            .filter(pl.col("e") > 0))


def to_points(df: pl.DataFrame) -> pl.DataFrame:
    """Adds lon/lat (WGS84) from BNG e/n, dropping rows outside Great Britain's bounds."""
    tr = Transformer.from_crs(27700, 4326, always_xy=True)
    lon, lat = tr.transform(df["e"].to_list(), df["n"].to_list())
    out = df.with_columns(pl.Series("lon", list(lon)), pl.Series("lat", list(lat)))
    return out.filter(pl.col("lat").is_between(49.8, 60.9) & pl.col("lon").is_between(-8.7, 1.9))


def sectors(df: pl.DataFrame) -> pl.DataFrame:
    """Aggregate postcode units to postcode sectors: meter-weighted mean location, total / meters for the mean."""
    d = df.with_columns(pl.col("key").str.replace(r"[A-Z]{2}$", "").alias("sector"))
    return (d.group_by("sector").agg(
        pl.col("m").sum(), pl.col("total").sum(),
        (pl.col("lon") * pl.col("m")).sum().alias("lx"), (pl.col("lat") * pl.col("m")).sum().alias("ly"))
        .with_columns((pl.col("total") / pl.col("m")).alias("kwh"), (pl.col("lx") / pl.col("m")).alias("lon"),
                      (pl.col("ly") / pl.col("m")).alias("lat"))
        .with_columns(pl.col("sector").str.replace(r"(\d)$", " $1").alias("pc")))


def _feat(r: dict, minz: int, maxz: int) -> str:
    return json.dumps({"type": "Feature", "tippecanoe": {"minzoom": minz, "maxzoom": maxz},
                       "geometry": {"type": "Point", "coordinates": [round(r["lon"], 5), round(r["lat"], 5)]},
                       "properties": {"pc": r["pc"], "kwh": round(r["kwh"]), "m": int(r["m"])}}, separators=(",", ":"))


def build(ctx: Ctx) -> dict[str, Path]:
    s_cons, s_cp = latest_snapshot("desnz_postcode_elec"), latest_snapshot("os_codepoint")
    if s_cons is None or s_cp is None:
        return {}
    csv = next(p for p in snapshot_files(s_cons) if p.suffix == ".csv")
    zp = next((p for p in snapshot_files(s_cp) if zipfile.is_zipfile(p)), None)
    if zp is None:
        return {}
    cons = read_consumption(csv)
    areas = {m[0] for m in (re.match(r"[A-Z]{1,2}", k) for k in cons["key"].to_list()) if m}
    pts = to_points(cons.join(read_codepoint(zp, areas), on="key", how="inner"))
    src = ctx.tmp / "elec_postcode_kwh.geojsonl"
    with src.open("w") as f:
        for r in sectors(pts).iter_rows(named=True):
            f.write(_feat(r, 6, 10) + "\n")
        for r in pts.iter_rows(named=True):
            f.write(_feat(r, 12, 13) + "\n")
    out = ctx.tmp / "elec_postcode_kwh.pmtiles"
    subprocess.run(["tippecanoe", "-q", "-f", "-o", str(out), "-l", "elec_postcode_kwh", "-Z6", "-z13",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)
    return {"elec_postcode_kwh": out}
