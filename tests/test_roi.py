"""Republic of Ireland: ESB heatmap adapter, page-link + ArcGIS fetchers, EirGrid transmission parse, ROI live
block, ROI outline dissolve, ESB PowerCheck parser, basemap bbox marker. Small inline fixtures, no live calls."""

import asyncio
import json

import openpyxl
import pytest
import respx

from gridmap import bootstrap
from gridmap.extra import dno_areas
from gridmap.extra import eirgrid_transmission as et
from gridmap.fetch import common, page_link
from gridmap.fetch.models import Source
from gridmap.fetch.runner import arun
from gridmap.fetch.store import Store
from gridmap.normalise import esb
from gridmap.server import live

HDR = ["Station Name", "Transformer GroupID", "Primary kV", "Secondary voltage(s)", "Voltage Class",
       "Transformer Configuration", "Installed Capacity MVA", "Demand Data", "Demand FirmCapacity MVA",
       "Demand Available MVA", "Parent Available MVA", "Demand Parent Constraint", "Generation Data",
       "Generation Firm Capacity MW", "Generation NonFirm Capacity MW", "Generation Total Committed MW",
       "Gen Available Firm MW", "Gen Available NonFirm MW", "Generation Parent Constraint", "General Data",
       "Parent Feeder", "Parent Station", "TSO Interface Station", "Comment", "Latitude", "Longitude"]


def _row(st, grp, pkv, vc, conf, inst, firm, avail, dem_note, gen_firm, committed, gen_avail, parent, tso, lat, lon,
         feeder=None):
    return [st, grp, pkv, "_", vc, conf, inst, " -->", firm, avail, "0", dem_note, " -->", gen_firm, 0, committed,
            gen_avail, 0, None, " -->", feeder, parent, tso, "", lat, lon]


ROWS = [
    _row("LIMERICK (100000) 110 kV STATION", "Limerick [T141,T142]", "110 kV", "38 kV", "2x63 MVA @ 38 kV", 126, 88.2,
         20.5, None, 77.8, 40, 30.8, "Transmission Source", "LIMERICK", 52.66, -8.63),
    _row("GARRYOWEN (067000) 38 kV STATION", "Garryowen [T41,T42]", "38 kV", "MV", "2x15 MVA @ 10 kV", 30, 22.5, 0,
         "Constrained, otherwise capacity available =4.37 MVA", 18.5, 2.7, 18.6, "Limerick [T141,T142]", "LIMERICK",
         52.663, -8.617),
    _row("MV/LV Substation BA***002", None, "10 kV", "LV", "400 kVA  :Kiosk Substation", 0.4, 0.4, 0,
         "Constrained, otherwise capacity available =100 kVA", 0.38, 0.02, 0.36, "Garryowen [T41,T42]", "LIMERICK",
         52.66, -8.61, "C1 ex GARRYOWEN"),
    _row("MV/LV Substation BA***003", None, "20 kV", "LV", "50 kVA  :Pole Substation", 0.05, 0.05, 0.03, None,
         0.04, 0, 0.04, "Garryowen [T41,T42]", "LIMERICK", 52.70, -8.60),
]


def _xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Heatmap data"
    ws.append([None, None, "Transformer Available Capacity"])
    ws.append(HDR)
    for r in ROWS:
        ws.append(r)
    ws.append(["_ESB note", "_HV header", "Report compiled 12/12/2025"])
    wb.save(path)
    return path


def test_esb_heatmap_mapping(tmp_path):
    rows = esb.read_rows(_xlsx(tmp_path / "h.xlsx"))
    assert esb.compiled_date(rows) == "2025-12-12"
    assets, caps = esb.build_hv(rows, "2025-12-12")
    a = {r["name"]: r for r in assets.to_dicts()}
    assert a["Limerick"]["kind"] == "GSP" and a["Limerick"]["kv_lv"] == 38.0
    g = a["Garryowen"]
    assert g["kind"] == "PRIMARY" and g["upstream_gsp"] == "Limerick" and g["kv_hv"] == 38.0 and g["kv_lv"] == 10.0
    assert g["asset_uid"] == "ESB:067000:Garryowen [T41,T42]"
    c = {r["asset_uid"]: r for r in caps.to_dicts()}[g["asset_uid"]]
    assert c["demand_headroom_mva"] == 0 and c["gen_headroom_mw"] == 18.6 and c["firm_capacity_mva"] == 22.5
    assert "otherwise capacity available" in c["upstream_constraint"]
    lv = {r["name"]: r for r in esb.build_lv(rows, "2025-12-12").to_dicts()}
    k = lv["MV/LV Substation BA***002"]
    assert k["transformer_kva"] == 400 and k["utilisation_pct"] == 75.0  # own headroom 100 of 400 kVA
    assert k["upstream_primary"] == "Garryowen"
    assert json.loads(k["native_ids"])["mount"] == "ground"
    p = lv["MV/LV Substation BA***003"]
    assert json.loads(p["native_ids"])["mount"] == "pole" and p["utilisation_pct"] == 40.0


