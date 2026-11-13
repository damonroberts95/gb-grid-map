"""NGED adapter tests: tiny real-file excerpts, no network, no data/ dependency."""

import json
from pathlib import Path

import polars as pl
import pytest

from gridmap.normalise import nged
from gridmap.normalise.schema import conform

FIX = Path(__file__).parent / "fixtures" / "norm" / "nged"
HEAT = [FIX / "east-midlands.json", FIX / "south-wales.json"]
E_COL = "location(x-coordinate):_eastings_(where_data_is_held)"


@pytest.fixture(scope="module")
def heat():
    cim = nged._read_csv(FIX / "cim.csv")
    return nged.build_heatmap(HEAT, "2026-07-27", cim)


def test_heatmap_column_mapping(heat):
    asset, cap = heat
    a = asset.filter(pl.col("asset_uid") == "NGED:680022").row(0, named=True)
    assert a["name"] == "Nottingham North 132Kv S Stn"
    assert a["kind"] == "BSP" and a["licence_area"] == "EMID"
    assert (a["kv_hv"], a["kv_lv"]) == (132.0, 33.0)
    assert (a["lat"], a["lon"], a["coord_quality"]) == (52.978954, -1.173381, "native_wgs84")
    assert a["upstream_gsp"] == "Stoke Bardolph 132Kv" and a["upstream_bsp"] is None  # ' ' -> null
    ids = json.loads(a["native_ids"])
    assert ids["mrid"] == "680022" and ids["cim_type"] == "Bulk Supply Point"
    c = conform(cap, "capacity").filter(pl.col("asset_uid") == "NGED:680022").row(0, named=True)
    assert c["demand_headroom_mva"] == pytest.approx(22.41184721)
    assert c["demand_headroom_mw"] is None  # units not stated: never put in the MW column
    assert c["gen_headroom_mw"] == pytest.approx(85.07793427)
    assert c["firm_capacity_mva"] == 90
    assert (c["gen_rag_native"], c["demand_rag_native"]) == ("G", "G")
    assert c["basis"].startswith("unknown")
    for t, df in (("asset", asset), ("capacity", cap)):
        conform(df, t)  # schema + enum check


def test_heatmap_null_handling(heat):
    asset, cap = heat
    # generationConstraint "0" is not a RAG colour -> null, but the numeric headroom is kept
    z = cap.filter(pl.col("gen_rag_native").is_null() & pl.col("gen_headroom_mw").is_not_null())
    assert z.height >= 1
    # a real zero headroom stays 0, not null
    assert (cap["demand_headroom_mva"] == 0).sum() >= 1
    # non-numeric string in generationAvailableCapacity (' ') -> null, not 0
    assert cap["gen_headroom_mw"].null_count() == 1
    # single-voltage sites ("11", "33") carry no upstream voltage
    single = asset.filter(~pl.col("native_ids").str.contains(r'"voltages": "[^"]*-'))
    assert single.height >= 1 and single["kv_hv"].null_count() == single.height


def test_heatmap_uid_unique_and_dup_mrid(heat):
    asset, cap = heat
    assert asset["asset_uid"].is_unique().all()
    tir = asset.filter(pl.col("native_ids").str.contains('"mrid": "520037"'))
    assert tir.height == 2 and tir["asset_uid"].n_unique() == 2  # BSP + Primary share one mRID
    assert set(cap["asset_uid"]) == set(asset["asset_uid"])  # capacity references asset table


def test_heatmap_bad_coords(tmp_path):
    rows = json.loads((FIX / "east-midlands.json").read_text())
    rows[0]["latitude"], rows[0]["longitude"] = -1.17, 52.97  # swapped
    rows[1]["latitude"], rows[1]["longitude"] = 0.0, 0.0  # zeroed
    p = tmp_path / "x.json"
    p.write_text(json.dumps(rows))
    asset, _ = nged.build_heatmap([p], None)
    q = dict(zip(asset["native_ids"].str.extract(r'"mrid": "(\d+)"'), asset["coord_quality"], strict=True))
    assert q[str(rows[0]["mRID"])] == "swapped_fixed"
    assert q[str(rows[1]["mRID"])] == "rejected"
    assert asset.filter(pl.col("coord_quality") == "rejected")["lat"].null_count() == 1


