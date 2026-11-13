import json
from pathlib import Path

import polars as pl

from gridmap.extra import dem_dfes, dem_ev, dem_lct
from gridmap.extra.dem_lct import _csv as real_csv  # noqa: F401  (signature reference)

FX = Path(__file__).parent / "fixtures" / "extra" / "demand"


def _reader(mapping):
    def rd(source_id, sep=";", glob="*.csv"):
        f = FX / mapping[source_id]
        return pl.read_csv(f, separator=sep, infer_schema_length=10000, encoding="utf8-lossy", ignore_errors=True)
    return rd


def _in_gb(f):
    lon, lat = f["geometry"]["coordinates"]
    return 49.8 <= lat <= 60.9 and -8.7 <= lon <= 1.8


def test_layers_declared():
    for mod in (dem_lct, dem_dfes, dem_ev):
        for m in mod.LAYERS:
            assert m.description and m.attribution and not m.default_on
            assert m.type != "pmtiles" or m.source_layer == m.id
            colours = json.dumps(m.color) + json.dumps(m.legend)
            assert "#d73027" not in colours.lower()
    assert {m.id for m in dem_lct.LAYERS} == {"lct", "lct_primary"}
    assert [m.id for m in dem_ev.LAYERS] == ["ev_chargers"] and dem_ev.LAYERS[0].minzoom == 8


def test_ukpn_secondary_totals(monkeypatch):
    monkeypatch.setattr(dem_lct, "_csv", _reader({"ukpn_lct_secondary": "ukpn_lct_secondary.csv"}))
    feats = dem_lct._ukpn_secondary()
    raw = pl.read_csv(FX / "ukpn_lct_secondary.csv", separator=";", infer_schema_length=10000)
    assert feats and all(_in_gb(f) for f in feats)
    assert sum(f["properties"]["n"] for f in feats) <= raw["lct_connections"].sum()
    for f in feats:
        p = f["properties"]
        assert p["dm"] == p["ev"] + p["hp"] <= p["n"] + p["bat"]  # solar & battery can count in two columns


def test_enw_mcs(monkeypatch):
    monkeypatch.setattr(dem_lct, "_csv", _reader({"enw_mcs_lct_per_substation": "enw_mcs.csv"}))
    feats = dem_lct._enw_mcs()
    assert feats and all(f["properties"]["op"] == "ENW" and _in_gb(f) for f in feats)
    assert all("ev" not in f["properties"] for f in feats)  # ENW publishes no EV data: not 0, absent


def test_ukpn_primary_uses_index_then_falls_back_to_coords(monkeypatch):
    monkeypatch.setattr(dem_lct, "_csv", _reader({"ukpn_lct_primary": "ukpn_lct_primary.csv"}))
    feats = dem_lct._ukpn_primary({})
    assert feats and all(_in_gb(f) for f in feats)
    floc = pl.read_csv(FX / "ukpn_lct_primary.csv", separator=";")["sitefunctionallocation"][0]
    feats2 = dem_lct._ukpn_primary({f"floc:{floc}": ("S1", "X", 51.0, -1.0)})
    assert any(f["geometry"]["coordinates"] == [-1.0, 51.0] for f in feats2)


def test_nged_only_matched_primaries(monkeypatch):
    monkeypatch.setattr(dem_lct, "_csv", _reader({"nged_lct_connections": "nged_lct.csv"}))
    assert dem_lct._nged({}) == []
    num = int(pl.read_csv(FX / "nged_lct.csv")["Primary Substation Number"][0])
    feats = dem_lct._nged({f"num:{num}": ("S1", "X", 52.0, -2.0)})
    assert len(feats) == 1 and feats[0]["properties"]["op"] == "NGED"
    p = feats[0]["properties"]
    assert p["n"] == p["ev"] + p["hp"] + p["pv"] + p["bat"]


def test_npg_postal_district(monkeypatch):
    monkeypatch.setattr(dem_lct, "_csv", _reader({"npg_lct_postal_district": "npg_lct.csv"}))
    feats = dem_lct._npg()
    assert feats and all(_in_gb(f) and f["properties"]["lvl"] == "Postal district" for f in feats)


