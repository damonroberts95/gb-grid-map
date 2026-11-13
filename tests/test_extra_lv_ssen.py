"""SSEN part of lv_smart from tiny real fixtures (no network, no real data dir)."""

import json
import shutil
from pathlib import Path

import polars as pl

from gridmap.extra import Ctx, lv_smart
from gridmap.normalise import common

FX = Path(__file__).parent / "fixtures" / "extra" / "lv_ssen"


def _snap(root: Path, sid: str, files: list[Path], meta: dict) -> None:
    d = root / "raw" / sid / "20261112T000000Z"
    d.mkdir(parents=True)
    for f in files:
        shutil.copy(f, d / f.name)
    (d / "meta.json").write_text(json.dumps({"status": "ok", **meta}))


def _setup(root: Path, monkeypatch, with_site: bool) -> list[dict]:
    monkeypatch.setattr(common, "DATA_DIR", root)
    _snap(root, "ssen_sm_gql", sorted(FX.glob("*.json.gz")), {"as_of": "2024-09-04"})
    _snap(root, "os_codepoint", [FX / "codepoint.zip"], {})
    # the first substation's postcode centroid decides where a "nearby" site goes
    rows = lv_smart._ssen(root)  # no sites yet: both estimated
    if with_site:
        first = rows[0]
        (root / "norm" / "SSEN").mkdir(parents=True)
        pl.DataFrame({"lat": [first["la"] + 0.0005], "lon": [first["lo"]]}).write_parquet(
            root / "norm" / "SSEN" / "lv_site.parquet")
    return rows


def test_ssen_aggregation_and_centroid(tmp_path, monkeypatch):
    rows = _setup(tmp_path / "data", monkeypatch, with_site=False)
    assert len(rows) == 2
    r = rows[0]
    assert r["op"] == "SSEN" and r["as_of"] == "2024-09-04" and r["mo"] == "2024-09"
    assert r["loc"] == "estimated from customer postcodes"
    assert 56 < r["la"] < 57 and -3.5 < r["lo"] < -2.5  # Dundee
    assert r["pk"] > 0 and r["av"] > 0 and r["pk"] >= r["av"]
    assert r["d"] and r["pt"][:10] == "2024-09-04"


def test_ssen_snaps_to_nearby_site(tmp_path, monkeypatch):
    root = tmp_path / "data"
    est = _setup(root, monkeypatch, with_site=True)
    rows = lv_smart._ssen(root)
    assert rows[0]["loc"].startswith("snapped")
    assert abs(rows[0]["la"] - (est[0]["la"] + 0.0005)) < 1e-9  # ~55 m away, inside the 250 m radius
    assert rows[1]["loc"].startswith("estimated")


def test_ssen_build_adds_features(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _setup(root, monkeypatch, with_site=False)
    monkeypatch.setattr(lv_smart, "to_pmtiles", lambda feats, tmp, name, *a: feats)
    out = lv_smart.build(Ctx(root, tmp_path))
    feats = out["lv_smart"]
    ssen = [f["properties"] for f in feats if f["properties"]["op"] == "SSEN"]
    assert len(ssen) == 2
    assert ssen[0]["as_of"] == "2024-09-04" and ssen[0]["pm"] > 0 and "loc" in ssen[0]
    assert "SSEN" in lv_smart.LAYERS[0].description and "2024" in lv_smart.LAYERS[0].description
