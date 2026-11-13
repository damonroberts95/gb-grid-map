"""ENW adapter tests: tiny excerpts of the real exports in tests/fixtures/norm/enw/. No network."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from gridmap.normalise import enw
from gridmap.normalise.schema import conform

FIX = Path(__file__).parent / "fixtures" / "norm" / "enw"


def csv(name: str) -> pl.DataFrame:
    return enw.load_csv(FIX / f"{name}.csv")


@pytest.fixture(scope="module")
def gis() -> pl.DataFrame:
    return enw.prep_gis(csv("enw_substations_gis"), enw.SRC_GIS, "2026-08-07")


@pytest.fixture(scope="module")
def heatmaps() -> dict[str, pl.DataFrame]:
    return {
        kind: enw.prep_heatmap(enw.load_geojson(FIX / f"{sid}.geojson"), kind, sid, "2026-10-29")
        for kind, sid in enw.SRC_HEATMAPS.items()
    }


@pytest.fixture(scope="module")
def assets(gis, heatmaps) -> pl.DataFrame:
    return enw.build_assets(gis, heatmaps)


@pytest.fixture(scope="module")
def queue() -> pl.DataFrame:
    parts = [(sid, csv(sid), "2026-10-20") for sid in enw.SRC_ECR]
    return enw.build_queue(parts)


@pytest.fixture(scope="module")
def lv(gis) -> pl.DataFrame:
    lvh = [("enw_lv_headroom_unmonitored", csv("enw_lv_headroom_unmonitored"), "2026-03-19")]
    return enw.build_lv(gis, csv("enw_capacity_distribution_tx"), csv("enw_smart_meter_installation"), lvh,
                        (enw.SRC_TX, "2026-03-24"))


def row(df: pl.DataFrame, key: str, val: str) -> dict:
    r = df.filter(pl.col(key) == val)
    assert r.height == 1, f"{val}: {r.height} rows"
    return r.row(0, named=True)


# ---------- asset ----------

def test_asset_uid_unique_and_conforms(assets):
    assert assets["asset_uid"].is_unique().all()
    assert assets["asset_uid"].str.starts_with("ENW:").all()
    conform(assets, "asset")  # raises on bad kind / duplicate uid


def test_same_number_bsp_and_primary_do_not_collide(assets):
    assert {"ENW:BSP:200208", "ENW:PRIMARY:200208"} <= set(assets["asset_uid"])


def test_asset_column_mapping(assets):
    r = row(assets, "asset_uid", "ENW:BSP:400128")
    assert (r["kind"], r["kv_hv"], r["kv_lv"]) == ("BSP", 132.0, 33.0)
    assert r["upstream_gsp"] == "400240_400125"
    assert r["coord_quality"] == "native_wgs84" and 53 < r["lat"] < 54 and -4 < r["lon"] < -2
    assert r["source_id"] == enw.SRC_GIS and r["as_of"] == "2026-08-07"
    p = row(assets, "asset_uid", "ENW:PRIMARY:100030")
    assert (p["kv_hv"], p["kv_lv"], p["upstream_bsp"], p["upstream_gsp"]) == (33.0, 11.0, "100001", "100092")
    assert '"number": "100030"' in p["native_ids"]


def test_primary_that_is_also_its_bsp_has_no_upstream_bsp(assets):
    assert row(assets, "asset_uid", "ENW:PRIMARY:100139")["upstream_bsp"] is None


def test_compound_heatmap_key_consumes_gis_components(assets):
    r = row(assets, "asset_uid", "ENW:GSP:400240_400241")
    assert r["lat"] is not None and r["coord_quality"] == "native_wgs84"
    assert "ENW:GSP:400240" not in set(assets["asset_uid"]) and "ENW:GSP:400241" not in set(assets["asset_uid"])
    assert "ENW:PRIMARY:100601_T11_T12" in set(assets["asset_uid"])
    assert "ENW:PRIMARY:100601" not in set(assets["asset_uid"])


def test_heatmap_only_asset_has_no_invented_coords(assets):
    r = row(assets, "asset_uid", "ENW:BSP:200215")  # in heatmap, not in GIS
    assert r["lat"] is None and r["lon"] is None and r["coord_quality"] == "missing"
    assert r["kv_lv"] == 33.0 and r["upstream_gsp"] == "200220"


def test_foreign_and_network_rows_dropped(assets):
    uids = " ".join(assets["asset_uid"])
    assert "NGED" not in uids and "SPEN" not in uids
    assert set(assets["kind"]) <= {"GSP", "BSP", "PRIMARY"}  # NETWORK / DISTRIBUTION groups never become assets
    assert "ENW:GSP:200420" in uids  # 'No Heatmap Values' suffix stripped, row kept


# ---------- capacity ----------

def test_capacity_mapping_and_nulls(heatmaps, assets):
    cap = enw.build_capacity(heatmaps)
    assert cap["asset_uid"].is_unique().all()
    assert cap["asset_uid"].is_in(assets["asset_uid"].implode()).all()
    conform(cap, "capacity")
    p = row(cap, "asset_uid", "ENW:PRIMARY:300238")
    raw = enw.load_geojson(FIX / "enw_primary_heatmap.geojson").filter(pl.col("pry_number") == "300238").row(0, named=True)
    assert p["demand_headroom_mw"] == pytest.approx(float(raw["demand_headroom_firm_mw"]))
    gens = [float(raw[c]) for c in ("generation_headroom_non_firm_inverter_based_mw",
                                    "generation_headroom_non_firm_lv_synchronous_mw",
                                    "generation_headroom_non_firm_hv_synchronous_mw")]
    assert p["gen_headroom_mw"] == pytest.approx(min(gens))
    assert "firm" in p["basis"] and p["basis"].startswith("unknown")
    # a published 0.0 stays 0.0 (not null); rows with no values at all are not emitted
    assert row(cap, "asset_uid", "ENW:BSP:200208")["demand_headroom_mw"] == 0.0
    assert "ENW:BSP:200215" in set(cap["asset_uid"])  # heatmap-only asset still carries its headroom
    assert not any("No Heatmap" in u for u in cap["asset_uid"])


def test_gsp_capacity_has_connected_accepted_only(heatmaps):
    cap = enw.build_capacity(heatmaps)
    g = row(cap, "asset_uid", "ENW:GSP:305932")
    raw = enw.load_geojson(FIX / "enw_gsp_heatmap.geojson").filter(pl.col("gsp_number") == "305932").row(0, named=True)
    assert g["connected_gen_mw"] == pytest.approx(float(raw["connected_mw"]))
    assert g["contracted_gen_mw"] == pytest.approx(float(raw["accepted_mw"]))
    assert g["gen_headroom_mw"] is None and g["demand_headroom_mw"] is None


# ---------- queue ----------

def test_queue_uid_unique_status_and_nulls(queue):
    assert queue["project_uid"].is_unique().all()
    conform(queue, "queue_project")
    assert set(queue["status"]) <= {"connected", "accepted", "other"}
    assert {"connected", "accepted"} <= set(queue["status"])
    # 'DATA NOT AVAILABLE' / '-' text tokens never leak through as values
    for c in ("site_name", "primary_name", "bsp_name", "gsp_name", "technology"):
        assert not queue[c].drop_nulls().str.to_uppercase().is_in(["DATA NOT AVAILABLE", "-", "N/A"]).any()


def test_queue_capacity_mapping(queue):
    conn = queue.filter((pl.col("status") == "connected") & (pl.col("source_id") == "enw_ecr_under_1mw"))
    raw = csv("enw_ecr_under_1mw").filter(pl.col("connection_status") == "CONNECTED")
    got = sorted(conn["export_mw"].drop_nulls())
    want = sorted(float(x) for x in raw["maximum_export_capacity_mw"].drop_nulls())
    assert got == pytest.approx(want) and 0.0 in got  # real zero export kept, not nulled
    acc = queue.filter(pl.col("status") == "accepted")
    assert acc["export_mw"].drop_nulls().min() >= 0
    assert (queue["connection_point"].is_not_null() | queue["gsp_name"].is_null()).all()
    # storage_mw only set for storage technologies
    assert queue.filter(pl.col("storage_mw").is_not_null())["technology"].str.to_uppercase().str.contains("STOR|BATTERY").all()


def test_queue_coordinates_incl_bad_rows(queue):
    bad = queue.filter(pl.col("site_name") == "BAD COORDS (edited)")  # edited row: out-of-GB lat/lon and BNG
    assert bad.height == 1 and bad["lat"][0] is None and bad["lon"][0] is None
    missing = csv("enw_ecr_under_1mw").filter(pl.col("geopoint").is_null()).height  # real row with no coords
    assert missing >= 1
    assert queue["lat"].null_count() == missing + 1
    ok = queue.filter(pl.col("lat").is_not_null())
    assert ok["lat"].is_between(49.8, 60.95).all() and ok["lon"].is_between(-8.7, 1.9).all()


# ---------- lv_site ----------

def test_lv_site_mapping(lv, gis):
    assert lv["asset_uid"].is_unique().all()
    conform(lv, "lv_site")
    a = row(lv, "asset_uid", "ENW:328147")
    assert a["kv_hv"] == 6.6 and a["upstream_primary"] == "304882" and a["source_id"] == enw.SRC_GIS
    assert a["transformer_kva"] == 315.0 and a["utilisation_pct"] == pytest.approx(6.25026323484495 / 315 * 100)
    assert a["customers"] is not None and a["customers"] > 0
    assert a["coord_quality"] == "native_wgs84"


def test_lv_multi_transformer_site_sums_and_415v(lv):
    m = row(lv, "asset_uid", "ENW:333822")  # in tx table + smart-meter table only: no GIS row
    assert m["transformer_kva"] == 2000.0 and m["source_id"] == enw.SRC_TX
    assert lv["kv_hv"].drop_nulls().max() < 100


def test_lv_headroom_only_site_added_with_bng_coords(lv):
    extra = lv.filter(pl.col("source_id") == "enw_lv_headroom_unmonitored")
    assert extra.height >= 1
    assert extra["coord_quality"].is_in(["from_bng", "native_wgs84"]).all() and extra["lat"].is_not_null().all()
    assert extra["transformer_kva"].is_null().all() and extra["customers"].is_null().all()


def test_lv_missing_load_gives_null_utilisation(gis):
    tx = csv("enw_capacity_distribution_tx").with_columns(
        pl.when(pl.col("distribution_tx_number") == "3281473TX001").then(None)
        .otherwise(pl.col("distribution_tx_load_kva")).alias("distribution_tx_load_kva"))
    lv = enw.build_lv(gis, tx, None, [], (enw.SRC_TX, None))
    r = row(lv, "asset_uid", "ENW:328147")
    assert r["transformer_kva"] == 315.0 and r["utilisation_pct"] is None and r["customers"] is None


def test_kv_expr_units():
    df = pl.DataFrame({"v": ["11KV", "6.6KV", "415V", "33", None, "N/A"]})
    assert df.select(enw.kv_expr("v").alias("k"))["k"].to_list() == pytest.approx([11.0, 6.6, 0.415, 33.0, None, None])


def test_site_names_from_flex_tenders(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "export.geojson").write_bytes((FIX / "enw_flex_monthly_tender.geojson").read_bytes())
    monkeypatch.setattr(enw, "latest_snapshot", lambda sid: snap if sid == "enw_flex_monthly_tender" else None)
    names = enw.build_site_names()
    assert names.height == 12 and names["number"].is_unique().all()
    d = dict(names.select("number", "name").iter_rows())
    assert d["100504"] == "Frederick Rd BSP" and d["166515"] == "TRAMWAY RD"
    assert set(names["source_id"]) == {"enw_flex_monthly_tender"}