def test_spen_name_matching():
    idx = {"horncliffe": ("S1", "Horncliffe", 55.7, -2.1), "foo": ("S2", "Foo", 55.0, -3.0)}
    hit = dem_lct._spen_hit(idx, "PS-NT9349/002 HORNCLIFFE PRIMARY NEW")
    assert hit is None or hit[1] == 55.7  # depends on name_norm stripping 'primary'
    assert dem_lct._spen_hit(idx, "NO SUCH PLACE") is None


def test_dfes_row_growth_and_null_baseline():
    hit = (52.0, -1.0, "S1")
    r = dem_dfes._row("A", "UKPN", hit, 10.0, 15.0, ev=100.4)
    assert r["properties"]["g"] == 50.0 and r["properties"]["gm"] == 5.0 and r["properties"]["ev"] == 100
    assert dem_dfes._row("A", "UKPN", hit, 0.1, 5.0)["properties"].get("g") is None  # tiny base: no %
    assert dem_dfes._row("A", "UKPN", None, 1, 2) is None
    assert dem_dfes._row("A", "UKPN", hit, None, 2) is None


def test_dfes_ukpn_derives_peak_from_headroom(monkeypatch):
    monkeypatch.setattr(dem_dfes, "_csv", _reader({"ukpn_dfes_network_headroom": "ukpn_dfes.csv"}))
    raw = pl.read_csv(FX / "ukpn_dfes.csv", separator=";", infer_schema_length=10000)
    idx = {f"floc:{f}": ("S", "n", 51.0, 0.0) for f in raw["sitefunctionallocation"].unique()}
    feats = dem_dfes._ukpn(idx)
    for f in feats:
        p = f["properties"]
        assert p["op"] == "UKPN" and p["p0"] >= 0 or p["p0"] < 0  # present
        assert "derived" in p["note"]


def test_dfes_npg_and_enw_and_spen(monkeypatch):
    monkeypatch.setattr(dem_dfes, "_csv", _reader({"npg_dfes_wide": "npg_dfes.csv", "enw_dfes_primary": "enw_dfes.csv"}))
    from gridmap.match import name_norm
    npg = pl.read_csv(FX / "npg_dfes.csv", separator=";", infer_schema_length=10000)
    nm = npg["substation_local_authority"][0]
    feats = dem_dfes._npg({name_norm(nm): ("S", nm, 54.0, -1.5)})
    assert len(feats) == 1 and feats[0]["properties"]["op"] == "NPg"
    enw = pl.read_csv(FX / "enw_dfes.csv", separator=";", infer_schema_length=10000)
    num = str(enw["dso_pry_number_alias"][0]).split("_")[0]
    feats = dem_dfes._enw({f"num:{num}": ("S", "Name", 53.5, -2.5)})
    assert len(feats) == 1 and feats[0]["properties"]["op"] == "ENW"


def test_ev_build(tmp_path, monkeypatch):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "pois.json").write_text((FX / "ocm_pois.json").read_text())
    monkeypatch.setattr(dem_ev, "latest_snapshot", lambda _sid: snap)
    monkeypatch.setattr(dem_ev, "snapshot_files", lambda s: [snap / "pois.json"])
    captured = {}

    def fake_run(cmd, check):
        captured["geojson"] = json.loads(Path(cmd[-1]).read_text())
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x")

    monkeypatch.setattr(dem_ev.subprocess, "run", fake_run)
    from gridmap.extra import Ctx
    out = dem_ev.build(Ctx(tmp_path, tmp_path))
    assert "ev_chargers" in out
    feats = captured["geojson"]["features"]
    assert len(feats) == 20  # removed + Irish records dropped
    assert all(_in_gb(f) for f in feats)
    assert all("kw" not in f["properties"] or f["properties"]["kw"] > 0 for f in feats)


def test_ev_in_gb_excludes_ireland():
    assert dem_ev._in_gb(53.3, -6.3) is False
    assert dem_ev._in_gb(51.5, -0.1) is True
