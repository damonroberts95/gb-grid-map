"""Primary and BSP supply-area polygons from every operator that publishes them, joined to our sites and
coloured by that site's demand headroom. NGED publishes only a PDF (dsa041) for its spatial areas, so it
has no polygons here; NPG/SSEN/ENW/UKPN/SPEN are covered."""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta

# (source id, operator, level) -- level P = primary, B = BSP-level. Order matters only for tippecanoe.
AREA_SOURCES: list[tuple[str, str, str]] = [
    ("ukpn_primary_areas", "UKPN", "P"), ("ukpn_grid_areas", "UKPN", "B"),
    ("enw_primary_polygons", "ENW", "P"), ("enw_bsp_polygons", "ENW", "B"),
    ("ssen_primary_supply_areas", "SSEN", "P"), ("ssen_bsp_gsp_supply_areas", "SSEN", "B"),
    ("spen_polygons_spd", "SPEN", "P"), ("spen_polygons_spm", "SPEN", "P"),
    ("npg_service_areas", "NPG", "P"),
]
SOURCES = [s for s, _, _ in AREA_SOURCES]

STOPS = [[-1e9, "#440154"], [0.001, "#3b528b"], [2, "#2c728e"], [5, "#21918c"], [10, "#28ae80"],
         [20, "#7ad151"], [40, "#fde725"]]

LAYERS = [
    LayerMeta(
        id="primary_areas", title="Supply areas by demand headroom", group="Areas", type="pmtiles",
        geom="polygon", source_layer="primary_areas", minzoom=5, default_on=False, opacity=0.8,
        description="Area fed by each primary (and, zoomed out, each bulk supply point) substation, shaded by the "
                    "demand headroom the operator publishes for it (bright = lots of spare capacity, dark = little "
                    "or none, grey = not published). Polygons and headroom from each network operator's open data, "
                    "latest releases 2026. NGED publishes no polygons.",
        color={"prop": "d", "type": "steps", "default": "#c9ced3", "stops": STOPS},
        popup=[["n", "Substation", None], ["lv", "Level", None], ["op", "Operator", None],
               ["d", "Demand headroom", "MW or MVA (see unit)"], ["u", "Unit", None], ["g", "Generation headroom", "MW"],
               ["c", "LV readings coverage", "%"]],
        legend=[["#440154", "Over-subscribed (<0)"], ["#3b528b", "0-2"], ["#2c728e", "2-5"], ["#21918c", "5-10"],
                ["#28ae80", "10-20"], ["#7ad151", "20-40"], ["#fde725", "40+ MW"], ["#c9ced3", "Not published"]],
        attribution="Supply-area polygons and headroom: UK Power Networks, Electricity North West / SP Energy "
                    "Networks, SSEN Distribution, SP Energy Networks and Northern Powergrid open data portals "
                    "(CC BY 4.0; NPG Open Data Licence v1.0)",
        inputs=["derived/site.parquet", "derived/asset_site.parquet", "derived/site_capacity.parquet",
                "norm/UKPN/asset.parquet"],
    )
]

JOIN_STATS: dict[str, tuple[int, int, int]] = {}  # "op:level" -> (polygons, joined, with demand headroom)


def _round(c: Any, nd: int = 5) -> Any:
    if isinstance(c[0], (int, float)):  # a position: drop any altitude
        return [round(c[0], nd), round(c[1], nd)]
    return [_round(x, nd) for x in c]


def _centre(geom: dict) -> tuple[float, float]:
    """Rough (lat, lon) from the first ring's bbox: only used to break name ties."""
    c = geom["coordinates"]
    while isinstance(c[0][0], list):
        c = c[0]
    xs, ys = [p[0] for p in c], [p[1] for p in c]
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def _names(p: dict, op: str, snap_id: str, level: str) -> list[str]:
    """Candidate site names for a polygon, best first."""
    if op == "UKPN":
        return [p.get("primary") or p.get("grid_site") or ""]
    if op == "SSEN":
        if level == "P":
            return [p.get("primary_name_2025") or ""]
        return [p.get("bsp_name") or "", p.get("bsp_1") or "", p.get("bsp_2") or ""]
    if op == "SPEN":
        raw = p.get("primary") or p.get("psgroup") or p.get("primary_su") or ""
        return [x.strip() for x in raw.split("/")]
    if op == "NPG":
        return [p.get("primary") or p.get("name") or ""]
    return []


def _build_lookups(data_dir: Path):
    from ..match import NameIndex  # local: avoid import cycle at package import

    d = data_dir / "derived"
    site = pl.read_parquet(d / "site.parquet")
    asset_site = dict(pl.read_parquet(d / "asset_site.parquet").iter_rows())
    cap = pl.read_parquet(d / "site_capacity.parquet")
    capd: dict[str, dict] = {}
    for r in cap.iter_rows(named=True):
        mw, mva = r["demand_headroom_mw"], r["demand_headroom_mva"]
        capd[r["site_id"]] = {"d": mw if mw is not None else mva, "u": None if mw is None and mva is None else
                              ("MW" if mw is not None else "MVA"), "g": r["gen_headroom_mw"]}
    # UKPN floc -> site (the polygons carry flocs, which beat names)
    floc: dict[str, str] = {}
    ukpn = data_dir / "norm" / "UKPN" / "asset.parquet"
    if ukpn.exists():
        for uid, nat in pl.read_parquet(ukpn).select("asset_uid", "native_ids").iter_rows():
            try:
                f = json.loads(nat or "{}").get("floc")
            except ValueError:
                f = None
            if f and uid in asset_site:
                floc[f] = asset_site[uid]
    return NameIndex(site), asset_site, capd, floc, dict(site.select("site_id", "name").iter_rows())


