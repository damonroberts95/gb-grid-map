from pathlib import Path

import polars as pl

from gridmap.extra import gen_planning as g

FX = Path(__file__).parent / "fixtures" / "extra" / "gen_planning"


def test_layers_declared():
    ids = {m.id for m in g.LAYERS}
    assert ids == {"repd", "ecr_projects", "tec"}
    for m in g.LAYERS:
        assert m.description and m.attribution and not m.default_on
        assert m.type != "pmtiles" or m.source_layer == m.id


def test_tech_class():
    assert g.tech_class("Solar Photovoltaics") == "Solar"
    assert g.tech_class("Wind Offshore") == "Offshore wind"
    assert g.tech_class("Wind Onshore") == "Onshore wind"
    assert g.tech_class("BESS") == "Battery / storage"
    assert g.tech_class("Pumped Storage Hydroelectricity") == "Hydro"
    assert g.tech_class("Anaerobic Digestion") == "Biomass & waste"
    assert g.tech_class(None) == "Other"


def test_repd_filters_and_maps():
    feats, st = g.repd_features(FX / "repd.csv")
    assert st["rows"] == 30
    assert st["kept"] == len(feats) > 0
    statuses = {f["properties"]["s"] for f in feats}
    assert not statuses & {"Application Refused", "Revised"}
    assert "Operational" in statuses
    for f in feats:
        lon, lat = f["geometry"]["coordinates"]
        assert 49.8 <= lat <= 60.9 and -8.7 <= lon <= 1.8
        assert f["properties"]["t"] in {c[0] for c in g.TECH_COLORS}
    assert st["ni"] >= 1 and st["dropped_status"] >= 1


def test_ecr_features_skips_import_only_and_no_coords():
    df = pl.read_parquet(FX / "queue.parquet")
    feats = g.ecr_features(df)
    assert 0 < len(feats) < df.height
    assert all(f["properties"]["mw"] > 0 for f in feats)
    assert {f["properties"]["st"] for f in feats} <= {"connected", "accepted", "offered", "other"}


def test_tec_aggregates_per_matched_site():
    idx = {
        "berkswell": [{"site_id": "NESO:berkswell", "operator": "NESO", "name": "Berkswell", "kind": "GSP",
                       "lat": 52.4, "lon": -1.6}],
        "bolney": [{"site_id": "NESO:bolney", "operator": "NESO", "name": "Bolney", "kind": "GSP",
                    "lat": 50.9, "lon": -0.2}],
    }
    feats, st = g.tec_features(FX / "tec.csv", idx)
    assert st["rows"] == 13 and 0 < st["rows_matched"] < st["rows"]
    assert 0 < st["mw_matched"] <= st["mw_total"]
    by = {f["properties"]["n"]: f["properties"] for f in feats}
    assert set(by) <= {"Berkswell", "Bolney"} and by
    p = next(iter(by.values()))
    assert p["np"] >= 1 and "MW" in p["top"]


def test_match_site_prefers_gsp_and_host():
    idx = {"x": [
        {"site_id": "a", "operator": "SPEN", "name": "X", "kind": "BSP", "lat": 1, "lon": 1},
        {"site_id": "b", "operator": "NESO", "name": "X", "kind": "GSP", "lat": 1, "lon": 1},
    ]}
    assert g.match_site("X 132kV Substation", "SPT", idx)["site_id"] == "b"
    assert g.match_site("nowhere", None, idx) is None
