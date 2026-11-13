"""SSEN NeRDA monitored substations (4,600 SEPD sites: primaries, a few BSPs, and ~3,800 secondary substations with
LV feeder monitoring), coloured by kind or, for secondaries the background sweep has read, by latest peak LV feeder
current. Static data: weekly snapshot fetched by extra.nerda_client; live values: data/derived/nerda_latest.parquet
written by server/nerda.py. Click a feature for the on-demand live readings table (/api/nerda/<nrn>).
Licence: NeRDA terms (internal use only) - check before sharing the map publicly."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import Ctx, LayerMeta
from . import nerda_client as nc

SOURCES = ["ssen_nerda_static"]
MAX_AGE_H = 3  # a latest value older than this (as of build time) is a dead feed, not "live"
LV_AMP_STOPS = [(100, "a0"), (200, "a1"), (300, "a2")]  # A at the busiest LV feeder; >= last = "a3"

LAYERS = [
    LayerMeta(
        id="nerda_sites", title="SSEN live monitored substations (NeRDA)", group="Live", type="geojson", geom="point",
        minzoom=6, live=True, default_on=False,
        description="SSEN South live-monitored substations. LV feeder current every few minutes. Internal use only. "
                    "NeRDA.",
        color={"prop": "c", "type": "categorical", "default": "#8a8a8a", "stops": [
            ["bsp", "#08306b"], ["primary", "#2171b5"], ["lvm", "#9aa5b1"], ["sec", "#c9c9c9"],
            ["a0", "#4daf4a"], ["a1", "#e6c229"], ["a2", "#f28e2b"], ["a3", "#d7301f"], ["other", "#b0b0b0"]]},
        size={"prop": "z", "stops": [[1, 2.5], [2, 4], [3, 7]]}, opacity=0.9,
        popup=[["n", "Substation", None], ["nrn", "NRN", None], ["k", "Type", None], ["lvf", "LV feeders monitored", None],
               ["i_max", "Busiest LV feeder (latest)", "A"], ["t", "Reading as of (UTC)", None]],
        legend=[["#08306b", "Grid / bulk supply point"], ["#2171b5", "Primary"], ["#9aa5b1", "Secondary, LV monitored"],
                ["#4daf4a", "LV feeder < 100 A"], ["#e6c229", "100-200 A"], ["#f28e2b", "200-300 A"],
                ["#d7301f", "300+ A"]],
        attribution="Contains data from SSEN NeRDA (Network Rich Data Access); internal use under the NeRDA terms",
        inputs=["derived/nerda_latest.parquet"],
    )
]

KIND_LABEL = {"bsp": "Grid / bulk supply point", "primary": "Primary", "secondary": "Secondary", "other": "Other"}


def _amp_class(a: float) -> str:
    for lim, c in LV_AMP_STOPS:
        if a < lim:
            return c
    return "a3"


def _latest(data_dir: Path, now: datetime) -> dict[str, dict]:
    p = data_dir / "derived" / "nerda_latest.parquet"
    if not p.exists():
        return {}
    import polars as pl
    out = {}
    for r in pl.read_parquet(p).iter_rows(named=True):
        try:
            age = (now - datetime.fromisoformat(r["t"])).total_seconds() / 3600
        except (TypeError, ValueError, AttributeError):
            continue
        if r["i_max"] is not None and age <= MAX_AGE_H:
            out[r["nrn"]] = r
    return out


def build_features(sites: list[dict], latest: dict[str, dict]) -> list[dict]:
    feats = []
    for s in sites:
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None or lon is None or not (49.5 <= lat <= 61 and -9 <= lon <= 2.5):
            continue
        k = s["k"]
        lvf = len([ln for ln in nc.lv_feeders(s["lines"]) if nc.line_current_id(ln)]) if k == "secondary" else 0
        live = latest.get(s["nrn"])
        c = _amp_class(live["i_max"]) if live else ("lvm" if lvf else "sec" if k == "secondary" else k)
        props = {"n": s["n"], "nrn": s["nrn"], "k": KIND_LABEL.get(k, k), "c": c, "z": 3 if k == "bsp" else 2 if k == "primary" else 1}
        if lvf:
            props["lvf"] = lvf
        if live:
            props.update(i_max=live["i_max"], t=live["t"][11:16])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                      "properties": props})
    return feats


def lv_join_rate(sites: list[dict], data_dir: Path, max_m: float = 40.0) -> dict:
    """Share of NeRDA secondaries with an SSEN lv_site (SEPD) within max_m metres. The NRN is not in the SSEN
    open lv_site table (its 'number' is the site number), so this is a spatial join; the NRN digits (primary+HV
    feeder+secondary, 10 digits) are the SSEN smart-meter dataset_id prefix (extra.lv_smart)."""
    import math

    import polars as pl
    p = data_dir / "norm" / "SSEN" / "lv_site.parquet"
    if not p.exists():
        return {}
    d = pl.read_parquet(p).filter(pl.col("asset_uid").str.contains("SEPD") & pl.col("lat").is_not_null())
    cell = 0.002
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for lat, lon in zip(d["lat"].to_list(), d["lon"].to_list(), strict=True):
        grid.setdefault((int(lat / cell), int(lon / cell)), []).append((lat, lon))
    sec = [s for s in sites if s["k"] == "secondary" and s.get("lat") is not None]
    hit = 0
    for s in sec:
        gi, gj = int(s["lat"] / cell), int(s["lon"] / cell)
        for i in (gi - 1, gi, gi + 1):
            for j in (gj - 1, gj, gj + 1):
                if any(math.hypot((a - s["lat"]) * 111_000, (b - s["lon"]) * 70_000) <= max_m
                       for a, b in grid.get((i, j), [])):
                    break
            else:
                continue
            hit += 1
            break
    return {"secondaries": len(sec), "lv_site_within_m": max_m, "joined": hit,
            "rate": round(hit / len(sec), 3) if sec else None, "lv_sites_sepd": d.height}


def build(ctx: Ctx) -> dict[str, Path]:
    idx = nc.load_index()
    if idx is None:
        return {}
    latest = _latest(ctx.data_dir, datetime.now(UTC))
    feats = build_features(idx["sites"], latest)
    kinds: dict[str, int] = {}
    for s in idx["sites"]:
        kinds[s["k"]] = kinds.get(s["k"], 0) + 1
    print(f"nerda_sites: {len(feats)} points, kinds {kinds}, live values {len(latest)}, "
          f"lv_site join {lv_join_rate(idx['sites'], ctx.data_dir)}")
    out = ctx.tmp / "nerda_sites.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"nerda_sites": out}
