import json
from pathlib import Path

from gridmap.extra import live_carbon as lc

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


def test_ni_outline_is_nie_area(tmp_path, monkeypatch):
    from gridmap.extra import dno_areas
    d = tmp_path / "20261127T000000Z"
    d.mkdir()
    sq = [[-7.0, 54.0], [-6.0, 54.0], [-6.0, 55.0], [-7.0, 55.0], [-7.0, 54.0]]
    (d / "data.json").write_text(json.dumps({"features": [
        {"properties": {}, "geometry": {"type": "Polygon", "coordinates": [sq]}}]}))
    monkeypatch.setattr(dno_areas, "latest_snapshot", lambda sid: d if sid == "ons_ni_outline" else None)
    area, label = dno_areas.ni_area()
    assert area["properties"]["op"] == "NIE" and area["geometry"]["type"] == "MultiPolygon"
    assert label["properties"]["lbl"] == "NIE\nNorthern Ireland" and label["geometry"]["coordinates"] == [-6.6, 54.4]
