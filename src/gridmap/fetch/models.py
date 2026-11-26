"""Registry model (sources.yaml) and the result of one fetch."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "sources.yaml"

OPERATORS = {"UKPN", "NPG", "ENW", "SPEN", "NGED", "SSEN", "NIE", "SSEN-T", "NESO", "ELEXON", "CARBON",
             "DESNZ", "OSM", "OCM", "ONS", "OFGEM", "OTHER"}
PORTALS = {"opendatasoft", "ckan", "http", "json_attachment", "govuk", "ssen_gql", "smart_agg", "ocm"}
CATEGORIES = {
    "headroom", "sites", "ltds_fault", "ltds_other", "ecr", "queue", "tec", "lv_sites",
    "lv_usage", "outage", "live_national", "dfes", "lct", "boundary", "flex",
    "planning", "lines", "chargers", "constraint", "context",
}
STATUSES = ("ok", "unchanged", "needs_key", "empty_or_gated", "error", "skipped", "dry_run")


@dataclass(frozen=True)
class Source:
    id: str
    operator: str
    portal: str  # opendatasoft | ckan | http | json_attachment
    base_url: str
    category: str
    cadence: str  # e.g. 5m, 30m, 1d, 7d
    licence: str
    attribution: str
    dataset: str | None = None  # ODS dataset id / CKAN package id
    path: str | None = None  # http: path appended to base_url
    params: dict[str, str] = field(default_factory=dict)  # http query params
    auth_env: str | None = None  # env var holding the API key (None = no key concept)
    gated: bool = False  # known to need a key: skipped with needs_key when it is missing
    large: bool = False  # skipped unless --include-large
    format: str = "csv"  # ODS export format: csv | geojson | parquet; http: json | file
    max_mb: int | None = None  # per-source download cap override (default common.MAX_BYTES, 200 MB)
    timeout_s: int | None = None  # wall-clock cap override (default 15 min, 60 min when max_mb is set)
    strict_rows: bool = True  # False: export rows may differ from catalog records_count (e.g. partly public datasets)
    keep: int | None = None  # raw snapshots to keep (default: 3 for cadence <= 1h, else 6)
    mode: str = "file"  # ckan: file | datastore
    select: dict[str, Any] = field(default_factory=dict)  # resource / attachment selector
    rows_path: str | None = None  # dotted path to the row list inside a JSON document
    as_of_path: str | None = None  # dotted path to a data date inside a JSON document
    core: bool = False  # fetched in the first-run pass (see refresh.is_core) even if its category is not core
    note: str | None = None

    @property
    def cadence_seconds(self) -> int:
        m = re.fullmatch(r"(\d+)([smhd])", self.cadence)
        if not m:
            raise ValueError(f"{self.id}: bad cadence {self.cadence!r}")
        return int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


@dataclass
class Result:
    status: str
    rows: int | None = None
    expected_rows: int | None = None
    bytes: int = 0
    sha256: str | None = None
    portal_modified: str | None = None
    as_of: str | None = None
    url: str | None = None
    files: list[str] = field(default_factory=list)
    note: str | None = None
    snapshot: Path | None = None


def registry_files() -> tuple[Path, list[Path]]:
    """(main registry, sources.d files), all next to this package (an updated payload ships its own copies)."""
    return REGISTRY_PATH, sorted((REGISTRY_PATH.parent / "sources.d").glob("*.yaml"))


def load_sources(path: Path | None = None) -> list[Source]:
    """Load and validate the registry (sources.yaml + sources.d/*.yaml). Raises ValueError on a bad entry.
    An explicit `path` loads just that file."""
    if path is not None:
        entries = list(yaml.safe_load(path.read_text())["sources"])
    else:  # extras ship their own registries: src/gridmap/sources.d/*.yaml (one file per extra)
        main, extra_files = registry_files()
        entries = list(yaml.safe_load(main.read_text())["sources"])
        for f in extra_files:
            entries += (yaml.safe_load(f.read_text()) or {}).get("sources", [])
    known = {f.name for f in fields(Source)}
    out: list[Source] = []
    seen: set[str] = set()
    for entry in entries:
        extra = set(entry) - known
        if extra:
            raise ValueError(f"{entry.get('id')}: unknown keys {sorted(extra)}")
        src = Source(**entry)
        if src.id in seen:
            raise ValueError(f"duplicate source id {src.id}")
        seen.add(src.id)
        if src.operator not in OPERATORS or src.portal not in PORTALS or src.category not in CATEGORIES:
            raise ValueError(f"{src.id}: bad operator/portal/category")
        if src.gated and not src.auth_env:
            raise ValueError(f"{src.id}: gated sources need auth_env")
        if src.portal in {"opendatasoft", "ckan", "json_attachment", "govuk", "smart_agg"} and not src.dataset:
            raise ValueError(f"{src.id}: dataset required")
        _ = src.cadence_seconds  # validates
        out.append(src)
    return out
