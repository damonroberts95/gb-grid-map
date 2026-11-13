import json
from pathlib import Path

import polars as pl

from gridmap.extra import live_carbon as lc
from gridmap.extra import ni_network as ni

FX = Path(__file__).parent / "fixtures" / "extra"


def test_carbon_join_and_reproject():
    doc = json.loads((FX / "live_carbon" / "regional.json").read_text())
    areas = json.loads((FX / "live_carbon" / "areas.json").read_text())
    feats = lc.build_features(doc, areas)
    assert len(feats) == 14
    london = next(f for f in feats if f["properties"]["n"] == "London")
    assert isinstance(london["properties"]["i"], int)
    xs = [p[0] for p in london["geometry"]["coordinates"][0]]
    ys = [p[1] for p in london["geometry"]["coordinates"][0]]
    assert -1 < sum(xs) / len(xs) < 1 and 51 < sum(ys) / len(ys) < 52
    assert all(f["properties"]["mix"] for f in feats if (f["properties"]["i"] or 0) > 0)


def test_mix_text_drops_zero():
    assert lc.mix_text([{"fuel": "gas", "perc": 0}, {"fuel": "wind", "perc": 60.5}]) == "wind 60.5%"


def test_ni_sites():
    df = pl.read_csv(FX / "ni_network" / "sites.csv", separator=";", infer_schema=False)
    feats = ni.site_features(df)
    names = {f["properties"]["n"]: f for f in feats}
    assert set(names) == {"TEST GSP", "TEST PRIMARY"}  # (0,0) row rejected
    assert names["TEST GSP"]["properties"]["k"] == "GSP"
    assert names["TEST GSP"]["properties"]["v"] == 275.0
    _lon, lat = names["TEST PRIMARY"]["geometry"]["coordinates"]
    assert lat is not None and 54 < lat < 55.5  # Irish Grid fallback


def test_ni_secondary_rollup():
    df = pl.read_csv(FX / "ni_network" / "secondary.csv", separator=";", infer_schema=False)
    feats = {f["properties"]["n"]: f["properties"] for f in ni.secondary_features(df)}
    assert feats["TEST SEC"] == {"n": "TEST SEC", "c": 2, "r": 815}  # 500 kVA + 0.315 MVA
    assert feats["NO RATING"]["r"] is None  # null, not 0
