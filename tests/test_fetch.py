"""Fetch framework tests: respx mocks + recorded fixtures, no live calls."""

import asyncio
import json
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from gridmap.fetch import ckan, common
from gridmap.fetch.models import Source, load_sources
from gridmap.fetch.runner import arun
from gridmap.fetch.store import Store

FIX = Path(__file__).parent / "fixtures"
UKPN = "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets"
NGED = "https://connecteddata.nationalgrid.co.uk"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(common, "BACKOFF", 0)


def ods(id_: str, dataset: str, **kw) -> Source:
    base = {"id": id_, "operator": "UKPN", "portal": "opendatasoft", "base_url": "https://ukpowernetworks.opendatasoft.com",
                "dataset": dataset, "category": "queue", "cadence": "1d", "licence": "CC BY 4.0", "attribution": "x",
                "auth_env": "UKPN_API_KEY"}
    return Source(**{**base, **kw})


def ckan_src(id_: str, dataset: str, **kw) -> Source:
    base = {"id": id_, "operator": "NGED", "portal": "ckan", "base_url": NGED, "dataset": dataset, "category": "headroom",
                "cadence": "1d", "licence": "NGED", "attribution": "x", "auth_env": "NGED_API_KEY"}
    return Source(**{**base, **kw})


def catalog(name: str, **patch) -> dict:
    doc = json.loads((FIX / "ods" / name).read_text())
    doc.update(patch)
    return doc


def status_of(summary: dict, source: str) -> dict:
    return next(r for r in summary["runs"] if r["source"] == source)


def run(tmp_path, sources, **kw):
    return asyncio.run(arun(store=Store(tmp_path), registry=sources, env=kw.pop("env", {}), **kw))


