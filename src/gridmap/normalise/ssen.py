"""SSEN Distribution (SEPD + SHEPD) adapter.

Sources (all verified against real files, see docs/sources/ssen.md):
- ssen_headroom        Headroom Dashboard Data CSV (998 rows)  -> asset, capacity, fault_level (AssetID keyed)
- ssen_ltds_sepd/shepd LTDS capacity heatmap JSON (mRID keyed) -> asset, capacity, fault_level
- ssen_substation_data substation locations CSV (BNG metres)   -> lv_site (secondary, existing only)
- ssen_ecr             ECR Part 1 CSV (50kW-1MW file)          -> queue_project
- ssen_ecr_1mw         ECR Part 1 CSV (>=1MW file, same layout) -> queue_project
- ssen_queue           Connections Reform queue XLSX           -> queue_project (anonymised, GSP level only)

The headroom CSV and the LTDS JSON describe the same sites under different ids (AssetID vs CIM mRID);
both are kept as separate asset rows and the match phase clusters them. Cross-checked on 807 name-matched
sites: gen headroom == generationAvailableCapacity, demand headroom == demandAvailableCapacity,
3ph break rating == breakerBreakingCapacity, 3ph break level == breakerShortCircuitCurrent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl

from . import common as C

OPERATOR = "SSEN"
UNKNOWN_BASIS = "unknown"
_NA = ["N/A", "n/a", "NA", "DATA NOT AVAILABLE", "DATA NOT APPLICABLE", "Not Applicable", ""]


def _txt(col: str) -> pl.Expr:
    """Trimmed string with SSEN's placeholder tokens turned into null."""
    c = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return pl.when(c.is_in(_NA) | c.is_null()).then(None).otherwise(c)


def _clean_cols(df: pl.DataFrame) -> pl.DataFrame:
    return df.rename({c: re.sub(r"\s+", " ", c.replace("﻿", "")).strip() for c in df.columns})


