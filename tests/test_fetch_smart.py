"""smart_agg and ssen_gql fetchers: respx mocks, no network."""

import asyncio
import gzip
import json

import duckdb
import pytest
import respx

from gridmap.fetch import common
from gridmap.fetch.models import Source
from gridmap.fetch.runner import arun
from gridmap.fetch.store import Store

ODS = "https://spenergynetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/spm-feeder"
NGED = "https://connecteddata.nationalgrid.co.uk"
GQL = "https://gql.example.test/v1/graphql"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(common, "BACKOFF", 0)
    monkeypatch.setattr(common, "POST_GAP", 0)
    from gridmap.fetch import smart_agg
    monkeypatch.setattr(smart_agg, "PART_GAP", 0)


def go(tmp_path, src, env=None, **kw):
    out = asyncio.run(arun(store=Store(tmp_path), registry=[src], env=env or {}, **kw))
    return out["runs"][0]


def agg(tmp_path, sid):
    snap = max((tmp_path / "raw" / sid).iterdir())
    assert (snap / "meta.json").exists()
    return duckdb.sql("select * from read_parquet(?) order by sub_id", params=[str(snap / "agg.parquet")]).fetchall(), snap


def test_ods_grouped_export_is_reduced(tmp_path):
    src = Source(id="spen_agg", operator="SPEN", portal="smart_agg", base_url="https://spenergynetworks.opendatasoft.com",
                 dataset="spm-feeder", select={"kind": "ods"}, category="lv_usage", cadence="30d", licence="x",
                 attribution="x", gated=True, auth_env="SPEN_API_KEY")
    csv = ("secondary_substation_id;secondary_substation_name;substation_geo_location;ts;wh;devs;nfeed\n"
           "S1;One;(100, 200);2026-09-01T00:00:00;10;5;2\n"
           "S1;One;(100, 200);2026-09-01T00:30:00;30;6;2\n"
           "S2;Two;(300, 400);2026-09-01T00:00:00;7;3;1\n")
    with respx.mock:
        respx.get(ODS).respond(json={"metas": {"default": {"modified": "2026-10-01"}}, "data_visible": True})
        exp = respx.get(f"{ODS}/exports/csv").respond(content=csv.encode())
        row = go(tmp_path, src, env={"SPEN_API_KEY": "k"})
        assert exp.calls.last.request.headers["authorization"] == "Apikey k"
        assert "group_by" in str(exp.calls.last.request.url)
        again = go(tmp_path, src, env={"SPEN_API_KEY": "k"})
    assert row["status"] == "ok" and row["rows"] == 2
    rows, snap = agg(tmp_path, "spen_agg")
    s1 = rows[0]
    assert s1[0] == "S1" and s1[3] == 2 and s1[4] == 40 and s1[5] == 30 and s1[7] == 6
    assert not any(p.name.endswith(".csv") for p in snap.iterdir())
    assert again["status"] == "unchanged"


def test_ods_gated_without_key(tmp_path):
    src = Source(id="spen_agg", operator="SPEN", portal="smart_agg", base_url="https://spenergynetworks.opendatasoft.com",
                 dataset="spm-feeder", select={"kind": "ods"}, category="lv_usage", cadence="30d", licence="x",
                 attribution="x", gated=True, auth_env="SPEN_API_KEY")
    assert go(tmp_path, src)["status"] == "needs_key"


def test_ckan_parts_latest_month_only(tmp_path):
    src = Source(id="nged_agg", operator="NGED", portal="smart_agg", base_url=NGED, dataset="agg",
                 select={"kind": "ckan_parts"}, category="lv_usage", cadence="30d", licence="x", attribution="x",
                 gated=True, auth_env="NGED_API_KEY", max_mb=10)

    def part(rows):
        head = ("secondary_substation_id,secondary_substation_name,substation_geo_location,"
                "data_collection_log_timestamp,Total_consumption_active_import,aggregated_device_count_Active\n")
        return gzip.compress((head + "\n".join(rows) + "\n").encode())

    res = [{"name": f"Agg-Substation-{m}-part{i}.csv.gz", "url": f"https://dl.example.test/{m}-{i}.gz", "size": 100}
           for m in ("2026-08", "2026-09") for i in (0, 1)]
    with respx.mock:
        respx.get(f"{NGED}/api/3/action/package_show").respond(
            json={"success": True, "result": {"metadata_modified": "m1", "resources": res}})
        respx.get("https://dl.example.test/2026-09-0.gz").respond(
            content=part(["1,A,(1.0 2.0),2026-09-01 00:00:00,10,4", "1,A,(1.0 2.0),2026-09-01 00:30:00,50,5"]))
        respx.get("https://dl.example.test/2026-09-1.gz").respond(
            content=part(["1,A,(1.0 2.0),2026-09-01 01:00:00,20,6", "2,B,(3.0 4.0),2026-09-01 00:00:00,5,1"]))
        old = respx.get("https://dl.example.test/2026-08-0.gz").respond(content=b"nope")
        row = go(tmp_path, src, env={"NGED_API_KEY": "k"})
        assert not old.called
    assert row["status"] == "ok" and row["rows"] == 2
    rows, _ = agg(tmp_path, "nged_agg")
    assert rows[0][0] == "1" and rows[0][3] == 3 and rows[0][5] == 50 and rows[0][7] == 6


