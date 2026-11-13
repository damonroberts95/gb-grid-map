"""Build data/derived/places.parquet for offline map search from OS Open Names (+ Code-Point Open for postcodes).

Run: uv run python -I -m gridmap.build_places   (reads the newest ok snapshots of os_open_names / os_codepoint)."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import polars as pl
from pyproj import Transformer

from .normalise.common import DATA_DIR, latest_snapshot
from .server.places import norm_key, pc_compact

log = logging.getLogger("gridmap.build_places")
OUT = DATA_DIR / "derived" / "places.parquet"
RANK = {"City": 0, "Town": 1, "Village": 2, "Suburban Area": 3, "Hamlet": 4, "Other Settlement": 5, "Named Road": 6}
# Open Names column indices
C = {"id": 0, "name": 2, "ltype": 7, "x": 8, "y": 9, "x0": 12, "y0": 13, "x1": 14, "y1": 15, "pd": 16,
     "place": 18, "borough": 21, "county": 24, "region": 27}
KEEP = set(RANK) | {"Postcode"}
_tf = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def _wgs(x, y):
    lon, lat = _tf.transform(list(x), list(y))
    return [round(v, 6) for v in lat], [round(v, 6) for v in lon]


def _zip(snap: Path) -> Path:
    """The downloaded zip in a snapshot (named after the URL's last segment, e.g. `downloads`; older ones `file`)."""
    return next(p for p in sorted(snap.iterdir()) if p.name != "meta.json" and zipfile.is_zipfile(p))


def _open_names() -> pl.DataFrame:
    snap = latest_snapshot("os_open_names")
    if snap is None:
        raise SystemExit("os_open_names not fetched: uv run python -I -m gridmap.fetch --only os_open_names")
    parts = []
    with zipfile.ZipFile(_zip(snap)) as z:
        for n in z.namelist():
            if not n.startswith("Data/") or not n.endswith(".csv"):
                continue
            df = pl.read_csv(io.BytesIO(z.read(n)), has_header=False, infer_schema=False)
            df = df.select(pl.nth(i).alias(k) for k, i in C.items())
            parts.append(df.filter(pl.col("ltype").is_in(KEEP)))
    return pl.concat(parts)


def _codepoint() -> pl.DataFrame | None:
    snap = latest_snapshot("os_codepoint")
    if snap is None:
        return None
    parts = []
    with zipfile.ZipFile(_zip(snap)) as z:
        for n in z.namelist():
            if n.startswith("Data/CSV/") and n.endswith(".csv"):
                parts.append(pl.read_csv(io.BytesIO(z.read(n)), has_header=False, infer_schema=False)
                             .select(pl.nth(0).alias("name"), pl.nth(2).alias("x"), pl.nth(3).alias("y")))
    return pl.concat(parts)


def build() -> pl.DataFrame:
    on = _open_names()
    on = on.with_columns(pl.col(c).fill_null("") for c in ("place", "borough", "county", "region", "pd"))
    pc_on = on.filter(pl.col("ltype") == "Postcode")
    rest = on.filter(pl.col("ltype") != "Postcode").filter(pl.col("name").is_not_null())

    # roads: MBR centre; places: geometry point
    is_road = pl.col("ltype") == "Named Road"
    rest = rest.with_columns(
        x=pl.when(is_road).then((pl.col("x0").cast(pl.Float64) + pl.col("x1").cast(pl.Float64)) / 2)
        .otherwise(pl.col("x").cast(pl.Float64)),
        y=pl.when(is_road).then((pl.col("y0").cast(pl.Float64) + pl.col("y1").cast(pl.Float64)) / 2)
        .otherwise(pl.col("y").cast(pl.Float64)))
    rest = rest.filter(pl.col("x").is_not_null() & pl.col("y").is_not_null())
    lat, lon = _wgs(rest["x"], rest["y"])
    areas = [", ".join(dict.fromkeys(p for p in (pl_, b, c) if p)) for pl_, b, c in
             zip(rest["place"], rest["borough"], rest["county"], strict=True)]
    # roads: "Manchester, M1"; places: "Salford, Greater Manchester" (own name dropped from its context)
    ctx, ckey = [], []
    for nm, lt, place, pd_, a, region in zip(rest["name"], rest["ltype"], rest["place"], rest["pd"], areas,
                                              rest["region"], strict=True):
        if lt == "Named Road":
            ctx.append(", ".join(p for p in (place, pd_) if p))
        else:
            ctx.append(", ".join(p for p in a.split(", ") if p != nm) or region)
        ckey.append(norm_key(a + " " + pd_ + " " + region))
    names = pl.DataFrame({
        "id": ["on:" + i for i in rest["id"]], "name": rest["name"],
        "kind": ["road" if t == "Named Road" else "place" for t in rest["ltype"]],
        "context": ctx, "lat": lat, "lon": lon, "key": [norm_key(n) for n in rest["name"]],
        "ckey": ckey, "rank": [RANK[t] for t in rest["ltype"]]}).filter(pl.col("key") != "")

    # postcodes: Code-Point Open when fetched (full coverage), else Open Names postcode units
    cp = _codepoint()
    ctx_by_pc = {pc_compact(n): ", ".join(dict.fromkeys(p for p in (pl_, b) if p)) for n, pl_, b in
                 zip(pc_on["name"], pc_on["place"], pc_on["borough"], strict=True)}
    if cp is not None:
        src = cp.with_columns(pl.col("x").cast(pl.Float64), pl.col("y").cast(pl.Float64)).drop_nulls()
        log.info("postcodes from Code-Point Open: %d", src.height)
    else:
        src = pc_on.select("name", pl.col("x").cast(pl.Float64), pl.col("y").cast(pl.Float64)).drop_nulls()
    plat, plon = _wgs(src["x"], src["y"])
    comp = [pc_compact(n) for n in src["name"]]
    pcs = pl.DataFrame({
        "id": ["pc:" + c for c in comp], "name": [f"{c[:-3].upper()} {c[-3:].upper()}" for c in comp],
        "kind": "postcode", "context": [ctx_by_pc.get(c) or c[:-3].upper() for c in comp],
        "lat": plat, "lon": plon, "key": comp, "ckey": "", "rank": 7},
        schema_overrides={"rank": pl.Int8}).filter(pl.col("lat").is_between(49.8, 61) & pl.col("lon").is_between(-9, 2))
    df = pl.concat([names.with_columns(pl.col("rank").cast(pl.Int8)), pcs]).sort("key", "rank")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT, compression="zstd")
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    d = build()
    print(d.group_by("kind").len(), OUT.stat().st_size / 1e6, "MB")
