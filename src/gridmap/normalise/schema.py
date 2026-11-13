"""Canonical tables produced by the per-operator adapters.

Adapters are pure functions: raw snapshots in, polars DataFrames out, written to
data/norm/<operator>/<table>.parquet. They never touch DuckDB (single-writer lock);
the loader merges the parquet files into grid.duckdb afterwards.

Semantics are documented in docs/schema.md. Keep the two in sync.
"""

from __future__ import annotations

import polars as pl

KINDS = ("GSP", "BSP", "PRIMARY", "SECONDARY", "TRANSMISSION", "OTHER")
QUEUE_STATUSES = ("connected", "accepted", "offered", "scoping", "other")

# One row per published substation/busbar record (as the operator models it).
ASSET = {
    "asset_uid": pl.Utf8,        # "<OPERATOR>:<native id>" — stable across runs
    "operator": pl.Utf8,         # UKPN | NPG | ENW | SPEN | NGED | SSEN | SSEN-T | ...
    "licence_area": pl.Utf8,     # EPN/LPN/SPN, SPD/SPM, SEPD/SHEPD, WMID/EMID/SWALES/SWEST, NE/YO, ENW
    "name": pl.Utf8,             # as published (trimmed)
    "kind": pl.Utf8,             # one of KINDS
    "kv_hv": pl.Float64,         # upstream / primary-side voltage
    "kv_lv": pl.Float64,         # busbar / secondary-side voltage
    "lat": pl.Float64,           # WGS84, validated inside GB bounds, else null
    "lon": pl.Float64,
    "coord_quality": pl.Utf8,    # native_wgs84 | from_bng | swapped_fixed | missing | rejected
    "upstream_gsp": pl.Utf8,     # names as published (resolved to ids in the match phase)
    "upstream_bsp": pl.Utf8,
    "upstream_primary": pl.Utf8,
    "native_ids": pl.Utf8,       # JSON object of native identifiers: floc, mrid, nrn, ltds_node, enid, number...
    "source_id": pl.Utf8,
    "as_of": pl.Utf8,            # ISO date the operator says the data is for, else portal modified date
}

# Capacity / headroom per asset. Nulls mean "not published", never zero.
CAPACITY = {
    "asset_uid": pl.Utf8,
    "gen_headroom_mw": pl.Float64,
    "demand_headroom_mw": pl.Float64,   # converted from MVA only if the source says pf; else stored in _mva
    "demand_headroom_mva": pl.Float64,
    "firm_capacity_mva": pl.Float64,
    "max_demand_mva": pl.Float64,
    "connected_gen_mw": pl.Float64,
    "contracted_gen_mw": pl.Float64,
    "gen_rag_native": pl.Utf8,           # operator's own RAG: R | A | G | null
    "demand_rag_native": pl.Utf8,
    "upstream_constraint": pl.Utf8,      # short text if published
    "basis": pl.Utf8,                    # how the operator defines headroom (short; see docs/schema.md)
    "source_id": pl.Utf8,
    "as_of": pl.Utf8,
}

# Fault levels per asset/busbar and voltage. kA throughout.
FAULT_LEVEL = {
    "asset_uid": pl.Utf8,        # null if only an LTDS node is known (matched later via ltds_node)
    "operator": pl.Utf8,
    "ltds_node": pl.Utf8,
    "site_name": pl.Utf8,
    "kv": pl.Float64,
    "fl_3ph_make_ka": pl.Float64,   # peak
    "fl_3ph_break_ka": pl.Float64,  # RMS
    "fl_1ph_make_ka": pl.Float64,
    "fl_1ph_break_ka": pl.Float64,
    "rating_make_ka": pl.Float64,
    "rating_break_ka": pl.Float64,
    "source_id": pl.Utf8,
    "as_of": pl.Utf8,
}

# Embedded Capacity Register / queue projects. One row per project per source.
QUEUE_PROJECT = {
    "project_uid": pl.Utf8,      # "<OPERATOR>:<native id or hash>"
    "operator": pl.Utf8,
    "site_name": pl.Utf8,        # customer site / project name
    "connection_point": pl.Utf8, # substation name/id as published (POC / primary / BSP / GSP)
    "primary_name": pl.Utf8,
    "bsp_name": pl.Utf8,
    "gsp_name": pl.Utf8,
    "lat": pl.Float64,
    "lon": pl.Float64,
    "status": pl.Utf8,           # one of QUEUE_STATUSES
    "technology": pl.Utf8,
    "export_mw": pl.Float64,
    "import_mw": pl.Float64,
    "storage_mw": pl.Float64,
    "kv": pl.Float64,
    "connection_date": pl.Utf8,  # ISO date if present
    "source_id": pl.Utf8,
    "as_of": pl.Utf8,
}

# Secondary (11 kV/6.6 kV -> LV) substations. Separate from ASSET: ~10x the rows, thinner data.
LV_SITE = {
    "asset_uid": pl.Utf8,
    "operator": pl.Utf8,
    "name": pl.Utf8,
    "lat": pl.Float64,
    "lon": pl.Float64,
    "coord_quality": pl.Utf8,
    "kv_hv": pl.Float64,
    "transformer_kva": pl.Float64,
    "utilisation_pct": pl.Float64,   # peak load / rating, as published
    "customers": pl.Int64,
    "upstream_primary": pl.Utf8,
    "native_ids": pl.Utf8,
    "source_id": pl.Utf8,
    "as_of": pl.Utf8,
}

TABLES: dict[str, dict[str, pl.DataType]] = {
    "asset": ASSET,
    "capacity": CAPACITY,
    "fault_level": FAULT_LEVEL,
    "queue_project": QUEUE_PROJECT,
    "lv_site": LV_SITE,
}


def conform(df: pl.DataFrame, table: str) -> pl.DataFrame:
    """Select/cast to the canonical columns (missing ones become null) and check enums."""
    schema = TABLES[table]
    out = df.select(
        [
            (pl.col(c) if c in df.columns else pl.lit(None)).cast(t, strict=False).alias(c)
            for c, t in schema.items()
        ]
    )
    if table == "asset":
        bad = out.filter(~pl.col("kind").is_in(KINDS))
        if bad.height:
            raise ValueError(f"asset.kind outside {KINDS}: {bad['kind'].unique().to_list()[:5]}")
    if table == "queue_project":
        bad = out.filter(~pl.col("status").is_in(QUEUE_STATUSES))
        if bad.height:
            raise ValueError(f"queue_project.status outside {QUEUE_STATUSES}")
    key = "project_uid" if table == "queue_project" else "asset_uid"
    if table in ("asset", "lv_site", "queue_project"):
        dups = out.filter(pl.col(key).is_duplicated())
        if dups.height:
            raise ValueError(f"{table}.{key} not unique: {dups[key].head(5).to_list()}")
    return out
