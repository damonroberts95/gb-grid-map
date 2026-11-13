"""Where the app keeps things. One place, so dev (repo checkout) and the packaged macOS app agree.

GRIDMAP_HOME   app home. Dev default: the repo root. Packaged app: ~/Library/Application Support/GB Grid
  KEYS_FILE    <home>/.env          API keys (never committed, chmod 600)
  DATA_DIR     <home>/data          raw / norm / derived / public / basemap / grid.duckdb
  OVERLAY_DIR  <home>/overlay       config + extra-layer modules pulled from GitHub (auto-update); takes
                                    precedence over the bundled copies when present and valid
GRIDMAP_DATA_DIR still overrides DATA_DIR (tests).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("GRIDMAP_HOME") or REPO_ROOT)
KEYS_FILE = Path(os.environ.get("GRIDMAP_KEYS_FILE") or HOME / ".env")
DATA_DIR = Path(os.environ.get("GRIDMAP_DATA_DIR") or HOME / "data")
OVERLAY_DIR = Path(os.environ.get("GRIDMAP_OVERLAY_DIR") or HOME / "overlay")

# Public repo the app pulls config / layer-module updates from.
UPDATE_REPO = os.environ.get("GRIDMAP_UPDATE_REPO", "damonroberts95/gb-grid-map")
UPDATE_BRANCH = os.environ.get("GRIDMAP_UPDATE_BRANCH", "main")


def load_keys() -> None:
    """Load API keys into os.environ (values are never printed or logged)."""
    from dotenv import load_dotenv
    if KEYS_FILE.exists():
        load_dotenv(KEYS_FILE, override=True)
