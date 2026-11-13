"""SP Energy Networks (SPD Scotland + SPM Manweb) -> canonical tables.

Sources used (all Opendatasoft CSV exports, ';' delimited):
  asset + capacity : spen_heatmap_spd, spen_heatmap_spm  (LTDS-style capacity heatmaps)
  fault_level      : spen_ltds_fault_spd, spen_ltds_fault_spm_{132kv_3ph,132kv_1ph,33kv_3ph}
  queue_project    : spen_ecr_1mw_plus, spen_ecr_50kw_1mw, spen_queue_gsp_position (only rows absent from ECR)
  lv_site          : spen_smart_meter_transformer + spen_customer_connections_lv_tx (keyed by transformer ENID)
Skipped: spen_ltds_fault_spm_hv (MVA banding, not kA), naming tables (crosswalk, no asset data of their own),
polygons/DFES/flex/outage/NDP (not canonical tables), historic utilisation/loads/transformers (history or
forecast; no id shared with the heatmap), feeder utilisation (feeder level), lv_monitor and
capacity-management-system (no snapshot available).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from . import common as c

OP = "SPEN"
HEATMAP_BASIS = (
    "inferred from data, methodology text not stored locally: demand available = firm capacity - max demand "
    "(MVA, thermal); generation available = firm generation capacity + min demand (reverse power flow, MW); "
    "SPM values are per group row, repeated for each site in the group"
)


# ---------- helpers ----------

def _read(source_id: str, name: str = "export.csv") -> tuple[pl.DataFrame, dict] | None:
    snap = c.latest_snapshot(source_id)
    if snap is None:
        return None
    return read_csv(snap / name), c.snapshot_meta(snap)


def read_csv(path: Path) -> pl.DataFrame:
    """All columns as strings (nothing inferred), so null tokens are handled explicitly."""
    return pl.read_csv(path, separator=";", infer_schema_length=0, encoding="utf8")


def clean(col: str) -> pl.Expr:
    """Trimmed string; null for empty and for SPEN's 'data not available/applicable' tokens."""
    s = pl.col(col).cast(pl.Utf8).str.strip_chars()
    low = s.str.to_lowercase()
    return (
        pl.when(s.is_null() | (s == "") | low.str.starts_with("data not") | low.is_in(list(c.NULL_TOKENS)))
        .then(None)
        .otherwise(s)
    )


def _iso_date(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Utf8).str.extract(r"^(\d{4}-\d{2}-\d{2})", 1)


def _volts(col: str) -> tuple[pl.Expr, pl.Expr]:
    """'[33,11]' / '[33,6.6/6]' -> (hv, lv) kV; lv takes the first number of a 'a/b' pair."""
    nums = pl.col(col).cast(pl.Utf8).str.extract_all(r"\d+(?:\.\d+)?")
    return nums.list.get(0, null_on_oob=True).cast(pl.Float64), nums.list.get(1, null_on_oob=True).cast(pl.Float64)


def _fmt_kv(e: pl.Expr) -> pl.Expr:
    return e.cast(pl.Utf8)


# ---------- asset + capacity ----------

def _heatmap_rows(spd: pl.DataFrame, spm: pl.DataFrame) -> pl.DataFrame:
    """One row per site with native columns unified (SPD and SPM heatmaps differ in layout)."""
    a = spd.with_columns(
        pl.lit("SPD").alias("licence_area"),
        clean("name").alias("site"),
        clean("name").alias("group_name"),
        pl.col("latitude").alias("lat_s"),
        pl.col("longitude").alias("lon_s"),
        clean("gsp").alias("gsp_n"),
        pl.lit(None, pl.Utf8).alias("bsp_n"),
    )
    b = spm.with_columns(
        pl.lit("SPM").alias("licence_area"),
        pl.coalesce(clean("site_name"), clean("name")).alias("site"),
        clean("name").alias("group_name"),
        pl.col("lat").alias("lat_s"),
        pl.col("lon").alias("lon_s"),
        clean("gsp").alias("gsp_n"),
        clean("bsp").alias("bsp_n"),
    )
    keep = ["licence_area", "site", "group_name", "type", "voltage", "lat_s", "lon_s", "gsp_n", "bsp_n",
            "demandfirmcapacity", "demandmaximum", "demandavailablecapacity", "demandconstraint",
            "demandconstraintlimitingfactor", "generationavailablecapacity", "generationconstraint",
            "generationconstraintlimitingfactor"]
    df = pl.concat([a.select(keep), b.select(keep)], how="vertical")
    hv, lv = _volts("voltage")
    df = df.with_columns(hv.alias("kv_hv"), lv.alias("kv_lv"))
    # SPM heatmap repeats a site once per published coordinate variant: keep the first.
    df = df.with_columns(
        (pl.lit("SPEN:") + pl.col("licence_area") + ":" + pl.col("site").str.to_uppercase()
         + "|" + _fmt_kv(pl.col("kv_lv"))).alias("asset_uid")
    ).unique(subset="asset_uid", keep="first", maintain_order=True)
    return df


