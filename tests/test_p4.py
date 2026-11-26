"""P4: build output vs docs/api.md, manifest atomicity, range route, refresh due logic. No network."""

import json
import os
import threading
import time
from pathlib import Path

import duckdb
import polars as pl
from fastapi.testclient import TestClient

from gridmap import build
from gridmap import refresh as rf
from gridmap.fetch.models import Source
from gridmap.server import live
from gridmap.server.app import create_app

NOW = 1_800_000_000.0


def _src(sid="s1", cadence="1d", category="headroom", **kw):
    return Source(id=sid, operator="UKPN", portal="http", base_url="x", category=category, cadence=cadence,
                  licence="l", attribution="a", **kw)


def write_fault(d: Path, headroom: float) -> None:
    pl.DataFrame({"site_id": ["UKPN:b"], "break_headroom_ka": [headroom], "fl_3ph_break_ka": [1.0],
                  "fl_3ph_make_ka": [1.0], "fl_1ph_break_ka": [1.0], "rating_break_ka": [5.5],
                  "rating_make_ka": [5.5], "make_headroom_ka": [1.0]}).write_parquet(d / "site_fault.parquet")


def make_data(root: Path, lv_extra: int = 0) -> Path:
    d, n = root / "derived", root / "norm" / "UKPN"
    d.mkdir(parents=True)
    n.mkdir(parents=True)
    pl.DataFrame({
        "site_id": ["UKPN:a", "UKPN:b", "UKPN:c", "UKPN:nocoord"], "operator": ["UKPN"] * 4,
        "name": ["Alpha", "Bravo", "Charlie", "Nowhere"], "kind": ["GSP", "BSP", "PRIMARY", "PRIMARY"],
        "kv_max": [132.0, 33.0, 11.0, 11.0], "lat": [51.5, 51.6, 51.7, None], "lon": [-0.1, -0.2, -0.3, None],
        "parent_site_id": [None, "UKPN:a", "UKPN:b", "UKPN:a"],
    }).write_parquet(d / "site.parquet")
    pl.DataFrame({"site_id": ["UKPN:b", "UKPN:c"], "gen_headroom_mw": [12.3456, None],
                  "demand_headroom_mw": [None, None], "demand_headroom_mva": [None, 7.0],
                  "gen_rag_native": ["G", None], "firm_capacity_mva": [None, None], "demand_rag_native": [None, None],
                  "upstream_constraint": [None, None], "basis": ["b", "b"], "source_id": ["s", "s"],
                  "as_of": ["2026-01-01"] * 2}).write_parquet(d / "site_capacity.parquet")
    write_fault(d, 4.5)
    pl.DataFrame({"site_id": ["UKPN:b"], "queued_mw": [30.0], "connected_mw": [5.0], "other_mw": [0.0],
                  "queued_projects": [1], "projects": [2]}).write_parquet(d / "site_queue.parquet")
    pl.DataFrame({
        "asset_uid": [f"UKPN:lv{i}" for i in range(3 + lv_extra)], "operator": "UKPN",
        "lat": [51.5, 51.5001, None] + [51.51] * lv_extra, "lon": [-0.1, -0.1001, None] + [-0.11] * lv_extra,
        "utilisation_pct": [55.0, None, 1.0] + [None] * lv_extra, "transformer_kva": [500.0] * (3 + lv_extra),
        "customers": [10, 20, 30] + [1] * lv_extra,
    }).write_parquet(n / "lv_site.parquet")
    return root


