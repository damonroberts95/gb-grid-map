"""NIE Networks (Northern Ireland) adapter.

Sources (Opendatasoft CSV exports, free account key in NIE_API_KEY, see docs/sources/live_ni.md):
  nie_sites_grid_bulk_primary   `dataset` column gives the level: Grid/Bulk Supply Points, Primary Substations
  nie_secondary_transformers    one row per transformer; grouped here into one lv_site per site
  nie_network_faults            live outages (not normalised; read by the server live feed)

Tables written: asset, lv_site. NIE publishes no headroom, capacity, queue or fault-level data and no
parent links, so there are no capacity/fault_level/queue_project tables and upstream_* stay null.
Coordinates: the CSV `site_geopoint` is "lat, lon" text; Irish Grid (EPSG:29903) x/y is the fallback.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import polars as pl

from .common import (
    as_of,
    ids_json,
    kind_from,
    latest_snapshot,
    parse_num,
    snapshot_files,
    snapshot_meta,
    write_table,
)

OPERATOR = "NIE"
SRC_SITES = "nie_sites_grid_bulk_primary"
SRC_TX = "nie_secondary_transformers"

# Northern Ireland bounds for validation.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 54.0, 55.5, -8.3, -5.3
KVA_PER_UNIT = {"kva": 1.0, "mva": 1000.0, "va": 0.001}


@lru_cache(maxsize=1)
def _irish_grid() -> Any:
    from pyproj import Transformer
    return Transformer.from_crs(29903, 4326, always_xy=True)


def ni_coords(geopoint: Any, x: Any, y: Any) -> tuple[float | None, float | None, str]:
    """WGS84 (lat, lon, quality) inside Northern Ireland: 'lat, lon' geopoint first, else Irish Grid x/y."""
    cands: list[tuple[float, float, str]] = []
    if isinstance(geopoint, str) and "," in geopoint:
        a, b = (parse_num(p) for p in geopoint.split(",", 1))
        if a is not None and b is not None:
            cands.append((a, b, "native_wgs84"))
    fx, fy = parse_num(x), parse_num(y)
    if fx is not None and fy is not None and fx > 0 and fy > 0:
        lon, lat = _irish_grid().transform(fx, fy)
        cands.append((lat, lon, "from_irish_grid"))
    for lat, lon, q in cands:
        if LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX:
            return round(lat, 5), round(lon, 5), q
    return None, None, "rejected" if cands else "missing"


def kv_of(v: Any) -> float | None:
    """'110KV' -> 110.0, '415V' -> 0.415, bare numbers assumed kV."""
    s = str(v or "").strip().lower()
    n = parse_num(s.replace("kv", "").replace("v", ""))
    if n is None:
        return None
    return n / 1000 if s.endswith("v") and not s.endswith("kv") else n


def load_csv(path) -> pl.DataFrame:
    """ODS CSV export: ';' delimited, BOM possible. Everything read as text."""
    df = pl.read_csv(path, separator=";", encoding="utf8-lossy", infer_schema_length=0)
    return df.rename({c: c.lstrip("﻿").strip() for c in df.columns})


def _txt(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def build_assets(df: pl.DataFrame, source_id: str, asof: str | None) -> pl.DataFrame:
    rows, seen = [], set()
    for r in df.iter_rows(named=True):
        floc = _txt(r.get("site_functional_location")) or _txt(r.get("site_name"))
        kind = kind_from(f"{r.get('dataset') or ''} {r.get('site_type') or ''}")
        if floc is None or floc in seen or kind not in ("GSP", "BSP", "PRIMARY"):
            continue
        seen.add(floc)
        lat, lon, q = ni_coords(r.get("site_geopoint"), r.get("site_longitude_x_irish_grid"),
                                r.get("site_latitude_y_irish_grid"))
        rows.append({
            "asset_uid": f"{OPERATOR}:{floc}", "operator": OPERATOR, "licence_area": OPERATOR,
            "name": _txt(r.get("site_name")) or floc, "kind": kind,
            "kv_hv": kv_of(r.get("site_voltage")), "kv_lv": None, "lat": lat, "lon": lon, "coord_quality": q,
            "native_ids": ids_json(floc=floc, site_type=r.get("site_type"), town=r.get("site_town_city"),
                                   county=r.get("site_county"), lad=r.get("lad_name")),
            "source_id": source_id, "as_of": asof,
        })
    return pl.DataFrame(rows, schema_overrides={c: pl.Float64 for c in ("kv_hv", "kv_lv", "lat", "lon")},
                        infer_schema_length=None)


def build_lv(df: pl.DataFrame, source_id: str, asof: str | None) -> pl.DataFrame:
    """One row per secondary site. transformer_kva sums the ratings (kVA/MVA/VA converted); null if any
    transformer on the site has no rating or an unknown unit. customers and utilisation are not published."""
    sites: dict[str, dict[str, Any]] = {}
    for r in df.iter_rows(named=True):
        lat, lon, q = ni_coords(r.get("site_geopoint"), r.get("site_longitude_x_irish_grid"),
                                r.get("site_latitude_y_irish_grid"))
        if lat is None:
            continue
        floc = _txt(r.get("site_functional_location"))
        key = floc or f"{lat}_{lon}"
        s = sites.setdefault(key, {"floc": floc, "name": _txt(r.get("site_name")), "lat": lat, "lon": lon, "q": q,
                                   "kv": kv_of(r.get("voltage")), "kva": 0.0, "miss": False, "n": 0,
                                   "town": _txt(r.get("site_town_city")), "county": _txt(r.get("site_county"))})
        s["n"] += 1
        v = parse_num(r.get("nominal_rating"))
        mult = KVA_PER_UNIT.get(str(r.get("nominal_rating_unit_of_measurement") or "").strip().lower())
        if v is None or mult is None:
            s["miss"] = True
        else:
            s["kva"] += v * mult
    rows = [{
        "asset_uid": f"{OPERATOR}:{k}", "operator": OPERATOR, "name": s["name"] or k, "lat": s["lat"],
        "lon": s["lon"], "coord_quality": s["q"], "kv_hv": s["kv"],
        "transformer_kva": None if s["miss"] or s["kva"] <= 0 else round(s["kva"], 3),
        "utilisation_pct": None, "customers": None, "upstream_primary": None,
        "native_ids": ids_json(floc=s["floc"], transformers=s["n"], town=s["town"], county=s["county"]),
        "source_id": source_id, "as_of": asof,
    } for k, s in sites.items()]
    return pl.DataFrame(rows, schema_overrides={"kv_hv": pl.Float64, "transformer_kva": pl.Float64,
                                                "utilisation_pct": pl.Float64, "customers": pl.Int64,
                                                "upstream_primary": pl.Utf8},
                        infer_schema_length=None)


def _csv(source_id: str) -> tuple[pl.DataFrame | None, str | None]:
    snap = latest_snapshot(source_id)
    if snap is None:
        return None, None
    f = next((p for p in snapshot_files(snap) if p.suffix == ".csv"), None)
    if f is None:
        return None, None
    df = load_csv(f)
    return (df if df.height else None), as_of(snapshot_meta(snap))


def run() -> dict[str, int]:
    counts: dict[str, int] = {}
    sites, asof = _csv(SRC_SITES)
    if sites is None:
        raise RuntimeError(f"no ok snapshot with rows for {SRC_SITES} (needs NIE_API_KEY)")
    assets = build_assets(sites, SRC_SITES, asof)
    assert assets.height > 0, "no NIE assets"
    write_table(OPERATOR, "asset", assets)
    counts["asset"] = assets.height
    tx, tx_asof = _csv(SRC_TX)
    if tx is not None:
        lv = build_lv(tx, SRC_TX, tx_asof)
        assert lv.height > 0, "no NIE lv sites"
        write_table(OPERATOR, "lv_site", lv)
        counts["lv_site"] = lv.height
    return counts
