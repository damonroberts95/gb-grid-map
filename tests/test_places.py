import polars as pl
import pytest

from gridmap.server.places import Places, norm_key


@pytest.fixture()
def places(tmp_path):
    rows = [  # id, name, kind, context, ckey, rank
        ("a", "Oxford", "place", "Oxfordshire", "oxfordshire south east", 0),
        ("b", "Oxford Road", "road", "Manchester, M1", "manchester m1 north west", 6),
        ("c", "Oxford Road", "road", "Reading, RG1", "reading rg1 south east", 6),
        ("d", "Oxford Road East", "road", "Windsor, SL4", "windsor sl4 south east", 6),
        ("e", "Oxfordshire Way", "road", "Bicester, OX26", "bicester ox26 south east", 6),
    ]
    df = pl.DataFrame({
        "id": [r[0] for r in rows], "name": [r[1] for r in rows], "kind": [r[2] for r in rows],
        "context": [r[3] for r in rows], "lat": 51.0, "lon": -1.0, "key": [norm_key(r[1]) for r in rows],
        "ckey": [r[4] for r in rows], "rank": [r[5] for r in rows]})
    pc = pl.DataFrame({
        "id": ["pc:ox14sz", "pc:ox14sy", "pc:ox114aa"], "name": ["OX1 4SZ", "OX1 4SY", "OX11 4AA"],
        "kind": "postcode", "context": "Oxford", "lat": 51.7, "lon": -1.25, "key": ["ox14sz", "ox14sy", "ox114aa"],
        "ckey": "", "rank": 7})
    p = tmp_path / "places.parquet"
    pl.concat([df.with_columns(pl.col("rank").cast(pl.Int8)), pc.with_columns(pl.col("rank").cast(pl.Int8))]) \
        .sort("key", "rank").write_parquet(p)
    return Places(p)


def test_prefix_and_shape(places):
    r = places.search("oxfo", 10)
    assert r[0]["name"] == "Oxford"
    assert {"type", "id", "name", "op", "kind", "lat", "lon", "context"} == set(r[0])
    assert r[0]["type"] == "place" and r[0]["op"] is None


def test_exact_name_ranks_first(places):
    names = [x["name"] for x in places.search("oxford road", 10)]
    assert names[:2] == ["Oxford Road", "Oxford Road"] and names[2] == "Oxford Road East"


def test_multi_token_with_abbreviation(places):
    r = places.search("oxford rd manchester", 10)
    assert [x["id"] for x in r] == ["b"]
    assert [x["id"] for x in places.search("oxford road read", 10)] == ["c"]


def test_postcode_normalisation(places):
    for q in ("ox14sz", "OX1 4SZ", "ox1  4sz"):
        r = places.search(q, 5)
        assert r[0]["name"] == "OX1 4SZ" and r[0]["kind"] == "postcode" and r[0]["id"] == "pc:ox14sz"


def test_partial_postcode_respects_outward_code(places):
    assert {x["name"] for x in places.search("OX1 4", 10)} == {"OX1 4SZ", "OX1 4SY"}


def test_house_number_ignored_and_short_query(places):
    assert places.search("10 oxford road manchester", 5)[0]["id"] == "b"
    assert places.search("o", 5) == []
