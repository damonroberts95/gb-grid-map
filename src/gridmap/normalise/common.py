"""Shared helpers for adapters: snapshot lookup, parsing, coordinates, output."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import polars as pl
from pyproj import Transformer

from .schema import conform

REPO_ROOT = Path(__file__).resolve().parents[3]
from ..settings import DATA_DIR

# GB bounds (incl. Shetland, Scilly) for validation.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 49.8, 60.95, -8.7, 1.9
E_MIN, E_MAX, N_MIN, N_MAX = 0.0, 700_000.0, 0.0, 1_250_000.0

NULL_TOKENS = {"", "-", "--", "n/a", "na", "tbc", "tbd", "none", "null", "nan", "#n/a", "unknown", "not available",
              "data not available", "data not applicable", "not applicable", "redacted", "--redacted--"}

_bng_to_wgs = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


# ---------- snapshots ----------

def latest_snapshot(source_id: str) -> Path | None:
    """Newest snapshot dir for a source whose meta.json status is ok, else None."""
    base = DATA_DIR / "raw" / source_id
    if not base.exists():
        return None
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        meta = d / "meta.json"
        if meta.exists() and json.loads(meta.read_text()).get("status") == "ok":
            return d
    return None


def snapshot_meta(snap: Path) -> dict[str, Any]:
    return json.loads((snap / "meta.json").read_text())


def snapshot_files(snap: Path) -> list[Path]:
    return sorted(p for p in snap.iterdir() if p.name != "meta.json")


def as_of(meta: dict[str, Any]) -> str | None:
    """Operator as-of date if known, else portal modified, else fetch time (date part)."""
    v = meta.get("as_of") or meta.get("portal_modified") or meta.get("fetched_at")
    return v[:10] if v else None


# ---------- parsing ----------

_num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_num(v: Any) -> float | None:
    """'1,234.5 MW' -> 1234.5; null tokens -> None; keeps negatives and real zeros."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    s = str(v).strip()
    if s.lower() in NULL_TOKENS:
        return None
    m = _num_re.search(s.replace(",", ""))
    return float(m.group()) if m else None


def num_expr(col: str) -> pl.Expr:
    """Polars expression version of parse_num for string columns."""
    c = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return (
        pl.when(c.str.to_lowercase().is_in(list(NULL_TOKENS)) | c.is_null())
        .then(None)
        .otherwise(c.str.replace_all(",", "").str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", 1))
        .cast(pl.Float64, strict=False)
        .alias(col)
    )


def rag(v: Any) -> str | None:
    """Normalise operator RAG strings/colours to R/A/G."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s.startswith(("r", "#ff0000")) or "red" in s:
        return "R"
    if s.startswith(("a", "y", "o")) or "amber" in s or "yellow" in s or "orange" in s:
        return "A"
    if s.startswith("g") or "green" in s:
        return "G"
    return None


def kind_from(text: Any) -> str:
    """Map operator substation type labels to canonical kinds."""
    s = str(text or "").strip().lower()
    if not s:
        return "OTHER"
    if "gsp" in s or "grid supply" in s:
        return "GSP"
    if "bsp" in s or "bulk supply" in s or s.startswith("grid"):
        return "BSP"
    if "primary" in s or s in ("pry", "ps"):
        return "PRIMARY"
    if "secondary" in s or "distribution" in s or "ground mounted" in s or "pole" in s:
        return "SECONDARY"
    if "transmission" in s or "super grid" in s or "supergrid" in s:
        return "TRANSMISSION"
    return "OTHER"


# ---------- coordinates ----------

def in_gb(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def fix_latlon(lat: Any, lon: Any) -> tuple[float | None, float | None, str]:
    """Validate a WGS84 pair; detect swapped lat/lon. Returns (lat, lon, coord_quality)."""
    la, lo = parse_num(lat), parse_num(lon)
    if la is None or lo is None:
        return None, None, "missing"
    if la == 0 and lo == 0:
        return None, None, "rejected"
    if in_gb(la, lo):
        return la, lo, "native_wgs84"
    if in_gb(lo, la):
        return lo, la, "swapped_fixed"
    return None, None, "rejected"


def from_bng(e: Any, n: Any) -> tuple[float | None, float | None, str]:
    """OSGB36 easting/northing -> WGS84. Rejects out-of-range (e.g. x10 errors)."""
    ee, nn = parse_num(e), parse_num(n)
    if ee is None or nn is None:
        return None, None, "missing"
    if not (E_MIN < ee <= E_MAX and N_MIN < nn <= N_MAX):
        return None, None, "rejected"
    lon, lat = _bng_to_wgs.transform(ee, nn)
    return (lat, lon, "from_bng") if in_gb(lat, lon) else (None, None, "rejected")


def best_coords(lat: Any = None, lon: Any = None, e: Any = None, n: Any = None) -> tuple[float | None, float | None, str]:
    """Prefer valid WGS84, fall back to BNG."""
    la, lo, q = fix_latlon(lat, lon)
    if la is not None:
        return la, lo, q
    la2, lo2, q2 = from_bng(e, n)
    if la2 is not None:
        return la2, lo2, q2
    return None, None, q if q != "missing" else q2


def add_coords(df: pl.DataFrame, lat: str | None = None, lon: str | None = None,
               e: str | None = None, n: str | None = None) -> pl.DataFrame:
    """Append lat, lon, coord_quality columns computed row-wise with best_coords."""
    cols = [c for c in (lat, lon, e, n)]
    rows = df.select([pl.col(c).cast(pl.Utf8) if c else pl.lit(None, pl.Utf8).alias(f"_none{i}")
                      for i, c in enumerate(cols)]).rows()
    res = [best_coords(*r) for r in rows]
    return df.with_columns(
        pl.Series("lat", [r[0] for r in res], pl.Float64),
        pl.Series("lon", [r[1] for r in res], pl.Float64),
        pl.Series("coord_quality", [r[2] for r in res], pl.Utf8),
    )


def ids_json(**ids: Any) -> str:
    return json.dumps({k: str(v).strip() for k, v in ids.items() if v not in (None, "") and str(v).strip()},
                      sort_keys=True)


# ---------- output ----------

def write_table(operator: str, table: str, df: pl.DataFrame) -> Path:
    """Conform to the canonical schema and write data/norm/<operator>/<table>.parquet."""
    out = conform(df, table)
    path = DATA_DIR / "norm" / operator / f"{table}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    out.write_parquet(tmp)
    tmp.replace(path)
    return path
