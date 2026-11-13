"""Extra map layers: self-contained plug-ins for additional public datasets.

One module per extra in this package (e.g. `repd.py`). Each module defines:

    LAYERS: list[LayerMeta]      # what it publishes (see LayerMeta)
    SOURCES: list[str]           # source ids it reads (registered in src/gridmap/sources.d/<module>.yaml)
    def build(ctx: Ctx) -> dict[str, Path]:
        '''Write each layer's file into ctx.tmp and return {layer_id: path}. Pure: read raw snapshots
        (gridmap.normalise.common.latest_snapshot) and/or data/norm + data/derived parquet. Never open DuckDB.'''

The framework (build_extras) hashes each module's inputs (its source snapshots, its own code, and any
`inputs` paths it declares), rebuilds only when they change, content-addresses the output into data/public,
and adds it to manifest["extras"]. The web app renders every extra generically from its LayerMeta.

Contract for files:
- geojson: FeatureCollection, WGS84, numeric feature `id` optional. Keep properties short and few.
- pmtiles: built with tippecanoe; `source_layer` must match the -l name.
- Size budget: geojson ≤ 3 MB raw (else use pmtiles); pmtiles tiles ≤ 500 KB.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
import shutil
import tempfile
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..normalise.common import DATA_DIR, latest_snapshot

PKG = Path(__file__).resolve().parent


@dataclass
class LayerMeta:
    id: str                       # unique, short, [a-z0-9_]
    title: str                    # shown in the layer list
    group: str                    # UI section: "Network" | "Generation & planning" | "Demand & LV" | "Live" | "Areas"
    type: str                     # "geojson" | "pmtiles"
    geom: str                     # "point" | "line" | "polygon"
    description: str              # one or two plain-English sentences (hover help)
    source_layer: str | None = None   # pmtiles only
    minzoom: float = 0
    default_on: bool = False
    # Styling hints. color: fixed hex, or {"prop": p, "type": "categorical"|"steps", "stops": [[v, hex], ...],
    # "default": hex}. size: radius (points) / width (lines) as number or {"prop": p, "stops": [[v, px], ...]}.
    color: Any = "#555555"
    size: Any = 3
    opacity: float = 0.85
    # Popup: ordered [prop, label, unit|None]; first entry is the title.
    popup: list[list[Any]] = field(default_factory=list)
    legend: list[list[str]] = field(default_factory=list)  # [[hex, label], ...]
    attribution: str = ""
    live: bool = False            # rebuilt whenever its sources fetch (cadence < 1 h)
    label: str | None = None      # property to draw as a text label (polygons: from Point features in the same file)
    label_maxzoom: float = 24     # hide labels above this zoom (e.g. region names only at country scale)
    inputs: list[str] = field(default_factory=list)  # extra input paths relative to data/ (e.g. "derived/site.parquet")


@dataclass
class Ctx:
    data_dir: Path
    tmp: Path


def modules() -> list[Any]:
    out = []
    for m in pkgutil.iter_modules([str(PKG)]):
        if not m.name.startswith("_"):
            out.append(importlib.import_module(f"{__name__}.{m.name}"))
    return out


def _input_hash(mod: Any, data_dir: Path) -> str:
    h = hashlib.sha256(Path(mod.__file__).read_bytes())
    for sid in sorted(getattr(mod, "SOURCES", [])):
        snap = latest_snapshot(sid)
        h.update(f"{sid}:{snap.name if snap else '-'}".encode())
    for lm in mod.LAYERS:
        for rel in lm.inputs:
            p = data_dir / rel
            h.update(f"{rel}:{p.stat().st_mtime_ns if p.exists() else '-'}".encode())
    return h.hexdigest()[:10]


def build_extras(data_dir: Path | None, public: Path, old: dict[str, Any], only_live: bool = False
                 ) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Returns (extras manifest entries, changed layer ids, errors {module: message})."""
    data_dir = data_dir or DATA_DIR
    extras: dict[str, Any] = dict(old)
    changed: list[str] = []
    errors: dict[str, str] = {}
    present: set[str] = set()
    for mod in modules():
        metas: list[LayerMeta] = mod.LAYERS
        present |= {m.id for m in metas}
        if only_live and not any(m.live for m in metas):
            continue
        h = _input_hash(mod, data_dir)
        if all(extras.get(m.id, {}).get("hash") == h and (public / Path(extras[m.id]["url"]).name).exists()
               for m in metas):
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"extra_{mod.__name__.rsplit('.', 1)[-1]}_"))
        try:
            files = mod.build(Ctx(data_dir, tmp))
            for m in metas:
                f = files.get(m.id)
                if f is None or not Path(f).exists():
                    continue  # module may legitimately skip a layer (e.g. missing key)
                digest = hashlib.sha256(Path(f).read_bytes()).hexdigest()[:10]
                name = f"x_{m.id}.{digest}.{'geojson' if m.type == 'geojson' else 'pmtiles'}"
                if not (public / name).exists():
                    shutil.move(str(f), public / name)
                entry = {**asdict(m), "url": f"/data/{name}", "hash": h}
                entry.pop("inputs", None)
                if extras.get(m.id, {}).get("url") != entry["url"]:
                    changed.append(m.id)
                extras[m.id] = entry
        except Exception as e:  # noqa: BLE001 - one extra failing must not break the map
            errors[mod.__name__] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for k in list(extras):  # module removed
        if k not in present:
            extras.pop(k)
            changed.append(k)
    return extras, changed, errors


def describe() -> str:
    return json.dumps([asdict(m) for mod in modules() for m in mod.LAYERS], indent=1)