def test_build_shape_matches_contract(tmp_path):
    root = make_data(tmp_path)
    res = build.build_all(root)
    assert set(res["changed"]) == {"sites", "links", "lv"}
    m = json.loads((root / "public" / "manifest.json").read_text())
    assert m["version"] == 1 and m["refreshing"] is False and m["built_at"].endswith("Z")
    assert set(m["layers"]) == {"sites", "links", "lv"}
    for url in m["layers"].values():
        assert url.startswith("/data/") and (root / "public" / url.removeprefix("/data/")).exists()

    sites = json.loads((root / "public" / Path(m["layers"]["sites"]).name).read_text())
    feats = sites["features"]
    assert [f["properties"]["sid"] for f in feats] == ["UKPN:a", "UKPN:b", "UKPN:c"]  # no-coord site dropped
    assert [f["id"] for f in feats] == [0, 1, 2]
    keys = {"sid", "n", "op", "k", "kv", "g", "d", "du", "fh", "q", "c", "rg", "p"}
    assert all(set(f["properties"]) == keys for f in feats)
    a, b, c = (f["properties"] for f in feats)
    assert (a["k"], b["k"], c["k"]) == (1, 2, 3) and a["p"] is None and b["p"] == 0 and c["p"] == 1
    assert b["g"] == 12.346 and b["fh"] == 4.5 and b["q"] == 30.0 and b["c"] == 5.0 and b["rg"] == "G"
    assert c["d"] == 7.0 and c["du"] == 1 and b["du"] == 0 and a["g"] is None  # MVA flagged, null != 0
    assert feats[0]["geometry"] == {"type": "Point", "coordinates": [-0.1, 51.5]}

    links = json.loads((root / "public" / Path(m["layers"]["links"]).name).read_text())["features"]
    assert len(links) == 2 and links[0]["geometry"]["type"] == "LineString"
    assert set(links[0]["properties"]) == {"k", "op"}

    pm = (root / "public" / Path(m["layers"]["lv"]).name).read_bytes()
    assert pm[:7] == b"PMTiles"


def test_build_only_rebuilds_changed_inputs(tmp_path):
    root = make_data(tmp_path)
    first = build.build_all(root)
    assert build.build_all(root)["changed"] == []
    assert build.build_all(root)["manifest"]["version"] == first["manifest"]["version"]
    write_fault(root / "derived", 1.0)
    res = build.build_all(root)
    assert res["changed"] == ["sites"] and res["manifest"]["version"] == 2  # links/lv untouched


def test_cleanup_deletes_only_old_superseded_files(tmp_path):
    pub = tmp_path
    for n in ("sites.old.geojson", "sites.new.geojson", "tmp_lv.x.pmtiles", "manifest.json", "other.txt"):
        (pub / n).write_text("x")
    old = time.time() - 700
    os.utime(pub / "sites.old.geojson", (old, old))
    os.utime(pub / "tmp_lv.x.pmtiles", (old, old))
    gone = build.cleanup(pub, {"sites.new.geojson"})
    assert sorted(gone) == ["sites.old.geojson", "tmp_lv.x.pmtiles"]
    (pub / "sites.fresh.geojson").write_text("x")  # superseded but younger than 10 min: kept
    assert build.cleanup(pub, {"sites.new.geojson"}) == []


def test_manifest_atomic_under_concurrent_reads(tmp_path):
    root = make_data(tmp_path, lv_extra=0)
    build.build_all(root)
    pub = root / "public"
    bad, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            try:
                m = json.loads((pub / "manifest.json").read_text())
                for url in m["layers"].values():
                    if not (pub / Path(url).name).exists():
                        bad.append(f"dangling {url}")
            except Exception as e:  # noqa: BLE001
                bad.append(repr(e))

    t = threading.Thread(target=reader)
    t.start()
    for i in range(6):  # change the site layer each round
        write_fault(root / "derived", float(i))
        build.build_all(root)
    stop.set()
    t.join()
    assert not bad, bad[:3]


