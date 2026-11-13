"""Distribution Future Energy Scenarios (DFES): peak-demand growth per primary substation, 2025 -> 2035.

One comparable metric across operators: peak demand (MW; ENW publishes MVA) at each primary under each operator's
Holistic Transition scenario (all four publish one; ENW calls it '2-HOLISTIC_TRANSITION', SPEN 'HT').
UKPN does not publish demand, only demand *headroom* by year, so peak = firm capacity - headroom where
firm capacity = headroom_mw / headroom_percent (derived, flagged in the popup).
NGED and SSEN publish DFES only per local authority (no primary-level data), so they are not on this layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from .dem_lct import _csv, _feat, _i, _spen_hit, site_index

SOURCES = ["ukpn_dfes_network_headroom", "npg_dfes_wide", "enw_dfes_primary", "dem_spen_dfes_spd", "dem_spen_dfes_spm"]
Y0, Y1 = 2025, 2035
MIN_BASE_MW = 0.5  # below this a % growth is noise

LAYERS = [
    LayerMeta(
        id="dfes", title="Forecast demand growth (DFES)", group="Demand & LV", type="geojson", geom="point", minzoom=0,
        description="Peak demand growth by primary, 2025 to 2035. Holistic Transition. DFES 2026. UKPN, NPg, ENW, "
                    "SPEN.",
        color={"prop": "g", "type": "steps", "default": "#bdbdbd",
               "stops": [[-1000, "#d9d9d9"], [0, "#c6dbef"], [25, "#6baed6"], [50, "#2171b5"], [100, "#08306b"]]},
        size={"prop": "gm", "stops": [[0, 3], [2, 4.5], [5, 6.5], [15, 9], [40, 13]]},
        popup=[["nm", "Primary substation", None], ["op", "Network operator", None], ["p0", "Peak demand 2025", "MW"],
               ["p1", "Peak demand 2035", "MW"], ["gm", "Growth 2025-35", "MW"], ["g", "Growth 2025-35", "%"],
               ["ev", "EVs in 2035", None], ["hp", "Heat pumps in 2035", None], ["note", "Note", None]],
        legend=[["#d9d9d9", "demand falls"], ["#c6dbef", "0-25 % growth"], ["#6baed6", "25-50 %"],
                ["#2171b5", "50-100 %"], ["#08306b", "100 %+"], ["#bdbdbd", "no baseline or no % available"]],
        attribution="DFES 2026 data: UK Power Networks (CC BY 4.0), Northern Powergrid (NPg Open Data Licence v1.0), "
                    "Electricity North West (CC BY 4.0), SP Energy Networks (CC BY 4.0). Scenario: Holistic Transition.",
        inputs=["derived/site.parquet", "derived/asset_site.parquet"],
    )
]


def _row(nm, op, hit, p0, p1, ev=None, hp=None, note=None) -> dict | None:
    if hit is None or p0 is None or p1 is None:
        return None
    gm = p1 - p0
    return _feat(hit[0], hit[1], nm=nm, op=op, p0=round(p0, 1), p1=round(p1, 1), gm=round(gm, 1),
                 g=round(100 * gm / p0, 1) if p0 >= MIN_BASE_MW else None, ev=_i(ev), hp=_i(hp), note=note)


def _ukpn(idx: dict) -> list[dict]:
    d = _csv("ukpn_dfes_network_headroom")
    if d is None:
        return []
    d = d.filter((pl.col("category") == "Demand Headroom") & (pl.col("physical_level") == "PRIMARY")
                 & (pl.col("scenario") == "Holistic Transition") & pl.col("year").is_in([Y0, Y1]))
    # firm capacity = headroom / (headroom % / 100); needs a non-zero % (rounded %, so use the larger |%| year)
    d = d.with_columns((pl.col("headroom_mw") * 100 / pl.col("headroom_percent")).alias("cap"),
                       pl.col("headroom_percent").abs().alias("ap"))
    out = []
    for floc, g in d.group_by("sitefunctionallocation"):
        best = g.filter(pl.col("ap") > 5).sort("ap", descending=True)
        rows = {r["year"]: r["headroom_mw"] for r in g.iter_rows(named=True)}
        if best.is_empty() or Y0 not in rows or Y1 not in rows:
            continue
        cap = best["cap"][0]
        hit = idx.get(f"floc:{floc[0]}")
        nm = g["substation_name"][0]
        r = _row(nm, "UKPN", (hit[2], hit[3], hit[0]) if hit else None, cap - rows[Y0], cap - rows[Y1],
                 note="Peak demand derived from published headroom and implied firm capacity")
        if r:
            out.append(r)
    return out


def _npg(idx: dict) -> list[dict]:
    d = _csv("npg_dfes_wide")
    if d is None:
        return []
    d = d.filter((pl.col("dfes_level") == "Primary") & (pl.col("scenario_name") == "NG FES - Holistic Transition"))

    def val(g: pl.DataFrame, par: str, y: int):
        v = g.filter(pl.col("output_parameter") == par)[str(y)]
        return v[0] if len(v) else None

    from ..match import name_norm

    out = []
    for nm, g in d.group_by("substation_local_authority"):
        nm = nm[0]
        h = idx.get(name_norm(nm))
        r = _row(str(nm).title(), "NPg", (h[2], h[3], h[0]) if h else None,
                 val(g, "Total peak demand with ToUT (MW)", Y0), val(g, "Total peak demand with ToUT (MW)", Y1),
                 ev=val(g, "Number of electric cars and vans (hybrid and full electric)", Y1),
                 hp=val(g, "Number of heat pumps (total)", Y1))
        if r:
            out.append(r)
    return out


def _enw(idx: dict) -> list[dict]:
    d = _csv("enw_dfes_primary")
    if d is None:
        return []
    d = d.filter((pl.col("scenario") == "2-HOLISTIC_TRANSITION") & pl.col("year").is_in([Y0, Y1])).with_columns(
        pl.col("dso_pry_number_alias").cast(pl.Utf8).str.replace(r"_.*$", "").alias("num"),
        (pl.col("uptake_heat_pump_domestic_unit_count") + pl.col("uptake_heat_pump_non_domestic_unit_count")).alias("hp"))
    g = d.group_by("num", "year").agg(pl.col("energy_demand_maximum_demand_mva").sum().alias("mx"),
                                      pl.col("uptake_electric_vehicle_unit_count").sum().alias("ev"), pl.col("hp").sum())
    out = []
    for num, gg in g.group_by("num"):
        by = {r["year"]: r for r in gg.iter_rows(named=True)}
        h = idx.get(f"num:{num[0]}")
        if Y0 not in by or Y1 not in by:
            continue
        nm = h[1] if h and not h[1].startswith("ENW:") else f"Primary {num[0]}"
        r = _row(nm, "ENW", (h[2], h[3], h[0]) if h else None, by[Y0]["mx"], by[Y1]["mx"],
                 ev=by[Y1]["ev"], hp=by[Y1]["hp"], note="Maximum demand published in MVA")
        if r:
            out.append(r)
    return out


def _spen(data_dir: Path) -> list[dict]:
    idx = site_index(data_dir, "SPEN")
    out = []
    for sid in ("dem_spen_dfes_spd", "dem_spen_dfes_spm"):
        snap = latest_snapshot(sid)
        if snap is None:
            continue
        f = next(p for p in snapshot_files(snap) if p.suffix == ".csv")
        lf = (pl.scan_csv(f, separator=";", infer_schema_length=10000, encoding="utf8-lossy")
              .filter((pl.col("scenario") == "HT") & (pl.col("substation_level") == "PSS")
                      & pl.col("year").is_in([Y0, Y1])
                      & pl.col("technology").is_in(["Total peak demand", "Total EVs", "Heat pumps"])
                      & (pl.col("attribute").is_in(["Peak demand", "Number"])))
              .group_by("substation", "technology", "year").agg(pl.col("value").sum()).collect())
        for (name,), g in lf.group_by("substation"):
            v = {(r["technology"], r["year"]): r["value"] for r in g.iter_rows(named=True)}
            hit = _spen_hit(idx, name)
            if not hit:
                continue
            r = _row(hit[0], "SPEN", (hit[1], hit[2], None) if hit else None, v.get(("Total peak demand", Y0)),
                     v.get(("Total peak demand", Y1)), ev=v.get(("Total EVs", Y1)), hp=v.get(("Heat pumps", Y1)),
                     note="Group of primaries; location is their centroid" if " / " in name else None)
            if r:
                out.append(r)
    return out


def build(ctx: Ctx) -> dict[str, Path]:
    feats = (_ukpn(site_index(ctx.data_dir, "UKPN")) + _npg(site_index(ctx.data_dir, "NPG"))
             + _enw(site_index(ctx.data_dir, "ENW")) + _spen(ctx.data_dir))
    if not feats:
        return {}
    f = ctx.tmp / "dem_dfes.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"dfes": f}

