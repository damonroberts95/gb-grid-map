"""Transmission context: SSEN Transmission substation sites (with ETYS fault levels) and the NESO ETYS
study boundaries. Only SSEN Transmission publishes open site data; NGET/SPT sites come from OSM (extra osm_power)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from .lines import line_geom, read_shp_zip

SOURCES = ["ssent_sites_supergrid", "ssent_sites_grid", "ssent_etys_fault_levels_winter_y1", "neso_etys_boundaries"]
ATTR_SSEN = "Contains data from the SSEN Transmission open data portal, licensed under CC BY 4.0."

LAYERS = [
    LayerMeta(
        id="ssent_substations", title="SSEN Transmission substations", group="Network", type="geojson", geom="point",
        minzoom=5,
        description="132-400 kV substations in north Scotland. SSEN, Jan 2026. ETYS 2025 fault levels where matched.",
        color={"prop": "kv", "type": "steps", "default": "#4575b4",
               "stops": [[0, "#4575b4"], [200, "#762a83"], [350, "#1b1b1b"]]},
        size={"prop": "kv", "stops": [[0, 3.5], [200, 5], [350, 6.5]]},
        popup=[["n", "Substation", None], ["kv", "Voltage", "kV"], ["f3", "3-phase fault level (RMS break)", "kA"],
               ["f1", "1-phase fault level (RMS break)", "kA"], ["fk", "Fault level voltage", "kV"]],
        legend=[["#4575b4", "132 kV"], ["#762a83", "220-275 kV"], ["#1b1b1b", "320-400 kV"]],
        attribution=ATTR_SSEN + " Fault levels: SSEN Transmission ETYS 2025.",
    ),
    LayerMeta(
        id="etys_boundaries", title="ETYS boundaries", group="Network", type="geojson", geom="line", minzoom=3,
        description="Transmission study boundaries. NESO ETYS, March 2025.",
        color="#d95f02", size=2, opacity=0.8, popup=[["b", "Boundary", None]],
        legend=[["#d95f02", "ETYS boundary"]],
        attribution="Supported by National Energy SO Open Data",
    ),
]


def _norm(name: str) -> str:
    s = re.sub(r"\b(substation|grid|supergrid|s/s|training centre|switching station|ps|power station)\b", " ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def _faults(snap: Path | None) -> dict[str, list[tuple[float, float | None, float | None]]]:
    """normalised ETYS location -> [(kV, 3-ph RMS break kA, 1-ph RMS break kA), ...]. Null stays None."""
    out: dict[str, list[tuple[float, float | None, float | None]]] = {}
    if snap is None:
        return out
    df = pl.read_csv(snapshot_files(snap)[0], separator=";", encoding="utf8-lossy", infer_schema=False)
    df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})

    def num(v: str | None) -> float | None:
        try:
            return round(float(v), 2) if v not in (None, "") else None
        except ValueError:
            return None

    for r in df.iter_rows(named=True):
        kv = num(r.get("voltage_kv"))
        if kv is None:
            continue
        out.setdefault(_norm(r["location"]), []).append(
            (kv, num(r.get("symmetrical_three_phase_rms_break_current_ka")),
             num(r.get("symmetrical_single_phase_rms_break_current_ka"))))
    return out


def site_features(fcs: list[dict], faults: dict) -> list[dict]:
    feats = []
    for fc in fcs:
        for f in fc["features"]:
            p = f["properties"]
            gp = p.get("geo_point_2d")
            if not gp or gp.get("lat") is None:
                continue
            try:
                kv = float(p["voltage"]) / 1000
            except (TypeError, ValueError, KeyError):
                kv = None
            props: dict = {"n": re.sub(r"(\d)Kv", r"\1 kV", (p.get("name") or "").title()), "kv": kv if kv is not None else 0}
            rows = faults.get(_norm(p.get("name") or ""), [])
            # prefer the ETYS row at the site's own voltage, else the highest-voltage row for the location
            pick = next((r for r in rows if kv and abs(r[0] - kv) < 1), None) or (max(rows) if rows else None)
            if pick:
                props.update({"fk": pick[0], "f3": pick[1], "f1": pick[2]})
            feats.append({"type": "Feature", "geometry": {"type": "Point",
                          "coordinates": [round(gp["lon"], 5), round(gp["lat"], 5)]}, "properties": props})
    return feats


def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    fcs = []
    for sid in ("ssent_sites_supergrid", "ssent_sites_grid"):
        snap = latest_snapshot(sid)
        if snap:
            fcs.append(json.loads(next(f for f in snapshot_files(snap) if f.suffix == ".geojson").read_text()))
    if fcs:
        feats = site_features(fcs, _faults(latest_snapshot("ssent_etys_fault_levels_winter_y1")))
        out["ssent_substations"] = ctx.tmp / "ssent_substations.geojson"
        out["ssent_substations"].write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                       separators=(",", ":")))
    snap = latest_snapshot("neso_etys_boundaries")
    if snap:
        z = next(f for f in snapshot_files(snap) if f.suffix == ".zip")
        feats = [{"type": "Feature", "geometry": line_geom(parts), "properties": {"b": (row["Boundary_n"] or "")[:60]}}
                 for row, parts in read_shp_zip(z, 3857, {"Boundary_n"})]
        out["etys_boundaries"] = ctx.tmp / "etys_boundaries.geojson"
        out["etys_boundaries"].write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                     separators=(",", ":")))
    return out