def test_station_name_cleanup():
    assert esb.station_name("INCHICORE 220KV (964000) 220 kV STATION") == "Inchicore"
    assert esb.station_name("GRANGE (DR) (087000) 110 kV STATION") == "Grange (Dr)"


def test_page_link_picks_newest_xlsx():
    page = ('<a href="/a.pdf">x</a><a href="https://media.esbnetworks.ie/docs/customer-heatmap-download-july-2026.xlsx'
            '?sfvrsn=51c3b179_19">Download</a>')
    url = page_link.pick(page, "https://www.esbnetworks.ie/p", r"customer-heatmap-download[^\"]*\.xlsx")
    assert url == "https://media.esbnetworks.ie/docs/customer-heatmap-download-july-2026.xlsx?sfvrsn=51c3b179_19"


@pytest.fixture
def _no_backoff(monkeypatch):
    monkeypatch.setattr(common, "BACKOFF", 0)


def _src(**kw) -> Source:
    base = {"id": "s", "operator": "ESB", "portal": "http", "base_url": "https://x.test/api", "category": "outage",
            "cadence": "10m", "licence": "x", "attribution": "x", "format": "json"}
    return Source(**{**base, **kw})


def test_http_auth_header_and_needs_key(tmp_path, _no_backoff):
    src = _src(auth_env="ESB_POWERCHECK_KEY", auth_header="api-subscription-key", gated=True)
    with respx.mock:
        r = respx.get("https://x.test/api").respond(json={"outageMessage": []})
        asyncio.run(arun(store=Store(tmp_path / "a"), registry=[src], env={"ESB_POWERCHECK_KEY": "k1"}))
        assert r.calls.last.request.headers["api-subscription-key"] == "k1"
    with respx.mock:  # no key: gated -> needs_key, no request
        out = asyncio.run(arun(store=Store(tmp_path / "b"), registry=[src], env={}))
    assert out["runs"][0]["status"] == "needs_key"


def test_arcgis_pages_until_limit_cleared(tmp_path, _no_backoff):
    base = "https://x.test/FeatureServer"
    pages = [{"type": "FeatureCollection", "features": [{"properties": {"i": i}} for i in range(2000)],
              "properties": {"exceededTransferLimit": True}},
             {"type": "FeatureCollection", "features": [{"properties": {"i": 1}}]}]
    src = _src(portal="arcgis", base_url=base, params={"layers": "40"}, category="lines", cadence="30d")
    with respx.mock:
        route = respx.get(f"{base}/40/query").mock(side_effect=[
            respx.MockResponse(200, json=pages[0]), respx.MockResponse(200, json=pages[1])])
        out = asyncio.run(arun(store=Store(tmp_path), registry=[src], env={}))
        assert [c.request.url.params["resultOffset"] for c in route.calls] == ["0", "2000"]
    run = out["runs"][0]
    assert run["status"] == "ok" and run["rows"] == 2001


def test_eirgrid_lines_merge_sections_and_stations():
    span = lambda fid, sect, kv, xy: {"geometry": {"type": "LineString", "coordinates": xy},
                                      "properties": {"G3E_FID": fid, "CSECT_ID": sect, "VOLTAGE": kv,
                                                     "CSECT_NAME": "MONEYPOINT-OLDSTREET", "DATE_INST": "01-JAN-1984"}}
    layers = {
        "40": {"features": [span(1, "a", "400 kV", [[-8.7, 53.0], [-8.6, 53.0]]),
                            span(2, "a", "400 kV", [[-8.6, 53.0], [-8.5, 53.0]])]},
        "41": {"features": [span(2, "a", "400 kV", [[-8.6, 53.0], [-8.5, 53.0]]),  # duplicate span id: skipped
                            span(3, "b", "110 kV", [[-8.0, 53.0], [-7.9, 53.0]])]},
        "7": {"features": [{"geometry": {"type": "LineString", "coordinates": [[-6.1, 53.4], [-4.5, 53.2]]},
                            "properties": {}}]},
        "38": {"features": [{"geometry": {"type": "Point", "coordinates": [-6.5, 53.4]},
                             "properties": {"ST_NAME": "CASTLEFARM 110KV", "ST_NUMBER": "1", "VOLTAGE": "110 kV"}}]},
        "42": {"features": [{"geometry": {"type": "Point", "coordinates": [-6.5, 53.4]},
                             "properties": {"ST_NAME": "CASTLEFARM", "ST_NUMBER": "1", "VOLTAGE": "220 kV"}}]},
    }
    lines = {(f["properties"]["t"], f["properties"]["kv"]): f for f in et.line_features(layers)}
    a = lines[("Overhead line", 400.0)]
    assert len(a["geometry"]["coordinates"]) == 2 and a["properties"]["y"] == "1984"
    assert a["tippecanoe"]["minzoom"] == 4 and lines[("Interconnector (HVDC)", 400.0)]
    (st,) = et.station_features(layers)
    assert st["properties"] == {"n": "Castlefarm", "kv": 220.0, "no": "1", "w": "Yes"}


