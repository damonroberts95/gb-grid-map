"""SPEN adapter tests: tiny real excerpts in tests/fixtures/norm/spen, no network."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from gridmap.normalise import schema, spen

FIX = Path(__file__).parent / "fixtures" / "norm" / "spen"


def rd(name: str) -> pl.DataFrame:
    return spen.read_csv(FIX / name)


@pytest.fixture(scope="module")
def asset_cap():
    asset, cap = spen.build_asset_capacity(rd("heatmap_spd.csv"), rd("heatmap_spm.csv"), "2026-06-09", "2026-06-12")
    return schema.conform(asset, "asset"), schema.conform(cap, "capacity")


def test_asset_mapping(asset_cap):
    asset, _ = asset_cap
    schema.conform(asset, "asset")  # enum + uniqueness checks
    row = asset.filter(pl.col("asset_uid") == "SPEN:SPD:ANNAN|11.0").row(0, named=True)
    assert row["kind"] == "PRIMARY" and row["licence_area"] == "SPD"
    assert (row["kv_hv"], row["kv_lv"]) == (33.0, 11.0)
    assert row["upstream_gsp"] == "CHAPELCROSS"
    assert (row["lat"], row["lon"]) == (54.9942, -3.2528)
    assert row["coord_quality"] == "native_wgs84"
    assert set(asset["kind"]) <= {"PRIMARY", "BSP"}
    assert "BSP" in set(asset["kind"])  # SPM 132/33 rows


def test_asset_uid_unique_and_spm_dedupe(asset_cap):
    asset, cap = asset_cap
    assert asset["asset_uid"].is_unique().all()
    assert all(u.startswith("SPEN:") for u in asset["asset_uid"])
    # SPM heatmap repeats a site per coordinate variant; it must collapse to one asset
    raw = rd("heatmap_spm.csv")
    assert asset.filter(pl.col("licence_area") == "SPM").height < raw.height
    assert cap["asset_uid"].is_in(asset["asset_uid"].implode()).all()


def test_missing_coords_and_site_name_fallback(asset_cap):
    asset, _ = asset_cap
    nolat = asset.filter(pl.col("lat").is_null())
    assert nolat.height >= 1 and (nolat["coord_quality"] == "missing").all()
    assert asset["name"].is_not_null().all()  # null site_name falls back to the group name


def test_capacity_nulls_negatives_and_rag(asset_cap):
    _, cap = asset_cap
    annan = cap.filter(pl.col("asset_uid") == "SPEN:SPD:ANNAN|11.0").row(0, named=True)
    assert annan["gen_headroom_mw"] == pytest.approx(14.64911163)
    assert annan["demand_headroom_mva"] == pytest.approx(13.4035535)
    assert annan["demand_headroom_mw"] is None  # source unit is MVA
    assert annan["firm_capacity_mva"] == 24.0
    assert annan["gen_rag_native"] == "G" and annan["demand_rag_native"] == "G"
    assert annan["basis"] and annan["basis"] != "unknown"
    assert (cap["gen_headroom_mw"] < 0).any()  # negative headroom kept, not zeroed
    # generation constraint 'unknown' -> no RAG, never coerced to a colour
    unk = rd("heatmap_spd.csv").filter(pl.col("generationconstraint") == "unknown")
    assert unk.height >= 1
    assert cap["gen_rag_native"].null_count() >= 1


def test_fault_spd_mapping_and_null():
    fl = spen.build_fault_spd(rd("fault_spd.csv"), "2025-11-28")
    schema.conform(fl, "fault_level")
    r = fl.row(0, named=True)
    assert (r["site_name"], r["kv"]) == ("OLD BRIDGE", 11.0)
    assert (r["fl_3ph_make_ka"], r["fl_3ph_break_ka"]) == (19.16, 7.1)
    assert (r["fl_1ph_make_ka"], r["fl_1ph_break_ka"]) == (20.25, 7.72)
    assert (r["rating_make_ka"], r["rating_break_ka"]) == (32.8, 13.12)
    assert fl["asset_uid"].is_null().all() and r["ltds_node"] is None


def test_fault_spm_join_and_no_switchgear():
    parts = [
        ("spen_ltds_fault_spm_132kv_3ph", rd("fault_spm_132_3ph.csv"), "2025-11-28"),
        ("spen_ltds_fault_spm_132kv_1ph", rd("fault_spm_132_1ph.csv"), "2025-11-28"),
        ("spen_ltds_fault_spm_33kv_3ph", rd("fault_spm_33_3ph.csv"), "2025-11-28"),
    ]
    fl = spen.build_fault_spm(parts)
    schema.conform(fl, "fault_level")
    assert fl.height == 12 + 12  # 1ph merged into the matching 3ph busbars
    rhyd = fl.filter(pl.col("ltds_node") == "RHYD MC1").row(0, named=True)
    assert rhyd["fl_3ph_make_ka"] == pytest.approx(7.10569971289538)
    assert rhyd["fl_1ph_make_ka"] == pytest.approx(6.50719511649389)
    assert (rhyd["rating_make_ka"], rhyd["rating_break_ka"]) == (50.0, 20.0)
    junction = fl.filter(pl.col("ltds_node") == "LAMP1A").row(0, named=True)
    assert junction["rating_make_ka"] is None  # 'No switchgear' is unknown, not 0
    assert fl["source_id"].null_count() == 0 and fl["as_of"].null_count() == 0
    assert fl["asset_uid"].is_null().all()


def test_ecr_1mw_coords_status_and_bad_row():
    q = spen.build_ecr_1mw(rd("ecr_1mw.csv"), "2026-09-28")
    schema.conform(q, "queue_project")
    assert q["project_uid"].is_unique().all()
    first = q.row(0, named=True)
    assert first["project_uid"] == "SPEN:830_SPEN" and first["status"] == "accepted"
    assert first["lat"] == pytest.approx(55.7766, abs=1e-3) and first["lon"] == pytest.approx(-4.1289, abs=1e-3)
    assert first["export_mw"] == 63.0 and first["kv"] == 33.0
    assert first["gsp_name"] == "Westburn Road" and first["bsp_name"] is None  # 'data not applicable' -> null
    assert first["connection_date"] == "2028-03-01"
    bad = q.filter(pl.col("lat").is_null())
    assert bad.height >= 1  # rows with no valid location stay null, never 0
    assert not ((q["lat"] == 0) | (q["lon"] == 0)).any()
    assert set(q["status"]) <= set(schema.QUEUE_STATUSES)


def test_ecr_50kw_drops_shifted_rows_dedupes_and_ignores_coords():
    raw = rd("ecr_50kw.csv")
    q, dropped = spen.build_ecr_50kw(raw, "2026-10-09")
    schema.conform(q, "queue_project")
    assert dropped >= 2  # shifted rows (unique_id holds a date) + duplicates
    assert q["project_uid"].is_unique().all()
    assert q["lat"].is_null().all() and q["lon"].is_null().all()  # 1 km rounded coords never used
    assert q["export_mw"].drop_nulls().max() < 5  # kW converted to MW
    dup = raw.filter(pl.col("unique_id").is_duplicated() & pl.col("unique_id").str.starts_with("SPEN_50kW_"))[
        "unique_id"
    ].unique().to_list()
    assert dup and q.filter(pl.col("project_uid") == f"SPEN:{dup[0]}").height == 1
    kept = q.filter(pl.col("project_uid") == f"SPEN:{dup[0]}").row(0, named=True)
    assert kept["kv"] is not None  # the duplicate stating the voltage is kept


def test_pipeline_extra_excludes_ecr_ids():
    raw = rd("queue_gsp.csv")
    known = {u for u in raw["unique_id"].to_list() if u != "607_SPEN"}
    extra = spen.build_pipeline_extra(raw, known, "2026-09-02")
    assert extra["project_uid"].to_list() == ["SPEN:607_SPEN"]
    assert extra["status"].to_list() == ["other"]


def test_lv_sites_unique_and_nulls():
    lv = schema.conform(
        spen.build_lv(rd("smart_meter.csv"), rd("customer_connections.csv"), "2026-06-18"), "lv_site"
    )
    assert lv["asset_uid"].is_unique().all()
    assert lv["asset_uid"].str.starts_with("SPEN:").all()
    assert (lv["coord_quality"] == "missing").all() and lv["lat"].null_count() == lv.height
    assert (lv["customers"].drop_nulls() >= 0).all()
    first = lv.filter(pl.col("asset_uid") == "SPEN:10019562")
    if first.height:
        assert first["customers"][0] == 150  # 149 residential + 1 LV non-residential; export 'Not Applicable' ignored
    # smart-meter-only transformers (no connection row) still appear, with null customers
    assert lv["customers"].null_count() >= 1
