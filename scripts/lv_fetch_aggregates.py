"""Fetch + aggregate LV smart-meter feeds that are too big to snapshot whole (used by gridmap.extra.lv_smart).

    uv run python -I scripts/lv_fetch_aggregates.py [--only spen_wirral,spen_ayrshire,nged_secondary]

Writes data/raw/<id>/<ts>/agg.parquet + meta.json (same layout as gridmap.fetch snapshots, status ok), one row per
secondary substation for the latest published month: n half-hours, sum/peak Wh per half-hour, devices, location.
Never prints API keys. Honest User-Agent. Raw half-hourly rows are streamed to a temp dir and discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "gridmap-local/0.1"}
SPEN = "https://spenergynetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
NGED = "https://connecteddata.nationalgrid.co.uk/api/3/action/"

SPEN_JOBS = {
    "spen_wirral": ("spen_smart_consumption_wirral_agg", "spm-aggregated-smart-consumption-wirral-feeder-level"),
    "spen_ayrshire": ("spen_smart_consumption_ayrshire_agg",
                      "spd-aggregated-smart-consumption-ayrshire-clyde-south-feeder-level"),
}


def snap_dir(source_id: str) -> Path:
    d = RAW / source_id / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    d.mkdir(parents=True)
    return d


def write_meta(d: Path, source_id: str, url: str, rows: int, note: str, licence: str, attribution: str,
               as_of: str | None) -> None:
    sha = hashlib.sha256((d / "agg.parquet").read_bytes()).hexdigest()
    meta = {"source_id": source_id, "url": url, "status": "ok", "rows": rows, "expected_rows": None,
            "bytes": (d / "agg.parquet").stat().st_size, "sha256": sha, "portal_modified": None, "as_of": as_of,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": ["agg.parquet"], "note": note,
            "licence": licence, "attribution": attribution}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))


def fetch_spen(job: str) -> None:
    source_id, dataset = SPEN_JOBS[job]
    key = os.environ["SPEN_API_KEY"]
    params = {
        "select": "secondary_substation_id, secondary_substation_name, substation_geo_location, "
                  "data_collection_log_timestamp as ts, sum(total_consumption_active_import) as wh, "
                  "sum(aggregated_device_count_active) as devs, count(*) as nfeed",
        "group_by": "secondary_substation_id, secondary_substation_name, substation_geo_location, "
                    "data_collection_log_timestamp",
    }
    url = f"{SPEN}{dataset}/exports/csv"
    tmp = Path(tempfile.mkdtemp(prefix="lv_spen_"))
    try:
        csv = tmp / "g.csv"
        with httpx.stream("GET", url, params=params, headers={**UA, "Authorization": f"Apikey {key}"},
                          timeout=900) as r:
            r.raise_for_status()
            with csv.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        d = snap_dir(source_id)
        con = duckdb.connect()
        con.execute(f"""COPY (
          SELECT secondary_substation_id AS sub_id, any_value(secondary_substation_name) AS name,
                 any_value(substation_geo_location) AS geo, count(*) AS n_hh,
                 sum(CAST(wh AS DOUBLE)) AS sum_wh, max(CAST(wh AS DOUBLE)) AS peak_wh,
                 arg_max(ts, CAST(wh AS DOUBLE)) AS peak_ts, max(CAST(devs AS INTEGER)) AS devs,
                 min(ts) AS t0, max(ts) AS t1
          FROM read_csv('{csv}', delim=';', header=true, all_varchar=true)
          GROUP BY 1) TO '{d / 'agg.parquet'}' (FORMAT parquet)""")
        n, t1 = con.execute(f"select count(*), max(t1) from '{d / 'agg.parquet'}'").fetchone()
        write_meta(d, source_id, url, n, "per-substation aggregate of one published month (sum over feeders)",
                   "CC BY 4.0", "Contains data from the SP Energy Networks open data portal, licensed under CC BY 4.0.",
                   str(t1))
        print(job, "rows", n, "to", d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch_nged() -> None:
    key = os.environ["NGED_API_KEY"]
    h = {**UA, "Authorization": key}
    pkg = httpx.get(NGED + "package_show", params={"id": "aggregated-smart-meter-data-secondary-substation"},
                    headers=h, timeout=120).json()["result"]
    gz = [r for r in pkg["resources"] if str(r.get("name", "")).endswith(".csv.gz") and r.get("url") != "redacted"]
    month = max(r["name"].split("Substation-")[1][:7] for r in gz)
    parts = sorted((r for r in gz if f"-{month}-part" in r["name"]), key=lambda r: r["name"])
    print("nged month", month, "parts", len(parts))
    tmp = Path(tempfile.mkdtemp(prefix="lv_nged_"))
    con = duckdb.connect()
    partials = tmp / "partials"
    partials.mkdir()
    try:
        for i, r in enumerate(parts):
            f = tmp / "p.csv.gz"
            for attempt in range(3):
                try:
                    with httpx.stream("GET", r["url"], headers=h, timeout=600, follow_redirects=True) as resp:
                        resp.raise_for_status()
                        with f.open("wb") as o:
                            for c in resp.iter_bytes(1 << 20):
                                o.write(c)
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                    time.sleep(3 * (attempt + 1))
            con.execute(f"""COPY (
              SELECT secondary_substation_id::VARCHAR AS sub_id, any_value(secondary_substation_name) AS name,
                     any_value(substation_geo_location) AS geo, count(*) AS n_hh,
                     sum(TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS sum_wh,
                     max(TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS peak_wh,
                     arg_max(data_collection_log_timestamp, TRY_CAST(Total_consumption_active_import AS DOUBLE)) AS peak_ts,
                     max(TRY_CAST(aggregated_device_count_Active AS INTEGER)) AS devs,
                     min(data_collection_log_timestamp) AS t0, max(data_collection_log_timestamp) AS t1
              FROM read_csv('{f}', header=true, all_varchar=true, ignore_errors=true)
              GROUP BY 1) TO '{partials / f"{i:04d}.parquet"}' (FORMAT parquet)""")
            f.unlink()
            if i % 10 == 0:
                print("part", i, "of", len(parts), flush=True)
            time.sleep(0.3)
        d = snap_dir("nged_smart_meter_secondary_agg")
        con.execute(f"""COPY (
          SELECT sub_id, any_value(name) AS name, any_value(geo) AS geo, sum(n_hh) AS n_hh, sum(sum_wh) AS sum_wh,
                 max(peak_wh) AS peak_wh, arg_max(peak_ts, peak_wh) AS peak_ts, max(devs) AS devs,
                 min(t0) AS t0, max(t1) AS t1
          FROM read_parquet('{partials}/*.parquet') GROUP BY 1) TO '{d / 'agg.parquet'}' (FORMAT parquet)""")
        n, t1 = con.execute(f"select count(*), max(t1) from '{d / 'agg.parquet'}'").fetchone()
        write_meta(d, "nged_smart_meter_secondary_agg", NGED + "package_show?id=aggregated-smart-meter-data-secondary-substation",
                   n, f"aggregate of {len(parts)} gz parts for {month}", "NGED (WPD) Open Data Licence",
                   "Contains data (c) National Grid Electricity Distribution, used under the NGED (WPD) Open Data Licence.",
                   str(t1))
        print("nged rows", n, "to", d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="spen_wirral,spen_ayrshire,nged_secondary")
    a = ap.parse_args()
    for job in a.only.split(","):
        if job in SPEN_JOBS:
            fetch_spen(job)
        elif job == "nged_secondary":
            fetch_nged()
        else:
            print("unknown job", job)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
