"""Shared HTTP plumbing: limits, retries with backoff, streaming download, row counting."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .models import Result, Source
from .store import Store

USER_AGENT = "gridmap-local/0.1"  # honest UA; never spoof a browser
BACKOFF = 1.0  # seconds, doubled per attempt (tests set 0)
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_BYTES = 200 * 1024 * 1024


class FetchError(Exception):
    """Any fetch failure that should be reported as status 'error'."""


class HttpError(FetchError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


class TooLarge(FetchError):
    pass


class Limiter:
    """Global concurrency cap plus a per-host cap."""

    def __init__(self, total: int = 4, per_host: int = 2):
        self._total = asyncio.Semaphore(total)
        self._per_host = per_host
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        async with self._hosts[httpx.URL(url).host], self._total:
            yield


@dataclass
class Ctx:
    client: httpx.AsyncClient
    store: Store
    limiter: Limiter = field(default_factory=Limiter)
    env: Mapping[str, str] = field(default_factory=dict)
    dry_run: bool = False
    force: bool = False

    def key(self, src: Source) -> str | None:
        return (self.env.get(src.auth_env) or None) if src.auth_env else None

    def unchanged(self, src: Source, modified: str | None) -> Result | None:
        """Change detection for cadence >= 1d: portal `modified` equals the last successful run."""
        if self.force or not modified or src.cadence_seconds < 86400:
            return None
        last = self.store.last_ok(src.id)
        if last and last["portal_modified"] == modified:
            return Result("unchanged", rows=last["rows"], portal_modified=modified, as_of=last["as_of"],
                          note=f"portal modified unchanged since {last['fetched_at']}")
        return None


async def _backoff(attempt: int, resp: httpx.Response | None) -> None:
    delay = BACKOFF * 2**attempt
    retry_after = resp.headers.get("retry-after", "") if resp is not None else ""
    if retry_after.isdigit():
        delay = max(delay, min(int(retry_after), 30)) if BACKOFF else 0
    await asyncio.sleep(delay)


async def get(ctx: Ctx, url: str, *, headers: dict[str, str] | None = None,
              params: dict[str, Any] | None = None, retries: int = 3) -> httpx.Response:
    """GET with retries on transport errors / 429 / 5xx. Returns the final response (any status)."""
    for attempt in range(retries + 1):
        resp = None
        try:
            async with ctx.limiter.slot(url):
                resp = await ctx.client.get(url, headers=headers, params=params)
        except httpx.TransportError as e:
            if attempt == retries:
                raise FetchError(f"{type(e).__name__}: {e}") from e
        else:
            if resp.status_code not in RETRY_STATUS or attempt == retries:
                return resp
        await _backoff(attempt, resp)
    raise AssertionError("unreachable")


async def download(ctx: Ctx, url: str, dest: Path, *, headers: dict[str, str] | None = None,
                   params: dict[str, Any] | None = None, retries: int = 3,
                   max_bytes: int = MAX_BYTES) -> tuple[int, str]:
    """Stream url to dest. Returns (bytes, sha256). Raises HttpError / TooLarge / FetchError."""
    for attempt in range(retries + 1):
        resp = None
        try:
            async with ctx.limiter.slot(url), ctx.client.stream("GET", url, headers=headers, params=params) as r:
                resp = r
                if r.status_code in RETRY_STATUS and attempt < retries:
                    pass  # fall through to backoff
                elif r.status_code >= 400:
                    raise HttpError(r.status_code, url)
                else:
                    digest, size = hashlib.sha256(), 0
                    with dest.open("wb") as f:
                        async for chunk in r.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise TooLarge(f"{url} exceeds {max_bytes // 2**20} MB")
                            f.write(chunk)
                            digest.update(chunk)
                    return size, digest.hexdigest()
        except httpx.TransportError as e:
            dest.unlink(missing_ok=True)
            if attempt == retries:
                raise FetchError(f"{type(e).__name__}: {e}") from e
        except FetchError:
            dest.unlink(missing_ok=True)
            raise
        await _backoff(attempt, resp)
    raise AssertionError("unreachable")


def combine_hashes(hashes: list[str]) -> str | None:
    if not hashes:
        return None
    return hashes[0] if len(hashes) == 1 else hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()


def safe_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"
    out, i = base, 1
    while out in taken:
        i += 1
        stem, dot, ext = base.rpartition(".")
        out = f"{stem}_{i}.{ext}" if dot else f"{base}_{i}"
    taken.add(out)
    return out


def dig(obj: Any, path: str | None) -> Any:
    for part in (path or "").split("."):
        if not part:
            continue
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def count_rows(path: Path, *, rows_path: str | None = None) -> int | None:
    """Rows in a downloaded file by extension; None if not countable (zip, pdf, xlsx...)."""
    ext = path.suffix.lower()
    if ext in {".csv", ".txt"}:
        csv.field_size_limit(sys.maxsize)
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            head = f.readline()
            f.seek(0)
            delim = max(",;\t|", key=head.count)
            return max(0, sum(1 for row in csv.reader(f, delimiter=delim) if any(row)) - 1)
    if ext == ".jsonl":
        with path.open("rb") as f:
            return sum(1 for line in f if line.strip())
    if ext in {".json", ".geojson"}:
        try:
            doc = json.loads(path.read_bytes())
        except ValueError:
            return None
        target = dig(doc, rows_path) if rows_path else doc
        if isinstance(target, dict) and isinstance(target.get("features"), list):
            target = target["features"]
        if isinstance(target, dict) and isinstance(target.get("data"), list) and not rows_path:
            target = target["data"]
        return len(target) if isinstance(target, list) else None
    if ext == ".parquet":
        import duckdb

        return duckdb.sql("select count(*) from read_parquet(?)", params=[str(path)]).fetchone()[0]
    return None


def json_value(path: Path, dotted: str | None) -> str | None:
    """A scalar (e.g. a data date) from a JSON file, or None."""
    if not dotted:
        return None
    try:
        val = dig(json.loads(path.read_bytes()), dotted)
    except ValueError:
        return None
    return str(val) if isinstance(val, (str, int, float)) else None
