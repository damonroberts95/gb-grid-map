import asyncio
from types import SimpleNamespace

import httpx
import respx

from gridmap.fetch import govuk
from gridmap.fetch.models import Source

PAGE = {"details": {"attachments": [
    {"content_type": "application/pdf", "url": "https://assets.publishing.service.gov.uk/media/x/a.pdf"},
    {"content_type": "text/csv", "url": "https://assets.publishing.service.gov.uk/media/abc/REPD_Q3.csv"},
]}}


def _src():
    return Source(id="repd", operator="DESNZ", portal="govuk", base_url="https://www.gov.uk", category="planning",
                  cadence="7d", licence="OGL", attribution="x", dataset="/government/publications/repd",
                  params={"content_type": "text/csv"}, format="file")


@respx.mock
def test_resolve_picks_matching_attachment():
    respx.get("https://www.gov.uk/api/content/government/publications/repd").mock(
        return_value=httpx.Response(200, json=PAGE))

    async def run():
        async with httpx.AsyncClient() as client:
            ctx = SimpleNamespace(client=client, limiter=SimpleNamespace(slot=lambda url: _Null()))
            return await govuk.resolve(_src(), ctx)

    assert asyncio.run(run()) == "https://assets.publishing.service.gov.uk/media/abc/REPD_Q3.csv"


class _Null:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False
