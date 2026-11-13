"""gov.uk publication pages whose attachment URLs change on every release (e.g. DESNZ REPD quarterly).

Source: portal "govuk", dataset = publication path (e.g. "/government/publications/<slug>"),
params.content_type = attachment MIME type to pick (default text/csv). Resolved via the gov.uk content API
on every fetch, then downloaded like an http file; unchanged attachment URL => "unchanged".
"""

from __future__ import annotations

from dataclasses import replace

from . import http
from .common import Ctx, get
from .models import Result, Source

CONTENT_API = "https://www.gov.uk/api/content"


async def resolve(src: Source, ctx: Ctx) -> str | None:
    want = (src.params or {}).get("content_type", "text/csv")
    resp = await get(ctx, CONTENT_API + "/" + (src.dataset or "").lstrip("/"))
    if resp.status_code != 200:
        return None
    for a in resp.json().get("details", {}).get("attachments", []):
        if a.get("content_type") == want and a.get("url"):
            return a["url"]
    return None


async def fetch(src: Source, ctx: Ctx) -> Result:
    url = await resolve(src, ctx)
    if url is None:
        return Result("error", url=CONTENT_API + (src.dataset or ""), note="no matching attachment on gov.uk page")
    if not ctx.dry_run and not ctx.force and (ctx.store.last_ok(src.id) or {}).get("url") == url:
        return Result("unchanged", url=url, note="gov.uk attachment unchanged")
    scheme, _, rest = url.partition("://")
    host, _, path = rest.partition("/")
    return await http.fetch(replace(src, base_url=f"{scheme}://{host}", path="/" + path, params={}), ctx)
