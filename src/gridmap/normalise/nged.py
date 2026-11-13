"""NGED (National Grid Electricity Distribution) adapter.

Sources used (open, ok snapshots):
  nged_ltds_heatmap            -> asset (BSP/PRIMARY) + capacity       (JSON, one file per licence area)
  nged_ltds_cim_substation_numbers -> enrichment of heatmap native_ids (mRID == Substation Number)
  nged_ltds_fault_levels       -> fault_level (LTDS table 4; keyed by node code, no asset id)
  nged_ecr                     -> queue_project (connected + accepted)
  nged_queue                   -> queue_project (per-GSP connection queue; Bus Name = LTDS node)
  nged_connections_reform_register -> queue_project (CMP435/CMP446; 3 title lines before header)

Gated sources (NGED_API_KEY; all tested against real files):
  nged_network_capacity_map    -> GSP/BSP/PRIMARY rows folded into heatmap asset/capacity (duplicates merged,
                                  ids kept in native_ids); SECONDARY rows only enrich lv_site (never asset)
  nged_primary_locations       -> GSP (super grid) / BSP / primary points: extra asset rows + coordinate fill
  nged_distribution_substations (+ _locations, joined on Substation Number) -> lv_site
Not used: nged_network_opportunity_map (subset of the capacity map), gsp_technical_limits, gcr, lct_connections.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import polars as pl

from . import common as C

OP = "NGED"
LICENCE_AREAS = {
    "east midlands": "EMID", "west midlands": "WMID", "south wales": "SWALES", "south west": "SWEST",
}
# Heatmap RAG text is a colour word; anything else ("0", blank) is treated as not published.
HEADROOM_BASIS = "unknown (LTDS heatmap; units and contracted-gen treatment not stated in file)"


# ---------------------------------------------------------------- readers

def add_utilisation(lv: pl.DataFrame) -> pl.DataFrame:
    """NGED publishes day/night maximum demand (kVA) per LV substation: utilisation % = max(day, night) / transformer
    kVA. Published values, not an estimate; null when either is missing or the rating is 0."""
    peak = pl.max_horizontal(C.num_expr("_day"), C.num_expr("_night"))
    kva = pl.col("transformer_kva")
    util = pl.when((kva > 0) & peak.is_not_null()).then((peak / kva * 100).round(1)).otherwise(None)
    return lv.with_columns(util.alias("utilisation_pct"))


def _clean_cols(df: pl.DataFrame) -> pl.DataFrame:
    return df.rename({c: c.replace("﻿", "").strip() for c in df.columns})


def _read_csv(path: Path, skip_rows: int = 0) -> pl.DataFrame:
    df = pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy", skip_rows=skip_rows,
                     truncate_ragged_lines=True)
    return _clean_cols(df)


def _read_json_rows(paths: list[Path]) -> pl.DataFrame:
    """JSON arrays of flat objects -> all-string frame (columns have mixed int/float/str values)."""
    rows: list[dict] = []
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):  # tolerate {"data": [...]}
            data = next((v for v in data.values() if isinstance(v, list)), [])
        rows += [r for r in data if isinstance(r, dict)]
    keys = list(dict.fromkeys(k for r in rows for k in r))
    return pl.DataFrame({k: [None if r.get(k) is None else str(r[k]) for r in rows] for k in keys},
                        schema={k: pl.Utf8 for k in keys})


def _blank_to_null(c: str) -> pl.Expr:
    s = pl.col(c).cast(pl.Utf8).str.strip_chars()
    return pl.when(s.str.to_lowercase().is_in(list(C.NULL_TOKENS | {"data not available", "data not applicable"})))\
        .then(None).otherwise(s)


def _txt(c: str, alias: str | None = None) -> pl.Expr:
    return _blank_to_null(c).alias(alias or c)


def _iso_date(c: str) -> pl.Expr:
    """dd/mm/yyyy[ hh:mm] -> ISO date string; unparsable -> null."""
    return (pl.col(c).cast(pl.Utf8).str.strip_chars().str.slice(0, 10)
            .str.to_date("%d/%m/%Y", strict=False).dt.strftime("%Y-%m-%d"))


def _slug(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _unique(keys: list[str]) -> list[str]:
    """Make a list of ids unique by suffixing repeats with ~n (first occurrence unchanged)."""
    seen: Counter = Counter()
    out = []
    for k in keys:
        seen[k] += 1
        out.append(k if seen[k] == 1 else f"{k}~{seen[k]}")
    return out


def _hash(*parts) -> str:
    return hashlib.md5("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- heatmap: asset + capacity

def build_heatmap(files: list[Path], as_of: str | None, cim: pl.DataFrame | None = None,
                  source_id: str = "nged_ltds_heatmap") -> tuple[pl.DataFrame, pl.DataFrame]:
    df = _read_json_rows(files)
    mrid = pl.col("mRID").str.strip_chars()
    df = df.filter(mrid.is_not_null() & (mrid != ""))
    # a few mRIDs are shared by a BSP and Primary (or two 132/33 BSPs); disambiguate by name
    dup = df["mRID"].is_duplicated().to_list()
    names = df["name"].to_list()
    uids = [f"{OP}:{m}:{_slug(n)}" if d else f"{OP}:{m}" for m, n, d in zip(df["mRID"], names, dup)]
    df = df.with_columns(pl.Series("asset_uid", uids))

    if cim is not None and cim.height:
        cim = cim.select(
            pl.col("Substation Number").str.strip_chars().alias("mRID"),
            pl.col("LTDS Substation Name").alias("cim_name"),
            pl.col("Substation Type").alias("cim_type"),
        ).unique("mRID")
        df = df.join(cim, on="mRID", how="left")
    else:
        df = df.with_columns(pl.lit(None, pl.Utf8).alias("cim_name"), pl.lit(None, pl.Utf8).alias("cim_type"))

    df = C.add_coords(df, lat="latitude", lon="longitude")
    volts = pl.col("voltages").str.split("-")
    asset = df.with_columns(
        pl.lit(OP).alias("operator"),
        pl.col("area").str.to_lowercase().replace_strict(LICENCE_AREAS, default=None).alias("licence_area"),
        pl.col("name").str.strip_chars().alias("name"),
        pl.col("type").map_elements(C.kind_from, return_dtype=pl.Utf8).alias("kind"),
        volts.list.first().cast(pl.Float64, strict=False).alias("kv_hv"),
        volts.list.last().cast(pl.Float64, strict=False).alias("kv_lv"),
        _txt("GSP", "upstream_gsp"),
        _txt("BSP", "upstream_bsp"),
        _txt("Primary", "upstream_primary"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
        pl.struct("mRID", "cim_name", "cim_type", "voltages").map_elements(
            lambda r: C.ids_json(mrid=r["mRID"], cim_name=r["cim_name"], cim_type=r["cim_type"],
                                 voltages=r["voltages"]),
            return_dtype=pl.Utf8).alias("native_ids"),
    ).with_columns(
        # single-voltage sites ("11", "33") have no separate upstream voltage
        pl.when(pl.col("voltages").str.contains("-")).then(pl.col("kv_hv")).otherwise(None).alias("kv_hv"),
    )

    def rag_col(c: str) -> pl.Expr:
        return pl.col(c).map_elements(C.rag, return_dtype=pl.Utf8)

    lf_d, lf_g = _txt("demandConstraintLimitingFactor"), _txt("generationConstraintLimitingFactor")
    cap = df.with_columns(
        C.num_expr("generationAvailableCapacity").alias("gen_headroom_mw"),
        # units not stated; demand side is firm-capacity based so keep in MVA column, never MW
        C.num_expr("demandAvailableCapacity").alias("demand_headroom_mva"),
        C.num_expr("demandFirmCapacity").alias("firm_capacity_mva"),
        C.num_expr("generationConnectionOffersAcceptedCapacity").alias("contracted_gen_mw"),
        rag_col("generationConstraint").alias("gen_rag_native"),
        rag_col("demandConstraint").alias("demand_rag_native"),
        pl.when(lf_d.is_null() & lf_g.is_null()).then(None)
        .when(lf_d == lf_g).then(lf_d)
        .otherwise(pl.concat_str([pl.lit("demand: "), lf_d.fill_null("?"), pl.lit("; gen: "), lf_g.fill_null("?")]))
        .alias("upstream_constraint"),
        pl.lit(HEADROOM_BASIS).alias("basis"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )
    return asset, cap


# ---------------------------------------------------------------- LTDS table 4: fault_level

def build_fault_levels(path: Path, as_of: str | None, source_id: str = "nged_ltds_fault_levels") -> pl.DataFrame:
    """One row per (Node A, Node B, Branch) as published; no asset id exists in this table."""
    df = _read_csv(path)
    df = df.select([c for c in df.columns if c])  # trailing empty header column
    return df.select(
        pl.lit(None, pl.Utf8).alias("asset_uid"),
        pl.lit(OP).alias("operator"),
        _txt("Node A", "ltds_node"),
        _txt("Substation or Busbar Name", "site_name"),
        C.num_expr("Operating Voltage").alias("kv"),
        C.num_expr("3ph Peak Make").alias("fl_3ph_make_ka"),
        C.num_expr("3ph RMS Break").alias("fl_3ph_break_ka"),
        C.num_expr("1ph Peak Make").alias("fl_1ph_make_ka"),
        C.num_expr("1ph RMS Break").alias("fl_1ph_break_ka"),
        C.num_expr("3ph Peak Make Rating").alias("rating_make_ka"),
        C.num_expr("3ph RMS Break Rating").alias("rating_break_ka"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    ).filter(pl.col("ltds_node").is_not_null())


# ---------------------------------------------------------------- ECR -> queue_project

ECR_STATUS = {"connected": "connected", "accepted to connect": "accepted"}


def build_ecr(path: Path, as_of: str | None, source_id: str = "nged_ecr") -> pl.DataFrame:
    df = _read_csv(path)
    e = "location(x-coordinate):_eastings_(where_data_is_held)"
    n = "location(y-coordinate):_northings_(where_data_is_held)"
    df = C.add_coords(df, lat="lat", lon="lon", e=e, n=n)
    df = df.with_columns(
        _txt("export_mpan/msid", "_mpan"),
        _txt("customer_name", "_cust"),
        _txt("customer_site", "_site"),
        _txt("postcode", "_pc"),
        _txt("grid_supply_point", "gsp_name"),
        _txt("bulk_supply_point", "bsp_name"),
        _txt("primary", "primary_name"),
        pl.col("connection_status").str.strip_chars().str.to_lowercase()
        .replace_strict(ECR_STATUS, default="other").alias("status"),
        _txt("energy_source_1").alias("_src"),
        C.num_expr("energy_source_&_conversion_tech_1_reg_capacity_mw").alias("_reg1"),
        C.num_expr("already_connected_registered_capacity(mw)").alias("_conn_reg"),
        C.num_expr("connected_maximum_export_capacity(mw)").alias("_conn_exp"),
        C.num_expr("connected_maximum_import_capacity(mw)").alias("import_mw"),
        C.num_expr("accepted_to_connect_registered_capacity(mw)").alias("_acc_reg"),
        C.num_expr("accepted_change_to_maximum_export_capacity(mw)").alias("_acc_exp"),
        C.num_expr("point_of_connection(poc)_voltage(kv)").alias("kv"),
        _iso_date("date_connected").alias("_dc"),
        _iso_date("target_energisation_date").alias("_te"),
    ).with_columns(
        pl.when(pl.col("status") == "accepted")
        .then(pl.coalesce("_acc_exp", "_acc_reg", "_reg1"))
        .otherwise(pl.coalesce("_conn_exp", "_conn_reg", "_reg1")).alias("export_mw"),
        pl.coalesce("_dc", "_te").alias("connection_date"),
        pl.col("_src").str.to_lowercase().str.starts_with("stored").fill_null(False).alias("_is_storage"),
    ).with_columns(
        pl.when(pl.col("_is_storage")).then(pl.col("_reg1")).otherwise(None).alias("storage_mw"),
        pl.concat_str([pl.col("_src"), pl.col("Energy Conversion Technology 1")
                       .pipe(lambda c: pl.when(c.str.to_lowercase().str.contains("data not")).then(None).otherwise(c))],
                      separator=" / ", ignore_nulls=True).alias("technology"),
    )
    keys = [
        f"{OP}:ECR:{m}" if m and m.isdigit() else
        f"{OP}:ECR:H{_hash(*r)}"
        for m, r in zip(df["_mpan"], df.select("_cust", "_site", "_pc", "gsp_name", "bsp_name",
                                                 "primary_name", "_reg1", "status").rows())
    ]
    return df.with_columns(
        pl.Series("project_uid", _unique(keys)),
        pl.lit(OP).alias("operator"),
        pl.coalesce("_site", "_cust").alias("site_name"),
        pl.coalesce("primary_name", "bsp_name", "gsp_name").alias("connection_point"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------------------------------------------------------------- connection queue (per GSP)

QUEUE_STATUS = {"accepted not yet connected": "accepted", "recently connected": "connected"}


def build_queue(files: list[Path], as_of: str | None, source_id: str = "nged_queue") -> pl.DataFrame:
    frames = [_read_csv(f).with_columns(pl.lit(f.name).alias("_file")) for f in files]
    df = pl.concat(frames, how="diagonal_relaxed")
    df = df.with_columns(
        _txt("GSP", "gsp_name"),
        _txt("Bus Name", "connection_point"),
        _txt("Fuel type", "technology"),
        C.num_expr("Machine Export Capacity (MW)").alias("export_mw"),
        C.num_expr("Machine Import Capacity (MW)").alias("import_mw"),
        pl.col("Status").str.strip_chars().str.to_lowercase().replace_strict(QUEUE_STATUS, default="other").alias("status"),
    ).with_columns(
        pl.when(pl.col("technology").str.to_lowercase().is_in(["bess", "storage", "battery"]))
        .then(pl.col("export_mw")).otherwise(None).alias("storage_mw"),
    )
    keys = [f"{OP}:Q:{_slug(g)}:{s}:{a}:{m}" for g, s, a, m in
            zip(df["gsp_name"], df["Site ID"], df["Application ID"], df["Machine ID"])]
    return df.with_columns(
        pl.Series("project_uid", _unique(keys)),
        pl.lit(OP).alias("operator"),
        pl.concat_str([pl.lit("Site "), pl.col("Site ID")], ignore_nulls=True).alias("site_name"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------------------------------------------------------------- connections reform register

def build_reform(path: Path, as_of: str | None, source_id: str = "nged_connections_reform_register") -> pl.DataFrame:
    """CSV has 3 title lines before the header and ~1M trailing blank rows: keep rows with a Licence Area."""
    df = _read_csv(path, skip_rows=3)
    df = df.filter(pl.col("Licence Area").is_not_null() & (pl.col("Licence Area").str.strip_chars() != ""))
    df = df.with_columns(
        _txt("GSP", "gsp_name"),
        _txt("Site Name", "site_name"),
        _txt("Technology", "technology"),
        C.num_expr("Export Capacity (MW)").alias("export_mw"),
        pl.coalesce(_iso_date("Firm Connection Date"), _iso_date("Non-Firm Connection Date")).alias("connection_date"),
    ).with_columns(
        pl.col("gsp_name").alias("connection_point"),
        pl.lit("other").alias("status"),  # CMP435/CMP446 reform cohort; no connection status published
        pl.when(pl.col("technology").str.to_lowercase().str.contains("batter|storage"))
        .then(pl.col("export_mw")).otherwise(None).alias("storage_mw"),
    )
    keys = [f"{OP}:CR:H{_hash(*r)}" for r in df.select("Licence Area", "gsp_name", "Company Name", "site_name",
                                                         "technology", "export_mw", "G2TWQ Status").rows()]
    return df.with_columns(
        pl.Series("project_uid", _unique(keys)),
        pl.lit(OP).alias("operator"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------------------------------------------------------------- gated sources (registered NGED files)

NCM_BASIS = "NGED NCM: headroom MW = connected (pre-queue); RAG = contracted (post-queue)"
PL_KINDS = {"Super Grid Substation": "GSP", "Bulk Supply Point": "BSP", "Primary Substation": "PRIMARY"}
LV_MAX_KM = 2.0  # coordinate candidates for one substation further apart than this are rejected


def _num_key(c: str) -> pl.Expr:
    """Substation numbers are ints in some files and '926423.0' floats in others: normalise to digits."""
    return _blank_to_null(c).str.replace(r"\.0+$", "").alias("_num")


def _json_get(s: str | None, key: str) -> str | None:
    return json.loads(s).get(key) if s else None


def read_ncm(files: list[Path]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Network capacity map -> (GSP/BSP/PRIMARY rows, SECONDARY rows), both with parsed headroom/coords.

    One CSV (substations.csv), type = BSP | Primary | Secondary. Headroom columns are MW and named
    "Connected" (pre-queue) and "Contracted" (after accepted queue); the *RAG columns are text.
    """
    frames = [_read_csv(f) for f in files if f.suffix.lower() == ".csv"]
    if not frames:
        return pl.DataFrame(), pl.DataFrame()
    df = pl.concat(frames, how="diagonal_relaxed")
    df = df.with_columns(
        _num_key("substationNumber"),
        _txt("substationID", "ncm_id"),
        _txt("name", "_name"),
        pl.col("type").map_elements(C.kind_from, return_dtype=pl.Utf8).alias("kind"),
        C.num_expr("demandConnectedHeadroomMW").alias("d_mw"),
        C.num_expr("generationConnectedHeadroomMW").alias("g_mw"),
        pl.col("demandContractedRAG").map_elements(C.rag, return_dtype=pl.Utf8).alias("d_rag"),
        pl.col("generationContractedRAG").map_elements(C.rag, return_dtype=pl.Utf8).alias("g_rag"),
        pl.col("demandConnectedRAG").map_elements(C.rag, return_dtype=pl.Utf8).alias("d_rag_conn"),
        _txt("primary", "_primary"), _txt("BSP", "_bsp"), _txt("GSP", "_gsp"),
        pl.col("area").str.to_lowercase().replace_strict(LICENCE_AREAS, default=None).alias("licence_area"),
    ).filter(pl.col("_num").is_not_null())
    df = C.add_coords(df, lat="latitude", lon="longitude")
    df = df.with_columns(pl.col("_name").map_elements(_slug, return_dtype=pl.Utf8).alias("_slug"))
    pb = df.filter(pl.col("kind").is_in(["GSP", "BSP", "PRIMARY"]))
    sec = df.filter(pl.col("kind") == "SECONDARY")
    return pb, sec


