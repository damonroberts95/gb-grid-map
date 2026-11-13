"""Northern Powergrid (NPG) adapter.

Sources (see docs/sources/npg.md):
- npg_ltds_heatmap   -> asset (mrid keyed, 670 GSP/BSP/Primary) + capacity (available capacity, RAG)
- npg_heatmap_table  -> fault_level (kA, joined to asset by site name) + upstream constraint text
- npg_sites_list     -> lv_site (57k distribution substations), kv_hv/substation_id enrichment for assets
- npg_ltds_app6_fault -> fault_level (LTDS appendix 6: per busbar, 3-ph and 1-ph, make/break + ratings)
- npg_ecr_1mw_plus / npg_ecr_under_1mw / npg_queue -> queue_project
- npg_ltds_app7_generation -> queue_project (LTDS appendix 7: connected/accepted generation per
  GSP/BSP/primary and fuel, MVA; overlaps the ECR, so match.py keeps it out of the MW totals)
- npg_ltds_app3_circuits / app4_transformers / app5_load: fetched and registered, not mapped (no
  canonical table for circuits, transformers or load forecasts)

Skipped: NDP headroom (forecast by scenario/year, no canonical table), ltds_ehv_sites (no usable key to
the heatmap), DFES, LCT, flex, boundaries (not in the canonical schema).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from . import common as c

OP = "NPG"
AREA = {"NPgN": "NE", "NPgY": "YO", "North East": "NE", "Yorkshire": "YO"}

CAPACITY_BASIS = (
    "LTDS available capacity (MW); methodology not stated in data - appears limited by "
    "thermal, fault level and voltage"
)


# ---------- helpers ----------

def _read(source_id: str) -> tuple[pl.DataFrame, dict] | None:
    snap = c.latest_snapshot(source_id)
    if snap is None:
        return None
    return read_csv(c.snapshot_files(snap)[0]), c.snapshot_meta(snap)


def read_csv(path: Path) -> pl.DataFrame:
    """Opendatasoft export: ';' delimited, UTF-8 BOM; everything as text, numbers parsed via common."""
    return pl.read_csv(path, separator=";", encoding="utf8", infer_schema_length=0)


def _num(*cols: str) -> list[pl.Expr]:
    return [c.num_expr(x).alias(x) for x in cols]


def _split_latlon(df: pl.DataFrame, col: str) -> pl.DataFrame:
    """NPG CSV latlong/geopoint is the text '<lat>, <lon>' (lat first)."""
    parts = pl.col(col).cast(pl.Utf8).str.split_exact(",", 1)
    return df.with_columns(
        parts.struct.field("field_0").alias("_lat"), parts.struct.field("field_1").alias("_lon")
    )


def _clean(col: str) -> pl.Expr:
    """Trimmed text with NPG's 'not a value' markers turned into null."""
    s = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return (
        pl.when(
            s.is_null()
            | s.str.to_lowercase().is_in(list(c.NULL_TOKENS))
            | s.str.to_lowercase().str.starts_with("data not")
            | (s.str.to_lowercase() == "redacted")
        )
        .then(None)
        .otherwise(s)
    )


def _rag(col: str) -> pl.Expr:
    return pl.col(col).map_elements(c.rag, return_dtype=pl.Utf8)


def _mrid_names(df: pl.DataFrame) -> dict[str, str]:
    return dict(zip(df["mrid"].to_list(), df["name"].to_list(), strict=True))


# ---------- asset + capacity + fault_level ----------

