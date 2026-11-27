"""ESB Networks (Republic of Ireland) adapter.

Source: esb_heatmap, the customer capacity heatmap XLSX ("Heatmap data" sheet). One row per transformer
group: HV stations (220/110/38 kV) and every 3-phase MV/LV substation. WGS84 lat/lon. See docs/sources/roi.md.

Kind mapping (ESB topology is 220/110 kV transmission -> 110/38 kV -> 38/MV -> MV/LV):
  group fed from "Transmission Source" (TSO/DSO interface: 220/110, 110/38, 110/MV)  -> GSP
  other group with a 110 or 38 kV secondary (Dublin DSO 110 kV, 220/38)               -> BSP
  group with an MV (10/20 kV) secondary                                               -> PRIMARY
  Voltage Class LV (MV/LV substations)                                                -> lv_site
One asset per transformer group; match clusters groups at one station into one site (top kind wins).
Parents come from "Parent Station", which names the feeding transformer group exactly.

Capacity (per group): demand_headroom_mva = Demand Available MVA, gen_headroom_mw = Gen Available Firm MW.
Both already include upstream (parent) constraints; the "otherwise capacity available" note goes to
upstream_constraint. LV: utilisation_pct is derived as 1 - own available / firm capacity (own = before the
parent constraint). Names of MV/LV substations are part-redacted by ESB ("MV/LV Substation BA***002").
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from .common import (
    as_of,
    ids_json,
    latest_snapshot,
    parse_num,
    snapshot_files,
    snapshot_meta,
    write_table,
)

OPERATOR = "ESB"
SRC = "esb_heatmap"
SHEET = "Heatmap data"
TSO_SOURCE = "transmission source"

# Island of Ireland bounds (ESB stations near the border can sit a little inside NI).
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 51.3, 55.5, -10.8, -5.9

BASIS = ("ESB Networks heatmap: transformer capacity only, firm. Demand = firm capacity less load; "
         "generation = firm capacity less committed generation. Includes upstream (parent) constraints.")

_OTHERWISE = re.compile(r"otherwise capacity available\s*=\s*([\d.]+)\s*(kva|mva|kw|mw)", re.IGNORECASE)
_COMPILED = re.compile(r"report compiled\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE)


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Header row (the one starting 'Station Name') + data rows as dicts. Read-only openpyxl: 46k rows."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
        hdr: list[str] | None = None
        out = []
        for r in ws.iter_rows(values_only=True):
            if hdr is None:
                if r and str(r[0] or "").strip() == "Station Name":
                    hdr = [str(c or "").strip() for c in r]
                continue
            out.append(dict(zip(hdr, r, strict=False)))
        return out
    finally:
        wb.close()


def compiled_date(rows: list[dict[str, Any]]) -> str | None:
    """'Report compiled 12/12/2025' (a footer row) -> '2025-12-12'."""
    for r in rows:
        for v in r.values():
            if isinstance(v, str) and (m := _COMPILED.search(v)):
                d, mth, y = map(int, m.groups())
                return date(y, mth, d).isoformat()
    return None


def station_name(s: str) -> str:
    """'GARRYOWEN (067000) 38 kV STATION' -> 'Garryowen'; 'INCHICORE 220KV (964000) ...' -> 'Inchicore'."""
    s = re.sub(r"\s*\(\d+\).*$", "", s or "")
    s = re.sub(r"\s*\d+\s*kv\s*$", "", s, flags=re.IGNORECASE).strip()
    return s.title()


def station_no(s: str) -> str | None:
    m = re.search(r"\((\d{6})\)", s or "")
    return m.group(1) if m else None


def kv(v: Any) -> float | None:
    """'38 kV' -> 38.0, 'MV' / 'LV' / '_' -> None."""
    m = re.match(r"\s*([\d.]+)\s*kv", str(v or ""), re.IGNORECASE)
    return float(m.group(1)) if m else None


def coords(lat: Any, lon: Any) -> tuple[float | None, float | None, str]:
    la, lo = parse_num(lat), parse_num(lon)
    if la is None or lo is None:
        return None, None, "missing"
    if LAT_MIN <= la <= LAT_MAX and LON_MIN <= lo <= LON_MAX:
        return round(la, 6), round(lo, 6), "native_wgs84"
    return None, None, "rejected"


def otherwise(text: Any) -> float | None:
    """Own (pre-parent-constraint) available capacity from the note, in MVA/MW."""
    m = _OTHERWISE.search(str(text or ""))
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000 if m.group(2).lower() in ("kva", "kw") else v


def _txt(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s if s and s not in ("_", "N/A") else None


def group_kind(r: dict[str, Any]) -> str:
    parent = (_txt(r.get("Parent Station")) or "").lower()
    sec = str(r.get("Voltage Class") or "").strip()
    if parent == TSO_SOURCE:
        return "GSP"
    if kv(sec) is not None:  # 110 kV or 38 kV secondary, fed from the DSO network
        return "BSP"
    return "PRIMARY"


def build_hv(rows: list[dict[str, Any]], asof: str | None) -> tuple[pl.DataFrame, pl.DataFrame]:
    hv = [r for r in rows if _txt(r.get("Transformer GroupID")) and str(r.get("Voltage Class") or "").strip()
          not in ("", "LV") and station_no(str(r.get("Station Name") or ""))]
    by_group = {str(r["Transformer GroupID"]).strip(): r for r in hv}
    kinds = {g: group_kind(r) for g, r in by_group.items()}
    assets, caps = [], []
    for g, r in by_group.items():
        st = str(r["Station Name"])
        no = station_no(st)
        uid = f"{OPERATOR}:{no}:{g}"
        lat, lon, q = coords(r.get("Latitude"), r.get("Longitude"))
        up_gsp = up_bsp = None
        pg = _txt(r.get("Parent Station"))
        if pg in by_group:
            pname = station_name(str(by_group[pg]["Station Name"]))
            if kinds[pg] == "GSP":
                up_gsp = pname
            else:
                up_bsp = pname
        tso = _txt(r.get("TSO Interface Station"))
        if up_gsp is None and tso and kinds[g] != "GSP":
            up_gsp = tso.title()
        assets.append({
            "asset_uid": uid, "operator": OPERATOR, "licence_area": OPERATOR, "name": station_name(st),
            "kind": kinds[g], "kv_hv": kv(r.get("Primary kV")),
            "kv_lv": kv(r.get("Voltage Class")) or kv(str(r.get("Transformer Configuration") or "").rpartition("@")[2]),
            "lat": lat, "lon": lon, "coord_quality": q, "upstream_gsp": up_gsp, "upstream_bsp": up_bsp,
            "native_ids": ids_json(number=no, group=g, transformers=_txt(r.get("Transformer Configuration")),
                                   tso_interface=tso, station=st),
            "source_id": SRC, "as_of": asof,
        })
        dem_note, gen_note = _txt(r.get("Demand Parent Constraint")), _txt(r.get("Generation Parent Constraint"))
        notes = "; ".join(f"{k}: {v}" for k, v in (("Demand", dem_note), ("Generation", gen_note)) if v)
        caps.append({
            "asset_uid": uid,
            "gen_headroom_mw": parse_num(r.get("Gen Available Firm MW")),
            "demand_headroom_mva": parse_num(r.get("Demand Available MVA")),
            "firm_capacity_mva": parse_num(r.get("Demand FirmCapacity MVA")),
            "contracted_gen_mw": parse_num(r.get("Generation Total Committed MW")),
            "upstream_constraint": notes or None, "basis": BASIS, "source_id": SRC, "as_of": asof,
        })
    f64 = {c: pl.Float64 for c in ("kv_hv", "kv_lv", "lat", "lon")}
    return (pl.DataFrame(assets, schema_overrides=f64, infer_schema_length=None),
            pl.DataFrame(caps, infer_schema_length=None))


def _mount(structure: str | None) -> str | None:
    s = (structure or "").lower()
    return "pole" if s.startswith("pole") else ("ground" if s.startswith(("kiosk", "building")) else None)


def group_stations(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Transformer GroupID -> clean station name, for HV groups (what 'Parent Station' refers to)."""
    return {str(r["Transformer GroupID"]).strip(): station_name(str(r.get("Station Name") or ""))
            for r in rows if _txt(r.get("Transformer GroupID")) and str(r.get("Voltage Class") or "").strip() != "LV"}


