"""Live UKPN constraint readings (1-minute): flow in amps at each constrained circuit/transformer against its trim
limit, rolled up to the substation it sits at (worst constraint shown). Placed by matching the substation name in the
constraint description to UKPN's GSP/BSP/primary site list."""

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
from .live_gsp import GspIndex

SOURCES = ["ukpn_constraints_meter"]
STOPS = [[0, "#f1eef6"], [25, "#bdc9e1"], [50, "#74a9cf"], [75, "#2b8cbe"], [100, "#045a8d"]]

LAYERS = [
    LayerMeta(
        id="live_constraints", title="Constraint loading now (UKPN)", group="Live", type="geojson", geom="point",
        minzoom=0, live=True, default_on=False,
        description="Busiest constraint per substation, as % of limit. Above 100% means curtailment. UKPN, every "
                    "minute.",
        color={"prop": "u", "type": "steps", "default": "#8a8a8a", "stops": STOPS},
        size={"prop": "u", "stops": [[0, 4], [50, 6], [100, 10]]}, opacity=0.9,
        popup=[["n", "Substation", None], ["u", "Of limit (worst constraint)", "%"], ["amp", "Flow", "A"],
               ["lim", "Trim limit", "A"], ["nc", "Constraints metered", None], ["b", "In breach", None],
               ["t", "Data as of (UTC)", None]],
        legend=[["#f1eef6", "<25% of limit"], ["#bdc9e1", "25-50"], ["#74a9cf", "50-75"], ["#2b8cbe", "75-100"],
                ["#045a8d", "100%+"]],
        attribution="Contains data from the UK Power Networks Open Data Portal, licensed under CC BY 4.0",
        inputs=["derived/site.parquet"],
    )
]


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def site_name(desc: str, code: str) -> str:
    s = re.sub(rf"\b{re.escape(code)}\b|^\s*-\s*|\b\d+(/\d+)?\s*kv\b", " ", desc or "", flags=re.IGNORECASE)
    s = re.sub(r"^[\s\-]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def site_rollup(text: str) -> list[dict[str, Any]]:
    """CSV text -> one record per substation code: worst utilisation reading, count, breach flag."""
    sites: dict[str, dict[str, Any]] = {}
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"):
        u = _num(r.get("utilisation_percentage"))
        if u is None or not r.get("timestamp"):
            continue
        code = r["constraint_id"].split("_")[0]
        s = sites.setdefault(code, {"code": code, "name": "", "n": 0, "u": -1.0, "breach": False, "ts": ""})
        s["n"] += 1
        s["name"] = s["name"] or site_name(r.get("constraint_description") or "", code)
        s["breach"] |= r.get("breach_flag") == "Y"
        s["ts"] = max(s["ts"], r["timestamp"])
        if u > s["u"]:
            s.update(u=u, amp=_num(r.get("present_amps_value")), lim=_num(r.get("trim_amps")))
    return list(sites.values())


def build_features(sites: list[dict[str, Any]], idx: GspIndex) -> tuple[list[dict], dict[str, Any]]:
    feats, miss = [], []
    for s in sites:
        ll = idx.find("UKPN", s["name"]) if s["name"] else None
        if ll is None:
            miss.append(s["name"] or s["code"])
            continue
        props = {"n": s["name"].title(), "u": round(s["u"], 1), "amp": round(s["amp"] or 0), "lim": round(s["lim"] or 0),
                 "nc": s["n"], "b": "Yes" if s["breach"] else "No", "t": s["ts"][11:16]}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(ll[1], 5), round(ll[0], 5)]},
                      "properties": props})
    return feats, {"sites": len(sites), "located": len(feats), "unlocated": miss}


def _index(data_dir: Path) -> GspIndex:
    import polars as pl
    p = data_dir / "derived" / "site.parquet"
    sites = []
    if p.exists():
        df = pl.read_parquet(p).filter((pl.col("operator") == "UKPN") & pl.col("lat").is_not_null()
                                       & pl.col("kind").is_in(["GSP", "BSP", "PRIMARY"]))
        sites = [("UKPN", r["name"], r["lat"], r["lon"]) for r in df.iter_rows(named=True)]
    return GspIndex(sites, [])


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("ukpn_constraints_meter")
    if snap is None:
        return {}
    f = next(x for x in snapshot_files(snap) if x.suffix == ".csv")
    feats, st = build_features(site_rollup(f.read_text(errors="replace")), _index(ctx.data_dir))
    print(f"live_constraints: {st}")
    if not feats:
        return {}
    out = ctx.tmp / "live_constraints.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"live_constraints": out}
