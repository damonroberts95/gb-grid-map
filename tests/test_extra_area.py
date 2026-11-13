"""area_primary / area_flex / area_constraints from tiny real fixtures (no network, no real data dir)."""

import json
import shutil
from pathlib import Path

import pytest

from gridmap.extra import Ctx
from gridmap.extra import area_constraints as con
from gridmap.extra import area_flex as flex
from gridmap.extra import area_primary as pri
from gridmap.normalise import common

FX = Path(__file__).parent / "fixtures" / "extra" / "area"


def snap(root: Path, source_id: str, name: str) -> None:
    d = root / "raw" / source_id / "20261112T000000Z"
    d.mkdir(parents=True)
    shutil.copy(FX / name, d / ("export" + Path(name).suffix))
    (d / "meta.json").write_text(json.dumps({"status": "ok", "fetched_at": "2026-11-12T00:00:00Z",
                                             "files": ["export" + Path(name).suffix]}))


@pytest.fixture()
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(common, "DATA_DIR", root)
    for sid, f in [("ukpn_primary_areas", "ukpn_primary_areas.geojson"),
                   ("ukpn_flex_lv_zones", "ukpn_flex_lv_zones.geojson"),
                   ("enw_flex_monthly_tender", "enw_flex_monthly_tender.geojson"),
                   ("spen_flex_polygons_spd", "spen_flex_polygons_spd.geojson"),
                   ("npg_flex_upcoming", "npg_flex_upcoming.csv"),
                   ("ukpn_curtailment_events", "ukpn_curtailment_events.csv"),
                   ("ukpn_constraint_breaches", "ukpn_constraint_breaches.csv"),
                   ("npg_curtailment_events", "npg_curtailment_events.csv"),
                   ("ukpn_gsp_areas", "ukpn_gsp_areas.geojson")]:
        snap(root, sid, f)
    return root


def test_layers_declared():
    for m in [*pri.LAYERS, *flex.LAYERS, *con.LAYERS]:
        assert m.description and m.attribution and m.popup and m.legend
        assert "#d73027" not in json.dumps(m.color)
        assert m.type in ("geojson", "pmtiles") and (m.type == "geojson" or m.source_layer == m.id)
    assert pri.LAYERS[0].id == "primary_areas" and pri.LAYERS[0].color["prop"] == "d"


def test_primary_join_by_floc(data):
    floc = {"SPN-S000000008515": "UKPN:site-a"}
    capd = {"UKPN:site-a": {"d": 12.5, "u": "MW", "g": 3.0}}

    class NoIdx:
        def resolve(self, *a, **k):
            return None

    feats = pri._features(NoIdx(), {}, capd, floc, {"UKPN:site-a": "WEYBRIDGE"})
    assert len(feats) == 6
    joined = [f for f in feats if f["properties"].get("d") == 12.5]
    assert len(joined) == 1
    p = joined[0]["properties"]
    assert p["n"] == "Weybridge" and p["op"] == "UKPN" and p["u"] == "MW" and p["g"] == 3.0
    assert all("d" not in f["properties"] for f in feats if f not in joined)  # null is not 0
    assert pri.JOIN_STATS["UKPN:P"] == (6, 1, 1)


def test_flex_collect_and_build(data, tmp_path):
    feats, counts = flex.collect()
    assert counts["UKPN LV"] == 5 and counts["ENW monthly"] == 5 and counts["SPEN"] >= 1 and counts["NPG"] == 3
    assert {f["properties"]["op"] for f in feats} == {"UKPN", "ENW", "SPEN", "NPG"}
    assert {f["properties"]["s"] for f in feats} <= {s for s, _ in flex.STATUS_COLORS}
    assert flex.build(Ctx(data, tmp_path))["flex_zones"].stat().st_size > 0


def test_curtailment_points(data):
    pts = con.curtailment_points(data)
    ops = {f["properties"]["op"] for f in pts}
    assert ops == {"UKPN", "NPG"}
    uk = next(f for f in pts if f["properties"]["op"] == "UKPN")
    assert uk["properties"]["ev"] == 30 and uk["properties"]["e"] >= 0
    for f in pts:
        lon, lat = f["geometry"]["coordinates"]
        assert 49.8 <= lat <= 60.9 and -8.7 <= lon <= 1.8


def test_anm_build(data, tmp_path):
    polys = con.anm_polygons()
    assert any(f["properties"]["op"] == "UKPN" for f in polys)
    out = con.build(Ctx(data, tmp_path))
    assert out["curtailment"].stat().st_size > 0 and out["anm_zones"].stat().st_size > 0
