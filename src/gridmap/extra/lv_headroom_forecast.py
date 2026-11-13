"""ENW LV headroom forecast per secondary substation (DFES 2024, 'Best View' scenario, year 2030).

Headroom = forecast maximum demand subtracted from transformer rating, in kVA (negative = forecast to exceed
the rating). Monitored substations (PreSense) use measured maximum demand; unmonitored ones are modelled.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from . import Ctx, LayerMeta
from .lv_common import latlon, num, point, read_csv_snapshot, to_pmtiles

SOURCES = ["enw_lv_headroom_monitored", "enw_lv_headroom_unmonitored"]
YEAR, SCENARIO = 2030, "best_view"

LAYERS = [
    LayerMeta(
        id="lv_headroom_forecast", title="LV headroom forecast (ENW, 2030)", group="Demand & LV", type="pmtiles",
        geom="point", source_layer="lv_headroom_forecast", minzoom=9,
        description="Spare transformer capacity in 2030, in kVA. Negative is over capacity. ENW, DFES 2024, Best View. North "
                    "West only.",
        color={"prop": "hr", "type": "steps", "default": "#c7d7e5",
               "stops": [[-1e9, "#542788"], [0, "#8073ac"], [50, "#b2abd2"], [150, "#d8daeb"], [400, "#c7e0ea"]]},
        size=3, opacity=0.85,
        popup=[["id", "Substation", None], ["mon", "LV monitoring", None], ["hr", "Headroom 2030", "kVA"],
               ["h24", "Headroom 2024", "kVA"], ["h35", "Headroom 2035", "kVA"], ["kva", "Transformer rating", "kVA"],
               ["c", "Customers", None]],
        legend=[["#542788", "Over capacity in 2030 (<0 kVA)"], ["#8073ac", "0-50 kVA"], ["#b2abd2", "50-150 kVA"],
                ["#d8daeb", "150-400 kVA"], ["#c7e0ea", ">=400 kVA"]],
        attribution="Contains data from SP Energy Networks (Electricity North West) open data portal, "
                    "licensed under CC BY 4.0 (DFES 2024 LV headroom datasets).",
        inputs=["norm/ENW/lv_site.parquet"],
    )
]


def _f(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def build(ctx: Ctx) -> dict[str, Path]:
    lv_p = ctx.data_dir / "norm" / "ENW" / "lv_site.parquet"
    extra: dict[str, tuple[float | None, int | None]] = {}
    if lv_p.exists():
        lv = pl.read_parquet(lv_p).select("asset_uid", "transformer_kva", "customers")
        extra = {a.removeprefix("ENW:"): (k, c) for a, k, c in lv.iter_rows()}
    feats: list[dict] = []
    for sid_src, mon in (("enw_lv_headroom_monitored", "Monitored (PreSense)"), ("enw_lv_headroom_unmonitored", "Modelled")):
        got = read_csv_snapshot(sid_src)
        if got is None:
            continue
        df, _ = got
        col = f"{SCENARIO}_{YEAR}"
        for r in df.iter_rows(named=True):
            hr = _f(r.get(col))
            la, lo = latlon(r.get("geopoint"))
            if hr is None or la is None:
                continue
            sid = r["secondary_substation"]
            kva, cust = extra.get(sid, (None, None))
            feats.append(point(la, lo, {"id": sid, "mon": mon, "hr": num(hr, 0), "h24": num(_f(r.get(f"{SCENARIO}_2024")), 0),
                                        "h35": num(_f(r.get(f"{SCENARIO}_2035")), 0), "kva": num(kva, 0), "c": cust}))
    if not feats:
        return {}
    return {"lv_headroom_forecast": to_pmtiles(feats, ctx.tmp, "lv_headroom_forecast", 9, 14)}
