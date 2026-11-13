import json
from pathlib import Path

from gridmap.extra import nerda_client as nc
from gridmap.extra import nerda_sites
from gridmap.server import nerda as srv

FX = Path(__file__).parent / "fixtures" / "extra" / "nerda"


def static():
    return json.loads((FX / "static_secondary.json").read_text())


def test_dedupe_lines_and_measurements():
    idx = nc.build_index(static(), "snap")
    site = idx["sites"][0]
    assert site["nrn"] == "6100_007_120_00" and site["k"] == "secondary"
    line2 = next(ln for ln in site["lines"] if ln["name"].endswith("_02"))
    # id 5764227c appears under LineCurrent and PhaseVoltage: both kept, none repeated
    keys = [(m["id"], m["type"]) for m in line2["m"]]
    assert len(keys) == len(set(keys)) == 6
    assert ("5764227c-9f35-407b-92aa-ccfc8f08887f", "LineCurrent") in keys
    assert ("5764227c-9f35-407b-92aa-ccfc8f08887f", "PhaseVoltage") in keys
    dup = nc.dedupe_measurements([static()[0]["lines"][0], static()[0]["lines"][0]])
    assert len(dup) == 1 and len(dup[0]["m"]) == 6


def test_kind_inference():
    assert nc.infer_kind("6100_007_120_00") == "secondary"
    assert nc.infer_kind("6707", "STAPLEFORD") == "primary"
    assert nc.infer_kind("0242", "ABERNETHY GRID") == "bsp"
    assert nc.infer_kind("", "MP1") == "other" and nc.infer_kind("----") == "other"
    # site NRN lacks the secondary group but its lines carry one
    assert nc.infer_kind("4010_001_000_00", "X", [{"name": "4010_001_040_02"}]) == "secondary"
    assert nc.infer_kind("4010_001_000_00", "X", [{"name": "4010_001_000_02"}]) == "other"


def test_lv_feeders_and_current_ids():
    site = nc.build_index(static())["sites"][0]
    fs = nc.lv_feeders(site["lines"])
    assert [f["name"] for f in fs] == ["6100_007_120_02", "6100_007_120_01"]
    assert nc.line_current_id(fs[1]) == "3487e8a7-2fed-4df5-92c3-05922c54dfb3"


def test_parse_after_latest_per_channel():
    doc = json.loads((FX / "after_current.json").read_text())
    ch = nc.parse_after(doc)
    assert ch["in_max"] == {"v": 33.8, "ts": "2026-11-12T17:20:00Z"}
    assert "i1" not in ch  # empty history dropped
    v, ts = nc.latest_value(doc)
    assert v == 34 and ts == "2026-11-12T17:10:00Z"  # i2_max, neutral excluded
    s = srv.summarise(ch)
    assert s["LineCurrent"]["v"] == 34 and s["PhaseVoltage"]["unit"] == "V"


def test_resolve_by_nrn_and_line_uuid():
    idx = nc.build_index(static())
    assert srv.resolve(idx, "6100_007_120_00")["site"]["n"] == "37 HOLLYBANK ROAD"
    r = srv.resolve(idx, "3dc18577-3554-491c-b487-76364abada19")
    assert [ln["name"] for ln in r["lines"]] == ["6100_007_120_01"]
    assert srv.resolve(idx, "nope") is None
    assert srv.sweep_targets(idx) == [("6100_007_120_00", ["5764227c-9f35-407b-92aa-ccfc8f08887f",
                                                            "3487e8a7-2fed-4df5-92c3-05922c54dfb3"])]


def test_features_colour_by_latest_and_kind():
    sites = nc.build_index(static())["sites"]
    plain = nerda_sites.build_features(sites, {})[0]["properties"]
    assert plain["c"] == "lvm" and plain["lvf"] == 2 and "i_max" not in plain
    live = nerda_sites.build_features(sites, {"6100_007_120_00": {"i_max": 250.0, "t": "2026-11-12T17:20:00Z"}})
    p = live[0]["properties"]
    assert p["c"] == "a2" and p["i_max"] == 250.0 and p["t"] == "17:20"


def test_rewrite_env_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nA=1\nNERDA_API_KEY=old\nNERDA_USERNAME=me@x\n")
    nc.rewrite_env(env, "NERDA_API_KEY", "new")
    assert env.read_text() == "# c\nA=1\nNERDA_API_KEY=new\nNERDA_USERNAME=me@x\n"
    nc.rewrite_env(env, "ADDED", "z")
    assert env.read_text().endswith("ADDED=z\n") and "A=1" in env.read_text()
    assert not [p for p in tmp_path.iterdir() if p.name != ".env"]  # temp file replaced, not left behind


def test_extract_key():
    import httpx
    assert nc.extract_key(httpx.Response(200, json={"apiKey": "abc"})) == "abc"
    assert nc.extract_key(httpx.Response(200, text='"xyz"')) == "xyz"
    assert nc.extract_key(httpx.Response(200, json={"x": 1})) is None


def test_latest_roundtrip(tmp_path):
    p = tmp_path / "d" / "nerda_latest.parquet"
    m = srv.save_latest(p, {}, {"a": {"i_max": 1.5, "t": "2026-11-12T17:20:00Z", "nf": 2, "fetched": "x"}})
    srv.save_latest(p, m, {"b": {"i_max": 2.0, "t": "t", "nf": 1, "fetched": "x"}})
    assert set(srv.load_latest(p)) == {"a", "b"}
