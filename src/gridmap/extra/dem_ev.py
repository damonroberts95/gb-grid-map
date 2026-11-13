"""Public EV charge points from Open Charge Map (CC BY 4.0).

Fetch (network, needs OCM_API_KEY in .env; run by hand, ~weekly):
    uv run python -I -m gridmap.extra.dem_ev --fetch
Writes data/raw/ocm_gb_poi/<ts>/{pois.json,meta.json} in the usual snapshot layout. OCM has no offset parameter and
returns at most `maxresults` rows, so Great Britain is split into bounding boxes recursively until every box
returns fewer than the cap (never truncated). `build` below is pure: it only reads that snapshot.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ..normalise.common import DATA_DIR, best_coords, latest_snapshot, snapshot_files
from ..tools import tool
from . import Ctx, LayerMeta

SOURCES = ["ocm_gb_poi"]  # not in sources.yaml: needs a header key + bbox pagination (see docstring)
API = "https://api.openchargemap.io/v3"
USER_AGENT = "gridmap-local/0.1"
CAP = 5000
GB_BOX = (49.8, -8.7, 60.9, 1.8)  # lat_min, lon_min, lat_max, lon_max
ATTRIBUTION = ("Data from Open Charge Map and its contributors, licensed under CC BY 4.0 "
               "(some records originate from the National Chargepoint Registry, Open Government Licence v2.0)")
_DROP_STATUS = {100, 150, 200, 210}  # not operational, planned, removed, duplicate

LAYERS = [
    LayerMeta(
        id="ev_chargers", title="EV charge points", group="Demand & LV", type="pmtiles", geom="point",
        source_layer="ev_chargers", minzoom=8,
        description="Public EV charge points. Colour is fastest connector. Open Charge Map.",
        color={"prop": "kw", "type": "steps", "default": "#999999",
               "stops": [[0, "#56B4E9"], [7.01, "#0072B2"], [22.01, "#CC79A7"], [50.01, "#882255"], [150.01, "#1b1b3a"]]},
        size={"prop": "kw", "stops": [[0, 2.5], [22.01, 3.5], [50.01, 5], [150.01, 7]]},
        popup=[["nm", "Site", None], ["op", "Operator", None], ["kw", "Fastest connector", "kW"],
               ["c", "Connectors", None], ["use", "Access", None]],
        legend=[["#56B4E9", "7 kW or less"], ["#0072B2", "7-22 kW"], ["#CC79A7", "22-50 kW"],
                ["#882255", "50-150 kW"], ["#1b1b3a", "over 150 kW"], ["#999999", "power not stated"]],
        attribution=ATTRIBUTION,
    )
]


# ---------- fetch (manual, network) ----------

def _poi(p: dict) -> dict | None:
    a = p.get("AddressInfo") or {}
    conns = p.get("Connections") or []
    kws = [c["PowerKW"] for c in conns if c.get("PowerKW")]
    return {"id": p["ID"], "lat": a.get("Latitude"), "lon": a.get("Longitude"), "nm": a.get("Title"),
            "town": a.get("Town"), "pc": a.get("Postcode"), "op": p.get("OperatorID"), "use": p.get("UsageTypeID"),
            "st": p.get("StatusTypeID"), "kw": max(kws) if kws else None,
            "c": sum(c.get("Quantity") or 1 for c in conns) if conns else None,
            "conn": [[c.get("PowerKW"), c.get("ConnectionTypeID"), c.get("CurrentTypeID"), c.get("Quantity")]
                     for c in conns], "dlsu": p.get("DateLastStatusUpdate")}


def fetch(key: str, pause: float = 1.0) -> Path:
    import httpx

    hdr = {"X-API-Key": key, "User-Agent": USER_AGENT}
    pois: dict[int, dict] = {}
    calls = 0

    def get(path: str, **params):
        nonlocal calls
        for attempt in range(4):
            r = httpx.get(f"{API}/{path}", params=params, headers=hdr, timeout=120)
            calls += 1
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * 2**attempt)
                continue
            r.raise_for_status()
            time.sleep(pause)
            return r.json()
        raise RuntimeError(f"Open Charge Map {path}: gave up after retries")

    def box(b: tuple[float, float, float, float]) -> None:
        la0, lo0, la1, lo1 = b
        res = get("poi", countrycode="GB", maxresults=CAP, compact="true", verbose="false",
                  boundingbox=f"({la0},{lo0}),({la1},{lo1})")
        if len(res) >= CAP and max(la1 - la0, lo1 - lo0) > 0.002:
            if la1 - la0 >= lo1 - lo0:
                m = (la0 + la1) / 2
                box((la0, lo0, m, lo1)), box((m, lo0, la1, lo1))
            else:
                m = (lo0 + lo1) / 2
                box((la0, lo0, la1, m)), box((la0, m, la1, lo1))
            return
        for p in res:
            if (rec := _poi(p)):
                pois[rec["id"]] = rec

    box(GB_BOX)
    ref = get("referencedata")
    snap = DATA_DIR / "raw" / "ocm_gb_poi" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    snap.mkdir(parents=True)
    doc = {"operators": {str(o["ID"]): o["Title"] for o in ref["Operators"]},
           "usage": {str(u["ID"]): u["Title"] for u in ref["UsageTypes"]}, "pois": list(pois.values())}
    (snap / "pois.json").write_text(json.dumps(doc, separators=(",", ":")))
    if len(pois) < 10_000:
        raise RuntimeError(f"only {len(pois)} POIs fetched: refusing to mark the snapshot ok")
    (snap / "meta.json").write_text(json.dumps({
        "source_id": "ocm_gb_poi", "url": f"{API}/poi", "status": "ok", "rows": len(pois), "calls": calls,
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": ["pois.json"],
        "licence": "CC BY 4.0 (Open Charge Map); some records OGL v2.0", "attribution": ATTRIBUTION}, indent=1))
    return snap


# ---------- build (pure) ----------

def _in_gb(lat: float, lon: float) -> bool:
    """best_coords already bounds to the GB box; also drop Ireland / Northern Ireland, which OCM files under GB."""
    if lon < -5.9 and 51.3 < lat < 55.2:
        return False
    return not (lon < -5.2 and 54.0 < lat < 55.2)


def build(ctx: Ctx) -> dict[str, Path]:
    snap = latest_snapshot("ocm_gb_poi")
    if snap is None:
        return {}
    doc = json.loads(next(p for p in snapshot_files(snap) if p.name == "pois.json").read_text())
    ops, use = doc["operators"], doc["usage"]
    feats = []
    for p in doc["pois"]:
        if p["st"] in _DROP_STATUS:
            continue
        la, lo, _ = best_coords(p["lat"], p["lon"])
        if la is None or not _in_gb(la, lo):
            continue
        props = {"nm": p["nm"], "op": ops.get(str(p["op"])) if p["op"] not in (None, 44) else None,  # 44 = unknown
                 "kw": p["kw"], "c": p["c"], "use": use.get(str(p["use"])) if p["use"] else None}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 5), round(la, 5)]},
                      "properties": {k: v for k, v in props.items() if v is not None}})
    if not feats:
        return {}
    src = ctx.tmp / "dem_ev_chargers.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    out = ctx.tmp / "dem_ev_chargers.pmtiles"
    subprocess.run([tool("tippecanoe"), "-q", "-f", "-o", str(out), "-l", "ev_chargers", "-Z8", "-z14",
                    "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", str(src)], check=True)
    return {"ev_chargers": out}


if __name__ == "__main__":
    if "--fetch" not in sys.argv:
        sys.exit(__doc__)
    import os

    from dotenv import load_dotenv

    from ..settings import KEYS_FILE

    load_dotenv(KEYS_FILE)
    k = os.environ.get("OCM_API_KEY")
    if not k:
        sys.exit("OCM_API_KEY not set in .env")
    s = fetch(k)
    print(f"snapshot {s} ({json.loads((s / 'meta.json').read_text())['rows']} POIs)")
