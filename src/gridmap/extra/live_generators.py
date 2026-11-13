"""Live large generators: Elexon Physical Notifications (the output each BM unit has notified for this minute)
for transmission-connected and embedded BM units, placed on the map by matching the unit's name and capacity to
DESNZ REPD operational sites and to NESO TEC 'Built' projects (located via their connection substation).

Honest limits: PN is the notified plan, not metered output (metered B1610 actuals reach BMRS a day or more late);
BM units have no coordinates, so only units we can name+capacity match are shown (rate logged on every build and in
docs/sources/live.md); aggregator/virtual units (2__, C__, V_ prefixes) are not single sites and are left out."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..normalise.common import latest_snapshot, parse_num, snapshot_files, snapshot_meta
from . import Ctx, LayerMeta

SOURCES = ["elexon_pn_now", "elexon_bmunits", "desnz_repd", "neso_tec_register", "ssent_sites_grid",
           "ssent_sites_supergrid"]

CLASS_COLORS = [["Wind", "#56b4e9"], ["Solar", "#f0e442"], ["Gas", "#e69f00"], ["Nuclear", "#cc79a7"],
                ["Hydro & pumped storage", "#009e73"], ["Biomass & waste", "#8c510a"], ["Battery", "#7b3294"],
                ["Coal & other", "#6b6b6b"]]

LAYERS = [
    LayerMeta(
        id="live_generators", title="Large generators: output now (Elexon)", group="Live", type="geojson",
        geom="point", minzoom=4, live=True, default_on=False,
        description="Power stations, wind farms and large batteries that Elexon lists as BM units, sized by the "
                    "output they have notified for this minute (negative = charging or pumping). This is the notified "
                    "plan, not meter readings. Only units we could match to a mapped site by name and capacity are "
                    "shown, so coverage is partial (mostly transmission-connected plant).",
        color={"prop": "t", "type": "categorical", "default": "#6b6b6b", "stops": CLASS_COLORS},
        size={"prop": "a", "stops": [[0, 3], [20, 5], [100, 8], [400, 12], [1000, 16]]}, opacity=0.85,
        popup=[["n", "Generator", None], ["t", "Type", None], ["mw", "Notified output now", "MW"],
               ["cap", "Registered capacity", "MW"], ["op", "Lead party", None], ["bm", "BM unit", None],
               ["loc", "Location from", None], ["tt", "Data as of (UTC)", None]],
        legend=CLASS_COLORS,
        attribution="Contains BMRS data (c) Elexon Limited copyright and database right; locations: DESNZ REPD (OGL v3), "
                    "NESO TEC register (NESO Open Data Licence)",
        inputs=["derived/site.parquet"],
    )
]

_STOP = re.compile(r"\b(wind\s*farm|windfarm|wf|power\s*station|power|station|limited|ltd|plc|llp|energy|energies|"
                   r"renewables?|generation|generating|gen|offshore|onshore|extension|ext|the|farm|solar|park|bess|"
                   r"battery|storage|cascade|unit|phase|\d+)\b")
_CODE = re.compile(r"^[A-Z0-9_\-\.]+$")  # 'T_BLHLB-1', '2__AFLEX001': an ID, not a name

FUEL_CLASS = {"WIND": "Wind", "CCGT": "Gas", "OCGT": "Gas", "NUCLEAR": "Nuclear", "NPSHYD": "Hydro & pumped storage",
              "PS": "Hydro & pumped storage", "BIOMASS": "Biomass & waste", "COAL": "Coal & other",
              "OIL": "Coal & other", "OTHER": "Coal & other"}
REPD_CLASS = {"Solar": "Solar", "Onshore wind": "Wind", "Offshore wind": "Wind", "Battery / storage": "Battery",
              "Hydro": "Hydro & pumped storage", "Biomass & waste": "Biomass & waste", "Other": "Coal & other"}
TEC_CLASS = [(r"wind", "Wind"), (r"solar|photovolt", "Solar"), (r"nuclear", "Nuclear"),
             (r"ccgt|ocgt|gas|chp", "Gas"), (r"hydro|pump", "Hydro & pumped storage"),
             (r"biomass|waste|biogas", "Biomass & waste"), (r"storage|batter", "Battery"), (r"coal", "Coal & other")]


def norm_name(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", _STOP.sub(" ", s)).strip()


def tec_class(plant: str | None) -> str | None:
    for rx, cls in TEC_CLASS:
        if re.search(rx, plant or "", re.IGNORECASE):
            return cls
    return None


def current_levels(pn: list[dict[str, Any]], now: str) -> dict[str, tuple[float, str]]:
    """{bm unit: (MW now, interval start)} from PN rows; linear between levelFrom/levelTo inside the interval,
    otherwise the newest interval that started before `now`. ISO Z strings compare lexically."""
    from datetime import datetime
    out: dict[str, tuple[float, str]] = {}
    best: dict[str, str] = {}
    for r in pn:
        bm = r.get("bmUnit") or r.get("nationalGridBmUnit")
        tf, tt = r.get("timeFrom"), r.get("timeTo")
        lf, lt = r.get("levelFrom"), r.get("levelTo")
        if not bm or not tf or not tt or lf is None or lt is None or tf > now:
            continue
        if bm in best and best[bm] >= tf:
            continue
        best[bm] = tf
        level = lf
        if tt > now:
            a, b, c = (datetime.fromisoformat(x) for x in (tf, tt, now))
            span = (b - a).total_seconds()
            level = lf + (lt - lf) * ((c - a).total_seconds() / span) if span > 0 else lf
        out[bm] = (float(level), tf)
    return out


def repd_candidates(feats: list[dict]) -> list[dict[str, Any]]:
    out = []
    for f in feats:
        p = f["properties"]
        if p.get("s") != "Operational" or not p.get("mw"):
            continue
        lon, lat = f["geometry"]["coordinates"]
        out.append({"key": norm_name(p["n"]), "n": p["n"], "mw": p["mw"], "cls": REPD_CLASS.get(p.get("t")),
                    "lat": lat, "lon": lon, "src": "DESNZ REPD"})
    return out


def tec_candidates(csv_text: str, idx: dict[str, list[dict]]) -> list[dict[str, Any]]:
    from .gen_planning import match_site
    out = []
    for r in csv.DictReader(io.StringIO(csv_text.lstrip("﻿"))):
        if (r.get("Project Status") or "").strip() != "Built":
            continue
        mw = parse_num(r.get("Cumulative Total Capacity (MW)")) or parse_num(r.get("MW Connected"))
        site = match_site(r.get("Connection Site") or "", r.get("HOST TO"), idx)
        if not mw or site is None:
            continue
        out.append({"key": norm_name(r.get("Project Name")), "n": r["Project Name"], "mw": mw,
                    "cls": tec_class(r.get("Plant Type")), "lat": site["lat"], "lon": site["lon"],
                    "src": "NESO TEC (connection substation)"})
    return out


def bmu_class(bmu: dict[str, Any]) -> str | None:
    """Technology class from the BM unit record: battery wording in its names wins, then Elexon's fuelType
    (OTHER / missing = unknown, so the matched site's technology decides)."""
    text = f"{bmu.get('bmUnitName') or ''} {bmu.get('leadPartyName') or ''}"
    if re.search(r"batter|bess|storage", text, re.IGNORECASE):
        return "Battery"
    fuel = bmu.get("fuelType")
    return FUEL_CLASS.get(fuel) if fuel not in (None, "OTHER") else None


def _score(q: str, k: str, fuzz: Any) -> float:
    """Token-sorted similarity; a pure subset counts only when the shorter side is a distinctive name (>= 8 chars,
    or 2+ words), so 'pivot' does not match every 'Pivot Power ...' site."""
    s = fuzz.token_sort_ratio(q, k)
    a, b = set(q.split()), set(k.split())
    small = q if len(a) <= len(b) else k
    if a and b and (a <= b or b <= a) and (len(small) >= 8 or len(small.split()) >= 2):
        s = max(s, 95)
    return s


def match_bmu(bmu: dict[str, Any], cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best candidate whose name fuzzy-matches (>= 90) the BM unit name or lead party and whose capacity is
    compatible (the unit may be one of several at a site, so unit <= ~1.3 x site and >= 15 % of it)."""
    from rapidfuzz import fuzz
    cap = parse_num(bmu.get("generationCapacity")) or 0.0
    if cap <= 0:
        return None
    names = []
    nm = (bmu.get("bmUnitName") or "").replace("\ufffd", " ")
    if not _CODE.match(nm):
        names.append(norm_name(nm))
    names.append(norm_name(bmu.get("leadPartyName")))
    fuel = bmu_class(bmu)
    best: tuple[float, float, dict] | None = None
    for c in cands:
        if not c["key"] or not (0.15 <= cap / c["mw"] <= 1.3):
            continue
        if fuel and c["cls"] and c["cls"] not in (fuel, "Coal & other"):
            continue
        for q in names:
            if len(q) < 5 or min(len(q), len(c["key"])) < 5:
                continue
            s = _score(q, c["key"], fuzz)
            if s >= 90 and (best is None or (s, -abs(cap - c["mw"])) > best[:2]):
                best = (s, -abs(cap - c["mw"]), c)
    return best[2] if best else None


def build_features(bmus: list[dict], levels: dict[str, tuple[float, str]], cands: list[dict],
                   min_mw: float = 5.0) -> tuple[list[dict], dict[str, Any]]:
    cand, matched, feats = 0, 0, []
    for b in bmus:
        if b.get("bmUnitType") not in ("T", "E", "M"):
            continue
        cap = parse_num(b.get("generationCapacity")) or 0.0
        lv = levels.get(b.get("elexonBmUnit")) or levels.get(b.get("nationalGridBmUnit"))
        if cap < min_mw and not (lv and abs(lv[0]) >= min_mw):
            continue
        cand += 1
        m = match_bmu(b, cands)
        if m is None or lv is None:
            continue
        matched += 1
        mw = lv[0]
        cls = bmu_class(b) or m["cls"] or "Coal & other"
        nm = (b.get("bmUnitName") or "").replace("\ufffd", " ")
        props = {"n": nm if not _CODE.match(nm) else m["n"],
                 "t": cls, "mw": round(mw, 1), "a": round(abs(mw), 1), "cap": round(cap, 1),
                 "op": b.get("leadPartyName"), "bm": b.get("elexonBmUnit"), "loc": m["src"], "tt": lv[1][11:16]}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(m["lon"], 5),
                                                                                         round(m["lat"], 5)]},
                      "properties": {k: v for k, v in props.items() if v is not None}})
    return feats, {"candidate_units": cand, "matched": matched,
                   "match_rate": round(matched / cand, 3) if cand else None}


def build(ctx: Ctx) -> dict[str, Path]:
    from .gen_planning import _target_sites, repd_features
    pn_s, bm_s = latest_snapshot("elexon_pn_now"), latest_snapshot("elexon_bmunits")
    if pn_s is None or bm_s is None:
        return {}
    pn = json.loads(next(f for f in snapshot_files(pn_s) if f.name == "data.json").read_text())
    bmus = json.loads(next(f for f in snapshot_files(bm_s) if f.name == "data.json").read_text())
    now = snapshot_meta(pn_s)["fetched_at"]
    cands: list[dict[str, Any]] = []
    repd = latest_snapshot("desnz_repd")
    if repd:
        csv_f = next(f for f in snapshot_files(repd) if f.suffix == ".csv")
        cands += repd_candidates(repd_features(csv_f)[0])
    tec = latest_snapshot("neso_tec_register")
    if tec:
        csv_f = next(f for f in snapshot_files(tec) if f.suffix == ".csv")
        cands += tec_candidates(csv_f.read_text(errors="replace"), _target_sites(ctx.data_dir))
    feats, st = build_features(bmus, current_levels(pn, now), cands)
    print(f"live_generators: {st}")
    if not feats:
        return {}
    out = ctx.tmp / "live_generators.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return {"live_generators": out}