def merge_ncm_into_heatmap(asset: pl.DataFrame, cap: pl.DataFrame, ncm_pb: pl.DataFrame, as_of: str | None,
                           source_id: str = "nged_network_capacity_map") -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """Fold NCM GSP/BSP/PRIMARY rows into the heatmap asset/capacity tables.

    An NCM row duplicates a heatmap row when (substation number, kind) agree (name breaks ties where a
    BSP and BSP/Primary share one number); failing that, when kind and name agree (the number differs
    by a typo in a couple of rows). The heatmap row is kept (more precise headroom, voltages, parentage),
    its nulls are filled from NCM and the NCM ids go into native_ids. Unmatched NCM rows become new rows.
    """
    stats = {"ncm_pb": ncm_pb.height, "ncm_pb_matched": 0, "ncm_pb_new": 0}
    asset, cap = _asset_cols(asset), _cap_cols(cap)
    if ncm_pb.height == 0:
        return asset, cap, stats
    asset = asset.with_columns(
        pl.col("native_ids").map_elements(lambda s: _json_get(s, "mrid"), return_dtype=pl.Utf8).alias("_num"),
        pl.col("name").map_elements(_slug, return_dtype=pl.Utf8).alias("_slug"),
    )
    by_nk: dict[tuple, list[str]] = {}
    by_ks: dict[tuple, list[str]] = {}
    for uid, n, k, s in zip(asset["asset_uid"], asset["_num"], asset["kind"], asset["_slug"], strict=True):
        by_nk.setdefault((n, k), []).append(uid)
        by_ks.setdefault((k, s), []).append(uid)
    taken: set[str] = set()
    match: dict[int, str] = {}
    rows = list(zip(ncm_pb["_num"], ncm_pb["kind"], ncm_pb["_slug"], strict=True))
    for pass_ in (1, 2):
        for i, (n, k, s) in enumerate(rows):
            if i in match:
                continue
            cands = by_nk.get((n, k), []) if pass_ == 1 else by_ks.get((k, s), [])
            cands = [u for u in cands if u not in taken]
            if pass_ == 1 and len(cands) > 1:
                cands = [u for u in cands if u.endswith(f":{s}")] or cands[:1]
            if len(cands) == 1 or (pass_ == 1 and cands):
                match[i] = cands[0]
                taken.add(cands[0])
    stats["ncm_pb_matched"] = len(match)
    ncm_pb = ncm_pb.with_row_index("_i")
    m = pl.DataFrame({"_i": list(match), "asset_uid": list(match.values())}, schema={"_i": pl.UInt32, "asset_uid": pl.Utf8})
    mj = m.join(ncm_pb, on="_i", how="left")

    ncm_cols = ["asset_uid", "ncm_id", "_num", "lat", "lon", "coord_quality", "_gsp", "_bsp", "_primary",
                "d_mw", "g_mw", "d_rag", "g_rag"]
    mj = mj.select([pl.col(c).alias(f"n_{c}" if c != "asset_uid" else c) for c in ncm_cols])
    a = asset.join(mj, on="asset_uid", how="left")
    a = a.with_columns(
        pl.when(pl.col("lat").is_null()).then(pl.col("n_lat")).otherwise(pl.col("lat")).alias("_lat"),
        pl.when(pl.col("lat").is_null()).then(pl.col("n_lon")).otherwise(pl.col("lon")).alias("_lon"),
        pl.when(pl.col("lat").is_null() & pl.col("n_lat").is_not_null()).then(pl.col("n_coord_quality"))
        .otherwise(pl.col("coord_quality")).alias("_cq"),
    ).with_columns(
        pl.col("_lat").alias("lat"), pl.col("_lon").alias("lon"), pl.col("_cq").alias("coord_quality"),
        pl.coalesce("upstream_gsp", "n__gsp").alias("upstream_gsp"),
        pl.coalesce("upstream_bsp", "n__bsp").alias("upstream_bsp"),
        pl.coalesce("upstream_primary", "n__primary").alias("upstream_primary"),
        pl.struct("native_ids", "n_ncm_id", "n__num", "_num").map_elements(
            lambda r: _add_ids(r["native_ids"], ncm_id=r["n_ncm_id"],
                               ncm_number=r["n__num"] if r["n__num"] != r["_num"] else None),
            return_dtype=pl.Utf8).alias("native_ids"),
    )
    c2 = cap.join(a.select("asset_uid", "n_d_mw", "n_g_mw", "n_d_rag", "n_g_rag", "n_ncm_id"), on="asset_uid", how="left")
    c2 = c2.with_columns(
        pl.when(pl.col("n_d_mw").is_not_null()).then(pl.col("n_d_mw")).otherwise(pl.col("demand_headroom_mw"))
        .alias("demand_headroom_mw"),
        pl.coalesce("gen_headroom_mw", "n_g_mw").alias("gen_headroom_mw"),
        pl.coalesce("gen_rag_native", "n_g_rag").alias("gen_rag_native"),
        pl.coalesce("demand_rag_native", "n_d_rag").alias("demand_rag_native"),
        pl.when(pl.col("n_ncm_id").is_not_null()).then(pl.lit(f"{HEADROOM_BASIS}; {NCM_BASIS}"))
        .otherwise(pl.col("basis")).alias("basis"),
    ).drop([c for c in c2.columns if c.startswith("n_")])
    a = a.drop([c for c in a.columns if c.startswith("n_") or c in ("_num", "_slug", "_lat", "_lon", "_cq")])

    # NCM rows with no heatmap twin: new asset + capacity rows
    new = ncm_pb.filter(~pl.col("_i").is_in(list(match))).drop("_i")
    stats["ncm_pb_new"] = new.height
    if new.height:
        existing = set(a["asset_uid"])
        uids = [f"{OP}:{n}:{s}" if f"{OP}:{n}" in existing else f"{OP}:{n}" for n, s in zip(new["_num"], new["_slug"], strict=True)]
        new = new.with_columns(pl.Series("asset_uid", _unique(uids)))
        na = new.with_columns(
            pl.lit(OP).alias("operator"), pl.col("_name").alias("name"),
            pl.col("_gsp").alias("upstream_gsp"), pl.col("_bsp").alias("upstream_bsp"),
            pl.col("_primary").alias("upstream_primary"),
            pl.struct("_num", "ncm_id").map_elements(lambda r: C.ids_json(mrid=r["_num"], ncm_id=r["ncm_id"]),
                                                     return_dtype=pl.Utf8).alias("native_ids"),
            pl.lit(source_id).alias("source_id"), pl.lit(as_of).alias("as_of"))
        nc = new.with_columns(
            pl.col("d_mw").alias("demand_headroom_mw"), pl.col("g_mw").alias("gen_headroom_mw"),
            pl.col("g_rag").alias("gen_rag_native"), pl.col("d_rag").alias("demand_rag_native"),
            pl.lit(NCM_BASIS).alias("basis"), pl.lit(source_id).alias("source_id"), pl.lit(as_of).alias("as_of"))
        a = pl.concat([a, _asset_cols(na)])
        c2 = pl.concat([c2, _cap_cols(nc)])
    return a, c2, stats


