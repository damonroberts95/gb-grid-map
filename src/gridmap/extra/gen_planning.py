"""Generation & planning layers: DESNZ REPD, distribution connection-register projects (ECR), NESO TEC.

Three layers in one module (they share the technology classifier):
- repd:          Renewable Energy Planning Database projects (points, pmtiles)
- ecr_projects:  DNO connection-register projects with coordinates (points, pmtiles)
- tec:           NESO TEC register projects aggregated per matched transmission connection site (geojson)
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl

from ..normalise.common import (
    add_coords,
    latest_snapshot,
    parse_num,
    snapshot_files,
    snapshot_meta,
)
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["desnz_repd", "neso_tec_register", "ssent_sites_grid", "ssent_sites_supergrid"]

TECH_COLORS = [
    ["Solar", "#f2e34c"], ["Onshore wind", "#0072b2"], ["Offshore wind", "#56b4e9"],
    ["Battery / storage", "#7b3294"], ["Hydro", "#00a6a6"], ["Biomass & waste", "#8c564b"],
    ["Other", "#8a8a8a"],
]
TECH_COLOR_SPEC = {"prop": "t", "type": "categorical", "default": "#8a8a8a", "stops": TECH_COLORS}
MW_SIZE = {"prop": "mw", "stops": [[0, 2], [1, 3], [5, 4.5], [20, 7], [100, 10], [500, 14]]}

REPD_ATTR = ("Contains public sector information from the DESNZ Renewable Energy Planning Database, "
             "licensed under the Open Government Licence v3.0.")
TEC_ATTR = "Supported by National Energy SO Open Data"
ECR_ATTR = ("Distribution Embedded Capacity Registers and connection queues from UKPN, NPG, ENW, SPEN, NGED and SSEN "
            "(CC BY 4.0 / each operator's open data licence).")

LAYERS = [
    LayerMeta(
        id="repd", title="Planned & operating renewables (REPD)", group="Generation & planning", type="pmtiles",
        geom="point", source_layer="repd", minzoom=4,
        description="Planned and built renewables and storage. DESNZ REPD, July 2026.",
        color=TECH_COLOR_SPEC, size=MW_SIZE, opacity=0.8,
        popup=[["n", "Project", None], ["t", "Technology", None], ["s", "Status", None], ["mw", "Capacity", "MW"],
               ["op", "Operator or applicant", None], ["pa", "Planning authority", None],
               ["dop", "Operational since", None], ["duc", "Construction started", None],
               ["dpg", "Permission granted", None], ["dsub", "Application submitted", None]],
        legend=TECH_COLORS, attribution=REPD_ATTR),
    LayerMeta(
        id="ecr_projects", title="Connection-register projects (DNO)", group="Generation & planning",
        type="pmtiles", geom="point", source_layer="ecr_projects", minzoom=6,
        description="Generation and storage projects with coordinates. DNO capacity registers. Small sites are "
                    "rounded or missing.",
        color={"prop": "st", "type": "categorical", "default": "#9a9a9a",
               "stops": [["connected", "#2c7fb8"], ["accepted", "#8856a7"], ["offered", "#e377c2"],
                         ["other", "#9a9a9a"]]},
        size=MW_SIZE, opacity=0.75,
        popup=[["n", "Project", None], ["t", "Technology", None], ["st", "Status", None], ["mw", "Export / storage", "MW"],
               ["cp", "Connection point", None], ["op", "Network operator", None]],
        legend=[["#2c7fb8", "Connected"], ["#8856a7", "Accepted"], ["#e377c2", "Offered"], ["#9a9a9a", "Other"]],
        attribution=ECR_ATTR,
        inputs=["norm/UKPN/queue_project.parquet", "norm/NPG/queue_project.parquet",
                "norm/ENW/queue_project.parquet", "norm/SPEN/queue_project.parquet",
                "norm/NGED/queue_project.parquet", "norm/SSEN/queue_project.parquet"]),
    LayerMeta(
        id="tec", title="Transmission connections (TEC)", group="Generation & planning", type="geojson",
        geom="point", minzoom=4,
        description="Transmission projects matched to sites by name. NESO TEC, 10 Nov 2026. Unmatched sites are "
                    "missing.",
        color={"prop": "q", "type": "steps", "default": "#cbc9e2",
               "stops": [[0, "#dadaeb"], [200, "#bcbddc"], [500, "#9e9ac8"], [1000, "#756bb1"], [2000, "#54278f"]]},
        size={"prop": "q", "stops": [[0, 4], [200, 6], [1000, 10], [3000, 15]]}, opacity=0.85,
        popup=[["n", "Connection site", None], ["q", "Pipeline (not yet built)", "MW"], ["b", "Built", "MW"],
               ["uc", "Under construction", "MW"], ["cn", "Consents approved", "MW"],
               ["aw", "Awaiting consents", "MW"], ["sc", "Scoping", "MW"], ["np", "Projects", None],
               ["top", "Largest projects", None]],
        legend=[["#dadaeb", "<200 MW pipeline"], ["#9e9ac8", "500-1000"], ["#54278f", "2 GW+"]],
        attribution=TEC_ATTR, inputs=["derived/site.parquet"]),
]


# ---------------------------------------------------------------- technology classifier

_TECH_RULES: list[tuple[str, str]] = [
    (r"offshore", "Offshore wind"),
    (r"wind", "Onshore wind"),
    (r"solar|photovolt|\bpv\b", "Solar"),
    (r"batter|bess|storage|flywheel|compressed air|liquid air|\bcaes\b|\blaes\b|accumulator", "Battery / storage"),
    (r"hydro|tidal|wave", "Hydro"),
    (r"biomass|biogas|anaerobic|landfill|sewage|waste|incinerat|\befw\b|advanced conversion|\bchp\b|bio", "Biomass & waste"),
]
_TECH_RE = [(re.compile(p, re.IGNORECASE), v) for p, v in _TECH_RULES]


def tech_class(text: str | None) -> str:
    if not text:
        return "Other"
    t = text
    if re.search(r"pump", t, re.IGNORECASE) and re.search(r"hydro", t, re.IGNORECASE):
        return "Hydro"
    for rx, v in _TECH_RE:
        if rx.search(t):
            return v
    return "Other"


def _first(*vals: Any) -> str | None:
    for v in vals:
        if v not in (None, ""):
            return v
    return None


def _write_pmtiles(ctx: Ctx, layer: str, feats: list[dict], zmin: int, zmax: int = 12) -> Path:
    src = ctx.tmp / f"gen_{layer}.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / f"{layer}.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", layer, f"-Z{zmin}", f"-z{zmax}",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)
    return out


def _pt(lat: float, lon: float, props: dict[str, Any]) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {k: v for k, v in props.items() if v is not None}}


# ---------------------------------------------------------------- REPD

REPD_DROP = {"Application Refused", "Application Withdrawn", "Appeal Refused", "Appeal Withdrawn", "Abandoned",
             "Planning Permission Expired", "Decommissioned", "Revised"}
REPD_STATUS = {"Awaiting Construction": "Awaiting construction", "Operational": "Operational",
               "Under Construction": "Under construction", "Application Submitted": "Application submitted",
               "Appeal Lodged": "Appeal lodged", "No Application Required": "No application required"}


def _ddmmyyyy(v: str | None) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", v or "")
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else None


def repd_features(csv_path: Path) -> tuple[list[dict], dict[str, int]]:
    d = pl.read_csv(csv_path, infer_schema=False, encoding="utf8-lossy")
    d = d.rename({c: c.lstrip("﻿").strip() for c in d.columns})
    stats = {"rows": d.height, "dropped_status": 0, "no_coords": 0, "ni": 0, "kept": 0}
    d = d.filter(pl.col("Development Status (short)").is_in(sorted(REPD_DROP)).not_())
    stats["dropped_status"] = stats["rows"] - d.height
    ni = d["Country"] == "Northern Ireland"  # Irish Grid, not BNG: outside a GB map
    stats["ni"] = int(ni.sum())
    d = d.filter(~ni.fill_null(False))
    d = add_coords(d, e="X-coordinate", n="Y-coordinate")
    stats["no_coords"] = int(d["lat"].is_null().sum())
    d = d.filter(pl.col("lat").is_not_null())
    feats = []
    for r in d.iter_rows(named=True):
        mw = parse_num(r["Installed Capacity (MWelec)"])
        status = REPD_STATUS.get(r["Development Status (short)"], r["Development Status (short)"])
        feats.append(_pt(r["lat"], r["lon"], {
            "n": r["Site Name"], "t": tech_class(r["Technology Type"]), "s": status,
            "mw": round(mw, 3) if mw is not None else None,
            "op": r["Operator (or Applicant)"], "pa": r["Planning Authority"],
            "dop": _ddmmyyyy(r["Operational"]), "duc": _ddmmyyyy(r["Under Construction"]),
            "dpg": _ddmmyyyy(r["Planning Permission  Granted"]), "dsub": _ddmmyyyy(r["Planning Application Submitted"]),
        }))
    stats["kept"] = len(feats)
    return feats, stats


# ---------------------------------------------------------------- ECR projects

def ecr_features(df: pl.DataFrame) -> list[dict]:
    df = df.filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null())
    feats = []
    for r in df.iter_rows(named=True):
        mw = r["export_mw"] if r["export_mw"] else r["storage_mw"]
        if not mw or mw <= 0:  # import-only (heat pumps, EV chargers) rows are not generation
            continue
        status = r["status"] if r["status"] in ("connected", "accepted", "offered") else "other"
        feats.append(_pt(r["lat"], r["lon"], {
            "n": _first(r["site_name"], r["connection_point"]), "t": tech_class(r["technology"]), "st": status,
            "mw": round(mw, 3), "cp": r["connection_point"], "op": r["operator"]}))
    return feats


# ---------------------------------------------------------------- TEC

TEC_STATUS = {"Built": "b", "Under Construction/Commissioning": "uc", "Consents Approved": "cn",
              "Awaiting Consents": "aw", "Scoping": "sc"}
_HOST_PREF = {"SHET": ("SSEN-T", "SSEN"), "SPT": ("SPEN",)}
_KIND_RANK = {"GSP": 0, "TRANSMISSION": 0, "BSP": 1, "PRIMARY": 2}


def _target_sites(data_dir: Path) -> dict[str, list[dict]]:
    """normalised name -> candidate sites (GSP/BSP/primary of every operator, plus SSEN-T substations)."""
    from ..match import name_norm
    out: dict[str, list[dict]] = defaultdict(list)
    p = data_dir / "derived" / "site.parquet"
    if p.exists():
        s = pl.read_parquet(p).filter(pl.col("lat").is_not_null() & pl.col("kind").is_in(list(_KIND_RANK)))
        for r in s.iter_rows(named=True):
            c = {"site_id": r["site_id"], "operator": r["operator"], "name": r["name"], "kind": r["kind"],
                 "lat": r["lat"], "lon": r["lon"]}
            out[name_norm(r["name"])].append(c)
    for sid in ("ssent_sites_supergrid", "ssent_sites_grid"):
        snap = latest_snapshot(sid)
        if snap is None:
            continue
        gj = next((f for f in snapshot_files(snap) if f.suffix == ".geojson"), None)
        if gj is None:
            continue
        for f in json.loads(gj.read_text())["features"]:
            pr, pt = f["properties"], f["properties"].get("geo_point_2d") or {}
            if pt.get("lat") is None:
                continue
            out[name_norm(pr["name"])].append(
                {"site_id": f"SSEN-T:{name_norm(pr['name'])}", "operator": "SSEN-T", "name": pr["name"].title(),
                 "kind": "TRANSMISSION", "lat": pt["lat"], "lon": pt["lon"]})
    return out


def match_site(text: str, host: str | None, idx: dict[str, list[dict]]) -> dict | None:
    from ..match import base_name, name_norm
    n = name_norm(text)
    if not n:
        return None
    cands = idx.get(n) or idx.get(base_name(n)) or []
    if not cands:
        return None
    pref = _HOST_PREF.get(host or "", ())
    return min(cands, key=lambda c: (_KIND_RANK.get(c["kind"], 9), c["operator"] not in pref,
                                     c["operator"] != "NESO"))


def tec_features(csv_path: Path, idx: dict[str, list[dict]]) -> tuple[list[dict], dict[str, float]]:
    d = pl.read_csv(csv_path, infer_schema=False, encoding="utf8-lossy")
    d = d.rename({c: c.lstrip("﻿").strip() for c in d.columns})
    sites: dict[str, dict] = {}
    tot = matched = 0.0
    rows = rows_m = 0
    for r in d.iter_rows(named=True):
        mw = parse_num(r["Cumulative Total Capacity (MW)"]) or 0.0
        rows += 1
        tot += mw
        site = match_site(r["Connection Site"] or "", r["HOST TO"], idx)
        if site is None:
            continue
        rows_m += 1
        matched += mw
        s = sites.setdefault(site["site_id"], {"site": site, "b": 0.0, "uc": 0.0, "cn": 0.0, "aw": 0.0, "sc": 0.0,
                                               "projects": []})
        key = TEC_STATUS.get((r["Project Status"] or "").strip())
        if key:
            s[key] += mw
        s["projects"].append((mw, r["Project Name"], r["Project Status"], r["Plant Type"]))
    feats = []
    for s in sites.values():
        top = sorted(s["projects"], key=lambda x: -x[0])[:5]
        pipeline = s["uc"] + s["cn"] + s["aw"] + s["sc"]
        feats.append(_pt(s["site"]["lat"], s["site"]["lon"], {
            "n": s["site"]["name"], "q": round(pipeline), "b": round(s["b"]), "uc": round(s["uc"]),
            "cn": round(s["cn"]), "aw": round(s["aw"]), "sc": round(s["sc"]), "np": len(s["projects"]),
            "top": "; ".join(f"{nm} ({mw:g} MW, {st})" for mw, nm, st, _plant in top)}))
    return feats, {"rows": rows, "rows_matched": rows_m, "mw_total": tot, "mw_matched": matched,
                   "sites": len(feats)}


# ---------------------------------------------------------------- build

def build(ctx: Ctx) -> dict[str, Path]:
    out: dict[str, Path] = {}
    snap = latest_snapshot("desnz_repd")
    if snap is not None:
        csv = next(f for f in snapshot_files(snap) if f.suffix == ".csv")
        feats, st = repd_features(csv)
        print(f"repd: {st}")
        out["repd"] = _write_pmtiles(ctx, "repd", feats, 4)

    frames = []
    for op in ("UKPN", "NPG", "ENW", "SPEN", "NGED", "SSEN"):
        p = ctx.data_dir / "norm" / op / "queue_project.parquet"
        if p.exists():
            frames.append(pl.read_parquet(p))
    if frames:
        feats = ecr_features(pl.concat(frames, how="diagonal_relaxed"))
        print(f"ecr_projects: {len(feats)} features")
        if feats:
            out["ecr_projects"] = _write_pmtiles(ctx, "ecr_projects", feats, 6, 13)

    snap = latest_snapshot("neso_tec_register")
    if snap is not None:
        csv = next(f for f in snapshot_files(snap) if f.suffix == ".csv")
        feats, st = tec_features(csv, _target_sites(ctx.data_dir))
        print(f"tec: {st}; as_of={snapshot_meta(snap).get('portal_modified')}")
        p = ctx.tmp / "tec.geojson"
        p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        out["tec"] = p
    return out
