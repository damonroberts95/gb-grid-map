"""SSEN adapter tests: tiny excerpts of the real files (bad-coordinate rows are edited copies). No network."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from gridmap.normalise import ssen
from gridmap.normalise.schema import conform

FX = Path(__file__).parent / "fixtures" / "norm" / "ssen"
ASOF = "2026-03-11"


def test_headroom_mapping_and_nulls():
    asset, cap, fault = ssen.headroom_tables(FX / "headroom.csv", ASOF)
    conform(asset, "asset"), conform(cap, "capacity"), conform(fault, "fault_level")
    assert asset.height == cap.height == 20
    assert set(asset["kind"]) == {"GSP", "BSP", "PRIMARY"}
    assert asset["asset_uid"].str.starts_with("SSEN:").all()
    assert set(asset["licence_area"]) == {"SEPD", "SHEPD"}
    gsp = asset.filter(pl.col("kind") == "GSP")
    assert gsp["upstream_gsp"].is_null().all()  # 'N/A' is not a name
    capg = cap.join(asset.select("asset_uid", "kind"), on="asset_uid")
    gsp_cap = capg.filter(pl.col("kind") == "GSP")
    assert gsp_cap["gen_headroom_mw"].is_null().all() and gsp_cap["demand_headroom_mva"].is_null().all()
    prim = capg.filter(pl.col("kind") == "PRIMARY")
    assert prim["gen_headroom_mw"].is_not_null().all()
    assert (prim["gen_headroom_mw"] == 0).any()  # real zeros survive, not nulled
    assert set(cap["gen_rag_native"].drop_nulls()) <= {"R", "A", "G"}
    assert (cap["basis"] == "unknown").all()
    # kv split
    assert asset.filter(pl.col("kind") == "GSP")["kv_hv"].min() >= 132


def test_headroom_fault_level_nulls_and_links():
    asset, _, fault = ssen.headroom_tables(FX / "headroom.csv", ASOF)
    assert set(fault["asset_uid"]) <= set(asset["asset_uid"])
    assert fault["asset_uid"].is_not_null().all()
    assert fault["fl_3ph_break_ka"].is_not_null().any()
    # fixture includes rows with 'N/A' rating: must be null, never 0
    assert fault["rating_break_ka"].is_null().any()
    assert (fault["rating_break_ka"].drop_nulls() > 0).all()


def test_headroom_coordinates_and_bad_row():
    asset, _, _ = ssen.headroom_tables(FX / "headroom.csv", ASOF)
    bad = asset.filter(pl.col("name") == "Bad Coord Primary")
    assert bad.height == 1 and bad["lat"][0] is None and bad["lon"][0] is None
    assert bad["coord_quality"][0] == "rejected"
    good = asset.filter(pl.col("name") != "Bad Coord Primary")
    assert good["lat"].is_between(49.8, 60.95).all() and good["lon"].is_between(-8.7, 1.9).all()
    assert (good["coord_quality"] == "native_wgs84").all()


def test_asset_uid_unique():
    asset, cap, _ = ssen.headroom_tables(FX / "headroom.csv", ASOF)
    assert asset["asset_uid"].is_unique().all()
    assert cap["asset_uid"].is_unique().all()
    la, lc, lf = ssen.ltds_tables(FX / "ltds.json", "ssen_ltds_sepd", ASOF)
    assert la["asset_uid"].is_unique().all()  # fixture holds a duplicated mRID; adapter drops the repeat
    assert la.height == 8 - 1
    assert set(lc["asset_uid"]) == set(la["asset_uid"])
    assert set(lf["asset_uid"]) <= set(la["asset_uid"])


def test_ltds_mapping_and_nulls():
    la, lc, lf = ssen.ltds_tables(FX / "ltds.json", "ssen_ltds_sepd", ASOF)
    assert (la["as_of"] == "2026-05-31").all()  # data's own date beats portal date
    assert la["upstream_gsp"].null_count() >= 1  # "Not Applicable" -> null
    row = lc.filter(pl.col("asset_uid") == "SSEN:_nullcap")
    assert row["gen_headroom_mw"][0] is None
    assert lf.filter(pl.col("asset_uid") == "SSEN:_nullcap").height == 0  # no fault data -> no row
    prim = lf.filter(pl.col("rating_break_ka").is_not_null())
    assert prim.height >= 1 and prim["ltds_node"].is_not_null().all()
    assert set(la["kind"]) <= {"GSP", "BSP", "PRIMARY"}
    assert la["lat"].is_not_null().all()


def test_lv_sites():
    lv = ssen.lv_sites(FX / "substations.csv", "2026-11-09")
    conform(lv, "lv_site")
    assert lv["asset_uid"].is_unique().all()
    assert lv.height == 17  # 22 fixture rows: -2 abandoned, -2 primary-type, -1 exact duplicate
    assert (lv["coord_quality"] == "from_bng").sum() >= 15
    bad = lv.filter(pl.col("name") == "BADXY")
    assert bad.height == 1 and bad["lat"][0] is None and bad["coord_quality"][0] == "rejected"
    assert set(lv["kv_hv"].drop_nulls()) <= {11.0, 33.0, 6.6}
    assert lv.filter(pl.col("name").is_null()).height >= 1  # null `number` kept, uid still unique via coords


def test_ecr_projects():
    q = ssen.ecr_projects(FX / "ecr.csv", "2026-10-12")
    conform(q, "queue_project")
    assert q.height == 17 and q["project_uid"].is_unique().all()
    assert set(q["status"]) == {"connected", "accepted"}
    assert (q["export_mw"].drop_nulls() >= 0).all()
    nocoord = q.filter(pl.col("lat").is_null())
    assert nocoord.height >= 4  # 'DATA NOT AVAILABLE' rows + the bad one
    bad = q.filter(pl.col("project_uid") == "SSEN:SSEN-BAD-50kW-CONNECTED")
    assert bad["lat"][0] is None
    assert q["kv"].drop_nulls().is_between(0.4, 33).all()  # 'HV'/'LV' labels are not numbers
    assert q["connection_date"].drop_nulls().str.contains(r"^\d{4}-\d{2}-\d{2}$").all()
    assert q["site_name"].drop_nulls().ne("DATA NOT AVAILABLE").all()


def test_queue_xlsx():
    q = ssen.queue_projects(FX / "queue.xlsx", "2026-10-29")
    conform(q, "queue_project")
    assert q.height >= 6 and q["project_uid"].is_unique().all()
    assert set(q["status"]) <= {"connected", "other"}
    assert q["export_mw"].is_not_null().all()
    assert q["gsp_name"].is_not_null().all()


def test_ecr_1mw_file_uses_own_source_id():
    q = ssen.ecr_projects(FX / "ecr_1mw.csv", "2026-10-12", source_id="ssen_ecr_1mw")
    conform(q, "queue_project")
    assert q.height == 12 and set(q["source_id"]) == {"ssen_ecr_1mw"}
    assert q["project_uid"].str.contains("1MW").all()
    first = q.row(0, named=True)
    assert first["gsp_name"] == "GREMISTA" and first["status"] == "connected" and first["export_mw"] == 4.275