def _add_ids(native_ids: str | None, **extra) -> str:
    d = json.loads(native_ids) if native_ids else {}
    d.update({k: str(v).strip() for k, v in extra.items() if v not in (None, "") and str(v).strip()})
    return json.dumps(d, sort_keys=True)


def build_primary_locations(path: Path, asset: pl.DataFrame, as_of: str | None,
                            source_id: str = "nged_primary_locations") -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """GSP (super grid) / BSP / primary points with E/N + lat/lon.

    Existing assets (same number+kind, else same kind+name) only gain a `primary_locations_number` id
    and any missing coordinates; every other GSP/BSP/PRIMARY point becomes a new asset (no capacity
    data). Switching stations are skipped (not a canonical kind). Returns (asset, new_rows_only, stats).
    """
    asset = _asset_cols(asset)
    df = _read_csv(path)
    df = df.with_columns(
        _num_key("Substation Number"), _txt("Substation Name", "_name"),
        pl.col("Substation Type").str.strip_chars().replace_strict(PL_KINDS, default=None).alias("kind"),
    ).filter(pl.col("kind").is_not_null() & pl.col("_num").is_not_null())
    df = C.add_coords(df, lat="Latitude", lon="Longitude", e="Easting", n="Northing")
    df = df.with_columns(pl.col("_name").map_elements(_slug, return_dtype=pl.Utf8).alias("_slug"))
    nums = asset["native_ids"].map_elements(lambda s: _json_get(s, "mrid"), return_dtype=pl.Utf8)
    slugs = asset["name"].map_elements(_slug, return_dtype=pl.Utf8)
    by_nk, by_ks = {}, {}
    for uid, n, k, s in zip(asset["asset_uid"], nums, asset["kind"], slugs, strict=True):
        by_nk.setdefault((n, k), []).append(uid)
        by_ks.setdefault((k, s), []).append(uid)
    hit: dict[str, tuple] = {}
    new_rows = []
    for r in df.iter_rows(named=True):
        c = by_nk.get((r["_num"], r["kind"])) or by_ks.get((r["kind"], r["_slug"]))
        if c:
            hit[c[0]] = (r["_num"], r["lat"], r["lon"], r["coord_quality"])
        else:
            new_rows.append(r)
    stats = {"pl_rows": df.height, "pl_matched_existing": len(hit), "pl_new": len(new_rows),
             "pl_coords_filled": sum(1 for u, v in hit.items() if v[1] is not None and
                                     asset.filter(pl.col("asset_uid") == u)["lat"][0] is None)}
    if hit:
        h = pl.DataFrame({"asset_uid": list(hit), "_pn": [v[0] for v in hit.values()],
                          "_la": [v[1] for v in hit.values()], "_lo": [v[2] for v in hit.values()],
                          "_cq": [v[3] for v in hit.values()]})
        asset = asset.join(h, on="asset_uid", how="left").with_columns(
            pl.when(pl.col("lat").is_null() & pl.col("_la").is_not_null()).then(pl.col("_cq")).otherwise(pl.col("coord_quality")).alias("coord_quality"),
            pl.coalesce("lat", "_la").alias("lat"), pl.coalesce("lon", "_lo").alias("lon"),
            pl.struct("native_ids", "_pn").map_elements(
                lambda r: _add_ids(r["native_ids"], primary_locations_number=r["_pn"]), return_dtype=pl.Utf8).alias("native_ids"),
        ).drop("_pn", "_la", "_lo", "_cq")
    if not new_rows:
        return asset, asset.clear(), stats
    nw = pl.DataFrame(new_rows, schema=df.schema)
    existing = set(asset["asset_uid"])
    uids = [f"{OP}:{n}:{s}" if f"{OP}:{n}" in existing else f"{OP}:{n}" for n, s in zip(nw["_num"], nw["_slug"], strict=True)]
    nw = nw.with_columns(pl.Series("asset_uid", _unique(uids))).with_columns(
        pl.lit(OP).alias("operator"), pl.col("_name").alias("name"),
        pl.struct("_num", "Substation Type").map_elements(
            lambda r: C.ids_json(mrid=r["_num"], primary_locations_number=r["_num"], pl_type=r["Substation Type"]),
            return_dtype=pl.Utf8).alias("native_ids"),
        pl.lit(source_id).alias("source_id"), pl.lit(as_of).alias("as_of"))
    return asset, _asset_cols(nw), stats


