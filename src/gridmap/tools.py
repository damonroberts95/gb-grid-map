"""Find external command-line tools (tippecanoe, pmtiles, osmium).

Order: $GRIDMAP_BIN, the packaged app's bin directory (PyInstaller bundle), then PATH.
Returns the bare name if nothing is found, so subprocess raises its usual FileNotFoundError.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bin_dirs() -> list[Path]:
    dirs: list[Path] = []
    if os.environ.get("GRIDMAP_BIN"):
        dirs.append(Path(os.environ["GRIDMAP_BIN"]))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        dirs += [base / "bin", Path(sys.executable).parent / "bin"]
    return dirs


def tool(name: str) -> str:
    for d in bin_dirs():
        p = d / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name) or name
