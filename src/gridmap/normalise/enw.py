"""ENW (Electricity North West, SP Energy Networks) adapter.

Sources (all Opendatasoft exports, see docs/sources/enw.md):
  enw_substations_gis      enwl-substation            GSP/BSP/PRY/DISTRIBUTION/NETWORK, 6-digit `number`, BNG x/y
  enw_{gsp,bsp,primary}_heatmap                        capacity heatmaps (geojson; polygons ignored here)
  enw_ecr_1mw_plus / enw_ecr_under_1mw                 Embedded Capacity Register (two parts)
  enw_capacity_distribution_tx                         per distribution transformer rating/load/utilisation
  enw_smart_meter_installation                         customers per distribution substation
  enw_lv_headroom_{monitored,unmonitored}              only used as a site list (see below)

Tables written: asset, capacity, queue_project, lv_site. ENW publishes no LTDS/fault-level data, so
no fault_level table is produced.

Skipped on purpose: polygons (geometry only), DFES/NDP projections (scenario/year matrix, no schema
column), lv_load_duration/mcs_lct (no schema column), live_incidents/flex tender (names only, see build_site_names),
GIS group NETWORK (6.6 kV network nodes, not substations). dfes-lv-headroom-* hold scenario
projections in kVA by year (not current headroom), so their values are not mapped; their ids and BNG
x/y only add LV sites missing from the GIS list.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import polars as pl

from .common import (
    DATA_DIR,
    NULL_TOKENS,
    add_coords,
    as_of,
    ids_json,
    latest_snapshot,
    num_expr,
    snapshot_meta,
    write_table,
)

OPERATOR = "ENW"

SRC_GIS = "enw_substations_gis"
SRC_HEATMAPS = {"GSP": "enw_gsp_heatmap", "BSP": "enw_bsp_heatmap", "PRIMARY": "enw_primary_heatmap"}
SRC_ECR = ("enw_ecr_1mw_plus", "enw_ecr_under_1mw")
SRC_TX = "enw_capacity_distribution_tx"
SRC_SMART = "enw_smart_meter_installation"
SRC_FLEX = ("enw_flex_monthly_tender", "enw_flex_biannual_tender", "enw_flex_historical_tender")
SRC_LVH = ("enw_lv_headroom_monitored", "enw_lv_headroom_unmonitored")

GIS_GROUP_KIND = {"GSP": "GSP", "BSP": "BSP", "PRY": "PRIMARY"}
_TXT_NULL = sorted(NULL_TOKENS | {"data not available", "to be confirmed"})
_NUM6 = r"(?<!\d)\d{6}(?!\d)"

HEATMAP_BASIS = {
    "GSP": "unknown: methodology text not in snapshot; GSP heatmap gives connected/accepted MW only (no headroom)",
    "BSP": "unknown methodology; from column labels: demand=firm MW; gen=min(non-firm inverter, non-firm synchronous) MW",
    "PRIMARY": "unknown methodology; from column labels: demand=firm MW; gen=min(non-firm inverter, LV sync, HV sync) MW",
}


# ---------- loading ----------

def load_csv(path: Path) -> pl.DataFrame:
    """ODS CSV export: ';' delimited, BOM possible. Everything read as text (parsed later)."""
    df = pl.read_csv(path, separator=";", encoding="utf8-lossy", infer_schema_length=0)
    return df.rename({c: c.lstrip("﻿") for c in df.columns})


def load_geojson(path: Path) -> pl.DataFrame:
    """GeoJSON export -> one row per feature of its properties, as text. Geometry is not used."""
    feats = json.loads(path.read_text())["features"]
    rows = [{k: (None if v is None else str(v)) for k, v in f["properties"].items()} for f in feats]
    return pl.DataFrame(rows, infer_schema_length=None).cast(pl.Utf8)


# ---------- small expressions ----------

def _none(dtype: pl.DataType = pl.Utf8) -> pl.Expr:
    return pl.lit(None, dtype)


def txt(col: str) -> pl.Expr:
    """Trimmed text with null tokens (incl. 'DATA NOT AVAILABLE') -> null."""
    c = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return pl.when(c.is_null() | c.str.to_lowercase().is_in(_TXT_NULL)).then(None).otherwise(c)


def kv_expr(col: str) -> pl.Expr:
    """'11KV' -> 11, '6.6KV' -> 6.6, '415V' -> 0.415, bare numbers assumed kV."""
    s = pl.col(col).cast(pl.Utf8).str.strip_chars().str.to_lowercase()
    v = num_expr(col)
    return pl.when(s.str.ends_with("kv")).then(v).when(s.str.ends_with("v")).then(v / 1000).otherwise(v)


def number_or_null(col: str) -> pl.Expr:
    """6-digit substation number (optionally '_...' compound); drops 'N/A' and 'NO BSP - DIRECT FROM ...'."""
    c = txt(col)
    return pl.when(c.str.contains(r"^\d{6}(_.*)?$")).then(c).otherwise(None)


def iso_date(col: str) -> pl.Expr:
    return txt(col).str.extract(r"^(\d{4}-\d{2}-\d{2})", 1)


def with_coords(df: pl.DataFrame, e: str | None, n: str | None, geopoint: str | None) -> pl.DataFrame:
    """Split ODS 'lat, lon' geopoint text and run common.add_coords (WGS84 first, BNG fallback)."""
    if geopoint and geopoint in df.columns:
        parts = pl.col(geopoint).cast(pl.Utf8).str.split(",")
        df = df.with_columns(
            parts.list.get(0, null_on_oob=True).str.strip_chars().alias("gp_lat"),
            parts.list.get(1, null_on_oob=True).str.strip_chars().alias("gp_lon"),
        )
    else:
        df = df.with_columns(_none().alias("gp_lat"), _none().alias("gp_lon"))
    return add_coords(df, lat="gp_lat", lon="gp_lon", e=e, n=n)


# ---------- asset + capacity ----------

def prep_gis(gis: pl.DataFrame, source_id: str, asof: str | None) -> pl.DataFrame:
    """GIS substations with parsed fields (all groups; callers filter)."""
    df = gis.with_columns(pl.col("group").str.strip_chars().str.to_uppercase().alias("grp"))
    df = with_coords(df, "x", "y", "geopoint")
    return df.with_columns(
        txt("number").alias("number"),
        kv_expr("infeed_voltage").alias("kv_hv"),
        kv_expr("outfeed_voltage").alias("kv_lv"),
        number_or_null("dso_pry_number_alias").alias("pry_alias"),
        number_or_null("dso_bsp_number_alias").alias("bsp_alias"),
        number_or_null("dso_gsp_number_alias").alias("gsp_alias"),
        txt("area").alias("area"),
        txt("local_authority").alias("local_authority"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def prep_heatmap(hm: pl.DataFrame, kind: str, source_id: str, asof: str | None) -> pl.DataFrame:
    """Heatmap rows: clean the key ('200420 - No Heatmap Values' -> 200420; non-ENW placeholders such as
    'NGED - No Heatmap Values' dropped), parse headroom numbers, dedupe preferring rows with values."""
    keycol = {"GSP": "gsp_number", "BSP": "bsp_number", "PRIMARY": "pry_number"}[kind]
    df = hm.with_columns(
        txt(keycol).str.replace(r"(?i)\s*-\s*no heatmap values\s*$", "").str.strip_chars().alias("key"),
    ).filter(pl.col("key").str.contains(r"^\d{6}"))

    def num(c: str) -> pl.Expr:
        return num_expr(c) if c in df.columns else _none(pl.Float64)

    df = df.with_columns(
        num("voltage_kv").alias("kv_lv"),
        (number_or_null("bsp_number") if kind == "PRIMARY" else _none()).alias("hm_bsp"),
        (number_or_null("gsp_number") if kind != "GSP" else _none()).alias("hm_gsp"),
        num("connected_mw").alias("connected_mw"),
        num("accepted_mw").alias("accepted_mw"),
        num("demand_headroom_firm_mw").alias("demand_firm"),
        pl.min_horizontal(
            num("generation_headroom_non_firm_inverter_based_mw"),
            num("generation_headroom_non_firm_synchronous_mw"),
            num("generation_headroom_non_firm_lv_synchronous_mw"),
            num("generation_headroom_non_firm_hv_synchronous_mw"),
        ).alias("gen_min"),
        pl.lit(kind).alias("kind"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )
    nn = pl.sum_horizontal(
        pl.col(c).is_not_null().cast(pl.Int32) for c in ("connected_mw", "accepted_mw", "demand_firm", "gen_min")
    )
    return df.with_columns(nn.alias("_nn")).sort("_nn", descending=True).unique("key", keep="first", maintain_order=True)


def build_assets(gis: pl.DataFrame, heatmaps: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Merge heatmap keys and GIS substations into asset rows (uid = ENW:<KIND>:<number>).

    The same 6-digit number is used by both a BSP and a PRIMARY row (24 cases) so the kind is part of
    the uid. Heatmap keys can be compound ('400240_400241'); they are matched to GIS rows through
    their 6-digit components and the GIS rows consumed that way are not emitted twice."""
    g = gis.filter(pl.col("grp").is_in(list(GIS_GROUP_KIND))).with_columns(
        pl.col("grp").replace_strict(GIS_GROUP_KIND).alias("kind"))
    gmap = {(r["kind"], r["number"]): r for r in g.iter_rows(named=True)}
    used: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for kind, hm in heatmaps.items():
        for h in hm.iter_rows(named=True):
            comps = re.findall(_NUM6, h["key"])
            gr = next((gmap[(kind, c)] for c in comps if (kind, c) in gmap), None)
            used.update((kind, c) for c in comps)
            rows.append(_asset_row(kind, h["key"], gr, h))
    for (kind, num), gr in gmap.items():
        if (kind, num) not in used:
            rows.append(_asset_row(kind, num, gr, None))
    return pl.DataFrame(rows, schema_overrides={c: pl.Float64 for c in ("lat", "lon", "kv_hv", "kv_lv")},
                        infer_schema_length=None)


