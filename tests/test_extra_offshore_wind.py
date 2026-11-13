import json
import struct
import zipfile
from pathlib import Path

from gridmap.extra import offshore_common as oc
from gridmap.extra import offshore_wind as w

FX = Path(__file__).parent / "fixtures" / "extra" / "offshore"


def _fixture(monkeypatch):
    def read(sid):
        f = FX / f"{sid}.json"
        return json.loads(f.read_text())["features"] if f.exists() else []
    monkeypatch.setattr(w, "read_geojson", read)


def test_layers_declared():
    ids = {m.id for m in w.LAYERS}
    assert ids == {"offshore_wind", "offshore_cable_zones", "offshore_ssep"}
    for m in w.LAYERS:
        assert m.description and m.attribution and not m.default_on and m.source_layer == m.id
    assert "Crown Estate" in w.LAYERS[0].attribution
    assert "Contains data provided by The Crown Estate that is protected by copyright and database rights." in w.TCE


def test_norm_status():
    assert oc.norm_status("Active/In Operation") == "Operational"
    assert oc.norm_status("Government Support on Offer") == "Consented"
    assert oc.norm_status("Pre Planning") == "In planning"
    assert oc.norm_status("Under Construction") == "Under construction"
    assert oc.norm_status("Inactive/Decommissioned") == "Decommissioned"


def test_pip_and_rep_point_with_hole():
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                                              [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
    assert oc.pip((1, 1), sq) and not oc.pip((5, 5), sq) and not oc.pip((11, 1), sq)
    assert oc.pip(w.rep_point(sq), sq)  # bbox centre is in the hole, so a fallback point is chosen


def test_wind_areas_merge_and_capacity(monkeypatch):
    _fixture(monkeypatch)
    areas = w.wind_areas()
    by = {f["properties"]["n"]: f["properties"] for f in areas}
    assert by["Walney 2"]["s"] == "Operational" and by["Walney 2"]["rd"] == "Round 2"
    assert by["Robin Rigg East"]["mw"] == 84  # Crown Estate Scotland's own figure
    assert "Berwick Bank OFTO" not in by  # OFTO leases go to the cable-zone layer
    assert any(p["s"] == "Lease / option round" for p in by.values())
    w.add_capacity(areas, w._emodnet(), [])
    by = {f["properties"]["n"]: f["properties"] for f in areas}
    assert by["Robin Rigg East"]["src"] == "Crown Estate Scotland"
    assert by["Walney 2"]["src"] == "EMODnet" and by["Walney 2"]["mw"] > 0
    assert all("src_org" in f["properties"] for f in areas)


def test_cable_zones(monkeypatch):
    _fixture(monkeypatch)
    zones = w.cable_zones()
    kinds = {f["properties"]["k"] for f in zones}
    assert {"Wind export cable", "OFTO transmission"} <= kinds
    assert all(f["properties"]["s"] for f in zones)


def _square_shp(tmp: Path) -> Path:
    """Minimal polygon shapefile in BNG: one 10 km square (clockwise) with a 2 km hole (anticlockwise)."""
    outer = [(400000, 400000), (400000, 410000), (410000, 410000), (410000, 400000), (400000, 400000)]
    hole = [(404000, 404000), (406000, 404000), (406000, 406000), (404000, 406000), (404000, 404000)]
    pts = outer + hole
    body = struct.pack("<i4d2i", 5, 400000, 400000, 410000, 410000, 2, len(pts)) + struct.pack("<2i", 0, len(outer))
    body += b"".join(struct.pack("<2d", *p) for p in pts)
    shp = b"\0" * 100 + struct.pack(">2i", 1, len(body) // 2) + body
    name = b"PUB_ZN_ID\0\0"
    desc = name + b"C" + b"\0" * 4 + bytes([8, 0]) + b"\0" * 14 + b"\r"
    rec = b" " + b"SCOT_1  "
    dbf = struct.pack("<4BIHH20x", 3, 0, 0, 0, 1, 32 + len(desc), len(rec)) + desc + rec
    z = tmp / "zones.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("zones.shp", shp)
        zf.writestr("zones.dbf", dbf)
    return z


def test_read_polygon_zip_assigns_holes(tmp_path):
    rows = list(oc.read_polygon_zip(_square_shp(tmp_path), 27700, {"PUB_ZN_ID"}))
    assert len(rows) == 1
    row, g = rows[0]
    assert row["PUB_ZN_ID"] == "SCOT_1"
    assert g["type"] == "MultiPolygon" and len(g["coordinates"]) == 1 and len(g["coordinates"][0]) == 2
    lon, lat = g["coordinates"][0][0][0]
    assert -3 < lon < -1 and 53 < lat < 54  # BNG 400000,400000 is near Manchester
