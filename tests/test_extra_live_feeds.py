"""Live extras (live_pv, live_gsp, live_generators): parsers and joins on tiny real snapshots, no network."""

import json
from pathlib import Path

from gridmap.extra import live_generators as lg
from gridmap.extra import live_gsp as gsp
from gridmap.extra import live_pv as pv

FX = Path(__file__).parent / "fixtures" / "extra"
LIVE = FX / "live"


def _json(name: str):
    return json.loads((LIVE / name).read_text())


# ---------------------------------------------------------------- PV_Live

def test_pv_region_values_and_empty_skipped():
    vals = pv.region_values({10: _json("pes_10.json"), 17: _json("pes_17.json"), 11: _json("pes_empty.json")})
    assert set(vals) == {10, 17}  # empty document skipped, not zero-filled
    v = vals[10]
    assert v["cap"] > 1000 and 0 <= v["pct"] <= 100 and len(v["t"]) == 5 and v["t"][2] == ":"
    assert v["pct"] == round(100 * v["mw"] / v["cap"], 1) or abs(v["pct"] - 100 * v["mw"] / v["cap"]) < 0.1


def test_pv_features_use_licence_area_polygons():
    areas = json.loads((FX / "live_carbon" / "areas.json").read_text())
    vals = pv.region_values({10: _json("pes_10.json"), 17: _json("pes_17.json")})
    feats = pv.build_features(vals, areas)
    assert {f["properties"]["n"] for f in feats} == {"East of England", "North Scotland"}
    north = next(f for f in feats if f["properties"]["n"] == "North Scotland")
    lat = [p[1] for p in north["geometry"]["coordinates"][0]]
    assert sum(lat) / len(lat) > 56  # reprojected to WGS84, in Scotland


def test_pv_pes_mapping_covers_every_region_once():
    assert sorted(r for r, _ in pv.PES_REGION.values()) == list(range(1, 15))
    assert set(pv.PES_REGION) == set(range(10, 24))


# ---------------------------------------------------------------- GSP flows

def test_nged_latest_row_and_name_from_filename():
    files = {p.name: p.read_text() for p in LIVE.glob("*_swales.csv")}
    rows = {r["n"]: r for r in gsp.nged_rows(files)}
    assert set(rows) == {"Upper Boat 132", "Pyle"}
    r = rows["Pyle"]
    last_line = (LIVE / "pyle_swales.csv").read_text().strip().splitlines()[-1].split(",")
    assert r["ts"] == last_line[0] and r["mw"] == float(last_line[3])  # Import column of the newest row
    assert r["op"] == "NGED"


def test_ukpn_rows_aggregate_only_and_skip_empty_power():
    rows = gsp.ukpn_rows((LIVE / "ukpn_sgt.csv").read_text())
    names = {r["n"] for r in rows}
    assert "Hackney 132" in names and "Hackney 66" in names
    assert "Amersham" not in names  # AGGREGATE row with no power reading
    assert all(r["op"] == "UKPN" for r in rows)
    assert "Barking C (EPN)" in names  # parenthetical operator tag keeps its case


def _index() -> gsp.GspIndex:
    sites = [("UKPN", "HACKNEY 132kV", 51.55, -0.06), ("UKPN", "HACKNEY 66kV", 51.5501, -0.0601),
             ("NGED", "Pyle 132kv", 51.52, -3.7), ("UKPN", "Wimbledon Grid C 33kV", 51.43, -0.19),
             ("UKPN", "Wimbledon Grid A 33kV", 51.9, -0.5)]
    return gsp.GspIndex(sites, [("Upper Boat 132kV", 51.6, -3.3), ("Upper Boat 33kV", 51.6, -3.3)])


def test_gsp_features_locate_and_report_misses():
    rows = gsp.ukpn_rows((LIVE / "ukpn_sgt.csv").read_text())
    rows += gsp.nged_rows({p.name: p.read_text() for p in LIVE.glob("*_swales.csv")})
    feats, st = gsp.build_features(rows, _index())
    by = {f["properties"]["n"]: f for f in feats}
    assert {"Hackney 132", "Hackney 66", "Pyle", "Upper Boat 132"} <= set(by)
    assert st["unlocated"] == ["Barking C (EPN)", "Wimbledon 1&2"]  # unknown site; Wimbledon namesakes 50 km apart
    h = by["Hackney 132"]["properties"]
    assert h["t"] == "16:30" and h["a"] == abs(h["mw"])
    assert by["Pyle"]["properties"]["gen"] is not None and "mvar" not in by["Pyle"]["properties"]
    lon, lat = by["Pyle"]["geometry"]["coordinates"]
    assert (lon, lat) == (-3.7, 51.52)


def test_gsp_stale_rows_dropped():
    fresh = {"n": "Pyle", "op": "NGED", "ts": "2026-11-12T16:40:00", "mw": 10.0}
    old = {"n": "Pyle", "op": "NGED", "ts": "2026-11-12T10:00:00", "mw": 99.0}
    feats, st = gsp.build_features([fresh, old], _index())
    assert len(feats) == 1 and st["stale"] == 1 and feats[0]["properties"]["mw"] == 10.0


