"""NESO GSP regions: the area each grid supply point serves, coloured by queued generation linked to
substations inside it. Reference implementation of an extra layer (see gridmap.extra)."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["neso_gsp_regions", "neso_fes_gsp_info"]

LAYERS = [
    LayerMeta(
        id="gsp_regions", title="GSP areas", group="Areas", type="pmtiles", geom="polygon",
        source_layer="gsp_regions", minzoom=0,
        description="Grid supply point areas shaded by queued generation. NESO, Feb 2026.",
        color={"prop": "q", "type": "steps", "default": "#cfd6dd",
               "stops": [[0, "#e8eef3"], [50, "#c6dbef"], [200, "#6baed6"], [500, "#2171b5"], [1000, "#08306b"]]},
        opacity=0.7,
        popup=[["n", "GSP", None], ["gsp", "GSP id", None], ["grp", "GSP group", None], ["q", "Queued gen", "MW"],
               ["c", "Connected (ECR)", "MW"]],
        legend=[["#e8eef3", "<50 MW queued"], ["#6baed6", "200–500"], ["#08306b", "≥1 GW"]],
        attribution="NESO GSP regions (NESO Open Data Licence)",
        inputs=["derived/site.parquet", "derived/site_queue.parquet"],
    )
]


def _queue_by_gsp(data_dir: Path) -> dict[str, tuple[float, float]]:
    """Queued/connected MW per NESO GSP id, rolling site_queue up the parent chain to the NESO GSP."""
    site_p, q_p = data_dir / "derived" / "site.parquet", data_dir / "derived" / "site_queue.parquet"
    if not (site_p.exists() and q_p.exists()):
        return {}
    site = pl.read_parquet(site_p).select("site_id", "parent_site_id")
    parent = dict(site.iter_rows())
    q = pl.read_parquet(q_p)
    out: dict[str, list[float]] = {}
    for sid, queued, conn in q.select("site_id", "queued_mw", "connected_mw").iter_rows():
        cur, hops = sid, 0
        while cur and not cur.startswith("NESO:") and hops < 4:
            cur, hops = parent.get(cur), hops + 1
        if cur and cur.startswith("NESO:"):
            acc = out.setdefault(cur, [0.0, 0.0])
            acc[0] += queued or 0
            acc[1] += conn or 0
    return {k: (v[0], v[1]) for k, v in out.items()}


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("neso_gsp_regions")
    if snap is None:
        return {}
    z = next(f for f in snapshot_files(snap) if f.suffix == ".zip")
    with zipfile.ZipFile(z) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".geojson") and "4326" in n)
        fc = json.loads(zf.read(name))

    # NESO site ids are "NESO:<normalised name>"; map GSP ids (e.g. LEGA_1) -> names via the FES GSP list
    names: dict[str, str] = {}
    info = latest_snapshot("neso_fes_gsp_info")
    if info:
        df = pl.read_csv(snapshot_files(info)[0], infer_schema=False, encoding="utf8-lossy")
        df = df.rename({c: c.lstrip("﻿").strip() for c in df.columns})
        names = dict(df.select("GSP ID", "Name").iter_rows())
    from ..match import name_norm  # local: avoid import cycle at package import

    q = _queue_by_gsp(ctx.data_dir)
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        gsps = [g.strip() for g in str(p.get("GSPs") or "").split("|") if g.strip()]
        nm = " / ".join(names.get(g, g) for g in gsps)
        qq = cc = 0.0
        for g in gsps:
            v = q.get(f"NESO:{name_norm(names.get(g, ''))}")
            if v:
                qq, cc = qq + v[0], cc + v[1]
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"n": nm, "gsp": ",".join(gsps), "grp": p.get("GSPGroup"),
                                     "q": round(qq, 1) if qq else None, "c": round(cc, 1) if cc else None}})
    src = ctx.tmp / "gsp_regions.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    out = ctx.tmp / "gsp_regions.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "gsp_regions", "-Z0", "-z11",
                    "--simplification=4", "--coalesce-densest-as-needed", "--detect-shared-borders", str(src)],
                   check=True)
    return {"gsp_regions": out}
