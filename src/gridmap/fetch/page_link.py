"""Web pages that link to a file whose name changes on every release (e.g. ESB Networks heatmap XLSX).

Source: portal "page_link", base_url = the HTML page, params.pattern = regex matched against each href
(first match wins, page order). Resolved on every fetch, then downloaded like an http file; an unchanged
link => "unchanged". The link's query string (e.g. a CMS version tag) is kept, so a re-upload counts as new.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import http
from .common import Ctx, get
from .models import Result, Source

_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def pick(page: str, base: str, pattern: str) -> str | None:
    """First href on the page matching `pattern` (case-insensitive), made absolute."""
    want = re.compile(pattern, re.IGNORECASE)
    for m in _HREF.finditer(page):
        href = html.unescape(m.group(1)).strip()
        if want.search(href):
            return urljoin(base, href)
    return None


async def resolve(src: Source, ctx: Ctx) -> str | None:
    resp = await get(ctx, src.base_url)
    if resp.status_code != 200:
        return None
    return pick(resp.text, src.base_url, (src.params or {}).get("pattern", r"\.xlsx\b"))


async def fetch(src: Source, ctx: Ctx) -> Result:
    url = await resolve(src, ctx)
    if url is None:
        return Result("error", url=src.base_url, note="no matching link on the page")
    if not ctx.dry_run and not ctx.force and (ctx.store.last_ok(src.id) or {}).get("url") == url:
        return Result("unchanged", url=url, note="linked file unchanged")
    u = urlsplit(url)
    res = await http.fetch(replace(src, base_url=f"{u.scheme}://{u.netloc}", path=u.path,
                                   params=dict(parse_qsl(u.query))), ctx)
    res.url = url  # full link incl. version query: the change key for the next run
    return res
