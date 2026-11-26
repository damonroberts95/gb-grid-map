"""Run per-operator adapters: `python -m gridmap.normalise [--only UKPN,NPG]`."""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback

ADAPTERS = {
    "UKPN": "ukpn", "NPG": "npg", "ENW": "enw", "SPEN": "spen", "NGED": "nged", "SSEN": "ssen", "NIE": "nie", "NESO": "neso",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="comma-separated operators")
    args = ap.parse_args()
    ops = args.only.split(",") if args.only else list(ADAPTERS)
    failed = 0
    for op in ops:
        try:
            mod = importlib.import_module(f"gridmap.normalise.{ADAPTERS[op]}")
        except ModuleNotFoundError:
            print(f"{op:6} no adapter yet")
            continue
        try:
            counts = mod.run()  # -> {table: rows}
            print(f"{op:6} " + "  ".join(f"{t}={n:,}" for t, n in counts.items()))
        except Exception:  # noqa: BLE001 — one adapter failing must not stop the others
            failed += 1
            print(f"{op:6} FAILED")
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
