"""Generic GET of a JSON document or file (Elexon, Carbon Intensity, outage feeds)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .common import Ctx, FetchError, count_rows, download, json_value
from .models import Result, Source

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # locale-proof %b
_NOW_RE = re.compile(r"\{now(?:-(\d+)([mh]))?\}|\{today:([^}]+)\}")


def expand_params(params: dict[str, str]) -> dict[str, str]:
    """Expand "{now}" / "{now-15m}" / "{now-2h}" in param values to UTC ISO timestamps (live feeds
    that need an explicit window, e.g. Elexon FREQ). "{today:%d-%b-%Y}" is today's UK date in that strftime
    format (EirGrid dashboard)."""
    def sub(m: re.Match) -> str:
        if fmt := m.group(3):
            t = datetime.now(ZoneInfo("Europe/London"))
            return t.strftime(fmt.replace("%b", _MONTHS[t.month - 1]))
        n, unit = m.group(1), m.group(2)
        delta = timedelta(**{"minutes" if unit == "m" else "hours": int(n)}) if n else timedelta(0)
        return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {k: _NOW_RE.sub(sub, str(v)) for k, v in params.items()}


def build_url(src: Source) -> str:
    base = src.base_url.rstrip("/")
    return f"{base}/{src.path.lstrip('/')}" if src.path else base


RETRY_EMPTY_WAIT_S = 2.0


async def fetch(src: Source, ctx: Ctx) -> Result:
    url = build_url(src)
    if ctx.dry_run:
        return Result("dry_run", url=url, note="GET " + url)
    snap = ctx.store.new_snapshot(src.id)
    # name after the URL (e.g. great-britain-latest.osm.pbf): extras find files by suffix
    name = "data.json" if src.format == "json" else (src.path or src.base_url).rstrip("/").rsplit("/", 1)[-1] or "file"
    dest = snap / name
    headers = {"Accept": "application/json"} if src.format == "json" else {}
    if src.auth_header and (key := ctx.key(src)):
        headers[src.auth_header] = key
    for attempt in range(src.retry_empty + 1):
        try:
            size, sha = await download(ctx, url, dest, headers=headers, params=expand_params(src.params) or None,
                                      **({"max_bytes": src.max_mb * 1024 * 1024} if src.max_mb else {}))
        except FetchError as e:
            return Result("error", url=url, snapshot=snap, note=str(e))
        rows = count_rows(dest, rows_path=src.rows_path)
        if rows or attempt == src.retry_empty:
            break
        await asyncio.sleep(RETRY_EMPTY_WAIT_S)
    res = Result("ok", rows=rows, bytes=size, sha256=sha, url=url, files=[dest.name], snapshot=snap,
                 as_of=json_value(dest, src.as_of_path))
    if size == 0:
        res.status, res.note = "empty_or_gated", "empty response body"
    elif src.retry_empty and not rows:
        res.status, res.note = "empty_or_gated", f"no rows after {src.retry_empty + 1} tries"
    return res
