"""UKPN normalisation adapter tests: tiny real-file excerpts, no network."""

import json
from pathlib import Path

import polars as pl
import pytest

from gridmap.normalise import ukpn
from gridmap.normalise.schema import conform

FIX = Path(__file__).parent / "fixtures" / "norm" / "ukpn"
ASOF = "2026-05-29"


@pytest.fixture(scope="module")
def sites():
    return ukpn.sites_assets(FIX / "grid_primary_sites.csv", ASOF)


@pytest.fixture(scope="module")
def heatmap():
    sites_raw = ukpn._csv(FIX / "grid_primary_sites.csv")
    t3a = ukpn._floc_t3a(FIX / "ltds_t3a.csv")
    asset, cap = ukpn.heatmap_tables([FIX / "heatmap_epn.json"], ASOF, sites_raw, t3a)
    return conform(asset, "asset"), conform(cap, "capacity")


def test_sites_mapping(sites):
    conform(sites, "asset")
    assert sites["asset_uid"].is_unique().all()
    assert sites["asset_uid"].str.starts_with("UKPN:").all()
    assert set(sites["kind"]) <= {"BSP", "PRIMARY"}
    assert set(sites["licence_area"].drop_nulls()) <= {"EPN", "LPN", "SPN"}
    assert (sites["coord_quality"] == "native_wgs84").all()
    # "132/33kV" style names give both voltages
    pair = sites.filter(pl.col("name").str.contains(r"(?i)\d/\d"))
    assert pair.height and pair["kv_lv"].is_not_null().all() and pair["kv_hv"].is_not_null().all()
    ids = json.loads(sites["native_ids"][0])
    assert ids["floc"] == sites["asset_uid"][0].removeprefix("UKPN:")


def test_heatmap_mapping_and_kind(heatmap):
    asset, cap = heatmap
    conform(asset, "asset")
    conform(cap, "capacity")
    assert asset["asset_uid"].is_unique().all()
    assert cap["asset_uid"].is_unique().all() and set(cap["asset_uid"]) == set(asset["asset_uid"])
    row = asset.filter(pl.col("name") == "Abberton Grid 33kV").row(0, named=True)
    assert row["kind"] == "BSP" and row["kv_lv"] == 33 and row["upstream_gsp"] == "Rayleigh"
    assert json.loads(row["native_ids"]).keys() >= {"mrid", "floc"}  # FLOC bridged via T3a name+area
    assert (asset["coord_quality"] == "native_wgs84").all()
    # unbridged row falls back on name; no FLOC recorded
    un = asset.filter(pl.col("name").str.starts_with("Luton St Marys")).row(0, named=True)
    assert un["kind"] == "PRIMARY" and "floc" not in json.loads(un["native_ids"])


def test_heatmap_capacity_values_and_rag(heatmap):
    _, cap = heatmap
    r = cap.filter(pl.col("asset_uid") == "UKPN:f3f97f72-f5c3-5af5-82d0-f79b7b5b5ada").row(0, named=True)
    assert r["gen_headroom_mw"] == 77.5 and r["demand_headroom_mw"] == 42.6
    assert r["gen_rag_native"] == "G" and r["demand_rag_native"] == "G"
    assert r["basis"].startswith("unknown")
    assert r["demand_headroom_mva"] is None and r["firm_capacity_mva"] is None
    assert "R" in set(cap["gen_rag_native"]) or "A" in set(cap["demand_rag_native"]) | set(cap["gen_rag_native"])


def test_heatmap_null_headroom_not_zero(tmp_path):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"Substations": [
        {"mRID": "AAA", "name": "X Primary 11kV", "area": "EPN", "latitude": 51.5, "longitude": -0.1,
         "voltages": [11], "demandAvailableCapacity": None, "generationAvailableCapacity": 0,
         "demandConstraint": "GREEN", "generationConstraint": None},
        {"mRID": "BBB", "name": "Y Primary 11kV", "area": "EPN", "latitude": 0, "longitude": 0, "voltages": [11]},
    ]}))
    asset, cap = ukpn.heatmap_tables([f], None)
    c = cap.filter(pl.col("asset_uid") == "UKPN:aaa").row(0, named=True)  # mRID lower-cased
    assert c["demand_headroom_mw"] is None and c["gen_headroom_mw"] == 0.0 and c["gen_rag_native"] is None
    bad = asset.filter(pl.col("asset_uid") == "UKPN:bbb").row(0, named=True)
    assert bad["lat"] is None and bad["coord_quality"] == "rejected"  # (0,0) placeholder rejected