def build_asset_capacity(spd: pl.DataFrame, spm: pl.DataFrame, asof_spd: str | None,
                         asof_spm: str | None) -> tuple[pl.DataFrame, pl.DataFrame]:
    df = _heatmap_rows(spd, spm)
    df = df.with_columns(
        pl.when(pl.col("licence_area") == "SPD").then(pl.lit(asof_spd)).otherwise(pl.lit(asof_spm)).alias("as_of")
    )
    df = c.add_coords(df, lat="lat_s", lon="lon_s")
    asset = df.with_columns(
        pl.col("site").alias("name"),
        pl.col("type").map_elements(c.kind_from, return_dtype=pl.Utf8).alias("kind"),
        pl.col("gsp_n").alias("upstream_gsp"),
        pl.col("bsp_n").alias("upstream_bsp"),
        pl.struct("site", "group_name", "licence_area").map_elements(
            lambda r: c.ids_json(heatmap_name=r["site"], group=r["group_name"] if r["group_name"] != r["site"] else None),
            return_dtype=pl.Utf8,
        ).alias("native_ids"),
        pl.lit(OP).alias("operator"),
        (pl.lit("spen_heatmap_") + pl.col("licence_area").str.to_lowercase()).alias("source_id"),
    )
    cap = df.select(
        "asset_uid",
        c.num_expr("generationavailablecapacity").alias("gen_headroom_mw"),
        c.num_expr("demandavailablecapacity").alias("demand_headroom_mva"),
        c.num_expr("demandfirmcapacity").alias("firm_capacity_mva"),
        c.num_expr("demandmaximum").alias("max_demand_mva"),
        pl.col("generationconstraint").map_elements(c.rag, return_dtype=pl.Utf8).alias("gen_rag_native"),
        pl.col("demandconstraint").map_elements(c.rag, return_dtype=pl.Utf8).alias("demand_rag_native"),
        pl.concat_str(
            [pl.lit("demand: ") + clean("demandconstraintlimitingfactor"),
             pl.lit("generation: ") + clean("generationconstraintlimitingfactor")],
            separator="; ", ignore_nulls=True,
        ).alias("upstream_constraint"),
        pl.lit(HEATMAP_BASIS).alias("basis"),
        pl.when(pl.col("licence_area") == "SPD").then(pl.lit("spen_heatmap_spd")).otherwise(pl.lit("spen_heatmap_spm"))
        .alias("source_id"),
        "as_of",
    ).with_columns(
        pl.when(pl.col("upstream_constraint") == "").then(None).otherwise(pl.col("upstream_constraint"))
        .alias("upstream_constraint")
    )
    return asset, cap


# ---------- fault level ----------

