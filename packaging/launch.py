"""PyInstaller entry script: starts the desktop app (src/gridmap/desktop.py)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # frozen app: multiprocessing helper processes re-run this exe; keep them out of main()
    args = sys.argv[1:]
    if "-c" in args[:2] and len(args) > args.index("-c") + 1:
        # code runs `sys.executable -I -c <code>` (updater import check); in the frozen app sys.executable is this exe
        exec(args[args.index("-c") + 1], {"__name__": "__main__"})  # noqa: S102
        raise SystemExit(0)
    from gridmap.desktop import main

    raise SystemExit(main())
