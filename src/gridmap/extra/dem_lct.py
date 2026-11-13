"""Low-carbon technology (EV chargers, heat pumps, solar PV, batteries) connected per substation.

Two layers because the scales differ by 100x and the publishers differ in level:
  lct          secondary (LV) substations: UKPN LCT register (24k substations) + ENW MCS installs (14k)
  lct_primary  primary substations: UKPN, NGED (monthly connections summed) and SPEN (kW ADMD, no counts), plus
               NPG by postal district (NPG publishes no per-substation LCT; SSEN only per local authority).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import polars as pl

from ..normalise.common import best_coords, latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["ukpn_lct_secondary", "ukpn_lct_primary", "enw_mcs_lct_per_substation", "nged_lct_connections",
           "spen_lct_spd", "spen_lct_spm", "npg_lct_postal_district"]

_SEQ = [[0, "#cbc9e2"], [3, "#9e9ac8"], [10, "#756bb1"], [30, "#54278f"]]
_SEQ_P = [[0, "#cbc9e2"], [100, "#9e9ac8"], [500, "#756bb1"], [2000, "#54278f"]]
_ATTR = ("LCT counts: UK Power Networks open data portal (CC BY 4.0); Electricity North West MCS installations "
         "(CC BY 4.0); National Grid Electricity Distribution LCT connections (NGED Open Data Licence); "
         "SP Energy Networks (CC BY 4.0); Northern Powergrid (NPg Open Data Licence v1.0)")

LAYERS = [
    LayerMeta(
        id="lct", title="Low-carbon tech at LV substations", group="Demand & LV", type="pmtiles", geom="point",
        source_layer="lct", minzoom=9,
        description="Connected EV chargers, heat pumps, solar PV and batteries per LV (secondary) substation: UKPN "
                    "LCT register (Nov 2026) and ENW microgeneration-scheme installs (Jul 2026, no EV data). "
                    "Size = total installs; colour = EV chargers + heat pumps (new demand).",
        color={"prop": "dm", "type": "steps", "default": "#bdbdbd", "stops": _SEQ},
        size={"prop": "n", "stops": [[1, 2.5], [10, 4], [50, 7], [200, 11]]},
        popup=[["nm", "LV substation", None], ["op", "Network operator", None], ["n", "Total installs", None],
               ["ev", "EV chargers", None], ["hp", "Heat pumps", None], ["pv", "Solar PV", None],
               ["bat", "Batteries", None], ["pvkw", "PV export capacity", "kW"]],
        legend=[["#bdbdbd", "no EV / heat-pump data"], ["#cbc9e2", "0-2 EV + HP"], ["#9e9ac8", "3-9"],
                ["#756bb1", "10-29"], ["#54278f", "30+"]],
        attribution=_ATTR, inputs=[],
    ),
    LayerMeta(
        id="lct_primary", title="Low-carbon tech at primaries", group="Demand & LV", type="geojson", geom="point",
        minzoom=5,
        description="Connected EV chargers, heat pumps, solar PV and batteries rolled up to primary substations "
                    "(UKPN, NGED), SPEN primaries (kW only) and NPG postal districts. Size = total installs; "
                    "colour = EV chargers + heat pumps.",
        color={"prop": "dm", "type": "steps", "default": "#bdbdbd", "stops": _SEQ_P},
        size={"prop": "n", "stops": [[100, 3], [1000, 6], [5000, 9], [20000, 13]]},
        popup=[["nm", "Substation / area", None], ["op", "Network operator", None], ["lvl", "Level", None],
               ["n", "Total installs", None], ["ev", "EV chargers", None], ["hp", "Heat pumps", None],
               ["pv", "Solar PV", None], ["bat", "Batteries", None], ["evkw", "EV demand (ADMD)", "kW"],
               ["hpkw", "Heating demand (ADMD)", "kW"], ["pvkw", "PV capacity", "kW"], ["src", "Source", None]],
        legend=[["#bdbdbd", "no counts published (SPEN: kW only)"], ["#cbc9e2", "<100 EV + HP"], ["#9e9ac8", "100-499"],
                ["#756bb1", "500-1,999"], ["#54278f", "2,000+"]],
        attribution=_ATTR, inputs=["derived/site.parquet", "derived/asset_site.parquet"],
    ),
]


# ---------- helpers shared with dem_dfes ----------

def _csv(source_id: str, sep: str = ";", glob: str = "*.csv") -> pl.DataFrame | None:
    snap = latest_snapshot(source_id)
    if snap is None:
        return None
    f = next((p for p in snapshot_files(snap) if p.match(glob)), None)
    if f is None:
        return None
    return pl.read_csv(f, separator=sep, infer_schema_length=10000, encoding="utf8-lossy", ignore_errors=True)


def site_index(data_dir: Path, operator: str) -> dict[str, tuple[str, str, float, float]]:
    """{name_norm: (site_id, name, lat, lon)} for the operator's primary sites, plus extra keys:
    'floc:<FLOC>' (UKPN) and 'num:<n>' (ENW / NGED asset numbers) resolved through asset_site."""
    from ..match import name_norm  # local import: avoids import cycle at package import

    site_p, link_p = data_dir / "derived" / "site.parquet", data_dir / "derived" / "asset_site.parquet"
    if not (site_p.exists() and link_p.exists()):
        return {}
    site = pl.read_parquet(site_p).filter((pl.col("operator") == operator) & (pl.col("kind") == "PRIMARY")
                                          & pl.col("lat").is_not_null())
    by_id = {r[0]: (r[0], r[1], r[2], r[3]) for r in site.select("site_id", "name", "lat", "lon").iter_rows()}
    idx: dict[str, tuple[str, str, float, float]] = {}
    for sid, nm, la, lo in by_id.values():
        idx.setdefault(name_norm(nm), (sid, nm, la, lo))
    asset_p = data_dir / "norm" / operator / "asset.parquet"
    if asset_p.exists():
        link = dict(pl.read_parquet(link_p).iter_rows())
        for uid, nid in pl.read_parquet(asset_p).filter(pl.col("kind") == "PRIMARY").select("asset_uid", "native_ids").iter_rows():
            sid = link.get(uid)
            if sid not in by_id:
                continue
            ids = json.loads(nid or "{}")
            for k in ("floc", "number", "mrid", "primary_locations_number"):
                if ids.get(k):
                    idx.setdefault(f"{k}:{ids[k]}", by_id[sid])
            idx.setdefault(f"num:{uid.rsplit(':', 1)[-1]}", by_id[sid])
    return idx


def _i(v) -> int | None:
    return None if v is None else round(float(v))


def _feat(lat: float, lon: float, **p) -> dict:
    props = {k: v for k, v in p.items() if v is not None}
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": props}


# ---------- LV layer ----------

def _ukpn_secondary() -> list[dict]:
    d = _csv("ukpn_lct_secondary")
    if d is None:
        return []
    t = pl.col("type")
    g = d.group_by("functionallocation").agg(
        pl.col("secondarysubstationalias").first().alias("nm"), pl.col("latitude").first(), pl.col("longitude").first(),
        pl.col("lct_connections").sum().alias("n"),
        pl.col("lct_connections").filter(t == "EV Charging Point").sum().alias("ev"),
        pl.col("lct_connections").filter(t == "Heat Pump").sum().alias("hp"),
        pl.col("lct_connections").filter(t.is_in(["Solar PV", "Solar & Battery"])).sum().alias("pv"),
        pl.col("lct_connections").filter(t.is_in(["Battery Storage", "Solar & Battery"])).sum().alias("bat"),
        pl.col("export").filter(t.is_in(["Solar PV", "Solar & Battery"])).sum().alias("pvkw"),
    )
    out = []
    for r in g.iter_rows(named=True):
        la, lo, _ = best_coords(r["latitude"], r["longitude"])
        if la is None:
            continue
        out.append(_feat(la, lo, nm=str(r["nm"]), op="UKPN", n=r["n"], ev=r["ev"], hp=r["hp"], pv=r["pv"],
                         bat=r["bat"], pvkw=_i(r["pvkw"]), dm=r["ev"] + r["hp"]))
    return out


def _enw_mcs() -> list[dict]:
    d = _csv("enw_mcs_lct_per_substation")
    if d is None:
        return []
    out = []
    for r in d.iter_rows(named=True):
        gp = str(r.get("geopoint") or "").split(",")
        la, lo, _ = best_coords(gp[0] if len(gp) == 2 else None, gp[1] if len(gp) == 2 else None, r["x"], r["y"])
        n = r["mcs_installations_total_lct"]
        if la is None or n is None:
            continue
        hp = sum(r[c] or 0 for c in ("mcs_installations_heat_pump_air_source_hpas",
                                     "mcs_installations_heat_pump_exhaust_air_hpea",
                                     "mcs_installations_heat_pump_ground_water_source_hpgws"))
        out.append(_feat(la, lo, nm=str(r["substation_number"]), op="ENW", n=_i(n), hp=_i(hp),
                         pv=_i(r["mcs_installations_solar_photovoltaic_spv"]),
                         bat=_i(r["mcs_installations_battery_storage_bs"])))
    return out


# ---------- primary layer ----------

def _ukpn_primary(idx: dict) -> list[dict]:
    d = _csv("ukpn_lct_primary")
    if d is None:
        return []
    t = pl.col("type")
    g = d.group_by("sitefunctionallocation").agg(
        pl.col("primaryname").first().alias("nm"), pl.col("spatial_coordinates").first().alias("xy"),
        pl.col("lct_connections").sum().alias("n"),
        pl.col("lct_connections").filter(t == "EV Charging Point").sum().alias("ev"),
        pl.col("lct_connections").filter(t == "Heat Pump").sum().alias("hp"),
        pl.col("lct_connections").filter(t.is_in(["Solar PV", "Solar & Battery"])).sum().alias("pv"),
        pl.col("lct_connections").filter(t.is_in(["Battery Storage", "Solar & Battery"])).sum().alias("bat"),
        pl.col("exportrating_mw").filter(t.is_in(["Solar PV", "Solar & Battery"])).sum().alias("pvmw"),
    )
    out = []
    for r in g.iter_rows(named=True):
        hit = idx.get(f"floc:{r['sitefunctionallocation']}")
        if hit:
            la, lo = hit[2], hit[3]
        else:  # spatial_coordinates is "lon, lat"
            xy = str(r["xy"] or "").split(",")
            la, lo, _ = best_coords(xy[1] if len(xy) == 2 else None, xy[0] if len(xy) == 2 else None)
        if la is None:
            continue
        out.append(_feat(la, lo, nm=str(r["nm"]).title(), op="UKPN", lvl="Primary", n=r["n"], ev=r["ev"], hp=r["hp"],
                         pv=r["pv"], bat=r["bat"], pvkw=_i((r["pvmw"] or 0) * 1000), dm=r["ev"] + r["hp"],
                         src="UKPN LCT register"))
    return out


def _nged(idx: dict) -> list[dict]:
    d = _csv("nged_lct_connections", sep=",")
    if d is None:
        return []
    g = d.group_by("Primary Substation Number").agg(
        pl.col("Primary Substation Name").first().alias("nm"), pl.col("EV").sum(), pl.col("HP").sum(),
        pl.col("PV").sum(), pl.col("ES").sum(), pl.col("Connected month").max().alias("last"))
    out = []
    for r in g.iter_rows(named=True):
        hit = idx.get(f"num:{int(r['Primary Substation Number'])}")
        if not hit:
            continue
        n = r["EV"] + r["HP"] + r["PV"] + r["ES"]
        out.append(_feat(hit[2], hit[3], nm=str(r["nm"]), op="NGED", lvl="Primary", n=n, ev=r["EV"], hp=r["HP"],
                         pv=r["PV"], bat=r["ES"], dm=r["EV"] + r["HP"], src="NGED LCT connections (new connections per month, summed)"))
    return out


def _spen_hit(idx: dict, raw: str) -> tuple[str, float, float] | None:
    """SPEN names are 'PS-NT9349/002 HORNCLIFFE PRIMARY NEW' (SPD) or 'A T1 / B T1 / C T2' (SPM groups of primaries).
    Match each part by normalised name; a group is placed at the centroid of the parts that matched."""
    import re

    from ..match import name_norm

    pts = []
    for part in raw.split(" / "):
        part = re.sub(r"^PS-\S+\s+", "", part).strip()
        for cand in (part, re.sub(r"\s+(NEW|OLD)$", "", part), re.sub(r"\s+[A-Z]{3,4}$", "", part),
                     re.sub(r"\s+'?[A-Z]'?$", "", part)):
            hit = idx.get(name_norm(cand))
            if hit:
                pts.append(hit)
                break
    if not pts:
        return None
    return (re.sub(r"^PS-\S+\s+", "", raw).title(), sum(h[2] for h in pts) / len(pts), sum(h[3] for h in pts) / len(pts))


def _spen(data_dir: Path) -> list[dict]:
    idx = site_index(data_dir, "SPEN")
    out = []
    for sid, col in (("spen_lct_spd", "substation_name"), ("spen_lct_spm", "primary_substation_group")):
        d = _csv(sid)
        if d is None:
            continue
        for r in d.iter_rows(named=True):
            hit = _spen_hit(idx, str(r[col]))
            if not hit:
                continue
            out.append(_feat(hit[1], hit[2], nm=hit[0], op="SPEN", lvl="Primary" if sid.endswith("spd") else "Primary group",
                             evkw=_i(r["ev_admd"]), hpkw=_i(r["heating_admd"]), pvkw=_i(r["generation_photovoltaic"]),
                             src="SPEN LCT connections (kW ADMD; no counts published)"))
    return out


def _npg() -> list[dict]:
    d = _csv("npg_lct_postal_district")
    if d is None:
        return []
    out = []
    for r in d.iter_rows(named=True):
        la, lo, _ = best_coords(*(str(r["geo_point_2d"]).split(",") + [None, None])[:2])
        if la is None or r["total_number_of_lcts"] is None:
            continue
        ev, hp = _i(r["number_of_ev_chargers"]), _i(r["number_of_heat_pumps"])
        out.append(_feat(la, lo, nm=str(r["unique_postal_districts"]), op="NPg", lvl="Postal district",
                         n=_i(r["total_number_of_lcts"]), ev=ev, hp=hp, pv=_i(r["number_of_pvs"]),
                         bat=_i(r["number_of_energy_storage"]), dm=(ev or 0) + (hp or 0),
                         src="NPg LCT by postal district"))
    return out


def _tiles(feats: list[dict], ctx: Ctx, name: str, zmin: int, zmax: int) -> Path:
    src = ctx.tmp / f"dem_{name}.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / f"dem_{name}.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", name, f"-Z{zmin}", f"-z{zmax}",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)
    return out


def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    lv = _ukpn_secondary() + _enw_mcs()
    if lv:
        out["lct"] = _tiles(lv, ctx, "lct", 9, 14)
    uk_idx, nged_idx = site_index(ctx.data_dir, "UKPN"), site_index(ctx.data_dir, "NGED")
    pr = _ukpn_primary(uk_idx) + _nged(nged_idx) + _spen(ctx.data_dir) + _npg()
    if pr:
        f = ctx.tmp / "dem_lct_primary.geojson"
        f.write_text(json.dumps({"type": "FeatureCollection", "features": pr}, separators=(",", ":")))
        out["lct_primary"] = f
    return out