def _read_csv(path: Path) -> pl.DataFrame:
    return _clean_cols(pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy"))


def _area(v: str | None) -> str | None:
    s = (v or "").upper()
    return "SHEPD" if "SHEPD" in s else "SEPD" if "SEPD" in s else None


def _kvs(v: str | None) -> tuple[float | None, float | None]:
    """'400 / 132' -> (400, 132); single value -> (v, None)."""
    parts = [C.parse_num(p) for p in str(v or "").split("/")]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None, None
    return parts[0], parts[1] if len(parts) > 1 else None


def _join_nonnull(*pairs: tuple[str, str | None]) -> str | None:
    out = [f"{k}: {v}" for k, v in pairs if v]
    return "; ".join(out) or None


# ---------------------------------------------------------------- headroom dashboard CSV

def headroom_tables(csv: Path, as_of: str | None) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    df = _read_csv(csv).filter(_txt("AssetID").is_not_null())
    df = C.add_coords(df, lat="Location Latitude", lon="Location Longitude")
    kv = [_kvs(v) for v in df["Voltage (kV)"]]
    uid = pl.concat_str([pl.lit(f"{OPERATOR}:"), pl.col("AssetID").str.strip_chars()])
    df = df.with_columns(
        uid.alias("asset_uid"),
        pl.Series("kv_hv", [k[0] for k in kv], pl.Float64),
        pl.Series("kv_lv", [k[1] for k in kv], pl.Float64),
    )
    ids = [
        C.ids_json(asset_id=r["AssetID"], ltds_cim_nodes=r["LTDS CIM Nodes"] if r["LTDS CIM Nodes"] not in ("N/A",) else None,
                   grid_reference=r["Grid Reference"])
        for r in df.select("AssetID", "LTDS CIM Nodes", "Grid Reference").iter_rows(named=True)
    ]
    asset = df.select(
        "asset_uid",
        pl.lit(OPERATOR).alias("operator"),
        pl.col("Map / License Area").map_elements(_area, return_dtype=pl.Utf8).alias("licence_area"),
        _txt("Substation").alias("name"),
        pl.col("Substation Type").map_elements(C.kind_from, return_dtype=pl.Utf8).alias("kind"),
        "kv_hv", "kv_lv", "lat", "lon", "coord_quality",
        _txt("Upstream GSP").alias("upstream_gsp"),
        _txt("Upstream BSP").alias("upstream_bsp"),
        pl.lit(None, pl.Utf8).alias("upstream_primary"),
        pl.Series("native_ids", ids, pl.Utf8),
        pl.lit("ssen_headroom").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )
    d = pl.col
    cap = df.select(
        "asset_uid",
        C.num_expr("Estimated Generation Headroom (MW)").alias("gen_headroom_mw"),
        C.num_expr("Estimated Demand Headroom (MVA)").alias("demand_headroom_mva"),  # MVA per column header
        C.num_expr("Maximum Observed Gross Demand (MVA)").alias("max_demand_mva"),
        C.num_expr("Connected Generation (MW)").alias("connected_gen_mw"),
        C.num_expr("Contracted Generation (MW)").alias("contracted_gen_mw"),
        d("Substation Generation RAG Status").map_elements(C.rag, return_dtype=pl.Utf8).alias("gen_rag_native"),
        d("Substation Demand RAG Status").map_elements(C.rag, return_dtype=pl.Utf8).alias("demand_rag_native"),
        pl.struct(_txt("Demand Constraint").alias("dc"), _txt("Generation Constraint").alias("gc"))
        .map_elements(lambda s: _join_nonnull(("demand", s["dc"]), ("generation", s["gc"])), return_dtype=pl.Utf8)
        .alias("upstream_constraint"),
        pl.lit(UNKNOWN_BASIS).alias("basis"),  # methodology is a separate PDF, not ingested
        pl.lit("ssen_headroom").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )
    fault = df.select(
        "asset_uid",
        pl.lit(OPERATOR).alias("operator"),
        _txt("LTDS CIM Nodes").alias("ltds_node"),
        _txt("Substation").alias("site_name"),
        pl.lit(None, pl.Float64).alias("kv"),  # busbar the fault level applies to is not stated
        C.num_expr("3-Phase Break Fault Level (kA)").alias("fl_3ph_break_ka"),
        C.num_expr("3-Phase Break Fault Rating (kA)").alias("rating_break_ka"),
        pl.lit("ssen_headroom").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    ).filter(pl.col("fl_3ph_break_ka").is_not_null() | pl.col("rating_break_ka").is_not_null())
    return asset, cap, fault


# ---------------------------------------------------------------- LTDS capacity heatmap JSON

def ltds_tables(path: Path, source_id: str, as_of: str | None) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    doc = json.loads(path.read_text())
    as_of = (doc.get("date") or "")[:10] or as_of
    seen: set[str] = set()
    rows = []
    for s in doc["Substations"]:
        m = s.get("mRID")
        if not m or m in seen:  # one mRID appears twice in SEPD (identical rows)
            continue
        seen.add(m)
        rows.append(s)
    n = len(rows)

    def col(k: str, t=pl.Float64):
        return pl.Series(k, [r.get(k) for r in rows], t, strict=False)

    def na(v):
        return None if v in (None, "", "Not Applicable") else v

    v = [r.get("voltages") or [] for r in rows]
    df = pl.DataFrame({
        "asset_uid": [f"{OPERATOR}:{r['mRID']}" for r in rows],
        "name": [r.get("name") for r in rows],
        "area": [r.get("area") for r in rows],
        "type": [r.get("type") for r in rows],
        "kv_hv": [x[0] if x else None for x in v],
        "kv_lv": [x[1] if len(x) > 1 else None for x in v],
        "lat_s": [None if r.get("latitude") is None else str(r["latitude"]) for r in rows],
        "lon_s": [None if r.get("longitude") is None else str(r["longitude"]) for r in rows],
        "gsp": [na(r.get("GSP")) for r in rows],
        "bsp": [na(r.get("BSP")) for r in rows],
        "mrid": [r["mRID"] for r in rows],
    }, schema_overrides={"kv_hv": pl.Float64, "kv_lv": pl.Float64, "lat_s": pl.Utf8, "lon_s": pl.Utf8})
    df = C.add_coords(df, lat="lat_s", lon="lon_s")
    asset = df.select(
        "asset_uid", pl.lit(OPERATOR).alias("operator"), pl.col("area").alias("licence_area"), "name",
        pl.col("type").map_elements(C.kind_from, return_dtype=pl.Utf8).alias("kind"),
        "kv_hv", "kv_lv", "lat", "lon", "coord_quality",
        pl.col("gsp").alias("upstream_gsp"), pl.col("bsp").alias("upstream_bsp"),
        pl.lit(None, pl.Utf8).alias("upstream_primary"),
        pl.col("mrid").map_elements(lambda m: C.ids_json(mrid=m, ltds_node=m), return_dtype=pl.Utf8).alias("native_ids"),
        pl.lit(source_id).alias("source_id"), pl.lit(as_of).alias("as_of"),
    )
    uc = [_join_nonnull(("demand", r.get("demandConstraintLimitingFactor")), ("generation", r.get("generationConstraintLimitingFactor")))
          for r in rows]
    cap = pl.DataFrame([
        df["asset_uid"],
        col("generationAvailableCapacity").alias("gen_headroom_mw"),
        col("demandAvailableCapacity").alias("demand_headroom_mva"),
        col("demandFirmCapacity").alias("firm_capacity_mva"),
        col("demandMaximum").alias("max_demand_mva"),
        pl.Series("gen_rag_native", [C.rag(r.get("generationConstraint")) for r in rows], pl.Utf8),
        pl.Series("demand_rag_native", [C.rag(r.get("demandConstraint")) for r in rows], pl.Utf8),
        pl.Series("upstream_constraint", uc, pl.Utf8),
        pl.Series("basis", [UNKNOWN_BASIS] * n, pl.Utf8),
        pl.Series("source_id", [source_id] * n, pl.Utf8),
        pl.Series("as_of", [as_of] * n, pl.Utf8),
    ])
    fault = pl.DataFrame([
        df["asset_uid"],
        pl.Series("operator", [OPERATOR] * n, pl.Utf8),
        df["mrid"].alias("ltds_node"),
        df["name"].alias("site_name"),
        pl.Series("kv", [None] * n, pl.Float64),
        col("breakerShortCircuitCurrent").alias("fl_3ph_break_ka"),
        col("breakerBreakingCapacity").alias("rating_break_ka"),
        pl.Series("source_id", [source_id] * n, pl.Utf8),
        pl.Series("as_of", [as_of] * n, pl.Utf8),
    ]).filter(pl.col("fl_3ph_break_ka").is_not_null() | pl.col("rating_break_ka").is_not_null())
    return asset, cap, fault


# ---------------------------------------------------------------- secondary substations

_NOT_SECONDARY_TYPES = ["Primary", "Switching Station", "Site Only", "Consumers", "Sealing End Compound",
                        "Grid/Bulk Supply pt", "Supergrid"]


def lv_sites(csv: Path, as_of: str | None) -> pl.DataFrame:
    df = _read_csv(csv)
    df = df.filter(
        (pl.col("status") == "Existing")
        & pl.col("class").str.contains(r"(?i)kV\s*/\s*LV$")
        & ~pl.col("type").is_in(_NOT_SECONDARY_TYPES)
    ).unique(maintain_order=True)
    df = C.add_coords(df, e="location_x_m", n="location_y_m")
    df = df.with_columns(
        pl.col("class").str.extract(r"(?i)^\s*([\d.]+)\s*kV", 1).cast(pl.Float64, strict=False).alias("kv_hv"),
        pl.concat_str([
            pl.col("owner_name"), pl.col("number").fill_null("NONUM"),
            pl.col("location_x_m").cast(pl.Float64, strict=False).round(0).cast(pl.Int64).cast(pl.Utf8).fill_null("x"),
            pl.col("location_y_m").cast(pl.Float64, strict=False).round(0).cast(pl.Int64).cast(pl.Utf8).fill_null("y"),
        ], separator="|").alias("_key"),
    )
    # `number` is only unique within an operating area/depot and 3% are null, so the site key adds BNG metres.
    df = df.unique(subset="_key", keep="first", maintain_order=True)
    ids = [C.ids_json(number=r["number"], type=r["type"], voltage_class=r["class"], operating_area=r["operating_area"],
                      locality=r["locality"], data_confidence=r["data_confidence"])
           for r in df.select("number", "type", "class", "operating_area", "locality", "data_confidence").iter_rows(named=True)]
    return df.select(
        pl.concat_str([pl.lit(f"{OPERATOR}:"), pl.col("_key")]).alias("asset_uid"),
        pl.lit(OPERATOR).alias("operator"),
        pl.col("number").alias("name"),
        "lat", "lon", "coord_quality", "kv_hv",
        pl.lit(None, pl.Float64).alias("transformer_kva"),
        pl.lit(None, pl.Float64).alias("utilisation_pct"),
        pl.lit(None, pl.Int64).alias("customers"),
        pl.lit(None, pl.Utf8).alias("upstream_primary"),
        pl.Series("native_ids", ids, pl.Utf8),
        pl.lit("ssen_substation_data").alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


# ---------------------------------------------------------------- ECR + queue

def _date(col: str) -> pl.Expr:
    return _txt(col).str.to_date("%d/%m/%Y", strict=False).dt.strftime("%Y-%m-%d")


def ecr_projects(csv: Path, as_of: str | None, source_id: str = "ssen_ecr") -> pl.DataFrame:
    df = _read_csv(csv)
    df = df.filter(_txt("Unique ID").is_not_null())
    df = C.add_coords(df, lat="Latitude", lon="Longitude",
                      e="Location (X-coordinate): Eastings (where data is held)",
                      n="Location (y-coordinate): Northings (where data is held)")
    status = pl.col("Connection Status").str.strip_chars().str.to_uppercase()
    return df.select(
        pl.concat_str([pl.lit(f"{OPERATOR}:"), pl.col("Unique ID").str.strip_chars()]).alias("project_uid"),
        pl.lit(OPERATOR).alias("operator"),
        _txt("Customer Site").alias("site_name"),
        _txt("Primary").alias("connection_point"),
        _txt("Primary").alias("primary_name"),
        _txt("Bulk Supply Point").alias("bsp_name"),
        _txt("Grid Supply Point").alias("gsp_name"),
        "lat", "lon",
        pl.when(status == "CONNECTED").then(pl.lit("connected"))
        .when(status.str.starts_with("ACCEPTED")).then(pl.lit("accepted"))
        .otherwise(pl.lit("other")).alias("status"),
        pl.coalesce(_txt("Energy Conversion Technology 1"), _txt("Energy Source 1")).alias("technology"),
        C.num_expr("Maximum Export Capacity (MW)").alias("export_mw"),
        C.num_expr("Maximum Import Capacity (MW)").alias("import_mw"),
        pl.lit(None, pl.Float64).alias("storage_mw"),  # only MWh published, and always 'DATA NOT AVAILABLE'
        C.num_expr("Point of Connection (POC) Voltage (kV)").alias("kv"),
        _date("Date Connected").alias("connection_date"),
        pl.lit(source_id).alias("source_id"),
        pl.lit(as_of).alias("as_of"),
    )


def queue_projects(xlsx: Path, as_of: str | None) -> pl.DataFrame:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    out = []
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        hdr = [re.sub(r"\s+", " ", str(h or "")).strip().lower() for h in rows[0]]

        def ix(prefix: str, hdr: list[str] = hdr) -> int:
            return next(i for i, h in enumerate(hdr) if h.startswith(prefix))

        i_net, i_tech, i_name, i_gsp, i_tec, i_stat = (ix("network"), ix("technology"), ix("project name"),
                                                        ix("gsp"), ix("transmission entry capacity"), ix("status"))
        for r in rows[1:]:
            if not r[i_name]:
                continue
            net = str(r[i_net] or ws.title).strip()
            stat = str(r[i_stat] or "").strip().lower()
            out.append({
                "project_uid": f"{OPERATOR}:QUEUE:{net}:{str(r[i_name]).strip()}",
                "site_name": str(r[i_name]).strip(),  # anonymised by SSEN (e.g. SOLAR_PROJECT_3)
                "connection_point": str(r[i_gsp]).strip() if r[i_gsp] else None,
                "gsp_name": str(r[i_gsp]).strip() if r[i_gsp] else None,
                "status": "connected" if stat == "connected" else "other",  # 'Offer' col is 'Gate 2' for all
                "technology": str(r[i_tech]).strip() if r[i_tech] else None,
                "export_mw": C.parse_num(r[i_tec]),  # Transmission Entry Capacity (MW)
            })
    df = pl.DataFrame(out, schema={"project_uid": pl.Utf8, "site_name": pl.Utf8, "connection_point": pl.Utf8,
                                   "gsp_name": pl.Utf8, "status": pl.Utf8, "technology": pl.Utf8, "export_mw": pl.Float64})
    return df.with_columns(pl.lit(OPERATOR).alias("operator"), pl.lit("ssen_queue").alias("source_id"),
                           pl.lit(as_of).alias("as_of"))


# ---------------------------------------------------------------- run

def _snap_file(source_id: str, suffix: str) -> tuple[Path, str | None] | None:
    snap = C.latest_snapshot(source_id)
    if snap is None:
        print(f"SSEN: no ok snapshot for {source_id}, skipped")
        return None
    files = [f for f in C.snapshot_files(snap) if f.suffix.lower() == suffix]
    return (files[0], C.as_of(C.snapshot_meta(snap))) if files else None


def run() -> dict[str, int]:
    assets, caps, faults = [], [], []
    if (h := _snap_file("ssen_headroom", ".csv")):
        a, c, f = headroom_tables(*h)
        assets.append(a), caps.append(c), faults.append(f)
    for sid in ("ssen_ltds_sepd", "ssen_ltds_shepd"):
        if (h := _snap_file(sid, ".json")):
            a, c, f = ltds_tables(h[0], sid, h[1])
            assets.append(a), caps.append(c), faults.append(f)
    counts: dict[str, int] = {}
    if assets:
        asset = pl.concat(assets, how="diagonal_relaxed")
        asset_uids = set(asset["asset_uid"])
        cap, fl = pl.concat(caps, how="diagonal_relaxed"), pl.concat(faults, how="diagonal_relaxed")
        assert asset.height > 0 and cap["asset_uid"].is_in(list(asset_uids)).all()
        for name, df in (("asset", asset), ("capacity", cap), ("fault_level", fl)):
            C.write_table(OPERATOR, name, df)
            counts[name] = df.height
    if (h := _snap_file("ssen_substation_data", ".csv")):
        df = lv_sites(*h)
        assert df.height > 0
        C.write_table(OPERATOR, "lv_site", df)
        counts["lv_site"] = df.height
    queue = []
    for sid in ("ssen_ecr", "ssen_ecr_1mw"):
        if (h := _snap_file(sid, ".csv")):
            queue.append(ecr_projects(*h, source_id=sid))
    if (h := _snap_file("ssen_queue", ".xlsx")):
        queue.append(queue_projects(*h))
    if queue:
        q = pl.concat(queue, how="diagonal_relaxed")
        C.write_table(OPERATOR, "queue_project", q)
        counts["queue_project"] = q.height
    return counts