def test_fault_levels():
    fl = nged.build_fault_levels(FIX / "fault.csv", "2026-07-03")
    assert fl.height == 12
    r = fl.row(0, named=True)
    assert r["ltds_node"] == "BANR5_BB_1_2" and r["site_name"] == "Banbury Road 33/11kV"
    assert r["asset_uid"] is None and r["kv"] == 11.0
    assert r["fl_3ph_make_ka"] == pytest.approx(13.42543) and r["fl_3ph_break_ka"] == pytest.approx(5.70159)
    assert r["rating_make_ka"] == 32.75 and r["rating_break_ka"] == 13.1
    assert r["fl_1ph_make_ka"] == pytest.approx(1.09246)
    # "-" means not published -> null, never 0
    assert r["fl_1ph_break_ka"] == pytest.approx(0.75737)
    assert fl["rating_break_ka"].null_count() >= 1
    conform(fl, "fault_level")


def test_ecr_mapping_and_coords(tmp_path):
    q = nged.build_ecr(FIX / "ecr.csv", "2026-08-06")
    assert q["project_uid"].is_unique().all()
    assert set(q["status"]) <= {"connected", "accepted"}
    first = q.row(0, named=True)
    assert first["project_uid"].startswith("NGED:ECR:") and first["status"] == "connected"
    assert first["lat"] is not None and first["kv"] is not None
    # accepted rows use accepted capacity and have no mpan -> hashed uid
    acc = q.filter(pl.col("status") == "accepted")
    assert acc.height >= 1 and acc["export_mw"].null_count() == 0
    assert acc["project_uid"].str.contains(":ECR:H").all()
    # redacted rows ("--REDACTED--" E/N, blank lat/lon) -> null coords, still a row
    red = q.filter(pl.col("lat").is_null())
    assert red.height >= 1 and red["export_mw"].null_count() == 0
    # storage capacity only for stored-energy rows
    assert q.filter(pl.col("storage_mw").is_not_null())["technology"].str.starts_with("Stored").all()
    conform(q, "queue_project")


def test_ecr_bad_bng_is_rejected(tmp_path):
    df = pl.read_csv(FIX / "ecr.csv", infer_schema_length=0)
    bad = df.filter(pl.col(E_COL).cast(pl.Float64, strict=False) > 700000).head(1)
    assert bad.height == 1  # real x10 easting row is in the fixture
    p = tmp_path / "ecr_bad.csv"
    bad.with_columns(pl.lit(None, pl.Utf8).alias("lat"), pl.lit(None, pl.Utf8).alias("lon")).write_csv(p)
    q = nged.build_ecr(p, None)
    assert q["lat"][0] is None and q["lon"][0] is None  # E>700000 never converted


def test_queue():
    q = nged.build_queue([FIX / "queue.csv"], "2026-10-30")
    assert q.height == 7 and q["project_uid"].is_unique().all()
    assert set(q["status"]) == {"connected", "accepted"}
    bess = q.filter(pl.col("technology") == "BESS").row(0, named=True)
    assert bess["storage_mw"] == 10.0 and bess["connection_point"] == "HARB1_103"
    assert bess["import_mw"] is None  # blank machine import stays null
    conform(q, "queue_project")


def test_reform_register_skips_titles_and_blank_rows():
    q = nged.build_reform(FIX / "reform.csv", "2025-12-12")
    assert q.height == 8
    assert q["project_uid"].is_unique().all()
    r = q.row(0, named=True)
    assert r["site_name"] == "Ashorne Solar" and r["export_mw"] == 49.9
    assert r["connection_date"] == "2025-09-26" and r["status"] == "other"
    conform(q, "queue_project")


def test_duplicate_rows_get_unique_uids(tmp_path):
    p = tmp_path / "dup.csv"
    lines = (FIX / "reform.csv").read_text(encoding="utf-8-sig").splitlines()
    p.write_text("\n".join(lines[:5] + [lines[4]] + lines[5:]) + "\n")
    q = nged.build_reform(p, None)
    assert q.height == 9 and q["project_uid"].is_unique().all()


def test_gated_paths_skip_without_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(nged.C, "latest_snapshot", lambda s: None)
    assert nged.run() == {}


