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


# Names people end up with when making the keys file by hand on a Mac: Finder hides or refuses a leading dot,
# TextEdit adds .txt. Read any of them (after .env, which the Keys page writes).
KEYS_ALT = ("env", "env.txt", ".env.txt", "keys.txt", "keys.env")


def keys_files() -> list[Path]:
    """Keys files present in the app home, .env first."""
    return [f for f in (KEYS_FILE, *(KEYS_FILE.parent / n for n in KEYS_ALT)) if f.is_file()]


def load_keys() -> list[str]:
    """Load API keys into os.environ. Returns the names loaded (values are never printed or logged).
    utf-8-sig: a byte-order mark would otherwise glue itself to the first key's name."""
    from dotenv import dotenv_values
    names: list[str] = []
    for f in reversed(keys_files()):  # .env last, so it wins over hand-made copies
        for k, v in dotenv_values(f, encoding="utf-8-sig").items():
            if k and v:
                os.environ[k.strip()] = v.strip()
                names.append(k.strip())
    return sorted(set(names))