def _asset_row(kind: str, key: str, gr: dict | None, h: dict | None) -> dict[str, Any]:
    if gr is not None:
        up_gsp = None if kind == "GSP" else gr["gsp_alias"]
        up_bsp = gr["bsp_alias"] if kind == "PRIMARY" and gr["bsp_alias"] != gr["number"] else None
        d = {
            "kv_hv": gr["kv_hv"], "kv_lv": gr["kv_lv"], "lat": gr["lat"], "lon": gr["lon"],
            "coord_quality": gr["coord_quality"], "upstream_gsp": up_gsp, "upstream_bsp": up_bsp,
            "source_id": gr["source_id"], "as_of": gr["as_of"],
            "native_ids": ids_json(number=gr["number"], heatmap_key=key if key != gr["number"] else None,
                                   gis_group=gr["grp"], area=gr["area"], local_authority=gr["local_authority"]),
        }
    else:  # heatmap-only: no site position published (polygon centroids are not site locations)
        d = {
            "kv_hv": None, "kv_lv": h["kv_lv"], "lat": None, "lon": None, "coord_quality": "missing",
            "upstream_gsp": h["hm_gsp"], "upstream_bsp": h["hm_bsp"],
            "source_id": h["source_id"], "as_of": h["as_of"], "native_ids": ids_json(heatmap_key=key),
        }
    return {"asset_uid": f"{OPERATOR}:{kind}:{key}", "operator": OPERATOR, "licence_area": OPERATOR,
            "name": key, "kind": kind, "upstream_primary": None, **d}


