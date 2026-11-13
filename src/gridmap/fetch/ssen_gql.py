"""SSEN smart-meter LV feeder data from the SSEN portal's GraphQL endpoint (Hasura over BigQuery).

Source: portal "ssen_gql", base_url = the /v1/graphql URL, params.day = the one day of half-hourly feeder usage to
take (the open `ssen_smart_meter_dev_*` tables end 2024-09-05; the `_prod_` tables answer BigQuery 403).
Snapshot = the raw GraphQL page responses, gzip, one file per page: lookup_NNNN.json.gz (feeder x postcode) and
usage_SS_NNNN.json.gz (SS = half-hour slot), read by extra/lv_smart._ssen. Polite: 10k rows per page, stable
order_by, >= 0.5 s between requests, honest User-Agent, no key. Data is static, so cadence is 30d and a re-run
for the same day is `unchanged` (portal_modified = the day).
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from .common import Ctx, FetchError, post_json
from .models import Result, Source

LOOKUP = "ssen_smart_meter_dev_lv_feeder_lookup_optimized"
USAGE = "ssen_smart_meter_dev_lv_feeder_usage_optimized"
LOOKUP_FIELDS = "dataset_id postcode total_mpan_count primary_substation_name secondary_substation_name"
USAGE_FIELDS = "dataset_id total_consumption_active_import aggregated_device_count_active data_collection_log_timestamp"
PAGE = 10000
MAX_PAGES = 600  # request budget per run (a normal day is ~200)
DEFAULT_DAY = "2024-09-04"


def _save(snap: Path, name: str, body: dict[str, Any]) -> int:
    rows = next(iter(body["data"].values()))
    (snap / name).write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), 6))
    return len(rows)


async def _count(ctx: Ctx, url: str, table: str, where: str = "") -> int:
    body = await post_json(ctx, url, {"query": f"{{ {table}_aggregate{where} {{ aggregate {{ count }} }} }}"})
    return int(body["data"][f"{table}_aggregate"]["aggregate"]["count"])


async def _pages(ctx: Ctx, url: str, snap: Path, prefix: str, table: str, fields: str, where: str, order: str,
                 total: int, budget: list[int]) -> int:
    got = 0
    for i, off in enumerate(range(0, total, PAGE)):
        if budget[0] <= 0:
            raise FetchError(f"request budget exhausted in {prefix} after {got} rows")
        budget[0] -= 1
        body = await post_json(ctx, url, {"query": f"{{ {table}(limit:{PAGE}, offset:{off}, order_by:{order}{where}) "
                                                    f"{{ {fields} }} }}"})
        n = _save(snap, f"{prefix}_{i:04d}.json.gz", body)
        got += n
        if n < PAGE:
            break
    return got


async def fetch(src: Source, ctx: Ctx) -> Result:
    url = src.base_url
    day = (src.params or {}).get("day", DEFAULT_DAY)
    if (res := ctx.unchanged(src, day)) is not None:
        res.url = url
        return res
    if ctx.dry_run:
        return Result("dry_run", url=url, portal_modified=day, as_of=day, note=f"POST {url}: lookup + usage for {day}")
    snap = ctx.store.new_snapshot(src.id)
    try:
        budget = [MAX_PAGES]
        n_lk = await _count(ctx, url, LOOKUP)
        lk = await _pages(ctx, url, snap, "lookup", LOOKUP, LOOKUP_FIELDS, "", "[{dataset_id:asc, postcode:asc}]",
                          n_lk, budget)
        us = 0
        for slot in range(48):
            ts = f"{day}T{slot // 2:02d}:{slot % 2 * 30:02d}:00Z"
            where = f'{{data_collection_log_timestamp: {{_eq: "{ts}"}}}}'
            n = await _count(ctx, url, USAGE, f"(where: {where})")
            if n:
                us += await _pages(ctx, url, snap, f"usage_{slot:02d}", USAGE, USAGE_FIELDS, f", where: {where}",
                                   "[{dataset_id:asc}]", n, budget)
    except FetchError as e:
        return Result("error", url=url, snapshot=snap, note=str(e))
    files = sorted(p.name for p in snap.iterdir())
    digest = hashlib.sha256()
    for f in files:
        digest.update((snap / f).read_bytes())
    res = Result("ok", rows=lk + us, expected_rows=n_lk + us, bytes=sum((snap / f).stat().st_size for f in files),
                 sha256=digest.hexdigest(), portal_modified=day, as_of=day, url=url, files=files, snapshot=snap,
                 note=f"lookup {lk} rows + half-hourly feeder usage for {day} ({us} rows)")
    if not us or not lk:
        res.status, res.note = "empty_or_gated", f"lookup {lk} rows, usage {us} rows for {day}"
    return res
