"""NIE Networks adapter on a small real-shaped fixture (no network)."""

from pathlib import Path

import polars as pl

from gridmap.normalise import nie

FX = Path(__file__).parent / "fixtures" / "norm" / "nie"


def _df(name: str) -> pl.DataFrame:
    return nie.load_csv(FX / name)


def test_assets_levels_coords_kv():
    a = nie.build_assets(_df("sites.csv"), "src", "2026-11-25")
    rows = {r["name"]: r for r in a.iter_rows(named=True)}
    assert set(rows) == {"TEST GSP", "TEST PRIMARY", "BAD COORDS"}
    assert rows["TEST GSP"]["kind"] == "GSP" and rows["TEST GSP"]["kv_hv"] == 275.0
    assert rows["TEST GSP"]["asset_uid"] == "NIE:NI-1" and rows["TEST GSP"]["operator"] == "NIE"
    p = rows["TEST PRIMARY"]  # no geopoint: Irish Grid fallback
    assert p["kind"] == "PRIMARY" and p["coord_quality"] == "from_irish_grid" and 54 < p["lat"] < 55.5
    assert rows["BAD COORDS"]["lat"] is None  # (0,0) rejected, not placed


def test_lv_groups_sites_and_converts_units():
    lv = nie.build_lv(_df("secondary.csv"), "src", None)
    rows = {r["name"]: r for r in lv.iter_rows(named=True)}
    assert set(rows) == {"TEST SEC", "NO RATING"}
    assert rows["TEST SEC"]["transformer_kva"] == 815.0  # 500 kVA + 0.315 MVA
    assert rows["TEST SEC"]["kv_hv"] == 11.0 and rows["TEST SEC"]["asset_uid"] == "NIE:S-1"
    assert rows["NO RATING"]["transformer_kva"] is None  # null, not 0
    assert lv["customers"].null_count() == 2 and lv["utilisation_pct"].null_count() == 2


def test_ni_coords_rejects_outside_ni():
    assert nie.ni_coords("51.5, -0.1", None, None)[0] is None
    assert nie.ni_coords("54.6, -5.93", None, None) == (54.6, -5.93, "native_wgs84")
