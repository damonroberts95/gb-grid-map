"""lv_monitored and lv_headroom_forecast from tiny real fixtures (no network, no real data dir)."""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest

from gridmap.extra import Ctx
from gridmap.extra import lv_headroom_forecast as hf
from gridmap.extra import lv_monitored as mon
from gridmap.normalise import common

FX = Path(__file__).parent / "fixtures" / "extra" / "lv"


def snap(root: Path, source_id: str, src: Path, name: str) -> None:
    d = root / "raw" / source_id / "20261112T000000Z"
    d.mkdir(parents=True)
    shutil.copy(src, d / name)
    (d / "meta.json").write_text(json.dumps({"status": "ok", "files": [name]}))


@pytest.fixture()
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(common, "DATA_DIR", root)
    for sid, f in [("enw_lv_headroom_monitored", "enw_lv_headroom_monitored.csv"),
                   ("enw_lv_headroom_unmonitored", "enw_lv_headroom_unmonitored.csv"),
                   ("enw_lv_peak_demand_monitored", "enw_lv_peak_demand_monitored.csv"),
                   ("spen_lv_monitor", "spen_lv_monitor.csv"),
                   ("npg_lv_monitor_deployment", "npg_lv_monitor_deployment.csv")]:
        snap(root, sid, FX / f, "export.csv")
    snap(root, "nged_lv_monitor_list", FX / "nged_lv_monitor_list.json", "data.json")
    for op, f in [("ENW", "enw_lv_site.parquet"), ("NGED", "nged_lv_site.parquet")]:
        (root / "norm" / op).mkdir(parents=True)
        shutil.copy(FX / f, root / "norm" / op / "lv_site.parquet")
    return root


def test_layers_declared():
    for m in [*mon.LAYERS, *hf.LAYERS]:
        assert m.description and m.attribution and not m.default_on
        assert m.type == "pmtiles" and m.source_layer == m.id and m.minzoom <= 5
        assert m.popup and m.legend
        for hexcol in ("#d73027",):
            assert hexcol not in json.dumps(m.color)


def test_monitored_features(data):
    enw, spen = [f for _, f in mon._enw()], [f for _, f in mon._spen()]
    npg, nged = [f for _, f in mon._npg(data)], [f for _, f in mon._nged(data)]
    assert len(enw) == 10 and len(spen) == 7 and len(nged) == 8  # '000000' has no lv_site row
    assert {f["properties"]["mon"] for f in enw} == {"PreSense"}
    assert any("pk" in f["properties"] for f in enw)
    assert all(0 <= f["properties"]["u"] < 500 for f in spen if "u" in f["properties"])
    st = {f["properties"]["s"] for f in npg}
    assert st == {"Monitored", "Shortlisted"}
    assert len(npg) == 14  # 17 fixture rows, 3 without a geopoint
    for f in enw + spen + npg + nged:
        lon, lat = f["geometry"]["coordinates"]
        assert 49.8 <= lat <= 60.9 and -8.7 <= lon <= 1.8
        assert f["properties"]["op"] in {"ENW", "SPEN", "NPG", "NGED"}


def test_monitored_build_pmtiles(data, tmp_path):
    out = mon.build(Ctx(data, tmp_path))
    assert out["lv_monitored"].exists() and out["lv_monitored"].stat().st_size > 0


def test_headroom_build_and_values(data, tmp_path):
    out = hf.build(Ctx(data, tmp_path))
    assert out["lv_headroom_forecast"].stat().st_size > 0
    df = pl.read_csv(FX / "enw_lv_headroom_monitored.csv", separator=";", infer_schema_length=0)
    assert df["best_view_2030"].cast(pl.Float64).null_count() == 0
    assert hf.YEAR == 2030 and hf.SCENARIO == "best_view"
    assert "2030" in hf.LAYERS[0].description and "Best View" in hf.LAYERS[0].description


def test_monitored_union(data):
    df = mon.monitored(data)
    assert df.columns == ["asset_uid", "ms"]
    assert df["asset_uid"].n_unique() == df.height
    ms = dict(df.group_by("ms").len().iter_rows())
    assert ms["ENW PreSense"] == 10 and ms["SPEN"] == 7 and ms["NGED"] == 8
    assert all(u.startswith(("ENW:", "SPEN:", "NGED:", "NPG:")) for u in df["asset_uid"])
    assert "NeRDA" not in ms  # no NeRDA index in the fixture data dir: degrades to nothing


def test_monitored_nerda_join(data):
    ssen = pl.DataFrame({"asset_uid": ["SSEN:SEPD|a", "SSEN:SEPD|b", "SSEN:SHEPD|c"], "lat": [51.0, 52.0, 51.0],
                         "lon": [-1.0, -1.0, -1.0]})
    (data / "norm" / "SSEN").mkdir()
    ssen.write_parquet(data / "norm" / "SSEN" / "lv_site.parquet")
    lines = [{"name": "6108_002_570_01", "m": [{"id": "x", "type": "LineCurrent"}]}]
    idx = {"snapshot": "s", "sites": [
        {"k": "secondary", "lat": 51.0001, "lon": -1.0, "lines": lines},   # ~11 m from a
        {"k": "secondary", "lat": 52.01, "lon": -1.0, "lines": lines},     # >1 km from b
        {"k": "primary", "lat": 51.0, "lon": -1.0, "lines": lines}]}
    (data / "derived").mkdir()
    (data / "derived" / "nerda_index.json").write_text(json.dumps(idx))
    df = mon.monitored(data)
    assert df.filter(pl.col("ms") == "NeRDA")["asset_uid"].to_list() == ["SSEN:SEPD|a"]
