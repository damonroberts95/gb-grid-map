"""Smart-meter consumption aggregated to secondary (LV) substations, latest published month.

UKPN (21 substations: the published extract is capped at 30,000 rows), SPEN (Wirral Sep 2026, Ayrshire & Clyde
South Oct 2026; feeders summed per substation) and NGED (all secondary substations, latest month). The raw feeds
are half-hourly; the `smart_agg` fetcher (fetch/smart_agg.py) streams SPEN/NGED and stores one row per substation
(data/raw/<id>/<ts>/agg.parquet). SSEN publishes feeder-level half-hourly usage through a GraphQL API with no
coordinates: the `ssen_gql` fetcher (fetch/ssen_gql.py) saves one day of it plus a feeder x postcode lookup, and `_ssen` sums
feeders per secondary substation and places it at the MPAN-weighted centroid of its postcodes (OS Code-Point Open),
snapped to the nearest SSEN lv_site within 250 m. NPG (file links) and ENW (installation counts only) are not
consumption feeds. Consumption is taken as Wh per half-hour (kW = Wh x 2 / 1000); only smart-metered customers
are counted, so peaks understate true substation load.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import polars as pl

from ..normalise.common import best_coords, latest_snapshot, snapshot_files
from . import Ctx, LayerMeta
from .lv_common import latlon, num, point, read_csv_snapshot, to_pmtiles

SOURCES = ["ukpn_smart_meter_substation", "spen_smart_consumption_wirral_agg",
           "spen_smart_consumption_ayrshire_agg", "nged_smart_meter_secondary_agg", "ssen_sm_gql", "os_codepoint"]

ATTR = ("UKPN and SPEN: CC BY 4.0, UK Power Networks / SP Energy Networks open data portals. "
        "NGED: (c) National Grid Electricity Distribution, NGED (WPD) Open Data Licence. "
        "SSEN: CC BY 4.0, SSEN Distribution data portal; located with OS Code-Point Open (OGL v3, Contains OS data "
        "(c) Crown copyright and database right).")

LAYERS = [
    LayerMeta(
        id="lv_smart", title="LV smart-meter demand", group="Demand & LV", type="pmtiles", geom="point",
        source_layer="lv_smart", minzoom=9,
        description="Peak demand per smart meter, in kW. Latest month. NGED, SPEN, UKPN (sample). SSEN is one old "
                    "day, 4 Sep 2024, with estimated positions.",
        color={"prop": "pm", "type": "steps", "default": "#cccccc",
               "stops": [[0, "#c6dbef"], [0.5, "#9ecae1"], [1, "#6baed6"], [1.5, "#3182bd"], [2.5, "#08306b"]]},
        size={"prop": "pk", "stops": [[0, 2.5], [50, 4], [150, 6], [400, 9]]}, opacity=0.85,
        popup=[["n", "Substation", None], ["op", "Operator", None], ["pk", "Peak demand (smart-metered)", "kW"],
               ["pm", "Peak per smart meter", "kW"], ["av", "Average demand", "kW"], ["d", "Smart meters", None],
               ["kva", "Transformer rating", "kVA"], ["ut", "Peak as % of rating", "%"], ["pt", "Peak at", None],
               ["mo", "Month", None], ["as_of", "Data date (SSEN only)", None], ["loc", "Position (SSEN only)", None]],
        legend=[["#c6dbef", "<0.5 kW per meter"], ["#9ecae1", "0.5-1"], ["#6baed6", "1-1.5"], ["#3182bd", "1.5-2.5"],
                ["#08306b", ">=2.5 kW per meter"]],
        attribution=ATTR,
        inputs=["norm/NGED/lv_site.parquet", "norm/SPEN/lv_site.parquet", "norm/UKPN/lv_site.parquet",
                "norm/SSEN/lv_site.parquet"],
    )
]


def _from_agg(source_id: str, op: str, coords) -> list[dict]:
    snap = latest_snapshot(source_id)
    if snap is None:
        return []
    df = pl.read_parquet(snap / "agg.parquet")
    rows = []
    for r in df.iter_rows(named=True):
        la, lo = coords(r["geo"])
        if la is None or not r["n_hh"] or r["peak_wh"] is None:
            continue
        rows.append({"op": op, "sid": str(r["sub_id"]), "n": r["name"], "la": la, "lo": lo,
                     "pk": r["peak_wh"] * 2 / 1000, "av": r["sum_wh"] / r["n_hh"] * 2 / 1000,
                     "d": r["devs"], "pt": str(r["peak_ts"])[:16], "mo": str(r["t1"])[:7]})
    return rows


def _bng_text(s: str | None) -> tuple[float | None, float | None]:
    m = re.match(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?$", s or "")
    if not m:
        return None, None
    la, lo, _ = best_coords(e=m[1], n=m[2])
    return la, lo


def _ukpn() -> list[dict]:
    got = read_csv_snapshot("ukpn_smart_meter_substation")
    if got is None:
        return []
    df, _ = got
    df = df.with_columns(pl.col("total_consumption_active_import").cast(pl.Float64, strict=False).alias("wh"),
                         pl.col("aggregated_device_count_active").cast(pl.Float64, strict=False).alias("dv"))
    g = (df.group_by("secondary_substation_id")
         .agg(pl.col("geopoint").first(), pl.col("wh").max().alias("pk"), pl.col("wh").mean().alias("av"),
              pl.col("dv").max().alias("d"), pl.col("data_collection_log_timestamp").max().alias("t1"),
              pl.col("data_collection_log_timestamp").sort_by("wh").last().alias("pt")))
    out = []
    for sid, gp, pk, av, d, t1, pt in g.iter_rows():
        la, lo = latlon(gp)
        if la is None or pk is None:
            continue
        out.append({"op": "UKPN", "sid": sid, "n": sid, "la": la, "lo": lo, "pk": pk * 2 / 1000,
                    "av": av * 2 / 1000, "d": d, "pt": pt[:16].replace("T", " "), "mo": t1[:7]})
    return out


def _nged_geo(s: str | None) -> tuple[float | None, float | None]:
    return latlon(s)

SSEN_SNAP_M = 250.0


def _pages(snap: Path, prefix: str) -> pl.DataFrame:
    """Concatenate saved GraphQL page responses (<prefix>*.json.gz) into one all-text frame."""
    frames = []
    for f in sorted(snap.glob(f"{prefix}*.json.gz")):
        rows = next(iter(json.loads(gzip.decompress(f.read_bytes()))["data"].values()))
        if rows:
            frames.append(pl.DataFrame(rows, infer_schema_length=None).cast(pl.Utf8))
    return pl.concat(frames, how="diagonal") if frames else pl.DataFrame()


def _codepoint(pcs: set[str]) -> dict[str, tuple[float, float]]:
    """Postcode (no spaces, upper) -> (easting, northing) from the Code-Point Open zip; only the areas needed."""
    snap = latest_snapshot("os_codepoint")
    zips = [p for p in snapshot_files(snap) if zipfile.is_zipfile(p)] if snap else []
    if not zips:
        return {}
    areas = {m[0] for m in (re.match(r"[A-Z]{1,2}", p) for p in pcs) if m}
    out: dict[str, tuple[float, float]] = {}
    with zipfile.ZipFile(zips[0]) as z:
        for name in z.namelist():
            m = re.fullmatch(r"(?:.*/)?CSV/([a-z]{1,2})\.csv", name)
            if not m or m[1].upper() not in areas:
                continue
            df = pl.read_csv(io.BytesIO(z.read(name)), has_header=False, infer_schema_length=0)
            for pc, _q, e, n, *_ in df.iter_rows():
                k = pc.replace(" ", "").upper()
                if k in pcs and e and n:
                    out[k] = (float(e), float(n))
    return out


def _ssen(data_dir: Path) -> list[dict]:
    """SSEN feeder-level day -> one row per secondary substation (primary 4 + HV feeder 3 + secondary 3 digits of the
    12-digit feeder dataset_id), located from postcodes. Same fields as the other operators plus as_of / loc."""
    snap = latest_snapshot("ssen_sm_gql")
    if snap is None:
        return []
    use = _pages(snap, "usage_")
    lk = _pages(snap, "lookup_")
    if use.is_empty() or lk.is_empty():
        return []
    day = str(json.loads((snap / "meta.json").read_text()).get("as_of") or "")[:10]
    use = use.with_columns(pl.col("dataset_id").str.slice(0, 10).alias("sub"),
                           pl.col("total_consumption_active_import").cast(pl.Float64, strict=False).alias("wh"),
                           pl.col("aggregated_device_count_active").cast(pl.Float64, strict=False).alias("dv"))
    hh = use.group_by("sub", "data_collection_log_timestamp").agg(pl.col("wh").sum(), pl.col("dv").sum())
    agg = (hh.group_by("sub")
           .agg(pl.col("wh").max().alias("pk"), pl.col("wh").mean().alias("av"), pl.col("dv").max().alias("d"),
                pl.col("data_collection_log_timestamp").sort_by("wh").last().alias("pt"),
                pl.len().alias("n_hh")))
    lk = lk.with_columns(pl.col("dataset_id").str.slice(0, 10).alias("sub"),
                         pl.col("total_mpan_count").cast(pl.Float64, strict=False).fill_null(1.0).alias("w"),
                         pl.col("postcode").str.replace_all(" ", "").str.to_uppercase().alias("pc"))
    # weight of a postcode within a secondary: sum over its feeders (one lookup row per feeder x postcode)
    pcw = lk.drop_nulls("pc").group_by("sub", "pc").agg(pl.col("w").sum())
    names = (lk.group_by("sub").agg(pl.col("secondary_substation_name").drop_nulls().first().alias("nm")))
    cp = _codepoint(set(pcw["pc"].to_list()))
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for sub, pc, w in pcw.iter_rows():
        if pc in cp:
            a = acc[sub]
            a[0] += cp[pc][0] * w
            a[1] += cp[pc][1] * w
            a[2] += w
    # nearest SSEN lv_site within SSEN_SNAP_M (local equirectangular metres, 0.01 degree grid buckets)
    sites: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    p = data_dir / "norm" / "SSEN" / "lv_site.parquet"
    if p.exists():
        for la, lo in pl.read_parquet(p).select("lat", "lon").drop_nulls().iter_rows():
            sites[(int(la * 100), int(lo * 100))].append((la, lo))
    nm = dict(names.iter_rows())
    out = []
    for sub, pk, av, d, pt, _n in agg.sort("sub").iter_rows():
        a = acc.get(sub)
        if not a or a[2] <= 0 or not pk:
            continue
        la, lo, _ = best_coords(e=a[0] / a[2], n=a[1] / a[2])
        if la is None:
            continue
        best, bd = None, SSEN_SNAP_M
        ci, cj = int(la * 100), int(lo * 100)
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for sla, slo in sites.get((i, j), ()):
                    dm = math.hypot((slo - lo) * 111320 * math.cos(math.radians(la)), (sla - la) * 111320)
                    if dm <= bd:
                        best, bd = (sla, slo), dm
        loc = "snapped to nearest SSEN substation record" if best else "estimated from customer postcodes"
        if best:
            la, lo = best
        out.append({"op": "SSEN", "sid": sub, "n": nm.get(sub) or f"SSEN secondary {sub}", "la": la, "lo": lo,
                    "pk": pk * 2 / 1000, "av": av * 2 / 1000, "d": int(d) if d else None,
                    "pt": str(pt)[:16].replace("T", " "), "mo": day[:7], "as_of": day, "loc": loc})
    return out


def build(ctx: Ctx) -> dict[str, Path]:
    rows = (_ukpn() + _from_agg("spen_smart_consumption_wirral_agg", "SPEN", _bng_text)
            + _from_agg("spen_smart_consumption_ayrshire_agg", "SPEN", _bng_text)
            + _from_agg("nged_smart_meter_secondary_agg", "NGED", _nged_geo) + _ssen(ctx.data_dir))
    if not rows:
        return {}
    kva: dict[tuple[str, str], float] = {}
    for op in ("NGED", "SPEN", "UKPN"):
        p = ctx.data_dir / "norm" / op / "lv_site.parquet"
        if p.exists():
            for a, k in pl.read_parquet(p).select("asset_uid", "transformer_kva").drop_nulls().iter_rows():
                kva[(op, a.removeprefix(f"{op}:"))] = k
    feats = []
    for r in rows:
        k = kva.get((r["op"], r["sid"]))
        d = r["d"]
        pm = r["pk"] / d if d else None
        feats.append(point(r["la"], r["lo"], {
            "n": r["n"] or r["sid"], "op": r["op"], "pk": num(r["pk"]), "av": num(r["av"]), "d": d,
            "pm": num(pm, 2), "kva": num(k, 0), "ut": num(r["pk"] / k * 100) if k else None,
            "pt": r["pt"], "mo": r["mo"], "as_of": r.get("as_of"), "loc": r.get("loc")}))
    return {"lv_smart": to_pmtiles(feats, ctx.tmp, "lv_smart", 9, 14)}
