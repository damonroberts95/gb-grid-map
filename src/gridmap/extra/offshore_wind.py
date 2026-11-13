"""Offshore wind: lease/option areas (Crown Estate + Crown Estate Scotland), their cable corridors and OFTO sites,
and NESO's draft SSEP offshore zones. MW comes from the operator's own field where given, else the EMODnet wind
farm footprint(s) inside the area, else a name match to the DESNZ REPD."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from .offshore_common import (
    STATUS_COLORS,
    STATUS_SPEC,
    norm_status,
    pip,
    read_geojson,
    read_polygon_zip,
    rep_point,
    round_geom,
    tile,
)

SOURCES = ["tce_wind_sites", "tce_wind_cables", "tce_r4_preferred", "tce_r5_pdas", "tce_r6_pdas",
           "ces_offshore_wind", "ces_scotwind_offers", "ces_intog", "emodnet_windfarms", "neso_ssep_offshore",
           "desnz_repd"]

TCE = "Contains data provided by The Crown Estate that is protected by copyright and database rights."
CES = ("Contains public sector information, licensed under the Open Government Licence v3.0, from Crown Estate "
       "Scotland.")
EMOD = "Wind farm footprints and MW from EMODnet Human Activities."
NESO = "Supported by National Energy SO Open Data"
ATTR = f"{TCE} {CES} {EMOD}"

LAYERS = [
    LayerMeta(
        id="offshore_wind", title="Offshore wind areas", group="Generation & planning", type="pmtiles",
        geom="polygon", source_layer="offshore_wind", minzoom=3,
        description="Leased and optioned offshore wind areas. Crown Estate and Crown Estate Scotland.",
        color=STATUS_SPEC, opacity=0.55,
        popup=[["n", "Site", None], ["s", "Stage", None], ["mw", "Capacity", "MW"], ["tur", "Turbines", None],
               ["t", "Developer or tenant", None], ["rd", "Leasing round", None], ["km2", "Area", "km²"],
               ["src", "Capacity from", None]],
        legend=STATUS_COLORS[:5], attribution=ATTR),
    LayerMeta(
        id="offshore_cable_zones", title="Offshore cable corridors & OFTO sites", group="Network", type="pmtiles",
        geom="polygon", source_layer="offshore_cable_zones", minzoom=5,
        description="Offshore cable and OFTO lease areas. Not exact routes. Crown Estate.",
        color=STATUS_SPEC, opacity=0.6,
        popup=[["n", "Name", None], ["s", "Stage", None], ["k", "Type", None], ["t", "Tenant", None]],
        legend=STATUS_COLORS[:3] + STATUS_COLORS[3:4] + STATUS_COLORS[5:], attribution=f"{TCE} {CES}"),
    LayerMeta(
        id="offshore_ssep", title="NESO offshore planning zones (SSEP)", group="Areas", type="pmtiles",
        geom="polygon", source_layer="offshore_ssep", minzoom=3,
        description="Offshore zones in NESO's Strategic Spatial Energy Plan.",
        color={"prop": "g", "type": "categorical", "default": "#999999",
               "stops": [["Scottish waters", "#009E73"], ["England & Wales waters", "#F0E442"]]},
        opacity=0.35,
        popup=[["n", "Zone", None], ["g", "Waters", None]],
        legend=[["#009E73", "Scottish option areas"], ["#F0E442", "England & Wales zones"]], attribution=NESO),
]


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def _norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)|offshore|wind ?farm|windfarm|extension|array|project|limited|ltd|owf|ofw", " ", s.lower())
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def _emodnet() -> list[tuple[tuple[float, float], dict]]:
    out = []
    for f in read_geojson("emodnet_windfarms"):
        p = f["properties"]
        if p.get("status") in ("Dismantled",) or not f.get("geometry"):
            continue
        out.append((rep_point(f["geometry"]), p))
    return out


def _repd() -> list[tuple[str, float, str]]:
    snap = latest_snapshot("desnz_repd")
    f = next((p for p in snapshot_files(snap) if p.suffix == ".csv"), None) if snap else None
    if f is None:
        return []
    df = pl.read_csv(f, infer_schema=False, encoding="utf8-lossy")
    df = df.filter(pl.col("Technology Type").str.contains("Wind Offshore")
                   & pl.col("Development Status (short)").is_in(
                       ["Operational", "Under Construction", "Awaiting Construction", "Application Submitted"]))
    out = []
    for name, mw, st in df.select("Site Name", "Installed Capacity (MWelec)", "Development Status (short)").iter_rows():
        v = _num(str(mw).replace(",", "")) if mw else None
        if v:
            out.append((_norm_name(name), v, st))
    return out


def wind_areas() -> list[dict]:
    """Merge the six area sources into one polygon list with a common property set."""
    feats: list[dict] = []

    def add(g: dict, **p: Any) -> None:
        feats.append({"type": "Feature", "geometry": round_geom(g), "properties": {k: v for k, v in p.items()
                                                                                     if v not in (None, "")}})

    for f in read_geojson("tce_wind_sites"):
        p = f["properties"]
        rd = p.get("Wind_Round")
        add(f["geometry"], n=p.get("Name_Prop"), s=norm_status(p.get("Inf_Status")), t=p.get("Name_Ten"),
            rd=f"Round {rd}" if str(rd).isdigit() else rd, km2=round(p.get("km2") or 0, 1) or None, src_org="TCE")
    for f in read_geojson("ces_offshore_wind"):
        p = f["properties"]
        if p.get("Property_Classification") != "Wind Farm":
            continue
        add(f["geometry"], n=p.get("Property_Description"), s=norm_status(p.get("Project_Phase")),
            t=p.get("Tenant_Name"), rd=p.get("Lease_Type_Description"), mw=_num(p.get("Capacity_MW")),
            src_org="CES")

    def option(sid: str, name: Any, rd: str, bidder: str, mw: str, km2: str | None, org: str) -> None:
        for f in read_geojson(sid):
            p = f["properties"]
            g = f["geometry"]
            if any(pip(rep_point(g), x["geometry"]) for x in feats if x["properties"].get("src_org") == org
                   and x["properties"]["s"] != "Lease / option round"):
                continue  # already shown as a named site
            add(g, n=name(p), s="Lease / option round", t=p.get(bidder) if bidder else None, rd=rd,
                mw=_num(p.get(mw)) if mw else None, km2=round(p[km2], 1) if km2 and p.get(km2) else None,
                src_org=org)

    option("tce_r4_preferred", lambda p: f"Round 4 preferred project {p.get('ProjectID')}", "Round 4",
           "PrefBidder", "CapMW", None, "TCE")
    option("tce_r5_pdas", lambda p: f"Round 5 development area {str(p.get('Name')).strip()}", "Round 5 (floating)", "", "",
           "Area_km2", "TCE")
    option("tce_r6_pdas", lambda p: f"Round 6 development zone {p.get('ID')}", "Round 6", "", "", "Area_km2", "TCE")
    option("ces_scotwind_offers", lambda p: f"ScotWind option area {p.get('Id')}", "ScotWind", "Lead_App",
           "Total_Cap", "Area_km2", "CES")
    option("ces_intog", lambda p: f"INTOG area {p.get('ID')}" + (f" ({p['IN_Cat'].strip()})" if (p.get("IN_Cat") or "").strip() else ""), "INTOG", "Lead_App", "Capacity",
           "Area_km2", "CES")
    return feats


def add_capacity(feats: list[dict], emod: list, repd: list) -> None:
    """Set mw/tur/src on each area: own figure, else EMODnet footprints inside it, else REPD name match."""
    from rapidfuzz import fuzz

    for f in feats:
        p = f["properties"]
        inside = [e for pt, e in emod if pip(pt, f["geometry"])]
        tur = sum(e.get("n_turbines") or 0 for e in inside)
        if tur:
            p["tur"] = int(tur)
        if p.get("mw"):
            p["src"] = "Crown Estate" if p["src_org"] == "TCE" else "Crown Estate Scotland"
            continue
        mw = sum(e.get("power_mw") or 0 for e in inside)
        if mw:
            p["mw"], p["src"] = round(mw), "EMODnet"
            continue
        key = _norm_name(p.get("n", ""))
        best = max(repd, key=lambda r: fuzz.token_set_ratio(key, r[0]), default=None) if key else None
        if best and len(key) >= 4 and fuzz.token_set_ratio(key, best[0]) >= 92:
            p["mw"], p["src"] = round(best[1]), "REPD (name match)"


def cable_zones() -> list[dict]:
    out = []
    for f in read_geojson("tce_wind_cables"):
        p = f["properties"]
        out.append({"type": "Feature", "geometry": round_geom(f["geometry"]),
                    "properties": {"n": p.get("Name_Prop"), "s": norm_status(p.get("Infra_Stat")),
                                   "k": "Wind export cable", "t": p.get("Name_Ten")}})
    for f in read_geojson("ces_offshore_wind"):
        p = f["properties"]
        if p.get("Property_Classification") in ("OFTO", "Substation"):
            out.append({"type": "Feature", "geometry": round_geom(f["geometry"]),
                        "properties": {"n": p.get("Property_Description"), "s": norm_status(p.get("Project_Phase")),
                                       "k": "OFTO transmission" if p["Property_Classification"] == "OFTO"
                                       else "Offshore substation", "t": p.get("Tenant_Name")}})
    return [f for f in out if {k: v for k, v in f["properties"].items() if v}]


def ssep_zones() -> list[dict]:
    snap = latest_snapshot("neso_ssep_offshore")
    z = next((p for p in snapshot_files(snap) if p.suffix == ".zip"), None) if snap else None
    if z is None:
        return []
    out = []
    for row, g in read_polygon_zip(z, 27700, {"PUB_ZN_ID", "Region", "Symb_Grp"}):
        scot = row["Symb_Grp"].lower().startswith("scot")
        name = (f"Scottish option area {row['PUB_ZN_ID'].rsplit('_', 1)[-1]}" if scot else row["Region"])
        out.append({"type": "Feature", "geometry": g,
                    "properties": {"n": name, "g": "Scottish waters" if scot else "England & Wales waters"}})
    return out


def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    areas = wind_areas()
    if areas:
        add_capacity(areas, _emodnet(), _repd())
        for f in areas:
            f["properties"].pop("src_org", None)
        out["offshore_wind"] = tile(areas, ctx.tmp / "offshore_wind.pmtiles", "offshore_wind", 3, 12,
                                    ["--simplification=2", "--no-tile-size-limit"])
    if zones := cable_zones():
        out["offshore_cable_zones"] = tile(zones, ctx.tmp / "offshore_cable_zones.pmtiles", "offshore_cable_zones",
                                           5, 12, ["--simplification=2"])
    if ssep := ssep_zones():
        out["offshore_ssep"] = tile(ssep, ctx.tmp / "offshore_ssep.pmtiles", "offshore_ssep", 3, 10,
                                    ["--simplification=4", "--detect-shared-borders"])
    return out