def build_lv(rows: list[dict[str, Any]], asof: str | None) -> pl.DataFrame:
    """One lv_site per (name, location). Several transformer rows at one site: kVA and capacities summed."""
    groups = group_stations(rows)
    sites: dict[str, dict[str, Any]] = {}
    for r in rows:
        if str(r.get("Voltage Class") or "").strip() != "LV":
            continue
        lat, lon, q = coords(r.get("Latitude"), r.get("Longitude"))
        name = _txt(r.get("Station Name"))
        if lat is None or name is None:
            continue
        key = f"{name.removeprefix('MV/LV Substation ').strip()}@{lat:.5f},{lon:.5f}"
        conf = str(r.get("Transformer Configuration") or "")
        firm, avail = parse_num(r.get("Demand FirmCapacity MVA")), parse_num(r.get("Demand Available MVA"))
        own = otherwise(r.get("Demand Parent Constraint"))
        own = own if own is not None else avail
        s = sites.setdefault(key, {"name": name, "lat": lat, "lon": lon, "q": q, "kv": kv(r.get("Primary kV")),
                                   "mva": 0.0, "firm": 0.0, "own": 0.0, "dem": 0.0, "gen": 0.0, "miss": False,
                                   "structure": conf.split(":", 1)[-1].strip() or None,
                                   "parent": _txt(r.get("Parent Station")), "feeder": _txt(r.get("Parent Feeder")),
                                   "tso": _txt(r.get("TSO Interface Station")), "n": 0})
        s["n"] += 1
        inst = parse_num(r.get("Installed Capacity MVA"))
        gen = parse_num(r.get("Gen Available Firm MW"))
        if None in (inst, firm, own, avail, gen):
            s["miss"] = True
            continue
        s["mva"] += inst
        s["firm"] += firm
        s["own"] += own
        s["dem"] += avail
        s["gen"] += gen
    rows_out = []
    for k, s in sites.items():
        parent = groups.get(s["parent"] or "") or (re.sub(r"\s*\[.*\]\s*$", "", s["parent"] or "").strip() or None)
        util = None if s["miss"] or s["firm"] <= 0 else round(max(0.0, 100 * (1 - s["own"] / s["firm"])), 1)
        rows_out.append({
            "asset_uid": f"{OPERATOR}:{k}", "operator": OPERATOR, "name": s["name"], "lat": s["lat"],
            "lon": s["lon"], "coord_quality": s["q"], "kv_hv": s["kv"],
            "transformer_kva": None if s["miss"] or s["mva"] <= 0 else round(s["mva"] * 1000, 1),
            "utilisation_pct": util, "customers": None, "upstream_primary": parent,
            "native_ids": ids_json(
                mount=_mount(s["structure"]), structure=s["structure"], feeder=s["feeder"],
                tso_interface=s["tso"], transformers=s["n"],
                demand_available_kva=None if s["miss"] else round(s["dem"] * 1000),
                gen_available_kw=None if s["miss"] else round(s["gen"] * 1000)),
            "source_id": SRC, "as_of": asof,
        })
    return pl.DataFrame(rows_out, schema_overrides={"kv_hv": pl.Float64, "transformer_kva": pl.Float64,
                                                    "utilisation_pct": pl.Float64, "customers": pl.Int64,
                                                    "upstream_primary": pl.Utf8},
                        infer_schema_length=None)


def run() -> dict[str, int]:
    snap = latest_snapshot(SRC)
    if snap is None:
        raise RuntimeError(f"no ok snapshot for {SRC}")
    f = next((p for p in snapshot_files(snap) if p.suffix == ".xlsx"), None)
    if f is None:
        raise RuntimeError(f"{SRC}: no .xlsx in {snap}")
    rows = read_rows(f)
    asof = compiled_date(rows) or as_of(snapshot_meta(snap))
    assets, caps = build_hv(rows, asof)
    assert assets.height > 0, "no ESB stations"
    lv = build_lv(rows, asof)
    assert lv.height > 0, "no ESB lv sites"
    write_table(OPERATOR, "asset", assets)
    write_table(OPERATOR, "capacity", caps)
    write_table(OPERATOR, "lv_site", lv)
    return {"asset": assets.height, "capacity": caps.height, "lv_site": lv.height}
