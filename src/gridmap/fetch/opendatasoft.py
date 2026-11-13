"""Opendatasoft Explore v2.1: full-dataset /exports (never paginated /records, which caps at 10k)."""

from __future__ import annotations

import re

from .common import (
    MAX_BYTES,
    Ctx,
    FetchError,
    combine_hashes,
    count_rows,
    download,
    get,
    json_value,
    safe_name,
)
from .models import Result, Source

ROW_TOLERANCE = 0.10  # export rows must be within 10% (min 5 rows) of catalog records_count


def _base(src: Source) -> str:
    return f"{src.base_url.rstrip('/')}/api/explore/v2.1/catalog/datasets/{src.dataset}"


async def fetch(src: Source, ctx: Ctx) -> Result:
    key = ctx.key(src)
    headers = {"Authorization": f"Apikey {key}"} if key else {}
    base = _base(src)

    resp = await get(ctx, base, headers=headers)
    if resp.status_code == 401:
        return Result("error", url=base, note=f"API key rejected (401); check {src.auth_env}")
    if resp.status_code != 200:
        return Result("error", url=base, note=f"catalog HTTP {resp.status_code}")
    meta = resp.json()
    default = (meta.get("metas") or {}).get("default") or {}
    modified = default.get("modified")
    expected = default.get("records_count")
    visible = meta.get("data_visible", True)

    if src.portal == "opendatasoft" and not visible and not key:
        return Result("needs_key", expected_rows=expected, portal_modified=modified, url=base,
                      note=f"data_visible=false; set {src.auth_env}")
    if (res := ctx.unchanged(src, modified)) is not None:
        res.url = base
        return res
    if ctx.dry_run:
        return Result("dry_run", rows=expected, expected_rows=expected, portal_modified=modified, url=base,
                      note=f"data_visible={visible}; would fetch {src.portal}")

    snap = ctx.store.new_snapshot(src.id)
    if src.portal == "json_attachment":
        return await _attachments(src, ctx, meta, snap, modified, base)

    url = f"{base}/exports/{src.format}"
    dest = snap / f"export.{src.format}"
    try:
        size, sha = await download(ctx, url, dest, headers=headers)
    except FetchError as e:
        return Result("error", url=url, portal_modified=modified, snapshot=snap, note=str(e))
    rows = count_rows(dest)
    res = Result("ok", rows=rows, expected_rows=expected, bytes=size, sha256=sha, portal_modified=modified,
                 url=url, files=[dest.name], snapshot=snap)
    if not rows:
        # The trap: HTTP 200 with a header-only CSV when the dataset is login-restricted.
        res.status = "empty_or_gated"
        res.note = f"export has 0 rows (data_visible={visible}, key={'yes' if key else 'no'})"
    elif expected and abs(rows - expected) > max(5, ROW_TOLERANCE * expected):
        if src.strict_rows:
            res.status = "empty_or_gated"
        res.note = f"export rows {rows} != catalog records_count {expected}"
    return res


async def _attachments(src: Source, ctx: Ctx, meta: dict, snap, modified: str | None, base: str) -> Result:
    """JSON files attached to a dataset (e.g. UKPN heatmap per licence area); ids change per release."""
    mime = src.select.get("mimetype", "application/json")
    pattern = re.compile(src.select.get("name_regex", "."), re.IGNORECASE)
    chosen = [a for a in meta.get("attachments") or []
              if a.get("mimetype") == mime and pattern.search(a.get("title", ""))]
    if not chosen:
        return Result("error", url=base, portal_modified=modified, snapshot=snap, note="no matching attachments")
    headers = {"Authorization": f"Apikey {ctx.key(src)}"} if ctx.key(src) else {}
    files: list[str] = []
    hashes: list[str] = []
    total = rows = 0
    as_of: list[str] = []
    taken: set[str] = set()
    for att in chosen:
        dest = snap / safe_name(att.get("title") or att["id"], taken)
        try:
            size, sha = await download(ctx, att["url"], dest, headers=headers, max_bytes=MAX_BYTES)
        except FetchError as e:
            return Result("error", url=att["url"], portal_modified=modified, snapshot=snap, note=str(e))
        total += size
        hashes.append(sha)
        files.append(dest.name)
        rows += count_rows(dest, rows_path=src.rows_path) or 0
        if (v := json_value(dest, src.as_of_path)):
            as_of.append(v)
    res = Result("ok", rows=rows, expected_rows=meta.get("metas", {}).get("default", {}).get("records_count"),
                 bytes=total, sha256=combine_hashes(hashes), portal_modified=modified, as_of=max(as_of, default=None),
                 url=base, files=files, snapshot=snap)
    if not rows:
        res.status, res.note = "empty_or_gated", "attachments contain 0 rows"
    elif res.expected_rows and abs(rows - res.expected_rows) > max(5, ROW_TOLERANCE * res.expected_rows):
        res.status, res.note = "empty_or_gated", f"attachment rows {rows} != records_count {res.expected_rows}"
    return res
