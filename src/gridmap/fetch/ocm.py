"""Open Charge Map EV charge points (CC BY 4.0), key in the X-API-Key header (auth_env, default OCM_API_KEY).

OCM has no offset parameter and returns at most `maxresults` rows, so Great Britain is split into bounding boxes
recursively until every box returns fewer than the cap (never truncated). Snapshot: pois.json
({"operators", "usage", "pois"}), read by extra/dem_ev. Polite: one request at a time, >= 1 s apart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from .common import BACKOFF, Ctx, FetchError, HttpError, get
from .models import Result, Source

CAP = 5000
GB_BOX = (49.8, -8.7, 60.9, 1.8)  # lat_min, lon_min, lat_max, lon_max
MIN_ROWS = 10_000  # fewer POIs than this means a bad key / outage, not a real GB dataset
PAUSE = 1.0
MIN_SPAN = 0.002  # degrees: stop splitting below this


def poi(p: dict) -> dict | None:
    a = p.get("AddressInfo") or {}
    conns = p.get("Connections") or []
    kws = [c["PowerKW"] for c in conns if c.get("PowerKW")]
    return {"id": p["ID"], "lat": a.get("Latitude"), "lon": a.get("Longitude"), "nm": a.get("Title"),
            "town": a.get("Town"), "pc": a.get("Postcode"), "op": p.get("OperatorID"), "use": p.get("UsageTypeID"),
            "st": p.get("StatusTypeID"), "kw": max(kws) if kws else None,
            "c": sum(c.get("Quantity") or 1 for c in conns) if conns else None,
            "conn": [[c.get("PowerKW"), c.get("ConnectionTypeID"), c.get("CurrentTypeID"), c.get("Quantity")]
                     for c in conns], "dlsu": p.get("DateLastStatusUpdate")}


async def _json(ctx: Ctx, url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
    resp = await get(ctx, url, headers=headers, params=params)
    if resp.status_code != 200:
        raise HttpError(resp.status_code, url)
    await asyncio.sleep(PAUSE if BACKOFF else 0)
    return resp.json()


async def fetch(src: Source, ctx: Ctx) -> Result:
    base = src.base_url.rstrip("/")
    key = ctx.key(src)
    if not key:
        return Result("needs_key", url=base, note=f"set {src.auth_env}")
    if ctx.dry_run:
        return Result("dry_run", url=base, note="would split GB into bounding boxes and fetch all POIs")
    headers = {"X-API-Key": key}
    pois: dict[int, dict] = {}

    async def box(b: tuple[float, float, float, float]) -> None:
        la0, lo0, la1, lo1 = b
        res = await _json(ctx, f"{base}/poi", headers, {"countrycode": "GB", "maxresults": CAP, "compact": "true",
                                                         "verbose": "false", "boundingbox": f"({la0},{lo0}),({la1},{lo1})"})
        if len(res) >= CAP and max(la1 - la0, lo1 - lo0) > MIN_SPAN:
            if la1 - la0 >= lo1 - lo0:
                m = (la0 + la1) / 2
                await box((la0, lo0, m, lo1))
                await box((m, lo0, la1, lo1))
            else:
                m = (lo0 + lo1) / 2
                await box((la0, lo0, la1, m))
                await box((la0, m, la1, lo1))
            return
        for p in res:
            if (rec := poi(p)):
                pois[rec["id"]] = rec

    try:
        await box(GB_BOX)
        ref = await _json(ctx, f"{base}/referencedata", headers)
    except HttpError as e:
        return Result("needs_key" if e.status in (401, 403) else "error", url=base, note=str(e))
    except FetchError as e:
        return Result("error", url=base, note=str(e))
    if len(pois) < MIN_ROWS:
        return Result("empty_or_gated", url=base, rows=len(pois), note=f"only {len(pois)} POIs fetched")
    doc = {"operators": {str(o["ID"]): o["Title"] for o in ref["Operators"]},
           "usage": {str(u["ID"]): u["Title"] for u in ref["UsageTypes"]}, "pois": list(pois.values())}
    snap = ctx.store.new_snapshot(src.id)
    dest = snap / "pois.json"
    data = json.dumps(doc, separators=(",", ":")).encode()
    dest.write_bytes(data)
    return Result("ok", rows=len(pois), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), url=base,
                  files=[dest.name], snapshot=snap)