def build_fault_spd(df: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    return df.select(
        pl.lit(OP).alias("operator"),
        pl.lit(None, pl.Utf8).alias("asset_uid"),
        pl.lit(None, pl.Utf8).alias("ltds_node"),
        clean("substation").alias("site_name"),
        c.num_expr("voltage_kv").alias("kv"),
        c.num_expr("calculated_3_phase_peak_make_ka").alias("fl_3ph_make_ka"),
        c.num_expr("calculated_3_phase_rms_break_ka").alias("fl_3ph_break_ka"),
        c.num_expr("calculated_single_phase_peak_make_ka").alias("fl_1ph_make_ka"),
        c.num_expr("calculated_single_phase_rms_break_ka").alias("fl_1ph_break_ka"),
        c.num_expr("equipment_rating_peak_make_ka").alias("rating_make_ka"),
        c.num_expr("equipment_rating_rms_break_ka").alias("rating_break_ka"),
        pl.lit("spen_ltds_fault_spd").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_fault_spm(parts: list[tuple[str, pl.DataFrame, str | None]]) -> pl.DataFrame:
    """parts: (source_id, df, as_of). Joins 3ph and 1ph tables on (group, substation, busbar, kV)."""
    frames = []
    for sid, df, asof in parts:
        group = "substation_group" if "substation_group" in df.columns else "grid_group"
        ph = "3ph" if "existing_system_3ph_peak_make_ka" in df.columns else "1ph"
        frames.append(
            df.select(
                clean(group).alias("grp"),
                clean("substation_name").alias("site_name"),
                clean("busbar_name").alias("ltds_node"),
                c.num_expr("voltage_level_kv").alias("kv"),
                c.num_expr(f"existing_system_{ph}_peak_make_ka").alias(f"fl_{ph}_make_ka"),
                c.num_expr(f"existing_system_{ph}_rms_break_ka").alias(f"fl_{ph}_break_ka"),
                c.num_expr(f"switchgear_rating_{ph}_make_ka").alias(f"rating_{ph}_make_ka"),
                c.num_expr(f"switchgear_rating_{ph}_break_ka").alias(f"rating_{ph}_break_ka"),
                pl.lit(sid).alias("source_id"),
                pl.lit(asof).alias("as_of"),
            )
        )
    keys = ["grp", "site_name", "ltds_node", "kv"]
    # 3ph and 1ph tables describe the same busbars: stack, then collapse to one row per busbar.
    out = (
        pl.concat(frames, how="diagonal_relaxed")
        .group_by(keys, maintain_order=True)
        .agg(pl.all().drop_nulls().first())
    )
    for col in ("fl_3ph_make_ka", "fl_3ph_break_ka", "fl_1ph_make_ka", "fl_1ph_break_ka",
                "rating_3ph_make_ka", "rating_3ph_break_ka", "rating_1ph_make_ka", "rating_1ph_break_ka"):
        if col not in out.columns:
            out = out.with_columns(pl.lit(None, pl.Float64).alias(col))
    # Ratings: the DNO gives separate 3ph/1ph switchgear ratings; canonical has one pair. Prefer 3ph, else 1ph.
    return out.with_columns(
        pl.lit(OP).alias("operator"),
        pl.lit(None, pl.Utf8).alias("asset_uid"),
        pl.coalesce("rating_3ph_make_ka", "rating_1ph_make_ka").alias("rating_make_ka"),
        pl.coalesce("rating_3ph_break_ka", "rating_1ph_break_ka").alias("rating_break_ka"),
    )


# ---------- queue ----------

def _status(col: str) -> pl.Expr:
    s = pl.col(col).cast(pl.Utf8).str.to_lowercase()
    return (
        pl.when(s.str.contains("accepted")).then(pl.lit("accepted"))
        .when(s.str.contains("offer")).then(pl.lit("offered"))
        .when(s.str.contains("scop")).then(pl.lit("scoping"))
        .when(s.str.contains("connected")).then(pl.lit("connected"))
        .otherwise(pl.lit("other"))
    )


def build_ecr_1mw(df: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    df = df.with_columns(
        clean("coordinates").str.split(",").list.get(0, null_on_oob=True).alias("_la"),
        clean("coordinates").str.split(",").list.get(1, null_on_oob=True).alias("_lo"),
    )
    df = c.add_coords(df, lat="_la", lon="_lo",
                      e="location_xcoordinate_eastings_where_data_is_held",
                      n="location_ycoordinate_northings_where_data_is_held")
    return df.select(
        (pl.lit("SPEN:") + pl.col("unique_id")).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        clean("customer_site").alias("site_name"),
        pl.coalesce(clean("primary"), clean("bulk_supply_point"), clean("grid_supply_point")).alias("connection_point"),
        clean("primary").alias("primary_name"),
        clean("bulk_supply_point").alias("bsp_name"),
        clean("grid_supply_point").alias("gsp_name"),
        "lat", "lon",
        _status("connection_status").alias("status"),
        clean("energy_conversion_technology_1").alias("technology"),
        pl.coalesce(c.num_expr("maximum_export_capacity_mw"), c.num_expr("change_to_maximum_export_capacity_mw"))
        .alias("export_mw"),
        pl.coalesce(c.num_expr("maximum_import_capacity_mw"), c.num_expr("change_to_maximum_import_capacity_mw"))
        .alias("import_mw"),
        pl.when(pl.col("energy_source_1").str.to_lowercase().str.contains("stored"))
        .then(c.num_expr("energy_source_energy_conversion_technology_1_registered_capacity_mw")).alias("storage_mw"),
        c.num_expr("point_of_connection_poc_voltage_kv").alias("kv"),
        pl.coalesce(_iso_date("date_connected"), _iso_date("target_energisation_date")).alias("connection_date"),
        pl.lit("spen_ecr_1mw_plus").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_ecr_50kw(df: pl.DataFrame, asof: str | None) -> tuple[pl.DataFrame, int]:
    """Coordinates deliberately not used (1 km rounded, ~49% missing). Corrupt shifted rows dropped.

    Returns (rows, number_of_dropped_rows)."""
    n0 = df.height
    ok = df.filter(pl.col("unique_id").str.starts_with("SPEN_50kW_"))  # shifted rows carry a date here
    ok = (
        ok.with_columns(clean("point_of_connection_poc_voltage_kv").is_null().alias("_nokv"))
        .sort("_nokv", maintain_order=True)  # duplicate rows: keep the one that states the voltage
        .unique(subset="unique_id", keep="first", maintain_order=True)
    )
    out = ok.select(
        (pl.lit("SPEN:") + pl.col("unique_id")).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        pl.lit(None, pl.Utf8).alias("site_name"),
        pl.coalesce(clean("primary"), clean("bulk_supply_point"), clean("grid_supply_point")).alias("connection_point"),
        clean("primary").alias("primary_name"),
        clean("bulk_supply_point").alias("bsp_name"),
        clean("grid_supply_point").alias("gsp_name"),
        pl.lit(None, pl.Float64).alias("lat"),
        pl.lit(None, pl.Float64).alias("lon"),
        _status("connection_status").alias("status"),
        clean("energy_conversion_technology_1").alias("technology"),
        (pl.coalesce(c.num_expr("maximum_export_capacity_kw"), c.num_expr("change_to_maximum_export_capacity_kw"))
         / 1000).alias("export_mw"),
        (pl.coalesce(c.num_expr("maximum_import_capacity_kw"), c.num_expr("change_to_maximum_import_capacity_kw"))
         / 1000).alias("import_mw"),
        pl.when(pl.col("energy_source_1").str.to_lowercase().str.contains("stored"))
        .then(c.num_expr("energy_source_energy_conversion_technology_1_registered_capacity_kw") / 1000)
        .alias("storage_mw"),
        c.num_expr("point_of_connection_poc_voltage_kv").alias("kv"),
        pl.coalesce(_iso_date("date_connected"), _iso_date("target_energisation_date")).alias("connection_date"),
        pl.lit("spen_ecr_50kw_1mw").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )
    return out, n0 - ok.height


def build_pipeline_extra(df: pl.DataFrame, known_ids: set[str], asof: str | None) -> pl.DataFrame:
    """Pipeline rows whose unique_id is not in the ECR (the rest duplicate ECR projects)."""
    df = df.filter(~pl.col("unique_id").is_in(list(known_ids)))
    df = df.with_columns(c.num_expr("export_capacity_mw").alias("_mw")).sort("_mw", descending=True, nulls_last=True)
    df = df.unique(subset="unique_id", keep="first", maintain_order=True)
    return df.select(
        (pl.lit("SPEN:") + pl.col("unique_id")).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        clean("gsp_name").alias("connection_point"),
        clean("gsp_name").alias("gsp_name"),
        pl.lit("other").alias("status"),
        clean("generator_type").alias("technology"),
        pl.col("_mw").alias("export_mw"),
        pl.lit("spen_queue_gsp_position").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


# ---------- lv_site ----------

def lv_monitor_latest(mon: pl.DataFrame) -> pl.DataFrame:
    """Latest month per monitored transformer: location ("lat, lon" text) + max utilisation %.
    The only open SPEN source with LV coordinates (GIS point assets / secondary polygons are access-restricted)."""
    m = mon.select(
        clean("transformer_enid").alias("enid"), clean("year_month").alias("ym"),
        c.num_expr("maximum_capacity_utilisation").alias("util"),
        clean("geo_points").alias("gp"),
    ).filter(pl.col("enid").is_not_null())
    m = m.sort("ym", descending=True, nulls_last=True).unique(subset="enid", keep="first")
    parts = pl.col("gp").str.split(",")
    return m.with_columns(
        parts.list.get(0, null_on_oob=True).alias("_lat"), parts.list.get(1, null_on_oob=True).alias("_lon"),
    ).select("enid", "util", "ym", "_lat", "_lon")


def build_lv(sm: pl.DataFrame, cc: pl.DataFrame, asof: str | None, mon: pl.DataFrame | None = None) -> pl.DataFrame:
    imports = ["residential_import", "non_residential_import_ehv", "non_residential_import_hv",
               "non_residential_import_lv"]
    cc = cc.select(
        clean("secondary_transformer_id").alias("enid"),
        clean("licence_area").alias("area_cc"),
        clean("primary_substation_name").alias("upstream_primary"),
        pl.sum_horizontal(*[c.num_expr(x) for x in imports]).alias("_cust"),
        pl.all_horizontal(*[c.num_expr(x).is_null() for x in imports]).alias("_allnull"),
    ).with_columns(pl.when(pl.col("_allnull")).then(None).otherwise(pl.col("_cust")).alias("customers"))
    sm = sm.select(clean("lv_transformer_enid").alias("enid"), clean("licence_area").alias("area_sm"))
    df = cc.join(sm, on="enid", how="full", coalesce=True).filter(pl.col("enid").is_not_null())
    df = df.unique(subset="enid", keep="first", maintain_order=True)
    if mon is not None:
        latest = lv_monitor_latest(mon)
        df = df.join(latest, on="enid", how="left")
        # monitored transformers missing from the connections list are still real LV sites with a location
        extra = latest.join(df.select("enid"), on="enid", how="anti")
        df = pl.concat([df, extra], how="diagonal_relaxed")
        df = c.add_coords(df, "_lat", "_lon")
    else:
        df = df.with_columns(pl.lit(None, pl.Float64).alias("lat"), pl.lit(None, pl.Float64).alias("lon"),
                             pl.lit("missing").alias("coord_quality"), pl.lit(None, pl.Float64).alias("util"))
    return df.select(
        (pl.lit("SPEN:") + pl.col("enid")).alias("asset_uid"),
        pl.lit(OP).alias("operator"),
        "lat", "lon", "coord_quality",
        pl.col("util").alias("utilisation_pct"),
        pl.col("customers").round(0).cast(pl.Int64, strict=False),
        "upstream_primary",
        pl.struct("enid", "area_cc", "area_sm").map_elements(
            lambda r: c.ids_json(transformer_enid=r["enid"], licence_area=r["area_cc"] or r["area_sm"]),
            return_dtype=pl.Utf8,
        ).alias("native_ids"),
        pl.lit("spen_customer_connections_lv_tx").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


# ---------- orchestration ----------

def run() -> dict[str, int]:
    out: dict[str, int] = {}

    # asset + capacity
    spd, spm = _read("spen_heatmap_spd"), _read("spen_heatmap_spm")
    if spd and spm:
        asset, cap = build_asset_capacity(spd[0], spm[0], c.as_of(spd[1]), c.as_of(spm[1]))
        c.write_table(OP, "asset", asset)
        c.write_table(OP, "capacity", cap)
        out["asset"], out["capacity"] = asset.height, cap.height

    # fault level
    faults = []
    f = _read("spen_ltds_fault_spd")
    if f:
        faults.append(build_fault_spd(f[0], c.as_of(f[1])))
    parts = []
    for sid in ("spen_ltds_fault_spm_132kv_3ph", "spen_ltds_fault_spm_132kv_1ph", "spen_ltds_fault_spm_33kv_3ph"):
        r = _read(sid)
        if r:
            parts.append((sid, r[0], c.as_of(r[1])))
    if parts:
        faults.append(build_fault_spm(parts))
    if faults:
        fl = pl.concat(faults, how="diagonal_relaxed")
        c.write_table(OP, "fault_level", fl)
        out["fault_level"] = fl.height

    # queue
    q, known = [], set()
    e1 = _read("spen_ecr_1mw_plus")
    if e1:
        q.append(build_ecr_1mw(e1[0], c.as_of(e1[1])))
        known |= set(e1[0]["unique_id"].to_list())
    e2 = _read("spen_ecr_50kw_1mw")
    if e2:
        rows, dropped = build_ecr_50kw(e2[0], c.as_of(e2[1]))
        q.append(rows)
        known |= set(e2[0]["unique_id"].to_list())
        print(f"SPEN ecr_50kw: dropped {dropped} corrupt/duplicate rows")
    pq = _read("spen_queue_gsp_position")
    if pq:
        q.append(build_pipeline_extra(pq[0], known, c.as_of(pq[1])))
    if q:
        qp = pl.concat(q, how="diagonal_relaxed")
        c.write_table(OP, "queue_project", qp)
        out["queue_project"] = qp.height

    # lv_site
    sm, cc = _read("spen_smart_meter_transformer"), _read("spen_customer_connections_lv_tx")
    if sm and cc:
        mon = _read("spen_lv_monitor")
        lv = build_lv(sm[0], cc[0], c.as_of(cc[1]), mon[0] if mon else None)
        c.write_table(OP, "lv_site", lv)
        out["lv_site"] = lv.height
    return out