_GRID_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # no I


def _gridref_en(ref: str | None) -> tuple[str | None, str | None]:
    """OS grid reference ('SK968140676270': 2 letters + even digits) -> (easting, northing) in metres."""
    m = re.fullmatch(r"([A-Za-z]{2})(\d{2,14})", (ref or "").replace(" ", ""))
    if not m or len(m.group(2)) % 2:
        return None, None
    a, b = (_GRID_LETTERS.find(ch) for ch in m.group(1).upper())
    if a < 0 or b < 0:
        return None, None
    e100 = ((a - 2) % 5) * 5 + (b % 5)
    n100 = 19 - (a // 5) * 5 - (b // 5)
    d = m.group(2)
    h = len(d) // 2
    scale = 10 ** (5 - h)
    return str(e100 * 100000 + int(d[:h]) * scale), str(n100 * 100000 + int(d[h:]) * scale)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return (((lat1 - lat2) * 111.2) ** 2 + ((lon1 - lon2) * 111.2 * math.cos(math.radians(lat1))) ** 2) ** 0.5


def _lv_coords(df: pl.DataFrame) -> pl.DataFrame:
    """Pick lat/lon per substation from up to four candidates, rejecting contradictions.

    Candidates, in priority order: locations-file BNG easting/northing (converted with add_coords; the
    file's own lat/lon agree with it) -> locations-file lat/lon -> substations-file lat/lon -> substations-file
    OS grid reference (converted to BNG) -> NCM lat/lon.
    The locations file's lat/lon is only used when its E/N is unusable. Candidates within LV_MAX_KM of each other form a cluster; the largest cluster wins if it strictly
    outnumbers the candidates that disagree with it, else the point is rejected (BNG typos of ~100 km exist).
    """
    names = ("a", "b", "c", "e", "d")
    en = [_gridref_en(g) for g in df["_gridref"]]
    df = df.with_columns(pl.Series("_ge", [x[0] for x in en], pl.Utf8), pl.Series("_gn", [x[1] for x in en], pl.Utf8))
    cols = {}
    for name, kw in zip(names, ({"e": "_e", "n": "_n"}, {"lat": "_llat", "lon": "_llon"},
                                {"lat": "_slat", "lon": "_slon"}, {"e": "_ge", "n": "_gn"},
                                {"lat": "_nlat", "lon": "_nlon"}), strict=True):
        t = C.add_coords(df.select(list(kw.values())), **kw)
        cols[name] = list(zip(t["lat"], t["lon"], t["coord_quality"], strict=True))
    lat, lon, q = [], [], []
    for i in range(df.height):
        # the locations file's lat/lon is derived from its E/N: not an independent vote when E/N is usable
        cand = [(k, *cols[k][i]) for k in names
                if cols[k][i][0] is not None and not (k == "b" and cols["a"][i][0] is not None)]
        if not cand:
            qs = [cols[k][i][2] for k in names if cols[k][i][2] != "missing"]
            lat.append(None); lon.append(None); q.append(qs[0] if qs else "missing")
            continue
        best = None
        for k, la, lo, cq in cand:
            agree = sum(1 for k2, la2, lo2, _ in cand if k2 != k and _km(la, lo, la2, lo2) <= LV_MAX_KM)
            dis = len(cand) - 1 - agree
            if best is None or agree + 1 > best[0]:
                best = (agree + 1, dis, la, lo, cq)
        if best[0] > best[1]:
            lat.append(best[2]); lon.append(best[3]); q.append(best[4])
        else:
            lat.append(None); lon.append(None); q.append("rejected")
    return df.with_columns(pl.Series("lat", lat, pl.Float64), pl.Series("lon", lon, pl.Float64),
                           pl.Series("coord_quality", q, pl.Utf8))


def build_lv_sites(subs: Path, locs: Path | None, ncm_sec: pl.DataFrame | None, as_of: str | None,
                   source_id: str = "nged_distribution_substations") -> tuple[pl.DataFrame, dict]:
    """lv_site from distribution-substations, with coordinates joined from the locations file.

    Join key: `Substation Number` in both files (ints in one, '926423.0' in the other; normalised to digits).
    The substations file also has 1,088 per-primary "Aggregated Distribution Substation Data" rows with no
    number: they are not sites and are dropped. Exact duplicate rows (1,095) are collapsed. NCM secondary
    rows join on the same number (headroom MW + RAG go to native_ids; lv_site has no headroom column);
    NCM secondaries absent from the substations file are appended as extra rows.
    """
    sub = _read_csv(subs)
    stats = {"sub_rows": sub.height}
    sub = sub.with_columns(_num_key("Substation Number")).filter(pl.col("_num").is_not_null())
    stats["sub_aggregate_rows_dropped"] = stats["sub_rows"] - sub.height
    sub = sub.unique("_num", keep="first", maintain_order=True)
    stats["sub_sites"] = sub.height
    base = sub.select(
        "_num",
        _txt("Substation Name", "name"), _txt("Primary Name", "upstream_primary"),
        _txt("Primary Number", "_primary_no"), _txt("Bulk Supply Point Number", "_bsp_no"),
        _txt("Grid Supply Point Number", "_gsp_no"), _txt("Substation Type", "_type"),
        _txt("Licence Area", "_area"), _txt("HV Feeder", "_feeder"), _txt("Grid Reference", "_gridref"),
        C.num_expr("Substation Rating").alias("_rating"),
        C.num_expr("Day Max Demand").alias("_day"), C.num_expr("Night Max Demand").alias("_night"),
        C.num_expr("Customers").alias("_cust"),
        _txt("Latitude", "_slat"), _txt("Longitude", "_slon"),
    ).with_columns(pl.lit(source_id).alias("source_id"))
    if locs is not None:
        loc = _read_csv(locs).with_columns(_num_key("Substation Number")).filter(pl.col("_num").is_not_null())
        loc = loc.unique("_num", keep="first", maintain_order=True).select(
            "_num", _txt("Easting", "_e"), _txt("Northing", "_n"), _txt("Latitude", "_llat"), _txt("Longitude", "_llon"))
        base = base.join(loc, on="_num", how="left")
        stats["loc_sites"] = loc.height
        stats["sub_joined_to_loc"] = base.filter(pl.col("_llat").is_not_null() | pl.col("_e").is_not_null()).height
    for c in ("_e", "_n", "_llat", "_llon"):
        if c not in base.columns:
            base = base.with_columns(pl.lit(None, pl.Utf8).alias(c))
    if ncm_sec is not None and ncm_sec.height:
        ns = ncm_sec.unique("_num", keep="first", maintain_order=True)
        only = ns.filter(~pl.col("_num").is_in(base["_num"].implode()))
        extra = only.select("_num", pl.col("_name").alias("name"), pl.col("_primary").alias("upstream_primary"),
                            pl.col("description").alias("_type"), pl.lit("ncm").alias("_ncm_only"))
        stats["ncm_secondary"] = ns.height
        stats["ncm_secondary_only_rows"] = extra.height
        j = ns.select("_num", pl.col("ncm_id").alias("_ncm_id"), pl.col("d_mw").alias("_ncm_d_mw"),
                      pl.col("d_rag_conn").alias("_ncm_d_rag"), pl.col("lat").alias("_nlat"), pl.col("lon").alias("_nlon"))
        base = pl.concat([base, extra], how="diagonal_relaxed").join(j, on="_num", how="left")
        stats["ncm_joined_to_sub"] = base.filter(pl.col("_ncm_id").is_not_null() & pl.col("_ncm_only").is_null()).height
        base = base.with_columns(
            pl.when(pl.col("_ncm_only").is_not_null()).then(pl.lit("nged_network_capacity_map")).otherwise(pl.col("source_id")).alias("source_id"))
    for c in ("_nlat", "_nlon", "_ncm_id", "_ncm_d_mw", "_ncm_d_rag"):
        if c not in base.columns:
            base = base.with_columns(pl.lit(None, pl.Utf8 if c != "_ncm_d_mw" else pl.Float64).alias(c))
    base = base.with_columns(*[pl.col(c).cast(pl.Utf8) for c in ("_nlat", "_nlon")])
    base = _lv_coords(base)
    out = base.with_columns(
        pl.format("{}:{}", pl.lit(OP), pl.col("_num")).alias("asset_uid"),
        pl.lit(OP).alias("operator"),
        # a 0 kVA rating means "not recorded", not a real transformer
        pl.when(pl.col("_rating") > 0).then(pl.col("_rating")).alias("transformer_kva"),
        # utilisation_pct stays null: NGED publishes day/night max demand (kVA), not a utilisation figure
        pl.col("_cust").round(0).cast(pl.Int64, strict=False).alias("customers"),
        pl.struct("_num", "_primary_no", "_bsp_no", "_gsp_no", "_type", "_area", "_feeder", "_gridref",
                  "_ncm_id", "_ncm_d_mw", "_ncm_d_rag", "_day", "_night").map_elements(
            lambda r: C.ids_json(number=r["_num"], primary_number=_int_str(r["_primary_no"]), bsp_number=_int_str(r["_bsp_no"]),
                                 gsp_number=_int_str(r["_gsp_no"]), type=r["_type"], licence_area=r["_area"],
                                 hv_feeder=r["_feeder"], grid_ref=r["_gridref"], ncm_id=r["_ncm_id"],
                                 ncm_demand_headroom_mw=r["_ncm_d_mw"], ncm_demand_rag_native=r["_ncm_d_rag"],
                                 day_max_demand_kva=r["_day"], night_max_demand_kva=r["_night"]),
            return_dtype=pl.Utf8).alias("native_ids"),
        pl.lit(as_of).alias("as_of"),
    )
    stats["lv_rows"] = out.height
    return out, stats


def _int_str(v: str | None) -> str | None:
    return None if v is None else re.sub(r"\.0+$", "", v)


# ---------------------------------------------------------------- orchestration

def _files(snap: Path, suffix: str) -> list[Path]:
    return [p for p in C.snapshot_files(snap) if p.suffix.lower() == suffix]


def _asset_cols(df: pl.DataFrame) -> pl.DataFrame:
    from .schema import conform
    return conform(df, "asset")


def _cap_cols(df: pl.DataFrame) -> pl.DataFrame:
    from .schema import conform
    return conform(df, "capacity")


def run() -> dict[str, int]:
    from .schema import QUEUE_PROJECT, conform

    counts: dict[str, int] = {}
    queue: list[pl.DataFrame] = []

    # --- LTDS heatmap (assets + capacity), then NCM GSP/BSP/PRIMARY folded in, then primary locations
    asset = cap = None
    ncm_sec = None
    snap = C.latest_snapshot("nged_ltds_heatmap")
    if snap:
        cim = None
        cs = C.latest_snapshot("nged_ltds_cim_substation_numbers")
        if cs and _files(cs, ".csv"):
            cim = _read_csv(_files(cs, ".csv")[0])
        a, c = build_heatmap(_files(snap, ".json"), C.as_of(C.snapshot_meta(snap)), cim)
        asset, cap = _asset_cols(a), _cap_cols(c)

    snap = C.latest_snapshot("nged_network_capacity_map")
    if snap:
        ncm_pb, ncm_sec = read_ncm(C.snapshot_files(snap))
        if asset is None:
            asset, cap = _asset_cols(pl.DataFrame()), _cap_cols(pl.DataFrame())
        asset, cap, st = merge_ncm_into_heatmap(asset, cap, ncm_pb, C.as_of(C.snapshot_meta(snap)))
        print(f"NGED NCM: {st}")

    snap = C.latest_snapshot("nged_primary_locations")
    if snap and asset is not None and _files(snap, ".csv"):
        asset, new, st = build_primary_locations(_files(snap, ".csv")[0], asset, C.as_of(C.snapshot_meta(snap)))
        if new.height:
            asset = pl.concat([asset, new])
        print(f"NGED primary_locations: {st}")

    if asset is not None:
        asset = conform(asset, "asset")
        assert asset.height > 0
        C.write_table(OP, "asset", asset)
        counts["asset"] = asset.height
        cap = conform(cap, "capacity").unique("asset_uid", keep="first", maintain_order=True)
        C.write_table(OP, "capacity", cap)
        counts["capacity"] = cap.height

    # --- fault levels
    snap = C.latest_snapshot("nged_ltds_fault_levels")
    if snap and _files(snap, ".csv"):
        fl = build_fault_levels(_files(snap, ".csv")[0], C.as_of(C.snapshot_meta(snap)))
        assert fl.height > 0
        C.write_table(OP, "fault_level", fl)
        counts["fault_level"] = fl.height

    # --- queue / ECR
    snap = C.latest_snapshot("nged_ecr")
    if snap and _files(snap, ".csv"):
        queue.append(build_ecr(_files(snap, ".csv")[0], C.as_of(C.snapshot_meta(snap))))
    snap = C.latest_snapshot("nged_queue")
    if snap and _files(snap, ".csv"):
        queue.append(build_queue(_files(snap, ".csv"), C.as_of(C.snapshot_meta(snap))))
    snap = C.latest_snapshot("nged_connections_reform_register")
    if snap and _files(snap, ".csv"):
        queue.append(build_reform(_files(snap, ".csv")[0], C.as_of(C.snapshot_meta(snap))))
    if queue:
        qp = pl.concat([conform(x, "queue_project") for x in queue])
        assert qp.height > 0 and set(qp.columns) == set(QUEUE_PROJECT)
        C.write_table(OP, "queue_project", qp)
        counts["queue_project"] = qp.height

    # --- LV sites: distribution substations + locations (+ NCM secondary fields)
    snap = C.latest_snapshot("nged_distribution_substations")
    if snap and _files(snap, ".csv"):
        loc_snap = C.latest_snapshot("nged_distribution_substation_locations")
        locs = _files(loc_snap, ".csv")[0] if loc_snap and _files(loc_snap, ".csv") else None
        lv, st = build_lv_sites(_files(snap, ".csv")[0], locs, ncm_sec, C.as_of(C.snapshot_meta(snap)))
        lv = add_utilisation(lv)
        st["utilisation"] = int(lv["utilisation_pct"].is_not_null().sum())
        print(f"NGED lv_site: {st}")
        if lv.height:
            C.write_table(OP, "lv_site", lv)
            counts["lv_site"] = lv.height

    return counts
