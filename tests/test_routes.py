"""Power routes along real lines (gridmap.routes + /api/route) on tiny synthetic graphs. No network."""

from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridmap import routes
from gridmap.normalise import common
from gridmap.server.app import create_app

A, B = (-0.300, 51.700), (-0.200, 51.600)  # lon, lat ~13 km apart


def _line(*pts):
    return (11.0, [[list(p) for p in pts]])


def _graph(lines):
    return routes.Graph(*routes.graph_frames(lines))


def _sums(segs):
    return (sum(s["m"] for s in segs if s["kind"] == "line"), sum(s["m"] for s in segs if s["kind"] == "jump"))


def test_path_follows_lines_and_splits_at_shared_vertices():
    mid = (-0.260, 51.650)
    # one long line through `mid`; a tee from `mid` to B shares only that vertex
    g = _graph([_line(A, mid, (-0.25, 51.70)), _line(mid, B)])
    assert len(g.lon) == 4  # A, mid, far end, B: the long line was split at the shared vertex
    segs = routes.route(g, A, B, 300, 800)
    line, jump = _sums(segs)
    assert jump == 0 and line == pytest.approx(routes.dist(*A, *mid) + routes.dist(*mid, *B))
    assert [s["kind"] for s in segs] == ["line"]  # merged into one feature
    assert segs[0]["coords"][0] == list(A) and segs[0]["coords"][-1] == list(B)


def test_gap_between_pieces_is_bridged_by_a_jump():
    m1, m2 = (-0.262, 51.652), (-0.258, 51.648)  # ~500 m gap: the line went underground
    g = _graph([_line(A, m1), _line(m2, B)])
    segs = routes.route(g, A, B, 300, 800)
    assert [s["kind"] for s in segs] == ["line", "jump", "line"]
    line, jump = _sums(segs)
    assert jump == pytest.approx(routes.dist(*m1, *m2)) and line > 10 * jump


def test_ends_a_few_metres_apart_count_as_one_line():
    m1, m2 = (-0.26, 51.65), (-0.26003, 51.65003)  # ~4 m: different snap cells, same line
    segs = routes.route(_graph([_line(A, m1), _line(m2, B)]), A, B, 300, 800)
    assert [s["kind"] for s in segs] == ["line"] and _sums(segs)[1] == 0


def test_no_graph_or_far_lines_gives_one_straight_jump():
    assert [s["kind"] for s in routes.route(None, A, B, 300, 800)] == ["jump"]
    far = _graph([_line((1.0, 52.5), (1.01, 52.5))])
    segs = routes.route(far, A, B, 300, 800)
    assert len(segs) == 1 and segs[0]["coords"] == [list(A), list(B)]
    assert segs[0]["m"] == pytest.approx(routes.dist(*A, *B))


def test_band():
    assert [routes.band(k) for k in (0.4, 0, 11, 33, 66, 132, 400)] == [None, "hv", "hv", "ehv", "ehv", "132", "132"]


def test_build_writes_graphs_and_goes_stale_on_new_snapshot(tmp_path, monkeypatch):
    import json
    import shutil
    fx = Path(__file__).parent / "fixtures" / "extra" / "lines"
    monkeypatch.setattr(common, "DATA_DIR", tmp_path)

    def snap(sid, stamp):
        d = tmp_path / "raw" / sid / stamp
        d.mkdir(parents=True)
        shutil.copy(fx / f"{sid}.geojson", d / "export.geojson")
        (d / "meta.json").write_text(json.dumps({"status": "ok"}))

    snap("ukpn_oh_hv", "20261112T000000Z")
    assert routes.stale(tmp_path)
    counts = routes.build(tmp_path)
    assert counts.get("UKPN_hv", 0) > 0 and not routes.stale(tmp_path)
    assert routes.Router(tmp_path).graph("UKPN", "hv") is not None
    snap("ukpn_oh_hv", "20261113T000000Z")
    assert routes.stale(tmp_path)


def test_api_route_stops_at_bsp(tmp_path):
    con = duckdb.connect(str(tmp_path / "grid.duckdb"))
    con.execute("CREATE TABLE site(site_id VARCHAR, name VARCHAR, kind VARCHAR, operator VARCHAR, lat DOUBLE, "
                "lon DOUBLE, parent_site_id VARCHAR)")
    con.execute(f"""INSERT INTO site VALUES ('U:g','G','GSP','UKPN',51.5,-0.1,NULL),
                   ('U:b','B','BSP','UKPN',{B[1]},{B[0]},'U:g'), ('U:p','P','PRIMARY','UKPN',{A[1]},{A[0]},'U:b')""")
    con.execute("CREATE TABLE lv_site(asset_uid VARCHAR, operator VARCHAR, lat DOUBLE, lon DOUBLE, "
                "upstream_primary VARCHAR)")
    con.execute("INSERT INTO lv_site VALUES ('U:lv1','UKPN',51.71,-0.31,NULL)")
    con.close()
    out = tmp_path / "derived" / "routes"
    out.mkdir(parents=True)
    nodes, edges = routes.graph_frames([(33.0, [[list(A), [-0.25, 51.66], list(B)]])])
    nodes.write_parquet(out / "UKPN_ehv.nodes.parquet")
    edges.write_parquet(out / "UKPN_ehv.edges.parquet")
    with TestClient(create_app(tmp_path, scheduler=False)) as c:
        r = c.get("/api/route?site=U:p").json()
        assert [h["hop"] for h in r["hops"]] == ["primary-bsp"] and r["hops"][0]["to"] == "U:b"
        assert r["summary"]["jump_m"] == 0 and r["summary"]["line_m"] > 10000
        assert r["features"][0]["properties"]["kind"] == "line" and r["features"][0]["properties"]["kv"] == 33.0
        r = c.get("/api/route?lv=U:lv1").json()  # nearest primary, no HV graph -> straight, then lines to the BSP
        assert [h["hop"] for h in r["hops"]] == ["lv-primary", "primary-bsp"]
        assert [f["properties"]["kind"] for f in r["features"]] == ["jump", "line"]
        r = c.get("/api/route?site=U:b").json()  # a BSP goes on to its GSP; no 132/EHV line near -> straight
        assert [h["hop"] for h in r["hops"]] == ["bsp-gsp"] and r["summary"]["line_m"] == 0
        assert c.get("/api/route?site=U:g").json()["features"] == []
        assert c.get("/api/route?site=nope").status_code == 404
