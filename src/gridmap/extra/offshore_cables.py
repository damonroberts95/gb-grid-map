"""Offshore cables and landfalls from OpenStreetMap (power=cable with location=underwater/seabed): wind farm
export and inter-array cables, interconnectors and island/HVDC links. Interconnectors are joined by name to the
NESO interconnector register for MW and status. Landfalls are where a subsea cable meets a land cable; offshore
substations/platforms come from OSM power=substation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from ._osm import export_geojsonseq, pbf_in
from .offshore_common import tile
from .osm_power import max_kv

SOURCES = ["osm_gb_pbf", "neso_interconnector_register"]
OSM = "© OpenStreetMap contributors (ODbL)"
NESO = "Supported by National Energy SO Open Data"

KIND_COLORS = [["#56B4E9", "Wind farm inter-array"], ["#0072B2", "Wind farm export"],
               ["#E69F00", "Interconnector"], ["#CC79A7", "HVDC / island link"], ["#7f7f7f", "Other subsea power"]]
KIND_NAMES = ["Wind farm inter-array", "Wind farm export", "Interconnector", "HVDC / island link", "Other subsea power"]

LAYERS = [
    LayerMeta(
        id="offshore_cables", title="Subsea power cables", group="Network", type="pmtiles", geom="line",
        source_layer="offshore_cables", minzoom=4,
        description="Subsea power cables. OpenStreetMap, so approximate and incomplete. Capacity from the NESO "
                    "interconnector register.",
        color={"prop": "k", "type": "categorical", "default": "#7f7f7f",
               "stops": [[n, c] for c, n in KIND_COLORS]},
        size=2, opacity=0.9,
        popup=[["n", "Cable", None], ["k", "Type", None], ["kv", "Voltage", "kV"], ["mw", "Capacity", "MW"],
               ["st", "Status", None], ["op", "Operator", None]],
        legend=KIND_COLORS, attribution=f"{OSM}; {NESO}"),
    LayerMeta(
        id="landfalls", title="Cable landfalls & offshore substations", group="Network", type="geojson",
        geom="point", minzoom=4,
        description="Cable landfalls and offshore platforms. Derived from OpenStreetMap. Approximate.",
        color={"prop": "k", "type": "categorical", "default": "#7f7f7f",
               "stops": [["Landfall", "#D55E00"], ["Offshore substation", "#009E73"]]},
        size=5, opacity=0.9,
        popup=[["n", "Name", None], ["k", "Type", None], ["c", "Cable type", None], ["kv", "Voltage", "kV"]],
        legend=[["#D55E00", "Cable landfall"], ["#009E73", "Offshore substation / platform"]],
        attribution=OSM),
]

_NOT_POWER = re.compile(r"pipe|outfall|tampnet|stratos|\bflags\b|scottish water|fibre|telecom|\bgas\b|cats|langeled|"
                        r"vesterled|norpipe|forties|\bbbl\b|field|sewer|water|atlantic crossing|apollo|shefa", re.IGNORECASE)
_NAMED_INTERCONNECTOR = re.compile(r"\bifa\b|ifa ?2|britned|north sea link|\bnsl\b|nemo|viking link|eleclink|greenlink|"
                                   r"moyle|east[- ]west|neuconnect|uk-ireland|iom/uk", re.IGNORECASE)
_OFFSHORE_INTERNAL = re.compile(r"osp|owf|substation|wind|nng|array|beatrice|race bank|platform", re.IGNORECASE)
_KIND_RULES = [
    ("Wind farm inter-array", re.compile(r"inter[- ]?array|array cable", re.IGNORECASE)),
    ("Wind farm export", re.compile(r"export|grid connection|owf|offshore wind|wind ?farm|ofto|offshore", re.IGNORECASE)),
    ("HVDC / island link", re.compile(r"green link|hvdc|kintyre|hunterston|western link|caithness|orkney|shetland|"
                                     r"skye|isle of|isles of|submarine|feeder|beatrice line|pentland|uist", re.IGNORECASE)),
]
# OSM cable name regex -> project name in the NESO interconnector register (checked in this order)
_REGISTER_ALIAS = [
    (r"ifa ?2\b|ifa2", "IFA2 Interconnector"), (r"ifa", "IFA Interconnector"), (r"britned", "Britned"),
    (r"north sea link|nsl", "NS Link"), (r"nemo", "Nemo Link"), (r"viking", "Viking Link Denmark Interconnector"),
    (r"eleclink", "ElecLink"), (r"greenlink", "Greenlink"), (r"moyle", "Auchencrosh (interconnector CCT)"),
    (r"east[- ]west", "East West Interconnector"), (r"neuconnect", "NeuConnect Interconnector"),
]


def classify(name: str, kv: float = 0) -> str | None:
    """Cable kind from its OSM name (and voltage), or None for things that are not power cables."""
    name = name or ""
    if _NOT_POWER.search(name) and not re.search(r"interconnector|hvdc|export|inter", name, re.IGNORECASE):
        return None
    if _OFFSHORE_INTERNAL.search(name) and re.search(r"interconnector", name, re.IGNORECASE):
        return "Wind farm inter-array"  # cable between offshore platforms of one wind farm
    if _NAMED_INTERCONNECTOR.search(name):
        return "Interconnector"
    for kind, rx in _KIND_RULES:
        if rx.search(name):
            return kind
    if re.search(r"interconnector", name, re.IGNORECASE):
        return "Interconnector" if kv >= 100 else "HVDC / island link"
    return "Other subsea power"


def register() -> dict[str, dict]:
    """Interconnector register rows by project name (MW export total, project status)."""
    snap = latest_snapshot("neso_interconnector_register")
    f = next((p for p in snapshot_files(snap) if p.suffix == ".csv"), None) if snap else None
    if f is None:
        return {}
    df = pl.read_csv(f, infer_schema=False, encoding="utf8-lossy")
    df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})
    out: dict[str, dict] = {}
    for r in df.iter_rows(named=True):
        try:
            mw = float(r["MW Export - Total"] or 0)
        except ValueError:
            mw = 0.0
        cur = out.setdefault(r["Project Name"], {"mw": 0.0, "status": r["Project Status"]})
        cur["mw"] = max(cur["mw"], mw)
    return out


def join_register(name: str, reg: dict[str, dict]) -> dict | None:
    for rx, proj in _REGISTER_ALIAS:
        if re.search(rx, name or "", re.IGNORECASE):
            hit = reg.get(proj)
            if hit:
                return {"mw": round(hit["mw"]), "st": "Operational" if hit["status"] == "Built" else hit["status"]}
    return None


def _coords_key(c: list[float]) -> tuple[float, float]:
    return (round(c[0], 5), round(c[1], 5))


def features(lines: Iterable[str], reg: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """GeoJSONSeq lines from `osmium export` -> (cable features, landfall/offshore-substation features)."""
    sub: list[dict] = []
    land_ends: set[tuple[float, float]] = set()
    uw: list[dict] = []
    seen_sub: set[tuple] = set()
    for raw in lines:
        f = json.loads(raw)
        p, g = f.get("properties") or {}, f["geometry"]
        gt = g["type"]
        if p.get("power") == "cable" and gt == "LineString":
            if p.get("location") in ("underwater", "seabed"):
                uw.append(f)
            else:
                land_ends.add(_coords_key(g["coordinates"][0]))
                land_ends.add(_coords_key(g["coordinates"][-1]))
        elif p.get("power") == "transition" and gt == "Point":
            land_ends.add(_coords_key(g["coordinates"]))
        elif p.get("power") == "substation" and (
                p.get("location") in ("platform", "underwater", "offshore", "sea") or p.get("substation") == "offshore"
                or re.search(r"offshore (sub|platform)|\bOSP\b|offshore converter", p.get("name") or "", re.IGNORECASE)):
            c = _centre(g)
            if c and (p.get("name"), round(c[0], 2), round(c[1], 2)) not in seen_sub:
                seen_sub.add((p.get("name"), round(c[0], 2), round(c[1], 2)))
                sub.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": c},
                            "properties": {"n": p.get("name"), "k": "Offshore substation",
                                           "kv": max_kv(p.get("voltage")) or None}})
    cables: list[dict] = []
    landfalls: dict[tuple[float, float], dict] = {}
    for f in uw:
        p, g = f["properties"], f["geometry"]
        name = p.get("name") or p.get("operator") or ""
        kind = classify(name, max_kv(p.get("voltage")))
        if kind is None:
            continue
        props = {"n": p.get("name"), "k": kind, "kv": max_kv(p.get("voltage")) or None, "op": p.get("operator")}
        if kind == "Interconnector" and (j := join_register(name, reg)):
            props.update(j)
        cables.append({"type": "Feature", "geometry": {"type": "LineString",
                       "coordinates": [[round(x, 5), round(y, 5)] for x, y in g["coordinates"]]},
                       "properties": {k: v for k, v in props.items() if v not in (None, "")}})
        if kind != "Wind farm inter-array":
            for end in (g["coordinates"][0], g["coordinates"][-1]):
                if _coords_key(end) in land_ends:
                    cell = (round(end[0], 2), round(end[1], 2))  # merge ends within ~1 km into one landfall
                    landfalls.setdefault(cell, {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [round(end[0], 5), round(end[1], 5)]},
                                         "properties": {k: v for k, v in {"n": p.get("name"), "k": "Landfall",
                                                                           "c": kind, "kv": props.get("kv")}.items()
                                                        if v}})
    return cables, list(landfalls.values()) + sub


def _centre(g: dict) -> list[float] | None:
    if g["type"] == "Point":
        return [round(g["coordinates"][0], 5), round(g["coordinates"][1], 5)]
    ring = g["coordinates"] if g["type"] == "LineString" else (g["coordinates"][0][0] if g["type"] == "Polygon"
                                                               else g["coordinates"][0][0][0:] if g["coordinates"] else [])
    if g["type"] == "MultiPolygon":
        ring = g["coordinates"][0][0]
    if not ring:
        return None
    return [round(sum(c[0] for c in ring) / len(ring), 5), round(sum(c[1] for c in ring) / len(ring), 5)]


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("osm_gb_pbf")
    if snap is None:
        return {}
    pbf = pbf_in(snapshot_files(snap))
    t = ctx.tmp
    export_geojsonseq(pbf, t / "off.geojsonseq", {"power": {"cable", "transition", "substation"}},
                      nodes=True, ways=True, areas=True)
    with (t / "off.geojsonseq").open() as src:
        cables, points = features(src, register())
    out: dict[str, Path] = {}
    if cables:
        out["offshore_cables"] = tile(cables, t / "offshore_cables.pmtiles", "offshore_cables", 4, 12,
                                      ["--drop-densest-as-needed", "--extend-zooms-if-still-dropping"])
    if points:
        p = t / "landfalls.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": points}, separators=(",", ":")))
        out["landfalls"] = p
    return out