def test_range_route_and_headers(tmp_path):
    (tmp_path / "public").mkdir()
    blob = bytes(range(256)) * 40
    (tmp_path / "public" / "lv.abc.pmtiles").write_bytes(blob)
    (tmp_path / "public" / "manifest.json").write_text("{}")
    with TestClient(create_app(tmp_path, scheduler=False)) as c:
        r = c.get("/data/lv.abc.pmtiles", headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and r.content == blob[10:20]
        assert r.headers["content-range"] == f"bytes 10-19/{len(blob)}"
        assert "immutable" in r.headers["cache-control"]
        r = c.get("/data/lv.abc.pmtiles", headers={"Range": "bytes=-5"})
        assert r.status_code == 206 and r.content == blob[-5:]
        assert c.get("/data/lv.abc.pmtiles").status_code == 200
        assert c.get("/data/manifest.json").headers["cache-control"] == "no-cache"
        assert c.get("/data/../grid.duckdb").status_code == 404
        # api answers 503 (not 500) before the first load
        assert c.get("/api/site/UKPN:a").status_code == 503
        assert c.get("/api/search?q=abc").json() == []
        m = c.get("/api/manifest").json()
        assert m["refreshing"] is False


def test_api_site_and_search_on_tiny_db(tmp_path):
    root = make_data(tmp_path)
    con = duckdb.connect(str(root / "grid.duckdb"))
    for t in ("site", "site_capacity", "site_fault", "site_queue"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_parquet('{root}/derived/{t}.parquet')")
    con.execute("CREATE TABLE asset(asset_uid VARCHAR, name VARCHAR, kind VARCHAR, kv_hv DOUBLE, kv_lv DOUBLE, "
                "source_id VARCHAR, as_of VARCHAR)")
    con.execute("CREATE TABLE asset_site(asset_uid VARCHAR, site_id VARCHAR)")
    con.execute("CREATE TABLE capacity(asset_uid VARCHAR)")
    con.execute("CREATE TABLE fault_level(asset_uid VARCHAR, operator VARCHAR, site_name VARCHAR, kv DOUBLE, "
                "source_id VARCHAR)")
    con.execute("CREATE TABLE queue_site(project_uid VARCHAR, site_id VARCHAR)")
    con.execute("CREATE TABLE queue_project(project_uid VARCHAR, operator VARCHAR, site_name VARCHAR, status VARCHAR, "
                "technology VARCHAR, export_mw DOUBLE, import_mw DOUBLE, storage_mw DOUBLE, connection_date VARCHAR, "
                "source_id VARCHAR)")
    con.execute("INSERT INTO queue_site VALUES ('p1','UKPN:b'),('p2','UKPN:b')")
    con.execute("INSERT INTO queue_project VALUES ('p1','UKPN','x','accepted','solar',10,NULL,NULL,NULL,'s'),"
                "('p2','UKPN','y','connected','wind',4,NULL,NULL,NULL,'s')")
    con.close()
    with TestClient(create_app(root, scheduler=False)) as c:
        d = c.get("/api/site/UKPN:b").json()
        assert d["site"]["site_id"] == "UKPN:b" and d["parent"]["site_id"] == "UKPN:a"
        assert [x["site_id"] for x in d["children"]] == ["UKPN:c"] and d["children"][0]["d"] == 7.0
        assert d["queue"]["by_status"] == {"accepted": 10.0, "connected": 4.0}
        assert d["queue"]["by_tech"] == {"solar": 10.0, "wind": 4.0}
        assert [p["project_uid"] for p in d["queue"]["projects"]] == ["p1", "p2"]
        assert set(d) == {"site", "parent", "chain", "children", "assets", "faults", "queue", "sources"}
        assert c.get("/api/site/nope").status_code == 404
        r = c.get("/api/search?q=alp").json()
        assert r == [{"type": "site", "id": "UKPN:a", "name": "Alpha", "op": "UKPN", "kind": 1, "lat": 51.5, "lon": -0.1}]
        assert c.get("/api/search?q=brav&limit=1").json()[0]["id"] == "UKPN:b"


def test_is_due_cadence_and_failure_backoff():
    s = _src(cadence="5m")
    assert rf.is_due(s, None, None, NOW)
    assert not rf.is_due(s, NOW - 299, NOW - 299, NOW)
    assert rf.is_due(s, NOW - 300, NOW - 300, NOW)
    # last attempt failed: not retried every minute; a 5m feed backs off to 10 min
    assert not rf.is_due(s, NOW - 4000, NOW - 120, NOW)
    assert rf.is_due(s, NOW - 4000, NOW - 600, NOW)
    daily = _src(cadence="1d")
    assert not rf.is_due(daily, None, NOW - 3599, NOW)  # needs_key forever: retried hourly at most
    assert rf.is_due(daily, None, NOW - 3600, NOW)


def test_due_sources_uses_source_run_and_force():
    con = duckdb.connect()
    from gridmap.fetch.store import DDL
    con.execute(DDL)

    def log(sid, status, age):
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - age))
        con.execute("INSERT INTO source_run (source_id, status, fetched_at) VALUES (?,?,?)", [sid, status, t])

    log("fresh", "ok", 100)
    log("stale", "ok", 90000)
    log("unch", "unchanged", 100)
    reg = [_src("fresh"), _src("stale"), _src("unch"), _src("never"), _src("big", large=True)]
    assert [s.id for s in rf.due_sources(reg, con, NOW)] == ["stale", "never"]
    assert [s.id for s in rf.due_sources(reg, con, NOW, force=True)] == ["fresh", "stale", "unch", "never"]


