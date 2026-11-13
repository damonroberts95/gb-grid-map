"""LV (secondary) substations that DNOs publish as having LV monitoring, from every operator that says so.

ENW: DFES "monitored" LV headroom list (PreSense units). SPEN: LV monitoring aggregated table (monthly
utilisation per transformer). NPG: LV monitor deployment strategy (3,000 installed + shortlist of the rest of
the 10,000 committed monitors). NGED: the lv-load-monitor-data package (one CKAN resource per monitored
substation), located via data/norm/NGED/lv_site.parquet. UKPN and SSEN publish no list of monitored LV sites.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ..normalise.common import latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from .lv_common import latlon, nearest_uids, num, point, read_csv_snapshot, to_pmtiles

SOURCES = ["enw_lv_headroom_monitored", "enw_lv_peak_demand_monitored", "spen_lv_monitor",
           "npg_lv_monitor_deployment", "nged_lv_monitor_list"]

ATTR = ("ENW and SPEN: CC BY 4.0, SP Energy Networks / Electricity North West open data portals. "
        "NPG: (c) Northern Powergrid, Open Data Licence v1.0. NGED: (c) National Grid Electricity Distribution, "
        "NGED (WPD) Open Data Licence.")

LAYERS = [
    LayerMeta(
        id="lv_monitored", title="LV monitored substations", group="Demand & LV", type="pmtiles", geom="point",
        source_layer="lv_monitored", minzoom=9,
        description="Secondary (LV) substations that the network operator publishes as having LV monitoring "
                    "(ENW, SPEN, NPG and NGED lists, Nov 2026), plus NPG's shortlist of sites still to be fitted. "
                    "No operator publishes the monitor vendor; UKPN and SSEN publish no list.",
        color={"prop": "s", "type": "categorical", "default": "#0b7285",
               "stops": [["Monitored", "#0b7285"], ["Shortlisted", "#99d5da"]]},
        size=3.5, opacity=0.9,
        popup=[["id", "Substation", None], ["op", "Operator", None], ["s", "Monitoring", None],
               ["mon", "Monitor vendor", None], ["u", "Max utilisation (latest month)", "%"],
               ["pk", "Max demand (2024 baseline)", "kVA"], ["c", "Customers", None], ["w", "Reason selected", None],
               ["m", "Latest data", None], ["src", "Source", None]],
        legend=[["#0b7285", "Monitored (published as installed / reporting)"],
                ["#99d5da", "Shortlisted for monitor (NPG)"]],
        attribution=ATTR,
        inputs=["norm/NGED/lv_site.parquet"],
    )
]


def _enw() -> list[tuple[str | None, dict]]:
    got = read_csv_snapshot("enw_lv_headroom_monitored")
    if got is None:
        return []
    df, _ = got
    pk: dict[str, float] = {}
    p = read_csv_snapshot("enw_lv_peak_demand_monitored")
    if p is not None:
        for sid, v in p[0].select("secondary_substation", "best_view_2024").iter_rows():
            try:
                pk[sid] = float(v)
            except (TypeError, ValueError):
                pass
    out = []
    for sid, gp in df.select("secondary_substation", "geopoint").iter_rows():
        la, lo = latlon(gp)
        if la is None:
            continue
        out.append((f"ENW:{sid}", point(la, lo, {"id": sid, "op": "ENW", "s": "Monitored", "mon": "PreSense",
                                  "pk": num(pk.get(sid)), "src": "ENW DFES LV monitored list"})))
    return out


def _spen() -> list[tuple[str | None, dict]]:
    got = read_csv_snapshot("spen_lv_monitor")
    if got is None:
        return []
    df, _ = got
    df = df.with_columns(pl.col("maximum_capacity_utilisation").cast(pl.Float64, strict=False).alias("u"))
    agg = (df.sort("year_month")
           .group_by("transformer_enid", maintain_order=True)
           .agg(pl.col("year_month").last().alias("m"), pl.col("u").last(), pl.col("geo_points").drop_nulls().last()))
    out = []
    for sid, m, u, gp in agg.iter_rows():
        la, lo = latlon(gp)
        if la is None:
            continue
        out.append((f"SPEN:{sid}", point(la, lo, {"id": sid, "op": "SPEN", "s": "Monitored", "mon": "not published",
                                  "u": num(u), "m": m, "src": "SPEN LV monitoring (aggregated)"})))
    return out


def _npg(data_dir: Path) -> list[tuple[str | None, dict]]:
    """NPG list has no id that matches the open lv_site table, so installed monitors are joined spatially (40 m)."""
    got = read_csv_snapshot("npg_lv_monitor_deployment")
    if got is None:
        return []
    df, _ = got
    out = []
    for r in df.iter_rows(named=True):
        la, lo = latlon(r["geopoint"])
        if la is None:
            continue
        installed = r["already_installed"] == "Installed"
        cust = r["customer_numbers"]
        out.append((installed, la, lo, point(la, lo, {
            "id": r["substation_name"] or r["eam_site_asset_id"], "op": "NPG",
            "s": "Monitored" if installed else "Shortlisted", "mon": "not published",
            "u": num(float(r["utilisation"])) if r["utilisation"] else None,
            "c": int(float(cust)) if cust else None, "w": r["reason_for_installation"],
            "src": "NPG LV monitor deployment strategy"})))
    uids: dict[int, str | None] = {}
    p = data_dir / "norm" / "NPG" / "lv_site.parquet"
    inst = [k for k, o in enumerate(out) if o[0]]
    if inst and p.exists():
        lv = pl.read_parquet(p).filter(pl.col("lat").is_not_null())
        uids = dict(zip(inst, nearest_uids([(out[k][1], out[k][2]) for k in inst], lv), strict=True))
    return [(uids.get(k), o[3]) for k, o in enumerate(out)]


def _nged(data_dir: Path) -> list[tuple[str | None, dict]]:
    snap = latest_snapshot("nged_lv_monitor_list")
    lv_p = data_dir / "norm" / "NGED" / "lv_site.parquet"
    if snap is None or not lv_p.exists():
        return []
    res = json.loads(snapshot_files(snap)[0].read_text())["result"]["resources"]
    names = {str(r["name"]).strip() for r in res if r.get("name")}
    lv = pl.read_parquet(lv_p).filter(pl.col("lat").is_not_null())
    lv = lv.with_columns(pl.col("asset_uid").str.replace("^NGED:", "").alias("n"))
    out = []
    for n, nm, la, lo in lv.filter(pl.col("n").is_in(list(names))).select("n", "name", "lat", "lon").iter_rows():
        out.append((f"NGED:{n}", point(la, lo, {"id": f"{nm} ({n})" if nm else n, "op": "NGED", "s": "Monitored",
                                                "mon": "not published", "src": "NGED lv-load-monitor-data"})))
    return out


def _nerda(data_dir: Path) -> list[str]:
    """SSEN SEPD lv_site uids of NeRDA secondaries with at least one LV feeder current channel (static snapshot,
    spatial join within 40 m: the NRN is not in the open lv_site table). Empty when NeRDA data is absent."""
    from . import nerda_client as nc
    ip = data_dir / "derived" / "nerda_index.json"  # built by nerda_client.load_index; never rebuilt here
    p = data_dir / "norm" / "SSEN" / "lv_site.parquet"
    if not ip.exists() or not p.exists():
        return []
    try:
        idx = json.loads(ip.read_text())
    except ValueError:
        return []
    sec = [(s["lat"], s["lon"]) for s in idx["sites"]
           if s.get("k") == "secondary" and s.get("lat") is not None
           and any(nc.line_current_id(ln) for ln in nc.lv_feeders(s["lines"]))]
    lv = pl.read_parquet(p).filter(pl.col("asset_uid").str.contains("SEPD") & pl.col("lat").is_not_null())
    return sorted({u for u in nearest_uids(sec, lv) if u})


def input_files(data_dir: Path) -> list[Path]:
    """Small files whose change alters monitored(): snapshot metas of the monitor lists + the NeRDA index."""
    out = [snap / "meta.json" for sid in SOURCES if (snap := latest_snapshot(sid)) is not None]
    nerda = data_dir / "derived" / "nerda_index.json"
    return sorted(f for f in out + [nerda] if f.exists())


def _all(data_dir: Path) -> list[tuple[str | None, dict]]:
    return _enw() + _spen() + _npg(data_dir) + _nged(data_dir)


def monitored(data_dir: Path | None = None) -> pl.DataFrame:
    """LV substations with a published monitor, as DataFrame[asset_uid, ms]: ms = monitor source (+ vendor where
    published, e.g. 'ENW PreSense'). Only installed/reporting monitors (not NPG's shortlist). One row per uid; if
    several sources cover a site, the first in order ENW, SPEN, NPG, NGED, NeRDA wins."""
    from ..normalise.common import DATA_DIR
    data_dir = data_dir or DATA_DIR
    rows: list[tuple[str, str]] = []
    for uid, f in _all(data_dir):
        pr = f["properties"]
        if uid and pr["s"] == "Monitored":
            rows.append((uid, "ENW PreSense" if pr["op"] == "ENW" else pr["op"]))
    rows += [(u, "NeRDA") for u in _nerda(data_dir)]
    df = pl.DataFrame(rows, schema={"asset_uid": pl.Utf8, "ms": pl.Utf8}, orient="row")
    return df.unique("asset_uid", keep="first", maintain_order=True)


def build(ctx: Ctx) -> dict[str, Path]:
    feats = [f for _, f in _all(ctx.data_dir)]
    if not feats:
        return {}
    return {"lv_monitored": to_pmtiles(feats, ctx.tmp, "lv_monitored", minzoom=9, maxzoom=14)}