def build_capacity(heatmaps: dict[str, pl.DataFrame]) -> pl.DataFrame:
    parts = []
    for kind, hm in heatmaps.items():
        gsp = kind == "GSP"
        df = hm.select(
            (pl.lit(f"{OPERATOR}:{kind}:") + pl.col("key")).alias("asset_uid"),
            (_none(pl.Float64) if gsp else pl.col("gen_min")).alias("gen_headroom_mw"),
            (_none(pl.Float64) if gsp else pl.col("demand_firm")).alias("demand_headroom_mw"),
            (pl.col("connected_mw") if gsp else _none(pl.Float64)).alias("connected_gen_mw"),
            (pl.col("accepted_mw") if gsp else _none(pl.Float64)).alias("contracted_gen_mw"),
            pl.lit(HEATMAP_BASIS[kind]).alias("basis"),
            "source_id", "as_of",
        ).filter(pl.any_horizontal(
            pl.col(c).is_not_null()
            for c in ("gen_headroom_mw", "demand_headroom_mw", "connected_gen_mw", "contracted_gen_mw")))
        parts.append(df)
    return pl.concat(parts)


# ---------- queue ----------

def _status(col: str) -> pl.Expr:
    s = pl.col(col).cast(pl.Utf8).str.strip_chars().str.to_uppercase()
    return (pl.when(s == "CONNECTED").then(pl.lit("connected"))
            .when(s.str.starts_with("ACCEPTED")).then(pl.lit("accepted"))
            .otherwise(pl.lit("other")))


