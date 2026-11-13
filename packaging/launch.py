"""PyInstaller entry script (the "shell"): picks the newest healthy update payload, then runs src/gridmap/desktop.py.

Keep this file tiny and stable: it is the one piece of app code that cannot be updated without a new .app.
Payload rules (the app side is src/gridmap/payload.py; keep them in step):
  <home>/payload/current.json  {"good": [sha...] newest first, "pending": sha|null, "trying": bool, "bad": [sha...]}
  pending and not yet tried  -> run it, set trying (the app clears trying + moves it to good once healthy)
  pending and trying already -> it never got healthy: mark it bad, fall through
  else the newest good payload that is still on disk, else the bundled gridmap.
"""
import importlib.machinery
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("GRIDMAP_HOME") or Path.home() / "Library" / "Application Support" / "GB Grid")


def bundle_dirs():
    if not getattr(sys, "frozen", False):
        return [Path(__file__).resolve().parent.parent]
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return [base, Path(sys.executable).resolve().parent.parent / "Resources", Path(sys.executable).parent]


def read_text(name):
    for d in bundle_dirs():
        try:
            return (d / name).read_text().strip()
        except OSError:
            continue
    return ""


def load_state(f):
    try:
        st = json.loads(f.read_text())
    except (OSError, ValueError):
        st = {}
    for k, v in (("good", []), ("pending", None), ("trying", False), ("bad", [])):
        st.setdefault(k, v)
    return st


def save_state(f, st):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name("current.json.tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(f)


def ok(sha):
    return (HOME / "payload" / sha / "src" / "gridmap" / "__init__.py").is_file()


def select():
    """sha of the payload to run, or None for the bundled copy."""
    f = HOME / "payload" / "current.json"
    st = load_state(f)
    pend = st["pending"]
    if pend:
        if not st["trying"] and ok(pend):
            st["trying"] = True
            save_state(f, st)
            return pend
        st["bad"] = [pend] + [b for b in st["bad"] if b != pend][:19]
        st["pending"], st["trying"] = None, False
        save_state(f, st)
    return next((g for g in st["good"] if ok(g) and g not in st["bad"]), None)


def mark_bad(sha):
    f = HOME / "payload" / "current.json"
    st = load_state(f)
    st["bad"] = [sha] + [b for b in st["bad"] if b != sha][:19]
    st["good"] = [g for g in st["good"] if g != sha]
    if st["pending"] == sha:
        st["pending"], st["trying"] = None, False
    save_state(f, st)


def use_payload(root):
    """Put <root>/src's gridmap in front of the bundled one. A finder ahead of the frozen importer means a file the
    payload removed is missing, not silently served from the old bundled copy."""
    src = str(Path(root) / "src")
    sys.path.insert(0, src)

    class Finder:
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name == "gridmap" or name.startswith("gridmap."):
                spec = importlib.machinery.PathFinder.find_spec(name, [src] if path is None else path)
                if spec is None:
                    raise ModuleNotFoundError(f"{name} is not in the update payload", name=name)
                return spec
            return None

    sys.meta_path.insert(0, Finder())


if __name__ == "__main__":
    multiprocessing.freeze_support()  # frozen app: multiprocessing helper processes re-run this exe; keep them out of main()
    args = sys.argv[1:]
    if "-c" in args[:2] and len(args) > args.index("-c") + 1:
        # code runs `sys.executable -I -c <code>` (updater payload check); in the frozen app sys.executable is this exe
        exec(args[args.index("-c") + 1], {"__name__": "__main__", "use_payload": use_payload})  # noqa: S102
        raise SystemExit(0)
    me = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    os.environ["GRIDMAP_SHELL_CMD"] = json.dumps(me)
    os.environ["GRIDMAP_SHELL_VERSION"] = read_text("SHELL_VERSION") or "0"
    try:
        info = json.loads(read_text("build_info.json") or "{}")
    except ValueError:
        info = {}
    for k, env in (("deps_hash", "GRIDMAP_DEPS_HASH"), ("sha", "GRIDMAP_BUILD_SHA")):
        if info.get(k):
            os.environ[env] = info[k]
    os.environ.pop("GRIDMAP_PAYLOAD_SHA", None)  # inherited when we re-exec ourselves
    os.environ.pop("GRIDMAP_PAYLOAD_DIR", None)
    sha = select() if os.environ.get("GRIDMAP_NO_PAYLOAD") != "1" else None
    if sha:
        os.environ["GRIDMAP_PAYLOAD_SHA"] = sha
        os.environ["GRIDMAP_PAYLOAD_DIR"] = str(HOME / "payload" / sha)
        use_payload(HOME / "payload" / sha)
    try:
        from gridmap.desktop import main
    except Exception:
        if not sha:
            raise
        mark_bad(sha)
        time.sleep(0.1)
        os.execv(me[0], me)
    raise SystemExit(main())
