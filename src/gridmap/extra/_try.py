"""Build ONE extra module into a scratch dir and report what it produced (safe to run in parallel:
never touches data/public or the manifest).

    uv run python -I -m gridmap.extra._try <module> [--keep DIR]
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..normalise.common import DATA_DIR
from ..tools import tool
from . import Ctx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--keep", help="copy outputs here for inspection")
    a = ap.parse_args()
    mod = importlib.import_module(f"gridmap.extra.{a.module}")
    tmp = Path(tempfile.mkdtemp(prefix=f"try_{a.module}_"))
    files = mod.build(Ctx(DATA_DIR, tmp))
    ids = {m.id for m in mod.LAYERS}
    for lid, f in files.items():
        f = Path(f)
        assert lid in ids, f"build() returned undeclared layer {lid}"
        size = f.stat().st_size
        info = ""
        if f.suffix == ".geojson":
            fc = json.loads(f.read_text())
            n = len(fc["features"])
            props = sorted({k for ft in fc["features"][:200] for k in ft["properties"]})
            info = f"features={n:,} props={props}"
            assert size <= 3_000_000, "geojson over 3 MB: use pmtiles"
        elif f.suffix == ".pmtiles":
            out = subprocess.run([tool("pmtiles"), "show", str(f)], capture_output=True, text=True,
                                 check=False).stdout
            info = " ".join(ln.strip() for ln in out.splitlines() if ln.split(":")[0].strip() in
                            ("min zoom", "max zoom", "addressed tiles count", "layer"))
        print(f"{lid:24} {size / 1e6:7.2f} MB  {info}")
    missing = ids - set(files)
    if missing:
        print("not produced:", sorted(missing))
    if a.keep:
        Path(a.keep).mkdir(parents=True, exist_ok=True)
        for f in files.values():
            shutil.copy(f, a.keep)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