def _resolve(feat_props: dict, geom: dict, op: str, snap_id: str, level: str, idx, asset_site, floc) -> str | None:
    kind = "PRIMARY" if level == "P" else "BSP"
    if op == "ENW":
        num = feat_props.get("pry_number" if level == "P" else "bsp_number")
        return asset_site.get(f"ENW:{kind}:{num}") if num else None
    if op == "UKPN":
        fl = feat_props.get("primary_floc" if level == "P" else "grid_site_floc")
        if fl and fl in floc:
            return floc[fl]
    near = _centre(geom)
    kinds = (kind,) if op != "NPG" or level == "P" else ("BSP",)
    for n in _names(feat_props, op, snap_id, level):
        if n and (s := idx.resolve(op, n, kinds, near)):
            return s
    return None


def _npg_level(p: dict) -> str | None:
    return {"Primary": "P", "BSP": "B"}.get(p.get("substation_class"))


def _features(idx, asset_site, capd, floc, snames) -> list[dict]:
    out: list[dict] = []
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    seen: set[tuple] = set()
    has_bsp: set[tuple[str, str]] = set()
    for sid, op, level in AREA_SOURCES:
        snap = latest_snapshot(sid)
        if snap is None:
            continue
        for f in snapshot_files(snap):
            if f.suffix != ".geojson":
                continue
            if sid == "ssen_bsp_gsp_supply_areas" and "bsp_supply" not in f.name:
                continue  # GSP-only files are covered by the NESO GSP layer
            area = "SHEPD" if "shepd" in f.name else "SEPD"
            for ft in json.loads(f.read_text())["features"]:
                g, p = ft.get("geometry"), ft["properties"]
                if not g:
                    continue
                lv = level
                if sid == "npg_service_areas":
                    lv = _npg_level(p)
                    if lv is None:
                        continue
                site_id = _resolve(p, g, op, sid, lv, idx, asset_site, floc)
                c = capd.get(site_id, {}) if site_id else {}
                name = snames.get(site_id or "") or (_names(p, op, sid, lv) or [""])[0]
                if not name or name.isdigit():
                    name = f"{op} {'primary' if lv == 'P' else 'BSP'} {p.get('pry_number') or p.get('bsp_number') or name}"
                key = (op, lv, site_id or name, json.dumps(g["coordinates"][0][0][:2]) if g["type"] == "MultiPolygon"
                       else json.dumps(g["coordinates"][0][:2]))
                if key in seen:
                    continue
                seen.add(key)
                props: dict[str, Any] = {"n": name.title() if name.isupper() else name,
                                         "op": op, "lv": "Primary" if lv == "P" else "BSP"}
                if c.get("d") is not None:
                    props["d"] = round(c["d"], 2)
                    props["u"] = c["u"]
                if c.get("g") is not None:
                    props["g"] = round(c["g"], 2)
                if p.get("lv_reading_coverage") is not None:
                    props["c"] = round(float(p["lv_reading_coverage"]), 1)
                s = counts[f"{op}:{lv}"]
                s[0] += 1
                s[1] += site_id is not None
                s[2] += "d" in props
                out.append({"type": "Feature", "geometry": {"type": g["type"], "coordinates": _round(g["coordinates"])},
                            "properties": props, "_k": (op, area, lv)})
                if lv == "B":
                    has_bsp.add((op, area))
    for ft in out:
        op, area, lv = ft.pop("_k")
        # BSP polygons carry z5-8, primaries z8+; where an operator has no BSP polygons primaries start at z5
        tp = {"minzoom": 5, "maxzoom": 8} if lv == "B" else {"minzoom": 8 if (op, area) in has_bsp else 5}
        ft["tippecanoe"] = tp
    JOIN_STATS.clear()
    JOIN_STATS.update({k: tuple(v) for k, v in counts.items()})
    return out


def build(ctx: Ctx) -> dict[str, Path]:
    d = ctx.data_dir / "derived"
    if not all((d / f).exists() for f in ("site.parquet", "asset_site.parquet", "site_capacity.parquet")):
        return {}
    idx, asset_site, capd, floc, snames = _build_lookups(ctx.data_dir)
    feats = _features(idx, asset_site, capd, floc, snames)
    if not feats:
        return {}
    for k, (n, j, dm) in sorted(JOIN_STATS.items()):
        print(f"area_primary {k}: polygons={n} joined={j} with_demand_headroom={dm}")
    src = ctx.tmp / "area_primary.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / "area_primary.pmtiles"
    subprocess.run(["tippecanoe", "-q", "-f", "-o", str(out), "-l", "primary_areas", "-Z5", "-z13",
                    "--simplification=6", "--simplify-only-low-zooms", "--coalesce-densest-as-needed",
                    "--detect-shared-borders", "--maximum-tile-bytes=450000", str(src)], check=True)
    return {"primary_areas": out}