def build_assets(ltds: pl.DataFrame, heat: pl.DataFrame | None, sites: pl.DataFrame | None,
                 asof: str | None) -> pl.DataFrame:
    df = ltds.with_columns(
        pl.col("mrid").str.strip_chars(),
        pl.col("name").str.strip_chars(),
    )
    if heat is not None:
        he = heat.select(
            pl.col("psp_name").str.strip_chars().alias("name"),
            pl.col("grid_easting").alias("_e"),
            pl.col("grid_northing").alias("_n"),
            pl.col("gsp_name").alias("_h_gsp"),
        )
        df = df.join(he, on="name", how="left")
    else:
        df = df.with_columns(pl.lit(None, pl.Utf8).alias("_e"), pl.lit(None, pl.Utf8).alias("_n"),
                             pl.lit(None, pl.Utf8).alias("_h_gsp"))
    df = c.add_coords(df, lat="latitude", lon="longitude", e="_e", n="_n")

    kind = {"GSP": "GSP", "BSP": "BSP", "PRIMARY": "PRIMARY"}
    df = df.with_columns(
        pl.col("type").map_elements(lambda t: kind.get(str(t).strip().upper()) or c.kind_from(t),
                                    return_dtype=pl.Utf8).alias("kind"),
    )
    names = _mrid_names(df)
    df = df.with_columns(
        # gsp/bsp columns hold mrids; 'NGET' / 'N/A' mean no NPG-owned upstream of that level
        pl.col("gsp").replace_strict(names, default=None, return_dtype=pl.Utf8).alias("upstream_gsp"),
        pl.col("bsp").replace_strict(names, default=None, return_dtype=pl.Utf8).alias("upstream_bsp"),
    ).with_columns(pl.coalesce("upstream_gsp", pl.col("_h_gsp")).alias("upstream_gsp"))

    # kv_hv + substation_id from the sites list, joined on (upper name, level) only when unambiguous
    df = df.with_columns(pl.col("name").str.to_uppercase().alias("_k"))
    if sites is not None:
        ehv = (
            sites.filter(pl.col("site_level") != "Distribution")
            .with_columns(
                pl.col("site_name").str.strip_chars().str.to_uppercase().alias("_k"),
                pl.col("site_level").str.to_uppercase().alias("kind"),
                c.num_expr("primary_voltage_kv").alias("_kv_hv"),
                pl.col("substation_id").alias("_substation_id"),
            )
            .unique(["_k", "kind"], keep="none")
            .select("_k", "kind", "_kv_hv", "_substation_id")
        )
        df = df.join(ehv, on=["_k", "kind"], how="left")
    else:
        df = df.with_columns(pl.lit(None, pl.Float64).alias("_kv_hv"), pl.lit(None, pl.Utf8).alias("_substation_id"))

    rows = df.select("mrid", "_substation_id", "gsp", "bsp").rows()
    native = [
        c.ids_json(mrid=m, substation_id=s, gsp_mrid=g if str(g).startswith("GSP-") else None,
                   bsp_mrid=b if str(b).startswith("BSP-") else None)
        for m, s, g, b in rows
    ]
    return df.with_columns(
        ("NPG:" + pl.col("mrid")).alias("asset_uid"),
        pl.lit(OP).alias("operator"),
        pl.col("area").replace_strict(AREA, default=None, return_dtype=pl.Utf8).alias("licence_area"),
        pl.col("name"),
        pl.col("_kv_hv").alias("kv_hv"),
        c.num_expr("voltages").alias("kv_lv"),
        pl.Series("native_ids", native, pl.Utf8),
        pl.lit("npg_ltds_heatmap").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_capacity(assets: pl.DataFrame, heat: pl.DataFrame | None, asof: str | None) -> pl.DataFrame:
    df = assets.with_columns(
        c.num_expr("generationavailablecapacity").alias("gen_headroom_mw"),
        c.num_expr("demandavailablecapacity").alias("demand_headroom_mw"),
        c.num_expr("demandfirmcapacity").alias("firm_capacity_mva"),
        c.num_expr("demandmaximum").alias("max_demand_mva"),
        c.num_expr("generationcontractedcapacity").alias("contracted_gen_mw"),
        _rag("generationconstraint").alias("gen_rag_native"),
        _rag("demandconstraint").alias("demand_rag_native"),
    )
    if heat is not None:
        up = heat.select(
            pl.col("psp_name").str.strip_chars().alias("name"),
            pl.col("upgenconst").str.strip_chars().alias("_ug"),
            pl.col("updemconst").str.strip_chars().alias("_ud"),
        )
        df = df.join(up, on="name", how="left")
        real = lambda col: pl.col(col).is_not_null() & ~pl.col(col).str.to_lowercase().is_in(["no", "nget"])
        df = df.with_columns(
            pl.concat_str(
                [pl.when(real("_ug")).then("gen: " + pl.col("_ug")),
                 pl.when(real("_ud")).then("demand: " + pl.col("_ud"))],
                separator="; ", ignore_nulls=True,
            ).alias("_uc")
        ).with_columns(pl.when(pl.col("_uc") == "").then(None).otherwise(pl.col("_uc")).alias("upstream_constraint"))
    return df.with_columns(
        pl.lit(CAPACITY_BASIS).alias("basis"),
        pl.lit("npg_ltds_heatmap").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_fault_level(heat: pl.DataFrame, assets: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    """heatmapdatatable: *_d = fault level now, *_r = switchgear rating (fl = make_d / make_r)."""
    df = heat.with_columns(pl.col("psp_name").str.strip_chars().alias("site_name"))
    df = df.with_columns(*_num("fl_make_d", "fl_make_r", "fl_break_d", "fl_break_r", "pvoltage"))
    # 0 kA is not a fault level; four values all exactly 1.0 kA is a placeholder row (e.g. Kielder)
    placeholder = (
        (pl.col("fl_make_d") == 1) & (pl.col("fl_make_r") == 1)
        & (pl.col("fl_break_d") == 1) & (pl.col("fl_break_r") == 1)
    )
    df = df.filter(~placeholder).with_columns(
        [pl.when(pl.col(x) > 0).then(pl.col(x)).alias(x)
         for x in ("fl_make_d", "fl_make_r", "fl_break_d", "fl_break_r")]
    )
    link = assets.select("name", "asset_uid", "mrid")
    df = df.join(link, left_on="site_name", right_on="name", how="left")
    return df.select(
        "asset_uid",
        pl.lit(OP).alias("operator"),
        pl.col("mrid").alias("ltds_node"),
        "site_name",
        pl.col("pvoltage").alias("kv"),
        # phase not stated in the dataset; NPG heatmap fault levels are the 3-phase figures (assumed)
        pl.col("fl_make_d").alias("fl_3ph_make_ka"),
        pl.col("fl_break_d").alias("fl_3ph_break_ka"),
        pl.col("fl_make_r").alias("rating_make_ka"),
        pl.col("fl_break_r").alias("rating_break_ka"),
        pl.lit("npg_heatmap_table").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_fault_level_app6(df: pl.DataFrame, assets: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    """LTDS appendix 6: one row per busbar and phase ('3_ph_or_1_ph' = 3 or 1). Peak make / RMS break kA
    plus switchgear ratings. Zero or negative values are not fault levels. site_name = busbar name,
    joined to an asset by exact name where possible (else linked by name in match)."""
    df = df.with_columns(
        _clean("s_s_or_busbar_name").alias("site_name"),
        *_num("nominal_voltage_kv", "3_ph_or_1_ph", "existing_system_peak_make_fault_currents_ka",
              "existing_system_a_c_rms_break_fault_currents_ka", "make_rating_ka", "break_rating_ka"),
    ).filter(pl.col("site_name").is_not_null())
    pos = [pl.when(pl.col(x) > 0).then(pl.col(x)).alias(x) for x in
           ("existing_system_peak_make_fault_currents_ka", "existing_system_a_c_rms_break_fault_currents_ka",
            "make_rating_ka", "break_rating_ka")]
    df = df.with_columns(*pos)
    one = pl.col("3_ph_or_1_ph") == 1
    link = assets.select("name", "asset_uid", "mrid").unique("name")
    df = df.join(link, left_on="site_name", right_on="name", how="left")
    mk, br = "existing_system_peak_make_fault_currents_ka", "existing_system_a_c_rms_break_fault_currents_ka"
    out = df.select(
        "asset_uid",
        pl.lit(OP).alias("operator"),
        pl.col("mrid").alias("ltds_node"),
        "site_name",
        pl.col("nominal_voltage_kv").alias("kv"),
        pl.when(~one).then(pl.col(mk)).alias("fl_3ph_make_ka"),
        pl.when(~one).then(pl.col(br)).alias("fl_3ph_break_ka"),
        pl.when(one).then(pl.col(mk)).alias("fl_1ph_make_ka"),
        pl.when(one).then(pl.col(br)).alias("fl_1ph_break_ka"),
        pl.col("make_rating_ka").alias("rating_make_ka"),
        pl.col("break_rating_ka").alias("rating_break_ka"),
        pl.lit("npg_ltds_app6_fault").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )
    return out.filter(pl.any_horizontal(pl.col(x).is_not_null() for x in
                                        ("fl_3ph_make_ka", "fl_3ph_break_ka", "fl_1ph_make_ka", "fl_1ph_break_ka")))


# ---------- lv_site ----------

def build_lv_sites(sites: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    d = sites.filter(pl.col("site_level") == "Distribution")
    d = _split_latlon(d, "latlong")
    d = d.with_columns(*_num("transformer_rating_kva", "primary_voltage_kv"))
    d = d.with_columns(
        pl.col("substation_id").str.strip_chars(),
        # customer_numbers is censored ("≤5"): only exact counts become numbers
        pl.when(pl.col("customer_numbers").str.strip_chars().str.contains(r"^\d+$"))
        .then(pl.col("customer_numbers").str.strip_chars()).cast(pl.Int64, strict=False).alias("customers"),
    )
    # a substation with several transformers is several rows sharing one id: one site, summed kVA
    d = d.group_by("substation_id", maintain_order=True).agg(
        pl.col("site_name").first(), pl.col("dno_area").first(),
        pl.col("_lat").first(), pl.col("_lon").first(),
        pl.col("eastings").first(), pl.col("northings").first(),
        pl.col("primary_voltage_kv").first(),
        pl.col("transformer_rating_kva").sum().alias("_kva"),
        pl.col("transformer_rating_kva").is_not_null().sum().alias("_nk"),
        pl.col("customers").first(),
        pl.col("upstream_substation").first(),
        pl.col("site_type").first(),
    )
    d = c.add_coords(d, lat="_lat", lon="_lon", e="eastings", n="northings")
    return d.select(
        ("NPG:" + pl.col("substation_id")).alias("asset_uid"),
        pl.lit(OP).alias("operator"),
        pl.col("site_name").str.strip_chars().alias("name"),
        "lat", "lon", "coord_quality",
        pl.col("primary_voltage_kv").alias("kv_hv"),
        pl.when(pl.col("_nk") > 0).then(pl.col("_kva")).alias("transformer_kva"),
        pl.lit(None, pl.Float64).alias("utilisation_pct"),
        "customers",
        _clean("upstream_substation").alias("upstream_primary"),
        pl.struct("substation_id", "site_type").map_elements(
            lambda r: c.ids_json(substation_id=r["substation_id"], site_type=r["site_type"]), return_dtype=pl.Utf8)
        .alias("native_ids"),
        pl.lit("npg_sites_list").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


# ---------- queue_project ----------

_STATUS = {"connected": "connected", "accepted to connect": "accepted"}


def build_ecr(df: pl.DataFrame, source_id: str, asof: str | None) -> pl.DataFrame:
    df = _split_latlon(df, "geopoint")
    df = c.add_coords(df, lat="_lat", lon="_lon",
                      e="location_x_coordinate_eastings_where_data_is_held",
                      n="location_y_coordinate_northings_where_data_is_held")
    reg = "energy_source_energy_conversion_technology_1_registered_capacity_mw"
    df = df.with_columns(
        *_num(reg, "maximum_export_capacity_mw", "maximum_import_capacity_mw",
              "point_of_connection_poc_voltage_kv", "already_connected_registered_capacity_mw",
              "accepted_to_connect_registered_capacity_mw"),
        pl.col("connection_status").str.strip_chars().str.to_lowercase()
        .replace_strict(_STATUS, default="other", return_dtype=pl.Utf8).alias("status"),
        pl.col("energy_source_1").str.to_lowercase().str.contains("stor").fill_null(False).alias("_stor"),
    )
    status_cap = pl.when(pl.col("status") == "connected").then(pl.col("already_connected_registered_capacity_mw")) \
        .when(pl.col("status") == "accepted").then(pl.col("accepted_to_connect_registered_capacity_mw"))
    df = df.with_columns(
        pl.coalesce("maximum_export_capacity_mw", status_cap, pl.col(reg)).alias("_export"),
        pl.col("date_connected").str.strip_chars().alias("_dc"),
    )
    return df.select(
        ("NPG:" + pl.col("ecrid").str.strip_chars()).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        _clean("customer_site").alias("site_name"),
        pl.coalesce(_clean("primary"), _clean("bulk_supply_point"), _clean("grid_supply_point"))
        .alias("connection_point"),
        _clean("primary").alias("primary_name"),
        _clean("bulk_supply_point").alias("bsp_name"),
        _clean("grid_supply_point").alias("gsp_name"),
        "lat", "lon", "status",
        pl.coalesce(_clean("energy_conversion_technology_1"), _clean("energy_source_1")).alias("technology"),
        pl.col("_export").alias("export_mw"),
        pl.col("maximum_import_capacity_mw").alias("import_mw"),
        pl.when(pl.col("_stor")).then(pl.col(reg)).alias("storage_mw"),
        pl.col("point_of_connection_poc_voltage_kv").alias("kv"),
        pl.when(pl.col("_dc").str.contains(r"^\d{4}-\d{2}-\d{2}")).then(pl.col("_dc").str.slice(0, 10))
        .alias("connection_date"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_queue(df: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    """connection-queue-information: transmission-node queue; status not stated -> 'other'."""
    df = df.with_columns(*_num("export_capacity", "row_number"))
    df = df.filter(pl.col("row_number").is_not_null())
    return df.select(
        ("NPG:Q" + pl.col("row_number").cast(pl.Int64).cast(pl.Utf8)).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        pl.lit(None, pl.Utf8).alias("site_name"),
        _clean("gsp").alias("connection_point"),
        pl.lit(None, pl.Utf8).alias("primary_name"),
        pl.lit(None, pl.Utf8).alias("bsp_name"),
        _clean("gsp").alias("gsp_name"),
        pl.lit(None, pl.Float64).alias("lat"),
        pl.lit(None, pl.Float64).alias("lon"),
        pl.lit("other").alias("status"),
        _clean("technology").alias("technology"),
        pl.col("export_capacity").alias("export_mw"),
        pl.when(pl.col("dno_acceptance_date").str.contains(r"^\d{4}-\d{2}-\d{2}"))
        .then(pl.col("dno_acceptance_date").str.slice(0, 10)).alias("connection_date"),
        pl.lit("npg_queue").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


def build_generation_app7(df: pl.DataFrame, asof: str | None) -> pl.DataFrame:
    """LTDS appendix 7: generation per GSP/BSP/primary/fuel. installed_capacity_mva is MVA (stored in
    export_mw as published; not converted). Rows have no id: uid = row number within the file."""
    df = df.with_columns(*_num("installed_capacity_mva", "connection_voltage_kv"),
                         pl.col("connected_accepted").str.strip_chars().str.to_lowercase().alias("_st"))
    df = df.with_row_index("_i", offset=1).filter(pl.col("installed_capacity_mva").is_not_null())
    fuel = _clean("fuel_type")
    return df.select(
        ("NPG:A7:" + pl.col("_i").cast(pl.Utf8)).alias("project_uid"),
        pl.lit(OP).alias("operator"),
        pl.lit(None, pl.Utf8).alias("site_name"),
        pl.coalesce(_clean("primary_substation"), _clean("bulk_supply_point"), _clean("grid_supply_point"))
        .alias("connection_point"),
        _clean("primary_substation").alias("primary_name"),
        _clean("bulk_supply_point").alias("bsp_name"),
        _clean("grid_supply_point").alias("gsp_name"),
        pl.lit(None, pl.Float64).alias("lat"),
        pl.lit(None, pl.Float64).alias("lon"),
        pl.col("_st").replace_strict({**_STATUS, "accepted": "accepted"}, default="other", return_dtype=pl.Utf8)
        .alias("status"),
        fuel.alias("technology"),
        pl.col("installed_capacity_mva").alias("export_mw"),
        pl.col("connection_voltage_kv").alias("kv"),
        pl.lit("npg_ltds_app7_generation").alias("source_id"),
        pl.lit(asof).alias("as_of"),
    )


# ---------- entry point ----------

def run() -> dict[str, int]:
    out: dict[str, int] = {}
    ltds = _read("npg_ltds_heatmap")
    heat = _read("npg_heatmap_table")
    sites = _read("npg_sites_list")
    if ltds is None:
        raise RuntimeError("NPG: no ok snapshot for npg_ltds_heatmap (needs portal key); cannot build assets")

    heat_df = heat[0] if heat else None
    sites_df = sites[0] if sites else None
    assets = build_assets(ltds[0], heat_df, sites_df, c.as_of(ltds[1]))
    cap = build_capacity(assets, heat_df, c.as_of(ltds[1]))
    assert assets.height > 0, "NPG asset rows == 0"
    c.write_table(OP, "asset", assets)
    c.write_table(OP, "capacity", cap)
    out["asset"], out["capacity"] = assets.height, cap.height

    if heat:
        fl = build_fault_level(heat[0], assets, c.as_of(heat[1]))
        c.write_table(OP, "fault_level", fl)
        out["fault_level"] = fl.height

    app6 = _read("npg_ltds_app6_fault")
    if app6:
        fl6 = build_fault_level_app6(app6[0], assets, c.as_of(app6[1]))
        if "fault_level" in out:
            fl = pl.concat([fl, fl6], how="diagonal_relaxed")
        else:
            fl = fl6
        c.write_table(OP, "fault_level", fl)
        out["fault_level"] = fl.height

    if sites:
        lv = build_lv_sites(sites[0], c.as_of(sites[1]))
        assert lv.height > 0, "NPG lv_site rows == 0"
        c.write_table(OP, "lv_site", lv)
        out["lv_site"] = lv.height

    queue = []
    for sid in ("npg_ecr_1mw_plus", "npg_ecr_under_1mw"):
        r = _read(sid)
        if r:
            queue.append(build_ecr(r[0], sid, c.as_of(r[1])))
    r = _read("npg_queue")
    if r:
        queue.append(build_queue(r[0], c.as_of(r[1])))
    r = _read("npg_ltds_app7_generation")
    if r:
        queue.append(build_generation_app7(r[0], c.as_of(r[1])))
    if queue:
        q = pl.concat(queue, how="diagonal_relaxed")
        c.write_table(OP, "queue_project", q)
        out["queue_project"] = q.height
    return out
