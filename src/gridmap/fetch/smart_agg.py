"""Half-hourly smart-meter feeds that are too big to snapshot whole, aggregated while fetching.

Portal "smart_agg"; the output of every kind is one snapshot file `agg.parquet` with one row per secondary
substation for the latest published month (sub_id, name, geo, n_hh, sum_wh, peak_wh, peak_ts, devs, t0, t1),
which is what extra/lv_smart reads. Raw rows are streamed to a temp dir and discarded, so memory and disk stay
bounded whatever the feed size. `select.kind`:

  ods         Opendatasoft export of feeder-level rows (SPEN). Feeders are summed per substation and half-hour
              by the portal (`select`/`group_by` on the export), then reduced to one row per substation here.
  ckan_parts  CKAN package of `*.csv.gz` parts named `...-YYYY-MM-partN` (NGED, ~520 MB gz per month). Only the
              newest month is taken; parts are downloaded one at a time and reduced to a partial parquet each.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from pathlib import Path

import duckdb

from . import opendatasoft
from .common import Ctx, FetchError, HttpError, download, get
from .models import Result, Source

ODS_PARAMS = {
    "select": "secondary_substation_id, secondary_substation_name, substation_geo_location, "
              "data_collection_log_timestamp as ts, sum(total_consumption_active_import) as wh, "
              "sum(aggregated_device_count_active) as devs, count(*) as nfeed",
    "group_by": "secondary_substation_id, secondary_substation_name, substation_geo_location, "
                "data_collection_log_timestamp",
}
PART_RE = re.compile(r"(\d{4}-\d{2})-part", re.IGNORECASE)
PART_GAP = 0.3  # seconds between part downloads (polite)
REDUCE = """
    SELECT sub_id, any_value(name) AS name, any_value(geo) AS geo, sum(n_hh) AS n_hh, sum(sum_wh) AS sum_wh,
           max(peak_wh) AS peak_wh, arg_max(peak_ts, peak_wh) AS peak_ts, max(devs) AS devs,
           min(t0) AS t0, max(t1) AS t1
    FROM read_parquet('{src}') GROUP BY 1"""


def _q(p: Path | str) -> str:
    return str(p).replace("'", "''")


def _ods_to_parquet(csv: Path, out: Path) -> None:
    duckdb.connect().execute(f"""COPY (
      SELECT secondary_substation_id AS sub_id, any_value(secondary_substation_name) AS name,
             any_value(substation_geo_location) AS geo, count(*) AS n_hh,
             sum(CAST(wh AS DOUBLE)) AS sum_wh, max(CAST(wh AS DOUBLE)) AS peak_wh,
             arg_max(ts, CAST(wh AS DOUBLE)) AS peak_ts, max(CAST(devs AS INTEGER)) AS devs,
             min(ts) AS t0, max(ts) AS t1
      FROM read_csv('{_q(csv)}', delim=';', header=true, all_varchar=true)
      GROUP BY 1) TO '{_q(out)}' (FORMAT parquet)""")


def _part_to_parquet(gz: Path, out: Path) -> None:
    duckdb.connect().execute(f"""COPY (
      SELECT secondary_substation_id::VARCHAR AS sub_id, any_value(secondary_substation_name) AS name,
             any_value(substation_geo_location) AS geo, count(*) AS n_hh,
             sum(TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS sum_wh,
             max(TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS peak_wh,
             arg_max(data_collection_log_timestamp, TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS peak_ts,
             max(TRY_CAST(aggregated_device_count_Active AS INTEGER)) AS devs,
             min(data_collection_log_timestamp) AS t0, max(data_collection_log_timestamp) AS t1
      FROM read_csv('{_q(gz)}', header=true, all_varchar=true, ignore_errors=true)
      GROUP BY 1) TO '{_q(out)}' (FORMAT parquet)""")


def _merge(partials: Path, out: Path) -> tuple[int, str | None]:
    duckdb.connect().execute(f"COPY ({REDUCE.format(src=_q(partials / '*.parquet'))}) TO '{_q(out)}' (FORMAT parquet)")
    return _count(out)


def _finish(snap: Path, url: str, modified: str | None, rows: int, t1: str | None, note: str) -> Result:
    f = snap / "agg.parquet"
    res = Result("ok", rows=rows, bytes=f.stat().st_size, sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
                 portal_modified=modified, as_of=t1, url=url, files=[f.name], snapshot=snap, note=note)
    if not rows:
        res.status, res.note = "empty_or_gated", "aggregate has 0 substations"
    return res


async def _ods(src: Source, ctx: Ctx) -> Result:
    key = ctx.key(src)
    headers = {"Authorization": f"Apikey {key}"} if key else {}
    base = opendatasoft._base(src)
    resp = await get(ctx, base, headers=headers)
    if resp.status_code != 200:
        return Result("error", url=base, note=f"catalog HTTP {resp.status_code}" + (f"; check {src.auth_env}" if resp.status_code == 401 else ""))
    meta = resp.json()
    modified = ((meta.get("metas") or {}).get("default") or {}).get("modified")
    if not meta.get("data_visible", True) and not key:
        return Result("needs_key", url=base, portal_modified=modified, note=f"data_visible=false; set {src.auth_env}")
    if (res := ctx.unchanged(src, modified)) is not None:
        res.url = base
        return res
    url = f"{base}/exports/csv"
    if ctx.dry_run:
        return Result("dry_run", url=url, portal_modified=modified, note="would stream grouped export and aggregate")
    snap = ctx.store.new_snapshot(src.id)
    tmp = Path(tempfile.mkdtemp(prefix="smart_agg_"))
    try:
        csv = tmp / "g.csv"
        await download(ctx, url, csv, headers=headers, params=ODS_PARAMS,
                       **({"max_bytes": src.max_mb * 2**20} if src.max_mb else {}))
        await asyncio.to_thread(_ods_to_parquet, csv, snap / "agg.parquet")
        rows, t1 = await asyncio.to_thread(lambda: _count(snap / "agg.parquet"))
    except FetchError as e:
        return Result("error", url=url, portal_modified=modified, snapshot=snap, note=str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _finish(snap, url, modified, rows, t1, "per-substation aggregate of one published month (sum over feeders)")


def _count(parquet: Path) -> tuple[int, str | None]:
    n, t1 = duckdb.connect().execute("select count(*), max(t1) from read_parquet(?)", [str(parquet)]).fetchone()
    return n, None if t1 is None else str(t1)


async def _ckan_parts(src: Source, ctx: Ctx) -> Result:
    key = ctx.key(src)
    headers = {"Authorization": key} if key else {}
    url = f"{src.base_url.rstrip('/')}/api/3/action/package_show"
    resp = await get(ctx, url, params={"id": src.dataset}, headers=headers)
    if resp.status_code != 200 or not resp.json().get("success"):
        return Result("error", url=url, note=f"package_show HTTP {resp.status_code}")
    pkg = resp.json()["result"]
    modified = pkg.get("metadata_modified")
    parts = [r for r in pkg.get("resources") or [] if str(r.get("name", "")).endswith(".csv.gz") and PART_RE.search(r["name"])]
    usable = [r for r in parts if r.get("url") and r["url"] != "redacted"]
    if not usable:
        return Result("needs_key", url=url, portal_modified=modified,
                      note=f"{len(parts)} part url(s) redacted (registered users only); set {src.auth_env}")
    month = max(PART_RE.search(r["name"])[1] for r in usable)
    chosen = sorted((r for r in usable if PART_RE.search(r["name"])[1] == month), key=lambda r: r["name"])
    declared = sum(int(r.get("size") or 0) for r in chosen)
    note = f"aggregate of {len(chosen)} gz parts for {month} ({declared / 2**20:.0f} MB downloaded and discarded)"
    if src.max_mb and declared > src.max_mb * 2**20:
        return Result("skipped", url=url, portal_modified=modified, note=f"{note}: over max_mb")
    if (res := ctx.unchanged(src, f"{modified}|{month}")) is not None:
        res.url = url
        return res
    if ctx.dry_run:
        return Result("dry_run", url=url, portal_modified=f"{modified}|{month}", note=note)
    snap = ctx.store.new_snapshot(src.id)
    tmp = Path(tempfile.mkdtemp(prefix="smart_agg_"))
    partials = tmp / "partials"
    partials.mkdir()
    try:
        for i, r in enumerate(chosen):
            gz = tmp / "part.csv.gz"
            await download(ctx, r["url"], gz, headers=headers)
            await asyncio.to_thread(_part_to_parquet, gz, partials / f"{i:04d}.parquet")
            gz.unlink(missing_ok=True)
            await asyncio.sleep(PART_GAP)
        rows, t1 = await asyncio.to_thread(_merge, partials, snap / "agg.parquet")
    except HttpError as e:
        return Result("needs_key" if e.status in (401, 403) else "error", url=url, snapshot=snap, note=str(e))
    except FetchError as e:
        return Result("error", url=url, snapshot=snap, note=str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _finish(snap, url, f"{modified}|{month}", rows, t1, note)


async def fetch(src: Source, ctx: Ctx) -> Result:
    kind = src.select.get("kind")
    if kind == "ods":
        return await _ods(src, ctx)
    if kind == "ckan_parts":
        return await _ckan_parts(src, ctx)
    raise FetchError(f"{src.id}: select.kind must be 'ods' or 'ckan_parts', got {kind!r}")
