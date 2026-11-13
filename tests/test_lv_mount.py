"""Mount type (ground/pole) mapping and kVA inference for the lv tiles."""

import polars as pl

from gridmap.extra.lv_common import with_mount


def mt(rows):
    df = pl.DataFrame({"native_ids": [r[0] for r in rows], "transformer_kva": [r[1] for r in rows]},
                      schema={"native_ids": pl.Utf8, "transformer_kva": pl.Float64})
    out = with_mount(df)
    return list(zip(out["mt"].to_list(), out["mti"].to_list(), strict=True))


def test_published_values():
    rows = [('{"type": "Pole Mtd Dist. Substation"}', 500.0), ('{"type": "Grd Mtd Dist. Substation"}', 15.0),
            ('{"type": "Pole Mounted Distribution"}', None), ('{"type": "Ground Mounted Distribution"}', None),
            ('{"type": "Secondary (Pad Mounted)"}', None), ('{"type": "Buried Distribution"}', None),
            ('{"mount": "GROUND MOUNTED"}', 15.0), ('{"mount": "POLE MOUNTED"}', 900.0),
            ('{"design": "PMT"}', 900.0), ('{"design": "GMT"}', 15.0),
            ('{"site_type": "Pole Mounted"}', None), ('{"site_type": "Ground Mounted"}', None)]
    assert mt(rows) == [("p", None), ("g", None), ("p", None), ("g", None), ("g", None), ("g", None),
                        ("g", None), ("p", None), ("p", None), ("g", None), ("p", None), ("g", None)]


def test_kva_inference():
    rows = [('{"number": "1"}', 50.0), ('{"number": "1"}', 100.0), ('{"number": "1"}', 150.0),
            ('{"number": "1"}', 200.0), ('{"number": "1"}', 201.0), ('{"number": "1"}', None),
            (None, 500.0), ('{"type": "Hv Switching Station"}', 25.0)]
    assert mt(rows) == [("p", 1), ("p", 1), (None, None), (None, None), ("g", 1), (None, None),
                        ("g", 1), ("p", 1)]
