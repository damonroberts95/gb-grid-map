"""lines / osm_power / transmission extras from tiny real fixtures (no network, no osmium run)."""

import json
import shutil
from pathlib import Path

import pytest

from gridmap.extra import Ctx, lines, osm_power, transmission
from gridmap.normalise import common

FX = Path(__file__).parent / "fixtures" / "extra"


def snap(root: Path, sid: str, src: Path, name: str) -> None:
    d = root / "raw" / sid / "20261112T000000Z"
    d.mkdir(parents=True)
    shutil.copy(src, d / name)
    (d / "meta.json").write_text(json.dumps({"status": "ok", "files": [name]}))


@pytest.fixture()
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(common, "DATA_DIR", root)
    for sid in ["ukpn_oh_132kv", "ukpn_oh_hv", "ukpn_oh_lv", "npg_oh_ehv", "npg_oh_lv", "enw_oh_11kv", "enw_oh_lv"]:
        snap(root, sid, FX / "lines" / f"{sid}.geojson", "export.geojson")
    snap(root, "ssen_overhead_lines_routes", FX / "lines" / "ssen_hv_sepd.zip", "hv_sepd.zip")
    for sid in ["ssent_sites_supergrid", "ssent_sites_grid"]:
        snap(root, sid, FX / "transmission" / f"{sid}.geojson", "export.geojson")
    snap(root, "ssent_etys_fault_levels_winter_y1", FX / "transmission" / "ssent_etys_fault_levels.csv", "export.csv")
    snap(root, "neso_etys_boundaries", FX / "transmission" / "etys_boundaries.zip", "b.zip")
    return root


def test_layers_declared():
    for m in [*lines.LAYERS, *osm_power.LAYERS, *transmission.LAYERS]:
        assert m.description and m.attribution and m.popup and m.legend
        blob = json.dumps(m.color)
        assert not any(c in blob.lower() for c in ("#d73027", "#1a9850", "#fdae61"))
        assert m.type in ("geojson", "pmtiles") and (m.type == "geojson" or m.source_layer == m.id)
    assert any("OpenStreetMap contributors (ODbL)" in m.attribution for m in osm_power.LAYERS)


@pytest.mark.parametrize("v,expected", [("11kV", 11), ("415V", 0.415), ("66000.0", 66), ("6.6kV", 6.6),
                                        ("33.000 kV", 33), (230.0, 0.23), (None, None), ("x", None), ("0", None)])
def test_parse_kv(v, expected):
    assert lines.parse_kv(v) == expected


@pytest.mark.parametrize("v,expected", [("400000", 400), ("275000;132000", 275), ("33 kV", 33), (None, 0), ("abc", 0)])
def test_osm_max_kv(v, expected):
    assert osm_power.max_kv(v) == expected


def test_geojson_features_split_hv_lv(data):
    f = common.snapshot_files(common.latest_snapshot("npg_oh_ehv"))[0]
    feats = list(lines._geojson_feats(f, "NPG", False))
    assert feats and all(x["properties"]["op"] == "NPG" and x["properties"]["kv"] >= 33 for x in feats)
    lv = list(lines._geojson_feats(common.snapshot_files(common.latest_snapshot("enw_oh_lv"))[0], "ENW", True))
    assert lv and all(x["properties"]["kv"] < 1 for x in lv)


def test_ssen_shapefile_is_wgs84(data):
    out = list(lines._ssen_feats(common.latest_snapshot("ssen_overhead_lines_routes")))
    assert len(out) == 6
    for feat, is_lv in out:
        lon, lat = feat["geometry"]["coordinates"][0] if feat["geometry"]["type"] == "LineString" \
            else feat["geometry"]["coordinates"][0][0]
        assert -8.7 < lon < 1.8 and 49.8 < lat < 60.9
        assert feat["properties"]["op"] == "SSEN" and not is_lv


def test_osm_seq_fixture():
    seq = (FX / "lines" / "osm_lines.geojsonseq").read_text().splitlines()
    out = list(osm_power.seq_features(seq))
    kinds = [k for k, _ in out]
    assert "line" in kinds and "cable" in kinds
    # a cable tagged overground is dropped; every kept cable is underground/underwater/untagged
    assert all(f["properties"]["kv"] >= 0 for _, f in out)
    assert max(f["properties"]["kv"] for _, f in out) == 400
    assert any(f["properties"]["kv"] == 0 for _, f in out)  # missing voltage stays 0 = unknown colour


def test_osm_substations_fixture():
    seq = (FX / "lines" / "osm_subs.geojsonseq").read_text().splitlines()
    out = list(osm_power.substation_features(seq))
    assert out and all(f["properties"]["kv"] >= 132 for f in out)
    assert all(f["geometry"]["type"] == "Point" for f in out)


def test_transmission_build(data, tmp_path):
    out = transmission.build(Ctx(data, tmp_path))
    sites = json.loads(out["ssent_substations"].read_text())["features"]
    assert len(sites) == 12
    matched = [f for f in sites if "f3" in f["properties"]]
    assert matched and all(f["properties"]["f3"] is None or f["properties"]["f3"] > 0 for f in matched)
    bound = json.loads(out["etys_boundaries"].read_text())["features"]
    assert len(bound) == 4
    lon, lat = bound[0]["geometry"]["coordinates"][0] if bound[0]["geometry"]["type"] == "LineString" \
        else bound[0]["geometry"]["coordinates"][0][0]
    assert -9 < lon < 2 and 49 < lat < 61


def test_line_min_zoom_by_voltage_same_for_every_dno():
    assert [lines.min_zoom(kv) for kv in (132, 66, 33, 11, 6.6, 0, 0.4)] == [4, 7, 7, 9, 9, 9, 11]
