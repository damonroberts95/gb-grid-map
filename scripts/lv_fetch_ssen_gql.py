"""Fetch SSEN smart-meter LV feeder data from the SSEN portal's GraphQL (Hasura over BigQuery) endpoint.

    uv run python -I scripts/lv_fetch_ssen_gql.py [--day 2024-09-04] [--max-pages 600]

Writes data/raw/ssen_sm_gql/<ts>/{lookup,usage}_NNNN.json.gz (raw GraphQL page responses, kept verbatim) and a
meta.json in the same shape as gridmap.fetch snapshots. Only the `ssen_smart_meter_dev_*` tables are used: the
`ssen_smart_meter_prod_*` tables answer BigQuery 403 (broken permissions) as of 2026-11. The dev feed ends 2024-09-05.
Polite: <=10k rows per page, stable order_by, <=2 requests/s, retries with back-off, honest User-Agent, no key.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
URL = "https://gql.internal.datopian.com/v1/graphql"
UA = {"User-Agent": "gridmap-local/0.1"}
LOOKUP = "ssen_smart_meter_dev_lv_feeder_lookup_optimized"
USAGE = "ssen_smart_meter_dev_lv_feeder_usage_optimized"
PAGE = 10000
MIN_GAP = 0.5  # seconds between requests


class Client:
    def __init__(self) -> None:
        self.c = httpx.Client(headers=UA, timeout=120)
        self.last = 0.0
        self.n = 0

    def q(self, query: str) -> dict:
        for attempt in range(5):
            time.sleep(max(0.0, MIN_GAP - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                r = self.c.post(URL, json={"query": query})
                self.n += 1
                body = r.json()
                if r.status_code == 200 and "errors" not in body:
                    return body
                err = str(body.get("errors", r.status_code))[:200]
            except (httpx.HTTPError, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
            if attempt == 4:
                raise RuntimeError(err)
            time.sleep(2 * 2 ** attempt)
        raise AssertionError


def save(d: Path, name: str, body: dict) -> None:
    (d / name).write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), 6))


def pages(cl: Client, d: Path, prefix: str, table: str, fields: str, where: str, order: str, total: int,
          budget: list[int]) -> int:
    got = 0
    for i, off in enumerate(range(0, total, PAGE)):
        if budget[0] <= 0:
            print("page budget exhausted at", prefix, got, "rows")
            break
        budget[0] -= 1
        body = cl.q(f"{{ {table}(limit:{PAGE}, offset:{off}, order_by:{order}{where}) {{ {fields} }} }}")
        rows = body["data"][table]
        save(d, f"{prefix}_{i:04d}.json.gz", body)
        got += len(rows)
        if len(rows) < PAGE:
            break
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2024-09-04")
    ap.add_argument("--max-pages", type=int, default=600)
    a = ap.parse_args()
    cl = Client()
    d = RAW / "ssen_sm_gql" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d.mkdir(parents=True)
    budget = [a.max_pages]
    n_lk = int(cl.q(f"{{ {LOOKUP}_aggregate {{ aggregate {{ count }} }} }}")["data"][f"{LOOKUP}_aggregate"]["aggregate"]["count"])
    lk = pages(cl, d, "lookup", LOOKUP, "dataset_id postcode total_mpan_count primary_substation_name secondary_substation_name", "",
               "[{dataset_id:asc, postcode:asc}]", n_lk, budget)
    print("lookup rows", lk, "of", n_lk, flush=True)
    uf = "dataset_id total_consumption_active_import aggregated_device_count_active data_collection_log_timestamp"
    us = 0
    for slot in range(48):
        ts = f"{a.day}T{slot // 2:02d}:{slot % 2 * 30:02d}:00Z"
        w = f', where: {{data_collection_log_timestamp: {{_eq: "{ts}"}}}}'
        n = int(cl.q(f"{{ {USAGE}_aggregate(where: {{data_collection_log_timestamp: {{_eq: \"{ts}\"}}}}) "
                     "{ aggregate { count } } }")["data"][f"{USAGE}_aggregate"]["aggregate"]["count"])
        if n:
            us += pages(cl, d, f"usage_{slot:02d}", USAGE, uf, w, "[{dataset_id:asc}]", n, budget)
        print(ts, n, "total", us, flush=True)
    files = sorted(p.name for p in d.iterdir())
    sha = hashlib.sha256(b"".join((d / f).read_bytes() for f in files)).hexdigest()
    meta = {"source_id": "ssen_sm_gql", "url": URL, "status": "ok", "rows": lk + us, "expected_rows": None,
            "bytes": sum((d / f).stat().st_size for f in files), "sha256": sha, "portal_modified": None,
            "as_of": a.day, "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files,
            "note": f"lookup {lk} rows + half-hourly feeder usage for {a.day} ({us} rows), {cl.n} requests",
            "licence": "CC BY 4.0",
            "attribution": "Contains data from the SSEN Distribution data portal, licensed under CC BY 4.0."}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    print("done", d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