def _eirgrid(raw, sid, rows, stamp="20261127T091500Z"):
    d = raw / sid / stamp
    d.mkdir(parents=True)
    (d / "meta.json").write_text("{}")
    (d / "data.json").write_text(json.dumps({"Rows": rows}))


def _r(field, value, t="27-Nov-2026 09:00:00"):
    return {"EffectiveTime": t, "FieldName": field, "Region": "ROI", "Value": value}


def test_roi_national_block_with_empty_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "MAX_AGE_S", 10**9)
    _eirgrid(tmp_path, "eirgrid_roi_demand", [_r("SYSTEM_DEMAND", 4300), _r("SYSTEM_DEMAND", None, "27-Nov-2026 09:15:00")])
    _eirgrid(tmp_path, "eirgrid_roi_wind", [_r("WIND_ACTUAL", 262)])
    _eirgrid(tmp_path, "eirgrid_roi_interconnection",
             [_r("INTER_EWIC", 208), _r("INTER_GRNLK", 513), _r("INTER_NET_ROI", 721)], "20261127T090000Z")
    _eirgrid(tmp_path, "eirgrid_roi_interconnection", [])  # newer snapshot, empty Rows: falls back
    roi = live.national(tmp_path)["roi"]
    assert roi == {"demand_mw": 4300, "wind_mw": 262, "ewic_mw": 208, "greenlink_mw": 513, "net_import_mw": 721,
                   "as_of": "2026-11-27T09:00:00+00:00"}
    assert live.national(tmp_path / "none")["roi"] is None


def test_dissolve_merges_shared_border():
    a = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
    b = [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]
    island = [[[5, 5], [6, 5], [6, 6], [5, 5]]]
    out = dno_areas.dissolve([a, b, island])
    assert len(out) == 2
    main = max(out, key=lambda p: len(p[0]))
    assert sorted(map(tuple, main[0][:-1])) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    assert abs(dno_areas._area(main[0])) == 2


def test_roi_area_label(tmp_path, monkeypatch):
    d = tmp_path / "s"
    d.mkdir()
    sq = lambda x: [[[x, 53], [x + 1, 53], [x + 1, 54], [x, 54], [x, 53]]]
    (d / "data.json").write_text(json.dumps({"features": [
        {"geometry": {"type": "Polygon", "coordinates": sq(-9)}}, {"geometry": {"type": "Polygon", "coordinates": sq(-8)}}]}))
    monkeypatch.setattr(dno_areas, "latest_snapshot", lambda sid: d if sid == "tailte_roi_provinces" else None)
    area, label = dno_areas.roi_area()
    assert area["properties"]["op"] == "ESB" and len(area["geometry"]["coordinates"]) == 1
    assert label["properties"]["lbl"] == "ESB\nIreland"


def test_esb_powercheck_parser(tmp_path):
    d = tmp_path / "esb_powercheck_outages" / "20261127T090000Z"
    d.mkdir(parents=True)
    (d / "data.json").write_text(json.dumps({"outageMessage": [
        {"outageId": "A1", "outageType": "Fault", "point": {"coordinates": "53.35,-6.26"},
         "startTime": "27/11/2026 08:10", "estRestoreTime": "27/11/2026 12:00", "numCustAffected": "12"},
        {"outageId": "P2", "outageType": "Planned", "point": {"coordinates": "715830,734697"}},  # ITM, Dublin
        {"outageId": "R3", "outageType": "Restored", "point": {"coordinates": "53.3,-6.2"}},
        {"outageId": "X4", "outageType": "Fault", "point": {"coordinates": "0,0"}}]}))
    recs = [r for r in live._esb_powercheck(d) if r]
    by = {r["ref"]: r for r in recs}
    assert sorted(by) == ["A1", "P2"]
    assert by["A1"]["ll"] == (53.35, -6.26) and by["A1"]["cust"] == 12 and by["A1"]["kind"] == "unplanned"
    assert by["A1"]["start"].startswith("2026-11-27T08:10")
    lat, lon = by["P2"]["ll"]
    assert abs(lat - 53.35) < 0.05 and abs(lon + 6.26) < 0.05 and by["P2"]["kind"] == "planned"


def test_basemap_outdated_marker(tmp_path):
    assert bootstrap.basemap_outdated(tmp_path) is False  # nothing yet: first run handles it
    p = bootstrap.basemap_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert bootstrap.basemap_outdated(tmp_path) is True  # old install: no marker
    bootstrap.bbox_marker(tmp_path).write_text(bootstrap.BBOX)
    assert bootstrap.basemap_outdated(tmp_path) is False
    assert float(bootstrap.BBOX.split(",")[0]) <= -10.7  # covers the west of Ireland