def test_needs_key_source_retried_once_keys_file_changes():
    con = duckdb.connect()
    from gridmap.fetch.store import DDL
    con.execute(DDL)
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 600))
    con.execute("INSERT INTO source_run (source_id, status, fetched_at) VALUES ('gated', 'needs_key', ?)", [t])
    reg = [_src("gated", cadence="7d")]
    # keys unchanged since the failed attempt: wait out the 1 h backoff
    assert rf.due_sources(reg, con, NOW, keys_mtime=NOW - 900) == []
    # key saved after the failed attempt: due now
    assert [s.id for s in rf.due_sources(reg, con, NOW, keys_mtime=NOW - 60)] == ["gated"]


def test_live_fetches_never_trigger_rebuild_chain(tmp_path, monkeypatch):
    root = make_data(tmp_path)
    (root / "public").mkdir()
    (root / "public" / "manifest.json").write_text("{}")
    con = duckdb.connect(str(root / "grid.duckdb"))
    con.execute("CREATE TABLE site AS SELECT 1 AS x")
    reg = [_src("outage1", cadence="5m", category="outage"), _src("nat1", cadence="5m", category="live_national")]
    monkeypatch.setattr(rf.runner, "run", lambda ids, **kw: {
        "counts": {"ok": 2}, "runs": [{"source": i, "status": "ok"} for i in ids]})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("chain must not run"))
    monkeypatch.setattr(rf.match, "run", boom)
    monkeypatch.setattr(rf.load, "load_all", boom)
    calls = []

    def build_all(*a, **k):  # live extras may rebuild, but only in only_live mode (never the core layers)
        assert k.get("only_live") is True, "live fetch must not trigger a full build"
        calls.append(k)
        return {"changed": []}

    monkeypatch.setattr(rf.build, "build_all", build_all)
    stages = []
    out = rf.refresh(con=con, data_dir=root, registry=reg, on_stage=lambda s, d: stages.append(s))
    assert out["live"] == ["national", "outages"] and out["normalised"] == [] and out["built"] == []
    assert stages == ["fetch", "idle"]
    assert len(calls) == (1 if rf._has_live_extras() else 0)


def test_stale_operator_triggers_normalise_only_for_non_live(tmp_path):
    from gridmap.fetch.store import DDL
    con = duckdb.connect()
    con.execute(DDL)
    (tmp_path / "norm" / "UKPN").mkdir(parents=True)
    f = tmp_path / "norm" / "UKPN" / "asset.parquet"
    f.write_bytes(b"x")
    os.utime(f, (NOW - 1000, NOW - 1000))
    t = lambda a: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - a))
    con.execute("INSERT INTO source_run (source_id, status, fetched_at) VALUES ('live1','ok',?),('head1','ok',?)",
                [t(10), t(2000)])
    reg = [_src("live1", category="outage"), _src("head1")]
    assert rf.stale_operators(reg, con, tmp_path, NOW) == []  # live fetch newer than norm: ignored; headroom older
    con.execute("INSERT INTO source_run (source_id, status, fetched_at) VALUES ('head1','ok',?)", [t(5)])
    assert rf.stale_operators(reg, con, tmp_path, NOW) == ["UKPN"]


def test_live_parsers_skip_unmappable_and_broken_feeds(tmp_path, monkeypatch):
    def snap(sid, name, content):
        d = tmp_path / sid / "20261112T120000Z"
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{}")
        (d / name).write_text(content)

    snap("ssen_powertrack_faults", "data.json", json.dumps({"faults": [
        {"reference": "1", "type": "PSI", "location": {"latitude": 51.0, "longitude": -1.0}, "customerCount": 3,
         "loggedAtUtc": "2026-11-12T10:00:00Z", "estimatedRestorationTimeUtc": None, "jobStatus": ""},
        {"reference": "2", "type": "LV", "location": {"latitude": None, "longitude": None}}]}))
    snap("ukpn_live_faults", "export.csv", "garbage without the expected columns\n1\n")  # broken feed: skipped
    monkeypatch.setattr(live, "MAX_AGE_S", 10**9)  # the fixture snapshots are dated, not "now"
    out = live.outages(tmp_path)
    assert [f["properties"]["ref"] for f in out["features"]] == ["1"]
    assert out["features"][0]["properties"] == {
        "op": "SSEN", "ref": "1", "status": "PSI", "start": "2026-11-12T10:00:00Z", "etr": None, "cust": 3,
        "kind": "planned"}
    nat = live.national(tmp_path)
    assert nat["fuel_mix"] == [] and nat["carbon"] == {"national": None, "regions": []} and nat["demand_mw"] is None
