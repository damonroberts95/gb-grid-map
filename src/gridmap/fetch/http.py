"""Generic GET of a JSON document or file (Elexon, Carbon Intensity, outage feeds)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from .common import Ctx, FetchError, count_rows, download, json_value
from .models import Result, Source

_NOW_RE = re.compile(r"\{now(?:-(\d+)([mh]))?\}")


def expand_params(params: dict[str, str]) -> dict[str, str]:
    """Expand "{now}" / "{now-15m}" / "{now-2h}" in param values to UTC ISO timestamps (live feeds
    that need an explicit window, e.g. Elexon FREQ)."""
    def sub(m: re.Match) -> str:
        n, unit = m.group(1), m.group(2)
        delta = timedelta(**{"minutes" if unit == "m" else "hours": int(n)}) if n else timedelta(0)
        return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {k: _NOW_RE.sub(sub, str(v)) for k, v in params.items()}


def build_url(src: Source) -> str:
    base = src.base_url.rstrip("/")
    return f"{base}/{src.path.lstrip('/')}" if src.path else base


async def fetch(src: Source, ctx: Ctx) -> Result:
    url = build_url(src)
    if ctx.dry_run:
        return Result("dry_run", url=url, note="GET " + url)
    snap = ctx.store.new_snapshot(src.id)
    # name after the URL (e.g. great-britain-latest.osm.pbf): extras find files by suffix
    name = "data.json" if src.format == "json" else (src.path or src.base_url).rstrip("/").rsplit("/", 1)[-1] or "file"
    dest = snap / name
    headers = {"Accept": "application/json"} if src.format == "json" else {}
    try:
        size, sha = await download(ctx, url, dest, headers=headers, params=expand_params(src.params) or None,
                                  **({"max_bytes": src.max_mb * 1024 * 1024} if src.max_mb else {}))
    except FetchError as e:
        return Result("error", url=url, snapshot=snap, note=str(e))
    rows = count_rows(dest, rows_path=src.rows_path)
    res = Result("ok", rows=rows, bytes=size, sha256=sha, url=url, files=[dest.name], snapshot=snap,
                 as_of=json_value(dest, src.as_of_path))
    if size == 0:
        res.status, res.note = "empty_or_gated", "empty response body"
    return res
