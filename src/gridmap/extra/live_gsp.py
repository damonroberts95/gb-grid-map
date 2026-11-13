"""Live grid-supply-point flows: NGED per-GSP demand / generation / solar / wind (5-minute rows) and UKPN grid-
transformer flows aggregated per GSP (half-hourly), as points at the GSP, coloured by net import from the
transmission system and sized by its magnitude. Nothing at GSP level is published live by the other four DNOs
(NPG operational metering lags 75-135 min, SPEN's flow datasets 4+ days, SSEN's NeRDA needs a key)."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta

NGED_AREAS = ["east_midlands", "south_wales", "south_west", "west_midlands"]
SOURCES = ["ukpn_sgt_live", "neso_fes_gsp_info"] + [f"nged_live_gsp_{a}" for a in NGED_AREAS]

NGED_ATTR = ("Contains data (c) National Grid Electricity Distribution, used under the NGED (WPD) Open Data Licence")
UKPN_ATTR = "Contains data from the UK Power Networks Open Data Portal, licensed under CC BY 4.0"
MAX_AGE_MIN = 180  # a row more than 3 h older than the snapshot is a dead feed, not "live"

STOPS = [[-100000, "#e69f00"], [0, "#c6dbef"], [25, "#6baed6"], [100, "#2171b5"], [250, "#08306b"]]

LAYERS = [
    LayerMeta(
        id="live_gsp", title="Grid supply point flows now", group="Live", type="geojson", geom="point", minzoom=4,
        live=True, default_on=False,
        description="Latest measured power drawn from the transmission grid at each GSP where a network operator "
                    "publishes it live: NGED (all 55 GSPs, 5-minute data, with local generation, solar and wind) and "
                    "UK Power Networks (half-hourly transformer flows). Blue = importing, orange = exporting back "
                    "to the grid. Other networks do not publish GSP flows live.",
        color={"prop": "mw", "type": "steps", "default": "#8a8a8a", "stops": STOPS},
        size={"prop": "a", "stops": [[0, 3], [50, 5], [150, 8], [400, 12], [1000, 16]]}, opacity=0.9,
        popup=[["n", "Grid supply point", None], ["op", "Network operator", None], ["mw", "Import from grid", "MW"],
               ["dem", "Demand incl. local generation", "MW"], ["gen", "Local generation", "MW"],
               ["sol", "of which solar", "MW"], ["wnd", "of which wind", "MW"], ["mvar", "Reactive power", "MVAr"],
               ["t", "Data as of (UTC)", None]],
        legend=[["#e69f00", "Exporting to grid"], ["#c6dbef", "0-25 MW import"], ["#6baed6", "25-100"],
                ["#2171b5", "100-250"], ["#08306b", "250+"]],
        attribution=f"{NGED_ATTR}; {UKPN_ATTR}; GSP locations: NGED, UKPN, NESO FES GSP list (NESO Open Data Licence)",
        inputs=["derived/site.parquet"],
    )
]


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(v, n)


def hhmm(ts: str | None) -> str | None:
    """'2026-11-12T16:40:00' / '...+00:00' -> '16:40'."""
    return ts[11:16] if ts and len(ts) >= 16 else None


def nged_rows(files: dict[str, str]) -> list[dict[str, Any]]:
    """{slug filename: csv text} -> one latest-row record per GSP. Names come from the file name
    ('upper-boat-132_swales.csv' -> 'Upper Boat 132')."""
    out = []
    for fname, text in files.items():
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        rows = [r for r in rows if r.get("Timestamp") and _num(r.get("Import")) is not None]
        if not rows:
            continue
        last = max(rows, key=lambda r: r["Timestamp"])
        name = re.sub(r"_[a-z]+\.csv$", "", fname).replace("-", " ").title()
        imp, gen = _num(last["Import"]), _num(last.get("Generation"))
        out.append({"n": name, "op": "NGED", "ts": last["Timestamp"], "mw": imp, "gen": gen,
                    "dem": _num(last.get("Net Demand")), "sol": _num(last.get("Solar")), "wnd": _num(last.get("Wind"))})
    return out


def ukpn_rows(text: str) -> list[dict[str, Any]]:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"):
        if r.get("sgt") != "AGGREGATE" or _num(r.get("activepower")) is None:
            continue
        nm = re.sub(r"\((\w+)\)", lambda m: f"({m[1].upper()})", r["gsp"].title())
        out.append({"n": nm, "raw": r["gsp"], "op": "UKPN", "ts": r["time"], "mw": _num(r["activepower"]),
                    "mvar": _num(r.get("reactivepower"))})
    return out


class GspIndex:
    """Name -> (lat, lon) for GSPs, from the operator's own site list (preferred) and NESO's FES GSP list."""

    def __init__(self, sites: list[tuple[str, str, float, float]], fes: list[tuple[str, float, float]]):
        from ..match import name_norm
        self._norm = name_norm
        self.sites: dict[str, list[tuple[str, str, float, float]]] = {}
        for op, name, lat, lon in sites:
            self.sites.setdefault(op, []).append((name_norm(name), name, lat, lon))
        self.fes = [(name_norm(n), n, lat, lon) for n, lat, lon in fes]

    @staticmethod
    def _pick(cands: list[tuple[str, str, float, float]], raw: str) -> tuple[float, float] | None:
        if len(cands) > 1:
            nums = re.findall(r"\d+", raw)
            by_num = [c for c in cands if nums and all(n in re.findall(r"\d+", c[1]) for n in nums)]
            cands = by_num or cands
        if not cands:
            return None
        lats, lons = [c[2] for c in cands], [c[3] for c in cands]
        if (max(lats) - min(lats)) * 111 > 3 or (max(lons) - min(lons)) * 70 > 3:
            return None  # namesakes far apart: do not guess
        return cands[0][2], cands[0][3]

    def find(self, op: str, raw: str) -> tuple[float, float] | None:
        key = self._norm(re.sub(r"\(.*?\)|\bsec\d\b", " ", raw, flags=re.IGNORECASE))
        if not key:
            return None
        for pool in (self.sites.get(op, []), self.fes):
            exact = [c for c in pool if c[0] == key]
            if exact and (hit := self._pick(exact, raw)):
                return hit
            pre = [c for c in pool if c[0].startswith(key + " ")]
            if pre and (hit := self._pick(pre, raw)):
                return hit
        return None


def build_features(rows: list[dict[str, Any]], idx: GspIndex) -> tuple[list[dict], dict]:
    from datetime import datetime
    newest = max((r["ts"][:16] for r in rows), default=None)
    feats, missed, stale = [], [], 0
    for r in rows:
        if newest and (datetime.fromisoformat(newest) - datetime.fromisoformat(r["ts"][:16])
                       ).total_seconds() > MAX_AGE_MIN * 60:
            stale += 1
            continue
        ll = idx.find(r["op"], r.get("raw") or r["n"])
        if ll is None:
            missed.append(r["n"])
            continue
        props = {"n": r["n"], "op": r["op"], "mw": _r(r["mw"]), "a": _r(abs(r["mw"])), "dem": _r(r.get("dem")),
                 "gen": _r(r.get("gen")), "sol": _r(r.get("sol")), "wnd": _r(r.get("wnd")), "mvar": _r(r.get("mvar")),
                 "t": hhmm(r["ts"])}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(ll[1], 5), round(ll[0], 5)]},
                      "properties": {k: v for k, v in props.items() if v is not None}})
    return feats, {"rows": len(rows), "located": len(feats), "unlocated": missed, "stale": stale}


def _index(data_dir: Path) -> GspIndex:
    import polars as pl
    sites: list[tuple[str, str, float, float]] = []
    p = data_dir / "derived" / "site.parquet"
    if p.exists():
        df = pl.read_parquet(p).filter((pl.col("kind") == "GSP") & pl.col("lat").is_not_null()
                                       & pl.col("operator").is_in(["NGED", "UKPN"]))
        sites = [(r["operator"], r["name"], r["lat"], r["lon"]) for r in df.iter_rows(named=True)]
    fes: list[tuple[str, float, float]] = []
    snap = latest_snapshot("neso_fes_gsp_info")
    if snap:
        text = snapshot_files(snap)[0].read_text(encoding="utf-8-sig", errors="replace")
        for r in csv.DictReader(io.StringIO(text)):
            lat, lon = _num(r.get("Latitude")), _num(r.get("Longitude"))
            if lat is not None and lon is not None and r.get("Name"):
                fes.append((r["Name"], lat, lon))
    return GspIndex(sites, fes)


def build(ctx: Ctx) -> dict[str, Path]:
    rows: list[dict[str, Any]] = []
    for area in NGED_AREAS:
        snap = latest_snapshot(f"nged_live_gsp_{area}")
        if snap:
            rows += nged_rows({f.name: f.read_text(errors="replace") for f in snapshot_files(snap)
                               if f.suffix == ".csv"})
    snap = latest_snapshot("ukpn_sgt_live")
    if snap:
        rows += ukpn_rows(next(f for f in snapshot_files(snap) if f.suffix == ".csv").read_text(errors="replace"))
    if not rows:
        return {}
    feats, st = build_features(rows, _index(ctx.data_dir))
    print(f"live_gsp: {st}")
    if not feats:
        return {}
    out = ctx.tmp / "live_gsp.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"live_gsp": out}
