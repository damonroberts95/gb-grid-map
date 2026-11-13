"""Flexibility zones: where distribution operators have tendered (or plan to tender) for flexibility instead of
reinforcing. UKPN LV and HV zones, ENW monthly/biannual tender sites, SPEN flexibility polygons, NPG signposted
needs. NGED and SSEN publish flexibility requirements as tables/PDFs without geometry, so they are not drawn."""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files, snapshot_meta
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["ukpn_flex_lv_zones", "ukpn_flex_hv_zones", "enw_flex_monthly_tender", "enw_flex_biannual_tender",
           "spen_flex_polygons_spm_piclo", "spen_flex_polygons_spd", "spen_flex_polygons_spm",
           "spen_flex_polygons_spd_opflex", "spen_flex_competition_polygons", "npg_flex_upcoming"]

STATUS_COLORS = [["Live", "#0072B2"], ["Upcoming", "#CC79A7"], ["Published", "#56B4E9"], ["Closed", "#9aa8b5"]]

LAYERS = [
    LayerMeta(
        id="flex_zones", title="Flexibility zones", group="Areas", type="pmtiles", geom="polygon",
        source_layer="flex_zones", minzoom=5, opacity=0.85,
        description="Areas where operators seek flexibility instead of new network. UKPN, ENW, SPEN, NPG. 2026.",
        color={"prop": "s", "type": "categorical", "default": "#9aa8b5", "stops": STATUS_COLORS},
        popup=[["n", "Zone / substation", None], ["op", "Operator", None], ["k", "Type", None],
               ["s", "Status", None], ["r", "Tender round / period", None], ["m", "Max requirement", "MVA"],
               ["t", "Need", None]],
        legend=[[c, s] for s, c in STATUS_COLORS],
        attribution="UK Power Networks, Electricity North West / SP Energy Networks, SP Energy Networks and "
                    "Northern Powergrid open data (CC BY 4.0; NPG Open Data Licence v1.0)",
    )
]


def _gj(source_id: str) -> tuple[list[dict], str]:
    snap = latest_snapshot(source_id)
    if snap is None:
        return [], ""
    f = next((p for p in snapshot_files(snap) if p.suffix == ".geojson"), None)
    if f is None:
        return [], ""
    return json.loads(f.read_text())["features"], (snapshot_meta(snap).get("fetched_at") or "")[:10]


def _round(c: Any, nd: int = 5) -> Any:
    if isinstance(c[0], (int, float)):
        return [round(c[0], nd), round(c[1], nd)]
    return [_round(x, nd) for x in c]


def _feat(geom: dict, **p: Any) -> dict:
    props = {k: v for k, v in p.items() if v not in (None, "", "N/A")}
    return {"type": "Feature", "geometry": {"type": geom["type"], "coordinates": _round(geom["coordinates"])},
            "properties": props}


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def collect() -> tuple[list[dict], dict[str, int]]:
    out: list[dict] = []
    n: dict[str, int] = {}

    def add(key: str, feats: list[dict]) -> None:
        n[key] = len(feats)
        out.extend(feats)

    lv, _ = _gj("ukpn_flex_lv_zones")
    live_rounds = {f["properties"]["tender_round"] for f in lv if f["properties"].get("status") == "Live"}
    add("UKPN LV", [_feat(f["geometry"], n=f["properties"]["transformer_id"], op="UKPN", k="LV zone",
                          s="Live" if f["properties"].get("status") == "Live" else "Closed",
                          r=f["properties"].get("tender_round")) for f in lv if f["geometry"]])
    hv, _ = _gj("ukpn_flex_hv_zones")
    add("UKPN HV", [_feat(f["geometry"], n=f["properties"].get("flexibility_zone"), op="UKPN", k="HV zone",
                          s="Live" if f["properties"].get("tender_round") in live_rounds else "Closed",
                          r=f["properties"].get("tender_round"), t=f["properties"].get("constraint_type"))
                    for f in hv if f["geometry"]])

    for sid, label in (("enw_flex_monthly_tender", "ENW monthly"), ("enw_flex_biannual_tender", "ENW biannual")):
        feats, fetched = _gj(sid)
        add(label, [_feat(f["geometry"], n=f["properties"].get("substation_name"), op="ENW", k="Tender site",
                          s="Live" if (f["properties"].get("delivery_start_date") or "") >= fetched else "Closed",
                          r=f["properties"].get("period"), m=_num(f["properties"].get("maximum_requirement_mva")),
                          t=f["properties"].get("need_type")) for f in feats if f["geometry"]])

    seen: set[str] = set()
    spen: list[dict] = []
    for sid, k in (("spen_flex_polygons_spm_piclo", "Deferred reinforcement"),
                   ("spen_flex_polygons_spd", "Deferred reinforcement"),
                   ("spen_flex_polygons_spm", "Deferred reinforcement"),
                   ("spen_flex_polygons_spd_opflex", "Operational flexibility"),
                   ("spen_flex_competition_polygons", "Flex competition")):
        for f in _gj(sid)[0]:
            p = f["properties"]
            nm = p.get("name") or p.get("substation") or p.get("picloname")
            key = f"{k}|{str(nm).lower()}|{p.get('voltage')}"
            if not f["geometry"] or key in seen:
                continue
            seen.add(key)
            spen.append(_feat(f["geometry"], n=str(nm).title() if str(nm).isupper() else nm, op="SPEN", k=k,
                              s="Published"))
    add("SPEN", spen)

    snap = latest_snapshot("npg_flex_upcoming")
    npg: list[dict] = []
    if snap:
        csv.field_size_limit(10**9)
        for r in csv.DictReader(io.StringIO(snapshot_files(snap)[0].read_text(encoding="utf-8-sig")), delimiter=";"):
            try:
                g = json.loads(r["geo_shape"])
            except (KeyError, ValueError):
                continue
            npg.append(_feat(g, n=r.get("upcoming_flexibility_needs_substation") or r.get("name"), op="NPG",
                             k=f"{r.get('point_of_flexibility_substation_type') or 'Site'} need",
                             s="Live" if r.get("scheme_status") == "On Flex Market" else "Upcoming",
                             r=(r.get("flexibility_needs_start_year") or "").split(".")[0] or None,
                             t=r.get("driver")))
    add("NPG", npg)
    return out, n


def build(ctx: Ctx) -> dict[str, Path]:
    feats, counts = collect()
    if not feats:
        return {}
    print("area_flex features:", counts)
    src = ctx.tmp / "area_flex.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / "area_flex.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "flex_zones", "-Z5", "-z13",
                    "--simplification=4", "--simplify-only-low-zooms", "--drop-densest-as-needed",
                    "--maximum-tile-bytes=450000", str(src)], check=True)
    return {"flex_zones": out}
