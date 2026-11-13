"""/api/stats and /api/queue on a tiny fixture DB. No network."""

import duckdb
import polars as pl
from fastapi.testclient import TestClient
from test_p4 import make_data

from gridmap.server.app import create_app


def _setup(tmp_path):
    root = make_data(tmp_path)
    con = duckdb.connect(str(root / "grid.duckdb"))
    for t in ("site", "site_capacity", "site_fault", "site_queue"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_parquet('{root}/derived/{t}.parquet')")
    pl.DataFrame({"asset_uid": ["UKPN:b1", "UKPN:b2", "UKPN:c1", "UKPN:x"], "operator": ["UKPN"] * 4,
                  "lat": [51.6, 51.6, 51.7, None], "lon": [-0.2, -0.2, -0.3, None],
                  "coord_quality": ["native_wgs84"] * 3 + ["missing"]}).write_parquet(root / "a.parquet")
    con.execute(f"CREATE TABLE asset AS SELECT * FROM read_parquet('{root}/a.parquet')")
    con.execute("CREATE TABLE asset_site AS SELECT * FROM (VALUES ('UKPN:b1','UKPN:b'),('UKPN:b2','UKPN:b'),"
                "('UKPN:c1','UKPN:c')) t(asset_uid, site_id)")
    con.execute("CREATE TABLE capacity AS SELECT * FROM (VALUES ('UKPN:b1', 12.3, NULL::DOUBLE, NULL::DOUBLE, "
                "'G', NULL, NULL, 'ukpn_heatmap'),('UKPN:c1', NULL, NULL, 7.0, NULL, NULL, 'x', 'ukpn_heatmap')) "
                "t(asset_uid, gen_headroom_mw, demand_headroom_mw, demand_headroom_mva, gen_rag_native, "
                "demand_rag_native, upstream_constraint, source_id)")
    con.execute("CREATE TABLE fault_level(asset_uid VARCHAR, rating_break_ka DOUBLE)")
    con.execute("CREATE TABLE lv_site AS SELECT * FROM read_parquet('" + str(root / "norm/UKPN/lv_site.parquet") + "')")
    con.execute("INSERT INTO site_queue VALUES ('UKPN:c', 10.0, 2.0, 50.0, 1, 3)")
    con.close()
    return root


def test_stats_and_queue_on_tiny_db(tmp_path):
    with TestClient(create_app(_setup(tmp_path), scheduler=False)) as c:
        s = c.get("/api/stats").json()
        h = s["headline"]
        assert h["sites"] == 4 and h["busbars"] == 4
        assert h["coords_pct"] == 75.0 and h["gen_pct"] == 25.0 and h["demand_pct"] == 25.0
        assert h["fault_pct"] == 50.0 and h["switchgear_pct"] == 50.0 and h["rag_pct"] == 25.0
        d = s["dno"][0]
        assert d["operator"] == "UKPN" and d["busbars"] == 4 and d["site_coords_pct"] == 75.0
        assert s["to"] == [] and {r["quality"]: r["n"] for r in s["coord_quality"]} == {"native_wgs84": 3, "missing": 1}
        assert s["headroom_sources"]["generation"] == [{"source_id": "ukpn_heatmap", "n": 1}]
        assert s["lv"]["sites"] == 3 and s["lv"]["coords_pct"] == 66.7 and s["lv"]["utilisation_pct"] == 66.7
        assert "by_operator" in s["freshness"]
        assert c.get("/api/stats").json() == s  # served from the manifest-versioned cache

        q = c.get("/api/queue").json()
        assert [x["site_id"] for x in q["sites"]] == ["UKPN:b", "UKPN:c"]
        assert q["totals"] == {"queued_gw": 0.04, "connected_gw": 0.01, "projects": 5, "sites": 2}
        assert q["tec_included"] is False and q["operators"] == ["UKPN"]
        assert q["sites"][0]["total_queued_mw"] == 30.0
        assert c.get("/api/queue?limit=1").json()["sites"][0]["site_id"] == "UKPN:b"
        assert c.get("/api/queue?op=NPG").json()["sites"] == []
        assert c.get("/api/queue?scoping=true").json()["sites"][0]["site_id"] == "UKPN:c"  # 10 + 50 MW scoping