# ---------------------------------------------------------------- gated sources (real excerpts)

@pytest.fixture(scope="module")
def ncm():
    return nged.read_ncm([FIX / "ncm.csv"])


def test_ncm_split_keeps_secondary_out_of_asset(ncm, heat):
    pb, sec = ncm
    assert set(pb["kind"]) <= {"GSP", "BSP", "PRIMARY"} and pb.height == 10
    assert set(sec["kind"]) == {"SECONDARY"} and sec.height == 9
    asset, _cap, st = nged.merge_ncm_into_heatmap(*heat, pb, "2026-11-12")
    assert "SECONDARY" not in set(asset["kind"])
    assert asset["asset_uid"].is_unique().all()
    assert st["ncm_pb"] == 10 and st["ncm_pb_matched"] + st["ncm_pb_new"] == 10


def test_ncm_duplicates_of_heatmap_are_merged(ncm, heat):
    asset, cap, st = nged.merge_ncm_into_heatmap(*heat, ncm[0], "2026-11-12")
    # same (number, kind): heatmap row kept, NCM id recorded
    a = asset.filter(pl.col("asset_uid") == "NGED:680022").row(0, named=True)
    ids = json.loads(a["native_ids"])
    assert a["source_id"] == "nged_ltds_heatmap" and ids["mrid"] == "680022" and ids["ncm_id"]
    # BSP and Primary share mRID 520037: each NCM row lands on its own heatmap row
    tir = asset.filter(pl.col("native_ids").str.contains('"mrid": "520037"'))
    assert tir.height == 2 and all("ncm_id" in json.loads(x) for x in tir["native_ids"])
    # NCM number typo (940006 vs heatmap 690006): matched by kind + name, NCM's number kept in native_ids
    brad = asset.filter(pl.col("name").str.starts_with("Bradwell Abbey")).row(0, named=True)
    assert json.loads(brad["native_ids"])["ncm_number"] == "940006"
    assert asset.filter(pl.col("name").str.starts_with("Bradwell Abbey")).height == 1
    assert st["ncm_pb_matched"] >= 8
    # capacity: heatmap precision kept; NCM adds an explicitly-MW demand headroom and fills RAG gaps
    c = cap.filter(pl.col("asset_uid") == "NGED:680022").row(0, named=True)
    assert c["demand_headroom_mva"] == pytest.approx(22.41184721) and c["demand_headroom_mw"] == 22.4
    assert "NCM" in c["basis"]
    conform(asset, "asset"), conform(cap, "capacity")
    assert set(cap["asset_uid"]) == set(asset["asset_uid"])


def test_ncm_only_primary_becomes_new_row(ncm):
    asset, cap, st = nged.merge_ncm_into_heatmap(*nged.build_heatmap(HEAT, None), ncm[0], None)
    new = asset.filter(pl.col("source_id") == "nged_network_capacity_map")
    assert new.height == st["ncm_pb_new"] >= 1
    nc = cap.filter(pl.col("asset_uid").is_in(new["asset_uid"].implode()))
    assert nc.height == new.height and nc["basis"][0].startswith("NGED NCM")
    assert nc["demand_headroom_mva"].null_count() == nc.height  # NCM states MW, so MVA column untouched


def test_primary_locations_adds_gsp_and_missing_points(ncm, heat):
    asset, _cap, _ = nged.merge_ncm_into_heatmap(*heat, ncm[0], None)
    asset, new, st = nged.build_primary_locations(FIX / "primary_locs.csv", asset, "2025-08-19")
    assert set(new["kind"]) == {"GSP", "BSP", "PRIMARY"}  # switching stations skipped
    assert (new["kind"] == "GSP").sum() == 2 and new["lat"].null_count() == 0
    assert st["pl_matched_existing"] >= 2  # 680022 and 520037 already known
    nott = asset.filter(pl.col("asset_uid") == "NGED:680022").row(0, named=True)
    assert json.loads(nott["native_ids"])["primary_locations_number"] == "680022"
    assert nott["lat"] == pytest.approx(52.978954)  # existing coordinates are not overwritten
    full = pl.concat([asset, new])
    assert full["asset_uid"].is_unique().all()
    conform(full, "asset")


