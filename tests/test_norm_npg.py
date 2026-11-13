"""NPG adapter tests: fixtures are <=30-row excerpts of the real exports (tests/fixtures/norm/npg).

ecr_1mw_plus.csv row 1 (first Connected row) has its coordinates overwritten with garbage to
exercise the bad-coordinate path; every other value is verbatim from the portal export.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from gridmap.normalise import npg
from gridmap.normalise.schema import conform

FX = Path(__file__).parent / "fixtures" / "norm" / "npg"


def fx(name: str) -> pl.DataFrame:
    return npg.read_csv(FX / name)


@pytest.fixture(scope="module")
def built():
    ltds, heat, sites = fx("ltds_heatmap.csv"), fx("heatmap_table.csv"), fx("sites_list.csv")
    assets = npg.build_assets(ltds, heat, sites, "2026-05-29")
    return {
        "assets": assets,
        "cap": npg.build_capacity(assets, heat, "2026-05-29"),
        "fl": npg.build_fault_level(heat, assets, "2026-07-27"),
        "lv": npg.build_lv_sites(sites, "2026-07-23"),
    }


# ---------- asset / capacity mapping ----------

def test_asset_mapping(built):
    a = conform(built["assets"], "asset").filter(pl.col("name") == "Acklam").row(0, named=True)
    assert a["asset_uid"] == "NPG:PSP-000002"
    assert a["kind"] == "PRIMARY"
    assert a["licence_area"] == "NE"
    assert a["kv_lv"] == 11.0 and a["kv_hv"] == 33.0  # kv_hv from sites list match
    assert a["lat"] == pytest.approx(54.5477584756945) and a["coord_quality"] == "native_wgs84"
    assert a["upstream_gsp"] == "Norton" and a["upstream_bsp"] == "Bowesfield"  # mrids resolved to names
    assert '"substation_id": "211030233868"' in a["native_ids"] and '"mrid": "PSP-000002"' in a["native_ids"]


def test_kinds_and_gsp_without_bsp(built):
    a = conform(built["assets"], "asset")
    assert set(a["kind"]) == {"GSP", "BSP", "PRIMARY"}
    drax = a.filter(pl.col("name") == "Drax").row(0, named=True)
    assert drax["kind"] == "GSP" and drax["upstream_bsp"] is None  # 'N/A' is not a name


def test_capacity_mapping_and_rag(built):
    c = conform(built["cap"], "capacity").join(built["assets"].select("asset_uid", "name"), on="asset_uid").filter(pl.col("name") == "Allerton Hill T2").row(0, named=True)
    assert c["gen_headroom_mw"] == pytest.approx(0.55) and c["demand_headroom_mw"] == 0.0  # real zero kept
    assert c["gen_rag_native"] == "R" and c["demand_rag_native"] == "R"
    assert c["firm_capacity_mva"] == 23.0 and c["basis"].startswith("LTDS available capacity")
    assert c["upstream_constraint"] is None or "gen:" in c["upstream_constraint"] or "demand:" in c["upstream_constraint"]


def test_capacity_nulls_not_zero(built):
    cap = conform(built["cap"], "capacity").join(built["assets"].select("asset_uid", "name"), on="asset_uid")
    kielder = cap.filter(pl.col("name") == "Kielder").row(0, named=True)
    assert kielder["demand_headroom_mw"] is None  # unpublished stays null
    assert kielder["demand_headroom_mva"] is None and kielder["connected_gen_mw"] is None
    assert cap.filter(pl.col("demand_headroom_mw") == 0).height >= 1  # but genuine zeros survive


# ---------- fault level ----------

def test_fault_level_mapping_and_links(built):
    fl, assets = conform(built["fl"], "fault_level"), built["assets"]
    abbey = fl.filter(pl.col("site_name") == "Airedale Road").row(0, named=True)
    assert abbey["fl_3ph_make_ka"] == pytest.approx(19.0916) and abbey["rating_make_ka"] == 62.5
    assert abbey["fl_3ph_break_ka"] == pytest.approx(8.048748) and abbey["rating_break_ka"] == 25.0
    assert abbey["kv"] == 11.0 and abbey["fl_1ph_make_ka"] is None
    linked = fl.filter(pl.col("asset_uid").is_not_null())
    assert linked["asset_uid"].is_in(assets["asset_uid"].implode()).all()


def test_fault_level_placeholders_and_zero(built):
    fl = built["fl"]
    assert fl.filter(pl.col("site_name") == "Kielder").height == 0  # all-1.0 kA placeholder row dropped
    ay = fl.filter(pl.col("site_name") == "Aycliffe Forrest").row(0, named=True)
    assert ay["fl_3ph_make_ka"] is None and ay["fl_3ph_break_ka"] == pytest.approx(8.8)  # 0 kA -> null


# ---------- coordinates ----------

def test_lv_site_coordinates_with_bad_row(built):
    lv = conform(built["lv"], "lv_site")
    bad = lv.filter(pl.col("name") == "RIVERSIDE WAY UNITS 60325").row(0, named=True)
    assert bad["lat"] is None and bad["lon"] is None and bad["coord_quality"] == "rejected"  # E/N 0,0 + no latlong
    good = lv.filter(pl.col("name") == "SKELTON MOOR").row(0, named=True)
    # CSV latlong is "lat, lon": lat first
    assert good["lat"] == pytest.approx(54.00893417913344) and good["lon"] == pytest.approx(-1.115954946792394)
    assert good["coord_quality"] == "native_wgs84"


def test_ecr_bad_coordinates_rejected():
    q = conform(npg.build_ecr(fx("ecr_1mw_plus.csv"), "npg_ecr_1mw_plus", "2026-10-14"), "queue_project")
    bad = q.row(0, named=True)  # garbage geopoint + scaled easting
    assert bad["lat"] is None and bad["lon"] is None
    assert q.filter(pl.col("lat").is_not_null()).height == q.height - 1
    ok = q.filter(pl.col("lat").is_not_null())
    assert ok["lat"].is_between(49.8, 60.95).all() and ok["lon"].is_between(-8.7, 1.9).all()


# ---------- lv_site / queue ----------

def test_lv_null_handling_and_merge(built):
    lv = conform(built["lv"], "lv_site")
    assert lv["asset_uid"].is_unique().all() and lv.filter(pl.col("name") == "CARVILLE").height == 1
    carville = lv.filter(pl.col("name") == "CARVILLE").row(0, named=True)
    assert carville["transformer_kva"] == 1300.0  # two transformers at one substation id summed
    assert carville["customers"] is None  # '<=5' is censored, not 5
    assert lv.filter(pl.col("name") == "SKELTON SCHOOL").row(0, named=True)["customers"] == 121
    assert lv["utilisation_pct"].null_count() == lv.height  # maximum_demand units/redaction: not published as %
    assert lv.filter(pl.col("name") == "ELEMORE").row(0, named=True)["upstream_primary"] is None  # 'TBC'


def test_queue_ecr_and_queue_mapping():
    ecr = conform(npg.build_ecr(fx("ecr_1mw_plus.csv"), "npg_ecr_1mw_plus", "2026-10-14"), "queue_project")
    assert set(ecr["status"]) == {"connected", "accepted"}
    first = ecr.row(0, named=True)
    assert first["project_uid"].startswith("NPG:NPg") and first["import_mw"] is None  # 'Data Not Available'
    small = conform(npg.build_ecr(fx("ecr_under_1mw.csv"), "npg_ecr_under_1mw", "2026-10-14"), "queue_project")
    assert small["export_mw"].null_count() == 0 and small["connection_date"].null_count() < small.height
    q = conform(npg.build_queue(fx("queue.csv"), "2026-02-18"), "queue_project")
    assert q["status"].unique().to_list() == ["other"] and q["export_mw"].null_count() == 0
    assert q["project_uid"].is_unique().all() and q["lat"].null_count() == q.height


# ---------- uid uniqueness ----------

def test_uids_unique(built):
    for df, key in ((built["assets"], "asset_uid"), (built["lv"], "asset_uid")):
        assert df[key].is_unique().all()
    assert built["assets"]["asset_uid"].str.starts_with("NPG:").all()
    conform(built["assets"], "asset")  # raises on duplicate uids / bad kinds


# ---------- LTDS appendices 6 and 7 ----------

def test_app6_fault_levels_phase_split_and_nulls(built):
    fl = npg.build_fault_level_app6(fx("ltds_app6.csv"), built["assets"], "2025-11-29")
    conform(fl, "fault_level")
    assert fl.height > 0 and set(fl["source_id"]) == {"npg_ltds_app6_fault"}
    three = fl.filter(pl.col("fl_3ph_break_ka").is_not_null())
    assert three.height > 0 and three["fl_1ph_break_ka"].is_null().all()
    first = fl.row(0, named=True)
    assert first["site_name"] == "Balme street" and first["kv"] == 11.0
    assert abs(first["fl_3ph_make_ka"] - 28.5159) < 1e-3 and abs(first["rating_break_ka"] - 13.1216) < 1e-3
    # 0 kA is not a fault level, and a row with no fault value at all is dropped
    assert (fl.select(pl.min_horizontal("fl_3ph_make_ka", "fl_3ph_break_ka")).to_series().drop_nulls() > 0).all()


def test_app7_generation_to_queue():
    q = npg.build_generation_app7(fx("ltds_app7.csv"), "2025-11-29")
    conform(q, "queue_project")
    assert q["project_uid"].is_unique().all() and set(q["status"]) <= {"connected", "accepted", "other"}
    row = q.row(0, named=True)
    assert row["gsp_name"] == "Blyth 132kV" and row["bsp_name"] == "Linton" and row["primary_name"] == "Denwick"
    assert row["status"] == "accepted" and row["export_mw"] == 1.0 and row["kv"] == 20.0