# ---------------------------------------------------------------- generators

def test_current_levels_interpolates_inside_interval():
    pn = [{"bmUnit": "T_X-1", "timeFrom": "2026-11-12T16:00:00Z", "timeTo": "2026-11-12T16:30:00Z",
           "levelFrom": 0, "levelTo": 300},
          {"bmUnit": "T_X-1", "timeFrom": "2026-11-12T16:30:00Z", "timeTo": "2026-11-12T17:00:00Z",
           "levelFrom": 300, "levelTo": 300},
          {"bmUnit": None, "nationalGridBmUnit": "Y-1", "timeFrom": "2026-11-12T16:10:00Z",
           "timeTo": "2026-11-12T16:20:00Z", "levelFrom": 5, "levelTo": 5}]
    lv = lg.current_levels(pn, "2026-11-12T16:15:00Z")
    assert lv["T_X-1"] == (150.0, "2026-11-12T16:00:00Z")
    assert lv["Y-1"][0] == 5.0
    assert lg.current_levels(pn, "2026-11-12T16:45:00Z")["T_X-1"][0] == 300.0  # newest interval, flat


def _cands():
    repd = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.589, 53.136]},
             "properties": {"n": "Race Bank Offshore Wind Farm", "t": "Offshore wind", "s": "Operational", "mw": 573.3}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-4.49, 57.25]},
             "properties": {"n": "Foyers G2", "t": "Hydro", "s": "Operational", "mw": 300}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.1, 52.6]},
             "properties": {"n": "Blackhillock Wind", "t": "Onshore wind", "s": "Operational", "mw": 50}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.1, 52.6]},
             "properties": {"n": "Planned Thing", "t": "Solar", "s": "Awaiting construction", "mw": 50}}]
    return lg.repd_candidates(repd)


def test_repd_candidates_operational_only():
    assert len(_cands()) == 3


def test_match_requires_name_capacity_and_technology():
    bm = _json("bmunits.json")
    by = {b["elexonBmUnit"]: b for b in bm}
    cands = _cands()
    assert lg.match_bmu(by["T_RCBKO-2"], cands)["n"] == "Race Bank Offshore Wind Farm"
    assert lg.match_bmu(by["T_FOYE-2"], cands)["n"] == "Foyers G2"
    # Blackhillock BESS: the name matches the *wind* site but battery technology must not match onshore wind
    assert lg.match_bmu(by["T_BLHLB-1"], cands) is None
    assert lg.match_bmu(by["2__AFLEX001"], cands) is None  # aggregator unit id, no site name


def test_generator_features_report_match_rate():
    bm = _json("bmunits.json")
    levels = lg.current_levels(_json("pn.json"), "2026-11-12T16:40:00Z")
    feats, st = lg.build_features(bm, levels, _cands())
    names = {f["properties"]["bm"]: f["properties"] for f in feats}
    assert set(names) == {"T_RCBKO-2", "T_FOYE-2"}
    assert names["T_FOYE-2"]["t"] == "Hydro & pumped storage" and names["T_FOYE-2"]["tt"] == "16:30"
    assert names["T_RCBKO-2"]["a"] == abs(names["T_RCBKO-2"]["mw"])
    assert st["matched"] == 2 and st["candidate_units"] > 2 and 0 < st["match_rate"] < 1


def test_subset_name_not_enough_for_short_generic_names():
    from rapidfuzz import fuzz
    assert lg._score("pivot", "pivot bustleholme", fuzz) < 90
    assert lg._score("race bank", "race bank phase", fuzz) >= 90


def test_layer_metas_are_live_group_and_off_by_default():
    for mod in (pv, gsp, lg):
        for m in mod.LAYERS:
            assert m.group == "Live" and m.live and not m.default_on
            assert any("as of" in row[1] and "UTC" in row[1] for row in m.popup)  # popups show the data time


# ---------------------------------------------------------------- constraints

def test_constraints_rollup_and_locate():
    from gridmap.extra import live_constraints as lc
    sites = lc.site_rollup((LIVE / "ukpn_constraints.csv").read_text())
    by = {s["code"]: s for s in sites}
    assert by["0C1068"]["name"] == "BRAMFORD GRID" and by["0C1068"]["n"] >= 2
    assert by["0C1068"]["u"] == max(float(line.split(";")[8]) for line in
                                     (LIVE / "ukpn_constraints.csv").read_text().splitlines()[1:]
                                     if line.startswith("2") and ";0C1068_" in line)
    idx = gsp.GspIndex([("UKPN", "BRAMFORD GRID 132kV", 52.08, 1.04)], [])
    feats, st = lc.build_features(sites, idx)
    assert len(feats) == 1 and st["located"] == 1 and st["sites"] == len(sites)
    p = feats[0]["properties"]
    assert p["n"] == "Bramford Grid" and len(p["t"]) == 5 and p["b"] == "No"
