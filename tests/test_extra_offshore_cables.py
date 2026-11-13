import json
from pathlib import Path

import polars as pl

from gridmap.extra import offshore_cables as c

FX = Path(__file__).parent / "fixtures" / "extra" / "offshore"


def _register() -> dict:
    df = pl.read_csv(FX / "interconnector_register.csv", infer_schema=False, encoding="utf8-lossy")
    df = df.rename({x: x.lstrip("﻿") for x in df.columns})
    out: dict = {}
    for r in df.iter_rows(named=True):
        cur = out.setdefault(r["Project Name"], {"mw": 0.0, "status": r["Project Status"]})
        cur["mw"] = max(cur["mw"], float(r["MW Export - Total"] or 0))
    return out


def test_layers_declared():
    assert {m.id for m in c.LAYERS} == {"offshore_cables", "landfalls"}
    for m in c.LAYERS:
        assert m.description and "OpenStreetMap" in m.attribution and not m.default_on


def test_classify():
    assert c.classify("Hornsea 2 inter array") == "Wind farm inter-array"
    assert c.classify("Moray East Wind Farm Export Cable") == "Wind farm export"
    assert c.classify("IFA 2000") == "Interconnector"
    assert c.classify("Beatrice OWF interconnector", 220) == "Wind farm inter-array"
    assert c.classify("South Uist Interconnector", 33) == "HVDC / island link"
    assert c.classify("Shetland HVDC Connection", 320) == "HVDC / island link"
    assert c.classify("BBL Pipeline (Balgzand Bacton Line)") is None
    assert c.classify("", 0) == "Other subsea power"


def test_register_join_uses_alias(monkeypatch):
    reg = _register()
    assert c.join_register("North Sea Link", reg) == {"mw": 1480, "st": "Operational"}
    assert c.join_register("IFA 2000", reg)["mw"] == 1988
    assert c.join_register("IFA2 HVAC Link", reg)["mw"] == 1150  # IFA2 must not match the IFA row
    assert c.join_register("Unknown cable", reg) is None


def test_features_cables_and_landfalls():
    lines = (FX / "osm_offshore.geojsonseq").read_text().splitlines()
    ifa = next(json.loads(x) for x in lines if '"IFA 2000"' in x)
    end = ifa["geometry"]["coordinates"][0]
    # a land cable (synthetic) that starts where the subsea cable comes ashore
    land = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [end, [end[0] - 0.01, end[1] + 0.01]]},
            "properties": {"power": "cable", "location": "underground"}}
    plat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.5, 53.0]},
            "properties": {"power": "substation", "name": "Test OSP", "location": "platform", "voltage": "220000"}}
    cables, pts = c.features(lines + [json.dumps(land), json.dumps(plat)], _register())
    kinds = {f["properties"]["k"] for f in cables}
    assert {"Interconnector", "Wind farm export", "Wind farm inter-array"} <= kinds
    ifa_f = next(f for f in cables if f["properties"].get("n") == "IFA 2000")
    assert ifa_f["properties"]["mw"] == 1988 and ifa_f["properties"]["st"] == "Operational"
    lf = [f for f in pts if f["properties"]["k"] == "Landfall"]
    assert len(lf) == 1 and lf[0]["properties"]["c"] == "Interconnector"
    assert [f["properties"]["n"] for f in pts if f["properties"]["k"] == "Offshore substation"] == ["Test OSP"]
