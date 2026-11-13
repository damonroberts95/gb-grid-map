from pathlib import Path

from gridmap.extra import portal_consumption as p

FX = Path(__file__).parent / "fixtures" / "extra" / "portals"


def test_layer_declared():
    (m,) = p.LAYERS
    assert m.id == "elec_postcode_kwh" and m.type == "pmtiles" and m.source_layer == m.id and not m.default_on
    assert "Open Government Licence" in m.attribution and m.legend and m.description


def test_read_consumption_drops_summary_rows():
    df = p.read_consumption(FX / "postcode_elec.csv")
    assert "All postcodes" not in df["pc"].to_list()
    assert df.height == 23 and df["key"][0] == "AB101AU"
    assert df["kwh"].min() > 0 and df["m"].min() >= 5


def test_join_codepoint_and_sectors():
    df = p.read_consumption(FX / "postcode_elec.csv")
    cp = p.read_codepoint(FX / "codepoint.zip", {"AB"})
    pts = p.to_points(df.join(cp, on="key", how="inner"))
    assert pts.height > 20
    assert pts["lat"].min() > 57 and pts["lon"].max() < -2  # Aberdeen
    sec = p.sectors(pts)
    assert sec["pc"][0].startswith("AB10 ")
    assert abs(sec["kwh"][0] - pts["total"].sum() / pts["m"].sum()) < 1e-6 if sec.height == 1 else True
    assert sec["m"].sum() == pts["m"].sum()