def test_open_dataset_ok(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/q").respond(json=catalog("catalog_open.json"))
        exp = respx.get(f"{UKPN}/q/exports/csv").respond(content=(FIX / "ods/open_export.csv").read_bytes())
        out = run(tmp_path, [ods("q", "q")])
        assert exp.calls.last.request.headers["user-agent"] == "gridmap-local/0.1"
    row = status_of(out, "q")
    assert (row["status"], row["rows"], row["expected_rows"]) == ("ok", 20, 20)
    snap = next((tmp_path / "raw" / "q").iterdir())
    meta = json.loads((snap / "meta.json").read_text())
    assert meta["rows"] == 20 and meta["sha256"] and meta["portal_modified"].startswith("2026-08-18")
    log = duckdb.connect(str(tmp_path / "grid.duckdb")).execute("select status, rows from source_run").fetchall()
    assert log == [("ok", 20)]


def test_header_only_csv_is_empty_or_gated(tmp_path):
    # Catalog claims visible (e.g. stale metadata) but the export is the HTTP-200 header-only trap.
    with respx.mock:
        respx.get(f"{UKPN}/g").respond(json=catalog("catalog_gated.json", data_visible=True))
        respx.get(f"{UKPN}/g/exports/csv").respond(content=(FIX / "ods/gated_export.csv").read_bytes())
        out = run(tmp_path, [ods("g", "g")])
    assert status_of(out, "g")["status"] == "empty_or_gated"
    assert not (tmp_path / "raw" / "g").exists()  # bad snapshot discarded


def test_row_count_mismatch_is_flagged(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/q").respond(json=catalog("catalog_open.json"))
        respx.get(f"{UKPN}/q/exports/csv").respond(content=b"a;b\n1;2\n")
        out = run(tmp_path, [ods("q", "q")])
    row = status_of(out, "q")
    assert row["status"] == "empty_or_gated" and "records_count" in row["note"]


def test_data_visible_false_without_key_is_needs_key(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/g").respond(json=catalog("catalog_gated.json"))
        exp = respx.get(f"{UKPN}/g/exports/csv").respond(content=b"x\n")
        out = run(tmp_path, [ods("g", "g")])
    assert status_of(out, "g")["status"] == "needs_key"
    assert not exp.called


def test_key_sent_as_apikey_header(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/q").respond(json=catalog("catalog_open.json"))
        exp = respx.get(f"{UKPN}/q/exports/csv").respond(content=(FIX / "ods/open_export.csv").read_bytes())
        run(tmp_path, [ods("q", "q")], env={"UKPN_API_KEY": "secret"})
        assert exp.calls.last.request.headers["authorization"] == "Apikey secret"


def test_gated_flag_without_key_makes_no_calls(tmp_path):
    with respx.mock:  # any request would raise: nothing is mocked
        out = run(tmp_path, [ods("g", "g", gated=True)])
    assert status_of(out, "g")["status"] == "needs_key"


def test_unchanged_modified_skips_download(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/q").respond(json=catalog("catalog_open.json"))
        exp = respx.get(f"{UKPN}/q/exports/csv").respond(content=(FIX / "ods/open_export.csv").read_bytes())
        first = run(tmp_path, [ods("q", "q")])
        second = run(tmp_path, [ods("q", "q")])
        assert exp.call_count == 1
        forced = run(tmp_path, [ods("q", "q")], force=True)
        assert exp.call_count == 2
    assert status_of(first, "q")["status"] == "ok"
    row = status_of(second, "q")
    assert (row["status"], row["rows"]) == ("unchanged", 20)
    assert status_of(forced, "q")["status"] == "ok"


def test_changed_modified_downloads_again(tmp_path):
    changed = catalog("catalog_open.json")
    changed["metas"]["default"]["modified"] = "2026-09-01T00:00:00+00:00"
    with respx.mock:
        respx.get(f"{UKPN}/q").mock(side_effect=[httpx.Response(200, json=catalog("catalog_open.json")),
                                                 httpx.Response(200, json=changed)])
        respx.get(f"{UKPN}/q/exports/csv").respond(content=(FIX / "ods/open_export.csv").read_bytes())
        run(tmp_path, [ods("q", "q")])
        out = run(tmp_path, [ods("q", "q")])
    assert status_of(out, "q")["status"] == "ok"


def test_json_attachments_heatmap(tmp_path):
    cat = catalog("catalog_heatmap.json")
    doc = {"issued": "2026-05-29", "Substations": [{"name": "a"}, {"name": "b"}]}
    with respx.mock:
        respx.get(f"{UKPN}/hm").respond(json=cat)
        for att in cat["attachments"]:
            if att["mimetype"] == "application/json":
                respx.get(att["url"]).respond(json=doc)
        src = ods("hm", "hm", portal="json_attachment", category="headroom", rows_path="Substations",
                  as_of_path="issued", select={"mimetype": "application/json"})
        out = run(tmp_path, [src])
    row = status_of(out, "hm")
    # 3 attachments x 2 rows = 6 vs catalog 967 -> flagged by the records_count check
    assert row["rows"] == 6 and row["as_of"] == "2026-05-29" and row["status"] == "empty_or_gated"


def test_ckan_redacted_url_is_needs_key(tmp_path):
    pkg = json.loads((FIX / "ckan/package_redacted.json").read_text())
    with respx.mock:
        respx.get(f"{NGED}/api/3/action/package_show").respond(json=pkg)
        out = run(tmp_path, [ckan_src("c", "nged-network-capacity", select={"format": "CSV"})])
    row = status_of(out, "c")
    assert row["status"] == "needs_key" and "redacted" in row["note"]


def test_ckan_open_package_downloads_all_resources(tmp_path):
    pkg = json.loads((FIX / "ckan/package_open.json").read_text())
    with respx.mock:
        route = respx.get(f"{NGED}/api/3/action/package_show").respond(json=pkg)
        for r in pkg["result"]["resources"]:
            respx.get(r["url"]).respond(json=[{"n": 1}, {"n": 2}])
        out = run(tmp_path, [ckan_src("c", "ltds-capacity-heatmap", select={"format": "JSON"})],
                        env={"NGED_API_KEY": "k"})
        assert route.calls.last.request.headers["authorization"] == "k"
    row = status_of(out, "c")
    assert (row["status"], row["rows"]) == ("ok", 8)


def _mrow(t, kind, desc, v, feeder=None):
    return {"Substation Name": "X", "Timestamp": t, "Attribute Type": kind, "Feeder ID": feeder, "Description": desc, "Value": v}


MONITOR_ROWS = [
    _mrow("2026-04-14T00:10:00", "busbar", "L1 Current (RMS)", 99.0),  # older timestamp is ignored
    _mrow("2026-04-14T00:30:00", "busbar", "L1 Current (RMS)", 5.0),  # newest but half written: ignored
    _mrow("2026-04-14T00:20:00", "busbar", "L1 Current (RMS)", 154.0),
    _mrow("2026-04-14T00:20:00", "busbar", "L2 Current (RMS)", 76.2),
    _mrow("2026-04-14T00:20:00", "busbar", "L1 Active Power", 34.5),
    _mrow("2026-04-14T00:20:00", "busbar", "L2 Active Power", 15.5),
    _mrow("2026-04-14T00:20:00", "busbar", "L1 Voltage (RMS)", 239.9),
    _mrow("2026-04-14T00:20:00", "transformer", "Temperature", 16.1),
    _mrow("2026-04-14T00:20:00", "feeder", "L1 Current (RMS)", 75.5, feeder=10),
    _mrow("2026-04-14T00:20:00", "feeder", "N Current (RMS)", 37.0, feeder=10),
]


def test_latest_reading_keeps_newest_complete_timestamp():
    got = ckan.latest_reading(MONITOR_ROWS)
    assert got["t"] == "2026-04-14T00:20:00" and got["temp"] == 16.1
    assert got["bus"] == {"i": [154.0, 76.2, None], "kw": 50.0, "v": [239.9, None, None]}
    assert got["fd"] == {"10": {"i": [75.5, None, None]}}  # neutral current is not a phase
    assert ckan.latest_reading([]) is None


def test_ckan_latest_mode_reads_datastore_not_files(tmp_path):
    res = [{"id": f"r{n}", "name": n, "format": "CSV", "url": f"https://x.test/{n}.csv", "size": 10**10} for n in ("1", "2")]
    pkg = {"success": True, "result": {"metadata_modified": "2026-09-24", "resources": res + [
        {"id": "p", "name": "Data Sharing Assessment", "format": "PDF", "url": "https://x.test/a.pdf"}]}}
    with respx.mock:
        respx.get(f"{NGED}/api/3/action/package_show").respond(json=pkg)
        ds = respx.get(f"{NGED}/api/3/action/datastore_search").mock(side_effect=lambda req: (
            httpx.Response(200, json={"result": {"records": MONITOR_ROWS}}) if req.url.params["resource_id"] == "r1"
            else httpx.Response(404)))
        out = run(tmp_path, [ckan_src("m", "lv-load-monitor-data", mode="latest", select={"format": "CSV"}, cadence="7d")],
                  env={"NGED_API_KEY": "k"})
        assert ds.calls.last.request.headers["authorization"] == "k"
        assert ds.calls.last.request.url.params["sort"] == "Timestamp desc"
    row = status_of(out, "m")
    assert (row["status"], row["rows"]) == ("ok", 1)  # 10 GB declared is ignored; the failed resource is left out
    snap = next((tmp_path / "raw" / "m").iterdir())
    latest = json.loads((snap / "latest.json").read_text())
    assert list(latest) == ["1"] and latest["1"]["bus"]["kw"] == 50.0


def test_http_retries_then_succeeds(tmp_path):
    src = Source(id="h", operator="ELEXON", portal="http", base_url="https://data.elexon.co.uk/bmrs/api/v1",
                 path="datasets/FUELINST", format="json", rows_path="data", category="live_national", cadence="5m",
                 licence="x", attribution="x")
    with respx.mock:
        route = respx.get("https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"data": [{"a": 1}]})])
        out = run(tmp_path, [src])
    assert route.call_count == 2
    assert status_of(out, "h")["rows"] == 1 and status_of(out, "h")["status"] == "ok"


def test_http_error_does_not_crash_run(tmp_path):
    src = Source(id="h", operator="CARBON", portal="http", base_url="https://api.carbonintensity.org.uk",
                 path="regional", format="json", category="live_national", cadence="30m", licence="x",
                 attribution="x")
    with respx.mock:
        respx.get("https://api.carbonintensity.org.uk/regional").respond(404)
        out = run(tmp_path, [src, ods("g", "g", gated=True)])
    assert status_of(out, "h")["status"] == "error" and out["counts"] == {"error": 1, "needs_key": 1}


def test_dry_run_downloads_nothing_and_logs_nothing(tmp_path):
    with respx.mock:
        respx.get(f"{UKPN}/q").respond(json=catalog("catalog_open.json"))
        out = run(tmp_path, [ods("q", "q")], dry_run=True)
    assert status_of(out, "q")["status"] == "dry_run" and status_of(out, "q")["rows"] == 20
    assert not (tmp_path / "grid.duckdb").exists()


def test_large_sources_skipped_unless_included(tmp_path):
    out = run(tmp_path, [ods("big", "big", large=True)])
    assert status_of(out, "big")["status"] == "skipped"


def test_registry_is_valid():
    sources = load_sources()
    assert len(sources) > 100
    assert len({s.id for s in sources}) == len(sources)
    assert all(s.auth_env for s in sources if s.gated)