def build_queue(parts: list[tuple[str, pl.DataFrame, str | None]]) -> pl.DataFrame:
    """parts: [(source_id, ecr dataframe, as_of)]. Handles both ECR layouts (the <1MW part has no
    separate 'already connected' column and a different easting/northing header spelling)."""
    out = []
    for source_id, df, asof in parts:
        df = df.rename({c: c.replace("wheredataisheld", "where_data_is_held") for c in df.columns})
        df = with_coords(df, "location_x_coordinate_eastings_where_data_is_held",
                         "location_y_coordinate_northings_where_data_is_held", "geopoint")

        def n_(c: str, _df: pl.DataFrame = df) -> pl.Expr:
            return num_expr(c) if c in _df.columns else _none(pl.Float64)

        st = _status("connection_status")
        tech = pl.coalesce(txt("energy_conversion_technology_1"), txt("energy_source_1"))
        # pure storage only: hybrids ('SOLAR + BATTERY', 'PV & BATTERY') report one combined registered capacity
        both = (pl.col("energy_source_1").fill_null("") + " | " + pl.col("energy_conversion_technology_1").fill_null("")
                ).str.to_uppercase()
        is_storage = (both.str.contains("STOR|BATTER|BESS")
                      & ~both.str.contains(r"SOLAR|PV|PHOTOVOLTAIC|WIND|CHP|\+|&| AND ")).fill_null(False)
        chg_exp, acc_reg = n_("change_to_maximum_export_capacity_mw"), n_("accepted_to_connect_registered_capacity_mw")
        export_acc = pl.when(chg_exp > 0).then(chg_exp).when(acc_reg.is_not_null()).then(acc_reg).otherwise(chg_exp)
        export = pl.when(st == "accepted").then(export_acc).otherwise(
            pl.coalesce(n_("maximum_export_capacity_mw"), n_("already_connected_registered_capacity_mw")))
        imp = pl.when(st == "accepted").then(n_("change_to_maximum_import_capacity_mw")).otherwise(
            n_("maximum_import_capacity_mw"))
        conn_date = pl.when(st == "connected").then(iso_date("date_connected")).otherwise(
            iso_date("target_energisation_date"))
        # 'primary' holds 'NAME (6-digit number)': kept as published so the match phase can use the number
        d = df.select(
            txt("customer_site").alias("site_name"),
            txt("grid_supply_point").alias("gsp_name"),
            txt("bulk_supply_point").alias("bsp_name"),
            txt("primary").alias("primary_name"),
            "lat", "lon",
            st.alias("status"),
            tech.alias("technology"),
            export.alias("export_mw"),
            imp.alias("import_mw"),
            pl.when(is_storage).then(n_("energy_source_energy_conversion_technology_1_registered_capacity_mw"))
            .otherwise(None).alias("storage_mw"),
            n_("point_of_connection_poc_voltage_kv").alias("kv"),
            conn_date.alias("connection_date"),
            pl.lit(source_id).alias("source_id"),
            pl.lit(asof).alias("as_of"),
            txt("customer_name").alias("_cust"),
            txt("postcode").alias("_pc"),
        ).with_columns(pl.coalesce("primary_name", "bsp_name", "gsp_name").alias("connection_point"))
        out.append(d)
    q = pl.concat(out)
    # ECR `reference` is ~empty, so uid = hash of identifying fields + occurrence counter for exact duplicates
    seen: dict[str, int] = {}
    uids = []
    for r in q.select("source_id", "_cust", "_pc", "site_name", "technology", "status", "export_mw", "import_mw",
                      "primary_name", "bsp_name", "kv").iter_rows():
        h = hashlib.md5("|".join("" if v is None else str(v) for v in r).encode()).hexdigest()[:12]
        seen[h] = seen.get(h, 0) + 1
        uids.append(f"{OPERATOR}:{h}-{seen[h]}")
    return q.with_columns(pl.Series("project_uid", uids), pl.lit(OPERATOR).alias("operator")).drop("_cust", "_pc")


