"""ArcGIS FeatureServer layers, paged (e.g. EirGrid transmission network: 25k line segments, 2,000 per page).

Source: portal "arcgis", base_url = the FeatureServer URL, params.layers = comma-separated layer ids.
Every layer is queried with where=1=1, outSR=4326, f=geojson and paged with resultOffset until the server
stops saying exceededTransferLimit. One file per layer: layer_<id>.geojson. Other params are passed through.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .common import Ctx, FetchError, get
from .models import Result, Source

PAGE = 2000
MAX_PAGES = 200  # 400k features: a runaway guard, far above any layer we use


async def layer(ctx: Ctx, base: str, lid: str, extra: dict[str, Any]) -> list[dict]:
    feats: list[dict] = []
    for page in range(MAX_PAGES):
        params = {"where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson", "geometryPrecision": "6",
                  **extra, "resultOffset": str(page * PAGE), "resultRecordCount": str(PAGE)}
        resp = await get(ctx, f"{base}/{lid}/query", params=params)
        if resp.status_code != 200:
            raise FetchError(f"layer {lid}: HTTP {resp.status_code}")
        doc = resp.json()
        if "error" in doc:
            raise FetchError(f"layer {lid}: {doc['error']}")
        got = doc.get("features") or []
        feats += got
        more = doc.get("exceededTransferLimit") or (doc.get("properties") or {}).get("exceededTransferLimit")
        if not got or not more:
            return feats
    raise FetchError(f"layer {lid}: more than {MAX_PAGES} pages")


async def fetch(src: Source, ctx: Ctx) -> Result:
    base = src.base_url.rstrip("/")
    params = dict(src.params or {})
    ids = [x.strip() for x in str(params.pop("layers", "")).split(",") if x.strip()]
    if not ids:
        return Result("error", url=base, note="params.layers is empty")
    if ctx.dry_run:
        return Result("dry_run", url=base, note=f"would page layers {','.join(ids)}")
    try:
        layers = {lid: await layer(ctx, base, lid, params) for lid in ids}
    except FetchError as e:
        return Result("error", url=base, note=str(e))
    rows = sum(len(f) for f in layers.values())
    if rows == 0:
        return Result("empty_or_gated", url=base, rows=0, note="no features")
    snap = ctx.store.new_snapshot(src.id)
    files, size, digest = [], 0, hashlib.sha256()
    for lid, feats in layers.items():
        data = json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")).encode()
        (snap / f"layer_{lid}.geojson").write_bytes(data)
        files.append(f"layer_{lid}.geojson")
        size += len(data)
        digest.update(data)
    return Result("ok", rows=rows, bytes=size, sha256=digest.hexdigest(), url=base, files=files, snapshot=snap,
                  note=", ".join(f"layer {k}: {len(v)}" for k, v in layers.items()))