def test_ckan_parts_redacted_needs_key(tmp_path):
    src = Source(id="nged_agg", operator="NGED", portal="smart_agg", base_url=NGED, dataset="agg",
                 select={"kind": "ckan_parts"}, category="lv_usage", cadence="30d", licence="x", attribution="x")
    res = [{"name": "A-Substation-2026-09-part0.csv.gz", "url": "redacted"}]
    with respx.mock:
        respx.get(f"{NGED}/api/3/action/package_show").respond(
            json={"success": True, "result": {"metadata_modified": "m", "resources": res}})
        assert go(tmp_path, src)["status"] == "needs_key"


def test_ssen_gql_pages_and_unchanged(tmp_path, monkeypatch):
    from gridmap.fetch import ssen_gql
    monkeypatch.setattr(ssen_gql, "PAGE", 2)
    src = Source(id="ssen_gql_t", operator="SSEN", portal="ssen_gql", base_url=GQL, category="lv_usage",
                 cadence="30d", licence="x", attribution="x", params={"day": "2024-09-04"})
    lookup = [{"dataset_id": f"d{i}", "postcode": "AB1 1AA", "total_mpan_count": "3",
               "primary_substation_name": "P", "secondary_substation_name": "S"} for i in range(3)]
    usage = [{"dataset_id": "d0", "total_consumption_active_import": "5", "aggregated_device_count_active": "2",
              "data_collection_log_timestamp": "2024-09-04T00:00:00Z"}]

    def handler(request):
        q = json.loads(request.content)["query"]
        import httpx
        if "_aggregate" in q:
            n = 3 if "lookup" in q else (1 if "T00:00:00Z" in q else 0)
            tab = ssen_gql.LOOKUP if "lookup" in q else ssen_gql.USAGE
            return httpx.Response(200, json={"data": {f"{tab}_aggregate": {"aggregate": {"count": n}}}})
        if "lookup" in q:
            off = int(q.split("offset:")[1].split(",")[0])
            return httpx.Response(200, json={"data": {ssen_gql.LOOKUP: lookup[off:off + 2]}})
        return httpx.Response(200, json={"data": {ssen_gql.USAGE: usage}})

    with respx.mock:
        route = respx.post(GQL).mock(side_effect=handler)
        row = go(tmp_path, src)
        n_calls = route.call_count
        again = go(tmp_path, src)
        assert route.call_count == n_calls
    assert row["status"] == "ok", row["note"]
    assert row["rows"] == 4 and row["as_of"] == "2024-09-04"
    snap = max((tmp_path / "raw" / "ssen_gql_t").iterdir())
    assert sorted(p.name for p in snap.glob("*.json.gz")) == ["lookup_0000.json.gz", "lookup_0001.json.gz", "usage_00_0000.json.gz"]
    assert again["status"] == "unchanged"


def test_ocm_splits_boxes_and_needs_key(tmp_path, monkeypatch):
    from gridmap.fetch import ocm
    monkeypatch.setattr(ocm, "CAP", 2)
    monkeypatch.setattr(ocm, "MIN_ROWS", 2)
    src = Source(id="ocm_t", operator="OCM", portal="ocm", base_url="https://ocm.example.test/v3", category="chargers",
                 cadence="7d", licence="x", attribution="x", gated=True, auth_env="OCM_API_KEY")
    assert go(tmp_path, src)["status"] == "needs_key"

    def handler(request):
        import httpx
        box = request.url.params["boundingbox"]
        full = box == f"({ocm.GB_BOX[0]},{ocm.GB_BOX[1]}),({ocm.GB_BOX[2]},{ocm.GB_BOX[3]})"
        ids = [1, 2] if full else [3 if box.startswith(f"({ocm.GB_BOX[0]},") else 4]
        return httpx.Response(200, json=[{"ID": i, "AddressInfo": {"Latitude": 51.0, "Longitude": -1.0},
                                          "Connections": [{"PowerKW": 7, "Quantity": 1}]} for i in ids])

    with respx.mock:
        respx.get("https://ocm.example.test/v3/poi").mock(side_effect=handler)
        respx.get("https://ocm.example.test/v3/referencedata").respond(
            json={"Operators": [{"ID": 1, "Title": "Op"}], "UsageTypes": [{"ID": 1, "Title": "Public"}]})
        row = go(tmp_path, src, env={"OCM_API_KEY": "k"})
    assert row["status"] == "ok", row["note"]
    assert row["rows"] == 2  # the full box hit the cap and was split: only the two halves count