def test_fault_level_ltds_and_links(sites):
    uids = set(sites["asset_uid"])
    fl = ukpn.fault_ltds(FIX / "ltds_t4a.csv", FIX / "ltds_t4b.csv", uids, ASOF)
    conform(fl, "fault_level")
    assert fl["asset_uid"].drop_nulls().is_in(list(uids)).all()
    assert fl["asset_uid"].is_not_null().any() and fl["asset_uid"].is_null().any()  # null FLOC -> node only
    assert fl.filter(pl.col("asset_uid").is_null())["ltds_node"].is_not_null().all()
    amer = fl.filter(pl.col("ltds_node") == "BHSS31")
    if amer.height:
        r = amer.row(0, named=True)
        assert r["fl_3ph_make_ka"] == 13.957252 and r["fl_1ph_break_ka"] == 1.747223
        assert r["rating_make_ka"] == 78.75 and r["rating_break_ka"] == 31.5


def test_fault_level_grid_primary(sites):
    uids = set(sites["asset_uid"])
    fl = conform(ukpn.fault_gp(FIX / "grid_primary_fault_levels.csv", uids, ASOF), "fault_level")
    assert fl.height == 8 and fl["asset_uid"].is_not_null().all()
    assert fl["fl_3ph_break_ka"].is_not_null().all() and fl["rating_make_ka"].is_null().all()


def test_ecr_part1_coords_status_and_uid_unique():
    q = ukpn.ecr_projects(FIX / "ecr_part1.csv", 1, ASOF, "ukpn_ecr_part1_under_1mw")
    conform(q, "queue_project")
    assert q["project_uid"].is_unique().all()
    assert set(q["status"]) == {"connected", "accepted"}
    assert q["lat"].is_null().sum() == 4  # 2 'Redacted'/missing + 2 rejected rows stay null, never (0,0)
    assert q.filter(pl.col("lat").is_not_null())["lat"].is_between(49.8, 60.95).all()
    acc = q.filter(pl.col("status") == "accepted")
    assert acc["export_mw"].is_not_null().any()
    assert not (q["export_mw"] == 0).all()


def test_ecr_part1_duplicate_rows_get_distinct_uids(tmp_path):
    one = pl.read_csv(FIX / "ecr_part1.csv", separator=";", infer_schema_length=0).head(1)
    p = tmp_path / "dup.csv"
    pl.concat([one, one]).write_csv(p, separator=";")
    q = ukpn.ecr_projects(p, 1, ASOF, "x")
    assert q["project_uid"].n_unique() == 2


def test_ecr_part2_storage_and_placeholders():
    q = ukpn.ecr_projects(FIX / "ecr_part2.csv", 2, ASOF, "ukpn_ecr_part2_1mw_plus")
    conform(q, "queue_project")
    assert q["project_uid"].is_unique().all()
    assert (q["lat"].is_not_null()).all()
    stor = q.filter(pl.col("technology").str.contains("Storage"))
    assert stor.height and stor["storage_mw"].is_not_null().all()
    assert q.filter(~pl.col("technology").str.contains("Storage"))["storage_mw"].is_null().all()
    assert not (q["bsp_name"] == "-").any() and not (q["bsp_name"] == "Direct").any()


def test_lv_sites():
    util = ukpn._csv(FIX / "secondary_utilisation.csv")
    lv = conform(ukpn.lv_sites(FIX / "secondary_sites.csv", ASOF, util), "lv_site")
    assert lv["asset_uid"].is_unique().all()
    # published as bands only: we store the band midpoint (open band "120% +" -> 130), band kept in native_ids
    import json
    for u, ids in lv.select("utilisation_pct", "native_ids").iter_rows():
        band = json.loads(ids).get("utilisation_band")
        if band is None:
            assert u is None
        elif band.endswith("+"):
            assert u == float(band.rstrip("% +")) + 10
        else:
            lo, hi = (float(x) for x in band.rstrip("%").split("-"))
            assert u == (lo + hi) / 2
    assert lv["transformer_kva"].is_not_null().sum() >= 8
    assert lv["customers"].dtype == pl.Int64
    assert (lv["coord_quality"] == "missing").sum() == 2 and lv["lat"].null_count() == 2
    ids = json.loads(lv["native_ids"][0])
    assert "utilisation_band" in ids and ids["floc"].startswith(("EPN-", "LPN-", "SPN-"))