@pytest.fixture(scope="module")
def lv(ncm):
    return nged.build_lv_sites(FIX / "dist_subs.csv", FIX / "dist_locs.csv", ncm[1], "2026-11-01")


def test_lv_site_join_and_dedupe(lv):
    df, st = lv
    # 12 rows: 1 exact duplicate + 1 per-primary aggregate (no number) are dropped; NCM-only secondary appended
    assert st["sub_rows"] == 12 and st["sub_aggregate_rows_dropped"] == 1 and st["sub_sites"] == 10
    assert st["ncm_secondary_only_rows"] == 1 and df.height == 11
    assert df["asset_uid"].is_unique().all()
    assert st["sub_joined_to_loc"] == 8  # two fixture substations are absent from the locations file
    conform(df, "lv_site")
    assert df.filter(pl.col("asset_uid").str.contains("Aggregated")).height == 0


def test_lv_site_coordinates(lv):
    df, _ = lv
    # BNG easting/northing from the locations file wins and is converted
    assert (df["coord_quality"] == "from_bng").sum() >= 4
    # substation only in the substations file keeps its own lat/lon; grid-reference-only row is recovered
    assert "native_wgs84" in set(df["coord_quality"])
    assert df["lat"].null_count() <= 1
    # the locations file has a 100 km northing error for 537409; substations lat/lon + grid ref outvote it
    bad = df.filter(pl.col("asset_uid") == "NGED:537409").row(0, named=True)
    assert bad["coord_quality"] == "native_wgs84" and bad["lat"] == pytest.approx(51.695642)
    assert df.filter(pl.col("lat").is_not_null())["lat"].is_between(49.8, 60.95).all()


def test_lv_site_fields_and_ncm_merge(lv):
    df, st = lv
    assert st["ncm_joined_to_sub"] >= 5
    r = df.filter(pl.col("native_ids").str.contains("ncm_demand_headroom_mw")).row(0, named=True)
    ids = json.loads(r["native_ids"])
    assert ids["ncm_demand_rag_native"] in {"R", "A", "G"} and ids["number"] in r["asset_uid"]
    assert r["transformer_kva"] is None or r["transformer_kva"] > 0  # rating 0.0 means not recorded
    assert conform(df, "lv_site")["utilisation_pct"].is_null().all()  # NGED publishes demand kVA, not utilisation
    assert df["customers"].dtype == pl.Int64 and df["customers"].drop_nulls().min() >= 0
    zero = df.filter(pl.col("native_ids").str.contains('"number": "' + df.filter(pl.col("transformer_kva").is_null() & pl.col("source_id").str.contains("substations"))["asset_uid"][0].split(":")[1]))
    assert zero.height == 1


def test_gridref_conversion():
    assert nged._gridref_en("SK968140676270") == ("496814.0", "367627.0")
    assert nged._gridref_en("sx 12 34") == ("212000", "34000")
    assert nged._gridref_en("N/A") == (None, None) and nged._gridref_en(None) == (None, None)


def test_lv_site_unresolvable_conflict_is_rejected(tmp_path):
    p = tmp_path / "subs.csv"
    df = nged._read_csv(FIX / "dist_subs.csv").with_columns(
        pl.when(pl.col("Substation Number") == "537409").then(pl.lit("N/A")).otherwise(pl.col("Grid Reference"))
        .alias("Grid Reference"))
    df.write_csv(p)
    out, _ = nged.build_lv_sites(p, FIX / "dist_locs.csv", None, None)
    bad = out.filter(pl.col("asset_uid") == "NGED:537409").row(0, named=True)
    assert bad["coord_quality"] == "rejected" and bad["lat"] is None  # BNG says 52.59, substations file says 51.70


def test_add_utilisation_from_published_max_demand():
    import polars as pl

    from gridmap.normalise.nged import add_utilisation
    lv = pl.DataFrame({"_day": ["405.0", None, "10", "5"], "_night": ["639.0", "50", None, "x"],
                       "transformer_kva": [800.0, 100.0, None, 0.0]})
    u = add_utilisation(lv)["utilisation_pct"].to_list()
    assert u[0] == 79.9          # max(day, night) / kVA
    assert u[1] == 50.0          # one side missing is fine
    assert u[2] is None          # no rating
    assert u[3] is None          # zero rating never divides