# ---------- lv_site ----------

def build_lv(gis: pl.DataFrame, tx: pl.DataFrame | None, smart: pl.DataFrame | None,
             lvh: list[tuple[str, pl.DataFrame, str | None]], tx_src: tuple[str, str | None]) -> pl.DataFrame:
    """Secondary substations: GIS DISTRIBUTION rows first, then ids only present in the transformer
    capacity table (coords from its geo_point_2d), then ids only in the LV headroom files (x/y BNG).
    transformer_kva/utilisation_pct are summed/derived per distribution_number from the per-transformer
    table, and null if any transformer on the site lacks rating or load."""
    cols = ["number", "kv_hv", "lat", "lon", "coord_quality", "upstream_primary", "area", "local_authority",
            "source_id", "as_of", "mount"]
    cand = [gis.filter(pl.col("grp") == "DISTRIBUTION").select(
        "number", "kv_hv", "lat", "lon", "coord_quality", pl.col("pry_alias").alias("upstream_primary"),
        "area", "local_authority", "source_id", "as_of",
        # published mounting ("GROUND MOUNTED" / "POLE MOUNTED"); only the GIS file has it
        pl.col("situation").str.strip_chars().str.to_uppercase().alias("mount"))]
    txa = None
    if tx is not None:
        t = with_coords(tx, None, None, "geo_point_2d").with_columns(
            txt("distribution_number").alias("number"),
            num_expr("distribution_tx_rating_kva").alias("r"),
            num_expr("distribution_tx_load_kva").alias("l"),
            kv_expr("voltage_kv").alias("kv_hv"),
            pl.col("primary_number").cast(pl.Utf8).str.extract(r"\((\d{6})\)", 1).alias("upstream_primary"),
        ).filter(pl.col("number").is_not_null())
        txa = t.group_by("number").agg(
            pl.col("r").sum().alias("_rsum"), pl.col("r").is_null().any().alias("_r_miss"),
            pl.col("l").sum().alias("_lsum"), pl.col("l").is_null().any().alias("_l_miss"),
            pl.col("lat").first(), pl.col("lon").first(), pl.col("coord_quality").first(),
            pl.col("kv_hv").first(), pl.col("upstream_primary").first(),
        ).with_columns(
            pl.when(pl.col("_r_miss") | (pl.col("_rsum") <= 0)).then(None).otherwise(pl.col("_rsum"))
            .alias("transformer_kva"),
        ).with_columns(
            pl.when(pl.col("_l_miss") | pl.col("transformer_kva").is_null()).then(None)
            .otherwise(pl.col("_lsum") / pl.col("transformer_kva") * 100).alias("utilisation_pct"),
        )
        cand.append(txa.select(
            "number", "kv_hv", "lat", "lon", "coord_quality", "upstream_primary",
            _none().alias("area"), _none().alias("local_authority"),
            pl.lit(tx_src[0]).alias("source_id"), pl.lit(tx_src[1]).alias("as_of"), _none().alias("mount")))
    for sid, df, asof in lvh:
        d = add_coords(df.with_columns(txt("secondary_substation").alias("number")), e="x", n="y")
        cand.append(d.select(
            "number", _none(pl.Float64).alias("kv_hv"), "lat", "lon", "coord_quality",
            _none().alias("upstream_primary"), _none().alias("area"), _none().alias("local_authority"),
            pl.lit(sid).alias("source_id"), pl.lit(asof).alias("as_of"), _none().alias("mount")))
    allc = pl.concat([c.select(cols) for c in cand], how="vertical_relaxed")
    allc = allc.filter(pl.col("number").is_not_null()).unique("number", keep="first", maintain_order=True)
    if txa is not None:
        allc = allc.join(txa.select("number", "transformer_kva", "utilisation_pct"), on="number", how="left")
    else:
        allc = allc.with_columns(_none(pl.Float64).alias("transformer_kva"), _none(pl.Float64).alias("utilisation_pct"))
    if smart is not None:
        s = smart.select(txt("dist_number").alias("number"), num_expr("total_customers").alias("customers"))
        s = s.filter(pl.col("number").is_not_null()).group_by("number").agg(pl.col("customers").max())
        allc = allc.join(s, on="number", how="left")
    else:
        allc = allc.with_columns(_none(pl.Float64).alias("customers"))
    nids = [ids_json(number=n, area=a, local_authority=la, mount=m)
            for n, a, la, m in zip(allc["number"], allc["area"], allc["local_authority"], allc["mount"])]
    return allc.with_columns(
        (pl.lit(f"{OPERATOR}:") + pl.col("number")).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        pl.col("number").alias("name"),
        pl.col("customers").round(0).cast(pl.Int64, strict=False),
        pl.Series("native_ids", nids),
    )


