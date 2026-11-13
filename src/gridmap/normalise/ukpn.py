"""UKPN (EPN/LPN/SPN) adapter: raw snapshots -> canonical parquet.

Sources used (ids from sources.yaml):
  ukpn_heatmap                  JSON attachments -> asset (mRID uid) + capacity
  ukpn_grid_primary_sites       -> asset (FLOC uid)
  ukpn_ltds_t3a_load_observed   -> only a name+area bridge heatmap -> FLOC (kind + native_ids)
  ukpn_ltds_t4a/t4b             -> fault_level (3ph / earth, joined on node)
  ukpn_grid_primary_fault_levels-> fault_level (earthing-study values, FLOC linked)
  ukpn_ecr_part1/part2          -> queue_project
  ukpn_secondary_sites (+ _utilisation) -> lv_site
Skipped: transformers, LTDS T1/T2/T3b/T5-T8, DFES, LCT, outages, polygons (not in canonical schema yet).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl

from . import common as C

OPERATOR = "UKPN"
HEATMAP_BASIS = (
    "unknown: heatmap JSON gives no unit or firm/intact/queue definition; "
    "available capacity assumed MW per recon, commentary PDF not in snapshot"
)
_BAD = ["data not available", "data not Available", "-", "direct"]


# ---------- small helpers ----------

def _csv(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, separator=";", infer_schema_length=0, encoding="utf8-lossy")
    return df.rename({c: c.lstrip("﻿").strip() for c in df.columns})


def _txt(col: str) -> pl.Expr:
    """Trimmed string; blanks and 'Data not available'/'-' placeholders -> null."""
    c = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return pl.when(c.str.to_lowercase().is_in([b.lower() for b in _BAD] + [""])).then(None).otherwise(c)


def _area(col: str) -> pl.Expr:
    """'South Eastern Power Networks (SPN)' / 'London Power Networks plc' -> EPN/LPN/SPN."""
    c = pl.col(col).cast(pl.Utf8).str.to_lowercase()
    return (
        pl.when(c.str.contains("south eastern") | c.str.contains(r"\(spn\)") | (c == "spn")).then(pl.lit("SPN"))
        .when(c.str.contains("london") | c.str.contains(r"\(lpn\)") | (c == "lpn")).then(pl.lit("LPN"))
        .when(c.str.contains("eastern") | c.str.contains(r"\(epn\)") | (c == "epn")).then(pl.lit("EPN"))
        .otherwise(None)
    )


def _split_latlon(df: pl.DataFrame, col: str) -> pl.DataFrame:
    """'51.48, 0.06' text pair -> _lat/_lon strings (lat first, per portal geo_point_2d text)."""
    parts = pl.col(col).cast(pl.Utf8).str.split(",")
    return df.with_columns(
        parts.list.get(0, null_on_oob=True).alias("_lat"),
        parts.list.get(1, null_on_oob=True).alias("_lon"),
    )


def _uid(expr: pl.Expr) -> pl.Expr:
    return pl.lit(f"{OPERATOR}:") + expr


def _ids(df: pl.DataFrame, **cols: str) -> pl.Series:
    return pl.Series([C.ids_json(**dict(zip(cols, vals))) for vals in df.select(list(cols.values())).rows()],
                     dtype=pl.Utf8)


def _floc_t3a(path: Path) -> pl.DataFrame:
    """LTDS T3a name+area -> FLOC bridge. (T3a substation_mrid has a leading '_' and does NOT equal heatmap mRID.)"""
    t = _csv(path)
    return (
        t.select(
            pl.col("substation").str.strip_chars().str.to_lowercase().alias("_n"),
            pl.col("functional_location").str.slice(0, 3).alias("_a"),
            pl.col("functional_location").alias("floc"),
        )
        .drop_nulls()
        .unique(subset=["_n", "_a"], keep="first", maintain_order=True)
    )


# ---------- asset: grid & primary sites ----------

def sites_assets(path: Path, as_of: str | None, source_id: str = "ukpn_grid_primary_sites") -> pl.DataFrame:
    df = _split_latlon(_csv(path), "spatial_coordinates")
    df = C.add_coords(df, lat="_lat", lon="_lon")
    name = pl.col("sitename").str.strip_chars()
    pair = name.str.extract_groups(r"(?i)(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*kv")
    df = df.with_columns(
        pair.struct.field("1").cast(pl.Float64, strict=False).alias("_hv"),
        pair.struct.field("2").cast(pl.Float64, strict=False).alias("_lv"),
        C.num_expr("sitevoltage").alias("_sv"),
    )
    return df.select(
        _uid(pl.col("sitefunctionallocation").str.strip_chars()).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        _area("licencearea").alias("licence_area"),
        name.alias("name"),
        pl.col("sitetype").map_elements(C.kind_from, return_dtype=pl.Utf8).alias("kind"),
        pl.coalesce("_hv", "_sv").alias("kv_hv"),
        pl.col("_lv").alias("kv_lv"),
        "lat", "lon", "coord_quality",
        _ids(df, floc="sitefunctionallocation", gridref="gridref").alias("native_ids"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------- heatmap: asset + capacity ----------

def _heatmap_rows(files: list[Path]) -> list[dict]:
    return [s for f in sorted(files) if f.suffix == ".json"
            for s in json.loads(f.read_text()).get("Substations", [])]


def _heatmap_frame(files: list[Path]) -> pl.DataFrame:
    rows = _heatmap_rows(files)
    cols = ["mRID", "name", "area", "longitude", "latitude", "GSP", "BSP",
            "demandAvailableCapacity", "generationAvailableCapacity", "demandConstraint", "generationConstraint",
            "demandConstraintLimitingFactor", "generationConstraintLimitingFactor"]
    data = {c: [r.get(c) for r in rows] for c in cols}
    data["kv"] = [(max(r["voltages"]) if r.get("voltages") else None) for r in rows]
    return pl.DataFrame(
        {k: v for k, v in data.items()},
        schema={**{c: pl.Utf8 for c in ("mRID", "name", "area", "GSP", "BSP", "demandConstraint",
                                          "generationConstraint", "demandConstraintLimitingFactor",
                                          "generationConstraintLimitingFactor")},
                **{c: pl.Float64 for c in ("longitude", "latitude", "demandAvailableCapacity",
                                           "generationAvailableCapacity", "kv")}},
    )


def heatmap_tables(files: list[Path], as_of: str | None, sites: pl.DataFrame | None = None,
                   t3a: pl.DataFrame | None = None, source_id: str = "ukpn_heatmap") -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (asset, capacity). sites = raw grid-primary frame (for kind); t3a = name/area->FLOC bridge."""
    h = _heatmap_frame(files).with_columns(
        pl.col("name").str.strip_chars(),
        pl.col("mRID").str.strip_chars().str.to_lowercase(),
    )
    h = C.add_coords(h, lat="latitude", lon="longitude")
    h = h.with_columns(
        pl.col("name").str.to_lowercase().alias("_n"),
        pl.col("area").alias("_a"),
    )
    if t3a is not None and t3a.height:
        h = h.join(t3a, on=["_n", "_a"], how="left")
    else:
        h = h.with_columns(pl.lit(None, pl.Utf8).alias("floc"))
    if sites is not None and sites.height:
        st = sites.select(pl.col("sitefunctionallocation").alias("floc"), pl.col("sitetype").alias("_stype"))
        h = h.join(st, on="floc", how="left")
    else:
        h = h.with_columns(pl.lit(None, pl.Utf8).alias("_stype"))
    # kind: from the site list's sitetype via FLOC; fallback on name ("... Grid 33kV" -> BSP, else PRIMARY)
    fallback = pl.when(pl.col("name").str.contains(r"(?i)\bgrid\b")).then(pl.lit("BSP")).otherwise(pl.lit("PRIMARY"))
    kind = pl.when(pl.col("_stype").is_not_null()).then(
        pl.col("_stype").map_elements(C.kind_from, return_dtype=pl.Utf8)
    ).otherwise(fallback)
    h = h.with_columns(kind.alias("kind"), _uid(pl.col("mRID")).alias("asset_uid"))
    asset = h.select(
        "asset_uid",
        pl.lit(OPERATOR).alias("operator"),
        pl.col("area").alias("licence_area"),
        "name", "kind",
        pl.lit(None, pl.Float64).alias("kv_hv"),
        pl.col("kv").alias("kv_lv"),
        "lat", "lon", "coord_quality",
        pl.col("GSP").str.strip_chars().alias("upstream_gsp"),
        pl.col("BSP").str.strip_chars().alias("upstream_bsp"),
        _ids(h, mrid="mRID", floc="floc").alias("native_ids"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )

    def lim(a: str, b: str) -> pl.Expr:
        parts = [pl.when(pl.col(c).is_not_null()).then(pl.lit(p) + pl.col(c)) for p, c in (("demand: ", a), ("generation: ", b))]
        return pl.concat_str(parts, separator="; ", ignore_nulls=True)

    cap = h.select(
        "asset_uid",
        pl.col("generationAvailableCapacity").alias("gen_headroom_mw"),
        pl.col("demandAvailableCapacity").alias("demand_headroom_mw"),
        pl.col("generationConstraint").map_elements(C.rag, return_dtype=pl.Utf8).alias("gen_rag_native"),
        pl.col("demandConstraint").map_elements(C.rag, return_dtype=pl.Utf8).alias("demand_rag_native"),
        pl.when(pl.col("demandConstraintLimitingFactor").is_null() & pl.col("generationConstraintLimitingFactor").is_null())
        .then(None).otherwise(lim("demandConstraintLimitingFactor", "generationConstraintLimitingFactor"))
        .alias("upstream_constraint"),
        pl.lit(HEATMAP_BASIS).alias("basis"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )
    return asset, cap


# ---------- fault levels ----------

def fault_ltds(path_3ph: Path, path_earth: Path, asset_uids: set[str], as_of: str | None) -> pl.DataFrame:
    """LTDS T4a (3ph) + T4b (earth) joined on node. Ratings: T4a's, else T4b's (they differ per fault type)."""
    def prep(p: Path) -> pl.DataFrame:
        d = _csv(p)
        d = d.with_columns(
            pl.col("nodename").str.strip_chars().alias("_node"),
            pl.col("substation").str.strip_chars().alias("_sub"),
            pl.col("gridsupplypoint").str.strip_chars().alias("_gsp"),
            C.num_expr("voltage_level_kv").alias("kv"),
            pl.col("sitefunctionallocation").str.strip_chars().alias("_floc"),
        )
        # nodes can repeat within a table: disambiguate with an occurrence index
        return d.with_columns(pl.int_range(pl.len()).over(["_gsp", "_sub", "_node", "kv"]).alias("_k"))

    a, b = prep(path_3ph), prep(path_earth)
    keys = ["_gsp", "_sub", "_node", "kv", "_k"]
    sel = lambda d, sfx: d.select(
        *keys, pl.col("_floc").alias(f"_floc{sfx}"),
        C.num_expr("existing_system_fault_currents_peak_make_ka").alias(f"make{sfx}"),
        C.num_expr("existing_system_fault_currents_rms_break_ka").alias(f"break{sfx}"),
        C.num_expr("fault_rating_peak_make_ka").alias(f"rmake{sfx}"),
        C.num_expr("fault_rating_peak_break_ka").alias(f"rbreak{sfx}"),
    )
    j = sel(a, "_a").join(sel(b, "_b"), on=keys, how="full", coalesce=True)
    floc = pl.coalesce("_floc_a", "_floc_b")
    return j.select(
        pl.when(_uid(floc).is_in(list(asset_uids))).then(_uid(floc)).otherwise(None).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        pl.col("_node").alias("ltds_node"),
        pl.col("_sub").alias("site_name"),
        "kv",
        pl.col("make_a").alias("fl_3ph_make_ka"),
        pl.col("break_a").alias("fl_3ph_break_ka"),
        pl.col("make_b").alias("fl_1ph_make_ka"),
        pl.col("break_b").alias("fl_1ph_break_ka"),
        pl.coalesce("rmake_a", "rmake_b").alias("rating_make_ka"),
        pl.coalesce("rbreak_a", "rbreak_b").alias("rating_break_ka"),
        pl.lit("ukpn_ltds_t4a_fault_3ph+t4b").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


def fault_gp(path: Path, asset_uids: set[str], as_of: str | None) -> pl.DataFrame:
    d = _csv(path)
    floc = pl.col("sitefunctionallocation").str.strip_chars()
    return d.select(
        pl.when(_uid(floc).is_in(list(asset_uids))).then(_uid(floc)).otherwise(None).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        pl.col("node").str.strip_chars().alias("ltds_node"),
        pl.col("substationname").str.strip_chars().alias("site_name"),
        C.num_expr("voltage").alias("kv"),
        C.num_expr("threephasepeakmake").alias("fl_3ph_make_ka"),
        C.num_expr("threephasermsbreak").alias("fl_3ph_break_ka"),
        C.num_expr("earthfaultpeakmake").alias("fl_1ph_make_ka"),
        C.num_expr("earthfaultrmsbreak").alias("fl_1ph_break_ka"),
        pl.lit("ukpn_grid_primary_fault_levels").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------- queue (ECR) ----------

def _hash(*parts: object) -> str:
    return hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:16]


def ecr_projects(path: Path, part: int, as_of: str | None, source_id: str) -> pl.DataFrame:
    d = _csv(path)
    p1 = part == 1
    exp_conn = "already_connected_maximum_export_capacity_mw" if p1 else "maximum_export_capacity_mw"
    imp_conn = "already_connected_maximum_import_capacity_mw" if p1 else "maximum_import_capacity_mw"
    d = C.add_coords(d, lat="latitude", lon="longitude",
                     e="location_x_coordinate_eastings_where_data_is_held",
                     n="location_y_coordinate_northings_where_data_is_held")
    status = pl.col("connection_status").str.strip_chars().str.to_lowercase()
    is_conn = status == "connected"
    cap_cols = [f"energy_source_energy_conversion_technology_{i}_registered_capacity_mw" for i in (1, 2, 3)]
    tech_cols = [f"energy_conversion_technology_{i}" for i in (1, 2, 3)]
    src_cols = [f"energy_source_{i}" for i in (1, 2, 3)]
    is_stor = [
        (pl.col(t).str.to_lowercase().str.contains("storage") | pl.col(s).str.to_lowercase().str.contains("stored"))
        for t, s in zip(tech_cols, src_cols)
    ]
    storage = pl.sum_horizontal(
        *[pl.when(st).then(C.num_expr(c)).otherwise(None) for st, c in zip(is_stor, cap_cols)]
    )
    d = d.with_columns(
        pl.when(is_conn).then(pl.lit("connected")).when(status.str.contains("accept")).then(pl.lit("accepted"))
        .otherwise(pl.lit("other")).alias("status"),
        pl.when(is_conn).then(C.num_expr(exp_conn)).otherwise(
            pl.coalesce(C.num_expr("change_to_maximum_export_capacity_mw"),
                        C.num_expr("accepted_to_connect_registered_capacity_mw"))).alias("export_mw"),
        pl.when(is_conn).then(C.num_expr(imp_conn)).otherwise(C.num_expr("change_to_maximum_import_capacity_mw")).alias("import_mw"),
        storage.alias("_stor"),
        pl.concat_str([_txt(c) for c in tech_cols], separator="; ", ignore_nulls=True).alias("_tech"),
    )
    # sum_horizontal gives 0 when all null: restore null when no storage slot
    any_stor = pl.any_horizontal(*[st.fill_null(False) for st in is_stor])
    d = d.with_columns(
        pl.when(any_stor).then(pl.col("_stor")).otherwise(None).alias("storage_mw"),
        pl.when(pl.col("_tech") == "").then(None).otherwise(pl.col("_tech")).alias("technology"),
    )
    # connection date: connected -> date_connected, else target energisation
    d = d.with_columns(
        pl.when(is_conn).then(pl.col("date_connected")).otherwise(pl.col("target_energisation_date"))
        .str.slice(0, 10).alias("connection_date"),
    )
    keyed = d.with_columns(
        pl.concat_str([
            pl.lit(f"p{part}"), _area("licence_area"), pl.col("customer_name"), pl.col("customer_site"),
            pl.col("postcode"), pl.col("status"), pl.col("technology"), pl.col("export_mw").cast(pl.Utf8),
            pl.col("location_x_coordinate_eastings_where_data_is_held"),
            pl.col("location_y_coordinate_northings_where_data_is_held"),
        ], separator="|", ignore_nulls=False).alias("_key")
    ).with_columns(pl.int_range(pl.len()).over("_key").alias("_dup"))
    return keyed.select(
        pl.struct("_key", "_dup").map_elements(lambda s: f"UKPN:ECR{part}-" + _hash(s["_key"], s["_dup"]),
                                               return_dtype=pl.Utf8).alias("project_uid"),
        pl.lit(OPERATOR).alias("operator"),
        _txt("customer_name").alias("site_name"),
        pl.coalesce(_txt("primary"), _txt("bulk_supply_point"), _txt("grid_supply_point")).alias("connection_point"),
        _txt("primary").alias("primary_name"),
        _txt("bulk_supply_point").alias("bsp_name"),
        _txt("grid_supply_point").alias("gsp_name"),
        "lat", "lon", "status", "technology", "export_mw", "import_mw", "storage_mw",
        C.num_expr("point_of_connection_poc_voltage_kv").alias("kv"),
        "connection_date",
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------- LV sites ----------

def lv_sites(path: Path, as_of: str | None, util: pl.DataFrame | None = None,
             source_id: str = "ukpn_secondary_sites") -> pl.DataFrame:
    """Secondary sites. utilisation_pct stays null: UKPN publishes bands (text), kept in native_ids."""
    d = C.add_coords(_csv(path), lat="latitude", lon="longitude")
    d = d.with_columns(pl.col("functionallocation").str.strip_chars().alias("_floc"))
    if util is not None and util.height:
        u = util.select(
            pl.col("functionallocation").str.strip_chars().alias("_floc"),
            pl.col("utilisation_band").alias("_uband"),
            pl.col("source").alias("_usrc"),
        ).unique(subset=["_floc"], keep="first")
        d = d.join(u, on="_floc", how="left")
    else:
        d = d.with_columns(pl.lit(None, pl.Utf8).alias("_uband"), pl.lit(None, pl.Utf8).alias("_usrc"))
    d = d.with_columns(
        pl.coalesce(_txt("utilisation_band"), _txt("_uband")).alias("_band"),
        pl.coalesce(_txt("source"), _txt("_usrc")).alias("_bsrc"),
    )
    return d.select(
        _uid(pl.col("_floc")).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        _txt("substationalias").alias("name"),
        "lat", "lon", "coord_quality",
        C.num_expr("substationvoltage").alias("kv_hv"),
        C.num_expr("onanrating").alias("transformer_kva"),
        # Bands like "20-40%" / "120% +": store the midpoint (open band: lower bound + 10) so the map can
        # colour LV utilisation; the original band stays in native_ids.utilisation_band.
        pl.col("_band").str.extract_all(r"\d+").list.eval(pl.element().cast(pl.Float64))
        .map_elements(lambda v: (v[0] + v[1]) / 2 if len(v) >= 2 else (v[0] + 10 if len(v) == 1 else None),
                      return_dtype=pl.Float64).alias("utilisation_pct"),
        pl.col("_band").alias("_b"),
        C.num_expr("customer_count").cast(pl.Int64, strict=False).alias("customers"),
        pl.col("primaryfeeder").str.strip_chars().alias("upstream_primary"),
        _ids(d, floc="_floc", alias="substationalias", primary_floc="primaryfeederfunctionallocation",
             dno="dno", utilisation_band="_band", utilisation_source="_bsrc",
             demand_headroom_band="demand_headroom", transformers="numberoftransformers",
             design="substationdesign").alias("native_ids"),  # design: PMT pole / GMT ground
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    ).drop("_b")


# ---------- orchestration ----------

def _snap(source_id: str) -> tuple[Path, str | None]:
    snap = C.latest_snapshot(source_id)
    if snap is None:
        raise FileNotFoundError(f"no ok snapshot for {source_id}")
    return snap, C.as_of(C.snapshot_meta(snap))


def _csv_of(snap: Path) -> Path:
    return next(p for p in C.snapshot_files(snap) if p.suffix == ".csv")


def run() -> dict[str, int]:
    out: dict[str, int] = {}

    # assets: grid/primary sites + heatmap
    gp_snap, gp_asof = _snap("ukpn_grid_primary_sites")
    gp_path = _csv_of(gp_snap)
    sites_raw = _csv(gp_path)
    a_sites = sites_assets(gp_path, gp_asof)
    t3a = None
    try:
        t3_snap, _ = _snap("ukpn_ltds_t3a_load_observed")
        t3a = _floc_t3a(_csv_of(t3_snap))
    except FileNotFoundError:
        pass
    hm_snap, hm_asof = _snap("ukpn_heatmap")
    a_hm, cap = heatmap_tables(C.snapshot_files(hm_snap), hm_asof, sites_raw, t3a)
    asset = pl.concat([a_hm, a_sites], how="diagonal_relaxed")
    C.write_table(OPERATOR, "asset", asset)
    C.write_table(OPERATOR, "capacity", cap)
    out["asset"], out["capacity"] = asset.height, cap.height
    uids = set(asset["asset_uid"].to_list())

    # fault levels
    f_parts = []
    try:
        a_snap, a_asof = _snap("ukpn_ltds_t4a_fault_3ph")
        b_snap, _ = _snap("ukpn_ltds_t4b_fault_earth")
        f_parts.append(fault_ltds(_csv_of(a_snap), _csv_of(b_snap), uids, a_asof))
    except FileNotFoundError:
        pass
    try:
        g_snap, g_asof = _snap("ukpn_grid_primary_fault_levels")
        f_parts.append(fault_gp(_csv_of(g_snap), uids, g_asof))
    except FileNotFoundError:
        pass
    if f_parts:
        fl = pl.concat(f_parts, how="diagonal_relaxed")
        C.write_table(OPERATOR, "fault_level", fl)
        out["fault_level"] = fl.height

    # queue
    q = []
    for sid, part in (("ukpn_ecr_part1_under_1mw", 1), ("ukpn_ecr_part2_1mw_plus", 2)):
        try:
            s, asof = _snap(sid)
            q.append(ecr_projects(_csv_of(s), part, asof, sid))
        except FileNotFoundError:
            pass
    if q:
        qp = pl.concat(q, how="diagonal_relaxed")
        C.write_table(OPERATOR, "queue_project", qp)
        out["queue_project"] = qp.height

    # LV
    try:
        s_snap, s_asof = _snap("ukpn_secondary_sites")
        util = None
        try:
            u_snap, _ = _snap("ukpn_secondary_utilisation")
            util = _csv(_csv_of(u_snap))
        except FileNotFoundError:
            pass
        lv = lv_sites(_csv_of(s_snap), s_asof, util)
        C.write_table(OPERATOR, "lv_site", lv)
        out["lv_site"] = lv.height
    except FileNotFoundError:
        pass

    assert out["asset"] > 0
    return out
