"""CKAN: resolve resource URLs via package_show every run (they change on republish)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse

from .common import (
    MAX_BYTES,
    Ctx,
    FetchError,
    HttpError,
    combine_hashes,
    count_rows,
    download,
    get,
    safe_name,
)
from .models import Result, Source

PAGE = 32000  # CKAN datastore_search hard limit
LATEST_ROWS = 600  # mode "latest": newest rows per resource (one request); the newest timestamp is complete
PHASES = ("L1", "L2", "L3")


def latest_reading(records: list[dict]) -> dict | None:
    """Newest complete timestamp of long-format monitor rows (Timestamp, Attribute Type busbar|feeder|transformer, Feeder ID,
    Description like 'L1 Current (RMS)' / 'L2 Active Power' / 'L1 Voltage (RMS)' / 'Temperature', Value) as
    {n, t, bus: {i: [L1..L3 A], kw, kvar, v: [L1..L3 V]}, temp, fd: {feeder: {i: [A x3], kw}}}. None if no rows."""
    rows = [r for r in records if r.get("Timestamp") and r.get("Value") is not None]
    if not rows:
        return None
    counts = Counter(r["Timestamp"] for r in rows)  # the tail of a file can hold a half-written timestamp
    t = max(ts for ts, n in counts.items() if n >= 0.9 * max(counts.values()))
    out: dict = {"n": rows[0].get("Substation Name"), "t": t, "bus": {}, "fd": {}}
    for r in rows:
        if r["Timestamp"] != t:
            continue
        kind, d, v = r.get("Attribute Type"), str(r.get("Description") or ""), float(r["Value"])
        if kind == "transformer" and d == "Temperature":
            out["temp"] = v
            continue
        box = out["bus"] if kind == "busbar" else out["fd"].setdefault(str(r.get("Feeder ID")), {}) if kind == "feeder" else None
        if box is None:
            continue
        ph = d[:2]
        if ph in PHASES and d.endswith("Current (RMS)"):
            box.setdefault("i", [None] * 3)[PHASES.index(ph)] = v
        elif ph in PHASES and d.endswith("Voltage (RMS)"):
            box.setdefault("v", [None] * 3)[PHASES.index(ph)] = v
        elif ph in PHASES and d.endswith("Active Power"):
            box["kw"] = round(box.get("kw", 0) + v, 2)
        elif ph in PHASES and d.endswith("Reactive Power"):
            box["kvar"] = round(box.get("kvar", 0) + v, 2)
    return out


def select_resources(resources: list[dict], select: dict) -> list[dict]:
    """Filter by `format` (case-insens), `name_regex` (resource name or file name); `latest: true` keeps one."""
    out = resources
    if fmt := select.get("format"):
        out = [r for r in out if (r.get("format") or "").lower() == fmt.lower()]
    if rx := select.get("name_regex"):
        pat = re.compile(rx, re.IGNORECASE)
        out = [r for r in out if pat.search(r.get("name") or "") or pat.search(unquote(str(r.get("url") or "")))]
    if select.get("latest") and out:
        out = [max(out, key=lambda r: r.get("last_modified") or r.get("created") or "")]
    return out


def _filename(res: dict) -> str:
    name = unquote(Path(urlparse(res["url"]).path).name)
    return name or f"{res.get('id', 'resource')}.{(res.get('format') or 'bin').lower()}"


async def fetch(src: Source, ctx: Ctx) -> Result:
    key = ctx.key(src)
    headers = {"Authorization": key} if key else {}
    api = f"{src.base_url.rstrip('/')}/api/3/action"
    url = f"{api}/package_show"

    resp = await get(ctx, url, params={"id": src.dataset}, headers=headers)
    if resp.status_code != 200:
        return Result("error", url=url, note=f"package_show HTTP {resp.status_code}")
    body = resp.json()
    if not body.get("success"):
        return Result("error", url=url, note=f"package_show failed: {body.get('error')}")
    pkg = body["result"]
    modified = pkg.get("metadata_modified")
    chosen = select_resources(pkg.get("resources") or [], src.select)
    if not chosen:
        return Result("error", url=url, portal_modified=modified, note="no resources match selector")
    usable = [r for r in chosen if r.get("url") and r["url"] != "redacted"]
    redacted = len(chosen) - len(usable)
    if not usable:
        return Result("needs_key", url=url, portal_modified=modified,
                      note=f"{redacted} resource url(s) redacted (registered users only); set {src.auth_env}")
    if (res := ctx.unchanged(src, modified)) is not None:
        res.url = url
        return res
    declared = 0 if src.mode == "latest" else sum(int(r.get("size") or 0) for r in usable)  # latest: CSVs not downloaded
    note = f"{len(usable)} resources, {declared / 2**20:.1f} MB declared" + (f", {redacted} redacted" if redacted else "")
    if declared > MAX_BYTES:
        return Result("skipped", url=url, portal_modified=modified, note=f"{note}: over size cap")
    if ctx.dry_run:
        return Result("dry_run", url=url, portal_modified=modified, note=note)

    snap = ctx.store.new_snapshot(src.id)
    try:
        if src.mode == "latest":
            files, sizes, hashes, counts, expected = await _latest(ctx, api, usable, snap, headers)
        elif src.mode == "datastore":
            files, sizes, hashes, counts, expected = await _datastore(src, ctx, api, usable, snap, headers)
        else:
            files, sizes, hashes, counts, expected = await _files(ctx, usable, snap, headers, src)
    except HttpError as e:
        status = "needs_key" if e.status in (401, 403) else "error"
        return Result(status, url=url, portal_modified=modified, snapshot=snap, note=str(e))
    except FetchError as e:
        return Result("error", url=url, portal_modified=modified, snapshot=snap, note=str(e))
    known = [c for c in counts if c is not None]
    rows = sum(known) if known else None
    res = Result("ok", rows=rows, expected_rows=expected, bytes=sum(sizes), sha256=combine_hashes(hashes),
                 portal_modified=modified, url=url, files=files, snapshot=snap,
                 note=note if redacted else None)
    if (len(known) == len(counts) and rows == 0) or sum(sizes) == 0:
        res.status, res.note = "empty_or_gated", "downloaded resources hold 0 rows"
    elif expected is not None and rows != expected:
        res.status, res.note = "empty_or_gated", f"datastore rows {rows} != reported total {expected}"
    return res


async def _files(ctx: Ctx, resources: list[dict], snap: Path, headers: dict, src: Source):
    taken: set[str] = set()
    jobs = []
    for r in resources:
        dest = snap / safe_name(_filename(r), taken)
        jobs.append((dest, download(ctx, r["url"], dest, headers=headers)))
    done = await asyncio.gather(*(j for _, j in jobs))
    files = [d.name for d, _ in jobs]
    counts = [count_rows(d, rows_path=src.rows_path) for d, _ in jobs]
    return files, [s for s, _ in done], [h for _, h in done], counts, None


async def _datastore(src: Source, ctx: Ctx, api: str, resources: list[dict], snap: Path, headers: dict):
    """Page datastore_search into one JSONL per resource; rows are checked against the reported total."""
    files, sizes, hashes, counts = [], [], [], []
    expected = 0
    taken: set[str] = set()
    for r in resources:
        if not r.get("datastore_active"):
            raise FetchError(f"resource {r.get('id')} has no datastore")
        dest = snap / safe_name(f"{r.get('name') or r['id']}.jsonl", taken)
        n, offset, total = 0, 0, None
        with dest.open("w") as f:
            while total is None or offset < total:
                resp = await get(ctx, f"{api}/datastore_search", headers=headers,
                                 params={"resource_id": r["id"], "limit": PAGE, "offset": offset})
                if resp.status_code != 200:
                    raise HttpError(resp.status_code, f"{api}/datastore_search")
                result = resp.json()["result"]
                total = result["total"]
                for rec in result["records"]:
                    f.write(json.dumps(rec) + "\n")
                n += len(result["records"])
                offset += PAGE
                if not result["records"]:
                    break
        data = dest.read_bytes()
        files.append(dest.name)
        sizes.append(len(data))
        hashes.append(hashlib.sha256(data).hexdigest())
        counts.append(n)
        expected += total or 0
    return files, sizes, hashes, counts, expected


async def _latest(ctx: Ctx, api: str, resources: list[dict], snap: Path, headers: dict):
    """Newest readings per resource through datastore_search (the CSVs are ~1.6 GB in total and only the latest
    values are shown), one `latest.json` {resource name: latest_reading}. Resources that fail are left out."""
    out: dict[str, dict] = {}
    denied = 0

    async def one(r: dict) -> None:
        nonlocal denied
        resp = await get(ctx, f"{api}/datastore_search", headers=headers,
                         params={"resource_id": r["id"], "limit": LATEST_ROWS, "sort": "Timestamp desc"})
        if resp.status_code in (401, 403):
            denied += 1
        elif resp.status_code == 200 and (got := latest_reading(resp.json()["result"]["records"])):
            out[str(r["name"]).strip()] = got

    await asyncio.gather(*(one(r) for r in resources))
    if not out and denied:
        raise HttpError(403, f"{api}/datastore_search")
    data = json.dumps(out, separators=(",", ":"), sort_keys=True).encode()
    (snap / "latest.json").write_bytes(data)
    return ["latest.json"], [len(data)], [hashlib.sha256(data).hexdigest()], [len(out)], None