# ---------- site names ----------

def build_site_names() -> pl.DataFrame:
    """number -> name from the flexibility tender site lists (site_number + substation_name).

    ENW's GIS/heatmap lists give 6-digit numbers only. The tenders are public ENW datasets that name
    ~215 of them (mostly primaries). Group rows ("BLACKLEY-HARPURHEY Group") and non-6-digit numbers
    are dropped. Written to data/norm/ENW/site_name.parquet (read by match._enw_names).
    """
    rows: list[dict[str, str]] = []
    for sid in SRC_FLEX:
        snap = latest_snapshot(sid)
        if snap is None:
            continue
        df = load_geojson(snap / "export.geojson")
        if not {"site_number", "substation_name"} <= set(df.columns):
            continue
        for num, name in df.select("site_number", "substation_name").unique().iter_rows():
            num, name = (num or "").strip(), (name or "").strip()
            if re.fullmatch(r"\d{6}", num) and name and not name.lower().endswith("group") \
                    and name.lower() not in _TXT_NULL:
                rows.append({"number": num, "name": name, "source_id": sid})
    schema = {"number": pl.Utf8, "name": pl.Utf8, "source_id": pl.Utf8}
    return pl.DataFrame(rows, schema=schema).unique("number", keep="first", maintain_order=True)


# ---------- run ----------

def _csv(source_id: str) -> tuple[pl.DataFrame | None, str | None]:
    snap = latest_snapshot(source_id)
    if snap is None:
        return None, None
    return load_csv(snap / "export.csv"), as_of(snapshot_meta(snap))


def run() -> dict[str, int]:
    counts: dict[str, int] = {}
    gis_df, gis_asof = _csv(SRC_GIS)
    if gis_df is None:
        raise RuntimeError(f"no ok snapshot for {SRC_GIS}")
    gis = prep_gis(gis_df, SRC_GIS, gis_asof)

    heatmaps = {}
    for kind, sid in SRC_HEATMAPS.items():
        snap = latest_snapshot(sid)
        if snap:
            heatmaps[kind] = prep_heatmap(load_geojson(snap / "export.geojson"), kind, sid,
                                          as_of(snapshot_meta(snap)))
    assets = build_assets(gis, heatmaps)
    assert assets.height > 0, "no ENW assets"
    write_table(OPERATOR, "asset", assets)
    counts["asset"] = assets.height

    if heatmaps:
        cap = build_capacity(heatmaps)
        assert cap.height > 0, "no ENW capacity rows"
        assert cap["asset_uid"].is_in(assets["asset_uid"].implode()).all(), "capacity rows without asset"
        write_table(OPERATOR, "capacity", cap)
        counts["capacity"] = cap.height

    ecr = [(sid, *_csv(sid)) for sid in SRC_ECR]
    ecr = [(s, d, a) for s, d, a in ecr if d is not None]
    if ecr:
        q = build_queue(ecr)
        write_table(OPERATOR, "queue_project", q)
        counts["queue_project"] = q.height

    names = build_site_names()
    if names.height:
        path = DATA_DIR / "norm" / OPERATOR / "site_name.parquet"
        names.write_parquet(path)
        counts["site_name"] = names.height

    tx, tx_asof = _csv(SRC_TX)
    smart, _ = _csv(SRC_SMART)
    lvh = [(sid, *_csv(sid)) for sid in SRC_LVH]
    lvh = [(s, d, a) for s, d, a in lvh if d is not None]
    lv = build_lv(gis, tx, smart, lvh, (SRC_TX, tx_asof))
    assert lv.height > 0, "no ENW lv sites"
    write_table(OPERATOR, "lv_site", lv)
    counts["lv_site"] = lv.height
    return counts
