"""Run the registry: concurrency-limited, never crashes on a missing key or a failing source."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from collections.abc import Mapping
from typing import Any

import httpx

from . import ckan, govuk, http, ocm, opendatasoft, smart_agg, ssen_gql
from .common import USER_AGENT, Ctx, FetchError, Limiter
from .models import Result, Source, load_sources
from .store import Store, utcnow

FETCHERS = {
    "opendatasoft": opendatasoft.fetch,
    "json_attachment": opendatasoft.fetch,
    "ckan": ckan.fetch,
    "http": http.fetch,
    "govuk": govuk.fetch,
    "ssen_gql": ssen_gql.fetch,
    "smart_agg": smart_agg.fetch,
    "ocm": ocm.fetch,
}


async def _run_one(src: Source, ctx: Ctx, include_large: bool) -> tuple[Result, str]:
    started = utcnow()
    try:
        if src.gated and src.auth_env and not ctx.key(src):
            res = Result("needs_key", note=f"gated: set {src.auth_env}")
        elif src.large and not include_large:
            res = Result("skipped", note="large: true (use --include-large)")
        else:
            # wall-clock cap per source: timeout_s, else 15 min, or 60 min for declared big downloads (max_mb)
            res = await asyncio.wait_for(FETCHERS[src.portal](src, ctx), timeout=src.timeout_s or (3600 if src.max_mb else 900))
    except TimeoutError:
        res = Result("error", note="timed out (per-source limit); will retry later")
    except FetchError as e:
        res = Result("error", note=str(e))
    except Exception as e:  # noqa: BLE001 - one bad source must never stop the run
        res = Result("error", note=f"{type(e).__name__}: {e}")
    if not ctx.dry_run:
        ctx.store.record(src, res, started)
    return res, started


async def arun(source_ids: list[str] | None = None, *, force: bool = False, dry_run: bool = False,
               include_large: bool = False, store: Store | None = None, env: Mapping[str, str] | None = None,
               registry: list[Source] | None = None, concurrency: int = 4, per_host: int = 2) -> dict[str, Any]:
    sources = registry if registry is not None else load_sources()
    if source_ids:
        known = {s.id for s in sources}
        if unknown := sorted(set(source_ids) - known):
            raise KeyError(f"unknown source id(s): {', '.join(unknown)}")
        sources = [s for s in sources if s.id in set(source_ids)]
    store = store or Store()
    timeout = httpx.Timeout(connect=15, read=300, write=30, pool=None)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True) as client:
        ctx = Ctx(client=client, store=store, limiter=Limiter(concurrency, per_host),
                  env=os.environ if env is None else env, dry_run=dry_run, force=force)
        results = await asyncio.gather(*(_run_one(s, ctx, include_large) for s in sources))
    store.close()
    rows = [
        {"source": s.id, "operator": s.operator, "status": r.status, "rows": r.rows, "expected_rows": r.expected_rows,
         "as_of": r.as_of or r.portal_modified, "bytes": r.bytes, "note": r.note}
        for s, (r, _) in zip(sources, results, strict=True)
    ]
    return {"counts": dict(Counter(r["status"] for r in rows)), "runs": rows}


def run(source_ids: list[str] | None = None, force: bool = False, **kwargs: Any) -> dict[str, Any]:
    """Fetch the given sources (default: all). Returns {"counts": {status: n}, "runs": [...]}."""
    return asyncio.run(arun(source_ids, force=force, **kwargs))


def format_table(summary: dict[str, Any]) -> str:
    head = ("source", "status", "rows", "as_of", "note")
    body = [
        (r["source"], r["status"], "" if r["rows"] is None else f"{r['rows']:,}",
         (r["as_of"] or "")[:19], (r["note"] or "")[:70])
        for r in summary["runs"]
    ]
    widths = [max(len(str(x[i])) for x in [head, *body]) for i in range(5)]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in [head, *body]]
    lines.append("")
    lines.append("  ".join(f"{k}={v}" for k, v in sorted(summary["counts"].items())))
    return "\n".join(lines)
