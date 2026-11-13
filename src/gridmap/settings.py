"""Where the app keeps things. One place, so dev (repo checkout) and the packaged macOS app agree.

GRIDMAP_HOME   app home. Dev default: the repo root. Packaged app: ~/Library/Application Support/GB Grid
  KEYS_FILE    <home>/.env          API keys (never committed, chmod 600)
  DATA_DIR     <home>/data          raw / norm / derived / public / basemap / grid.duckdb
  <home>/payload                    updated python package + web UI pulled from GitHub (gridmap.payload); the
                                    shell (packaging/launch.py) runs the newest healthy one in front of the bundled copy
GRIDMAP_DATA_DIR still overrides DATA_DIR (tests).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("GRIDMAP_HOME") or REPO_ROOT)
KEYS_FILE = Path(os.environ.get("GRIDMAP_KEYS_FILE") or HOME / ".env")
DATA_DIR = Path(os.environ.get("GRIDMAP_DATA_DIR") or HOME / "data")

# Public repo the app pulls updates from (its `latest-update` release).
UPDATE_REPO = os.environ.get("GRIDMAP_UPDATE_REPO", "damonroberts95/gb-grid-map")
# Set by the shell when running from an updated payload (<home>/payload/<sha>); unset for the bundled copy / dev.
PAYLOAD_DIR = Path(os.environ["GRIDMAP_PAYLOAD_DIR"]) if os.environ.get("GRIDMAP_PAYLOAD_DIR") else None


def load_keys() -> None:
    """Load API keys into os.environ (values are never printed or logged)."""
    from dotenv import load_dotenv
    if KEYS_FILE.exists():
        load_dotenv(KEYS_FILE, override=True)
