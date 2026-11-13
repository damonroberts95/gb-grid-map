"""CLI: uv run python -m gridmap.fetch [--only id,...] [--force] [--dry-run] [--include-large]"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .runner import format_table, run
from .store import REPO_ROOT


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gridmap.fetch", description="Fetch raw snapshots of all registered sources.")
    p.add_argument("--only", help="comma-separated source ids")
    p.add_argument("--force", action="store_true", help="ignore change detection")
    p.add_argument("--dry-run", action="store_true", help="read metadata only; no downloads, no log")
    p.add_argument("--include-large", action="store_true", help="also fetch sources marked large: true")
    p.add_argument("--no-keys", action="store_true", help="ignore .env / environment API keys (anonymous run)")
    args = p.parse_args(argv)
    if not args.no_keys:
        load_dotenv(REPO_ROOT / ".env")
    try:
        summary = run(args.only.split(",") if args.only else None, force=args.force, dry_run=args.dry_run,
                      include_large=args.include_large, env={} if args.no_keys else None)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 2
    print(format_table(summary))
    return 1 if summary["counts"].get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
