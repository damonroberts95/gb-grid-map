"""Native macOS app entry point ("GB Grid.app"): local server + a pywebview window.

    uv run python -m gridmap.desktop           # dev run, uses GRIDMAP_HOME (default: ~/Library/Application Support/GB Grid)

Env: GRIDMAP_HOME (data home), GRIDMAP_PORT (fixed port; default a free one), GRIDMAP_NO_WINDOW=1 (serve only,
no window: for tests; stop with Ctrl-C / SIGTERM). Logs: <home>/logs/app.log (rotated). Never logs key values.
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "GB Grid"
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / APP_NAME

# Home must be decided before gridmap.settings is imported anywhere.
os.environ.setdefault("GRIDMAP_HOME", str(DEFAULT_HOME))

from gridmap import settings

log = logging.getLogger("gridmap.desktop")


def bundle_dirs() -> list[Path]:
    """Where PyInstaller put bundled resources (onedir .app: Contents/Frameworks and/or Contents/Resources)."""
    if not getattr(sys, "frozen", False):
        return []
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return [base, Path(sys.executable).resolve().parent.parent / "Resources", Path(sys.executable).parent]


def setup_logging(home: Path) -> None:
    (home / "logs").mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(home / "logs" / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    for name in ("gridmap", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx"):
        lg = logging.getLogger(name)
        lg.handlers = [handler] if name == "gridmap" else []
        lg.propagate = name != "gridmap"
    root = logging.getLogger()
    root.handlers = [handler]  # uvicorn/httpx/anything else propagates here
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # server/app.py adds a stderr handler to "gridmap" when it has none; having ours there prevents that.
    logging.getLogger("gridmap").setLevel(logging.INFO)


def setup_keys() -> None:
    """First run: copy the bundled keys.env to KEYS_FILE (chmod 600) if there is no keys file yet."""
    keys = settings.KEYS_FILE
    if keys.exists():
        return
    for d in bundle_dirs():
        src = d / "keys.env"
        if src.is_file():
            keys.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, keys)
            keys.chmod(0o600)
            log.info("installed API keys file at %s", keys)  # path only, never contents
            return
    log.info("no bundled keys.env; running without API keys")


def free_port() -> int:
    if os.environ.get("GRIDMAP_PORT"):
        return int(os.environ["GRIDMAP_PORT"])
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alert(msg: str) -> None:
    subprocess.run(["osascript", "-e", f'display dialog "{msg}" with title "{APP_NAME}" buttons {{"OK"}}'],
                   check=False, capture_output=True)


def main() -> int:
    home = settings.HOME
    home.mkdir(parents=True, exist_ok=True)
    setup_logging(home)
    lock = open(home / ".app.lock", "w")  # noqa: SIM115 - held for the life of the process
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("another GB Grid is already running")
        alert("GB Grid is already running.")
        return 1
    log.info("starting; home=%s frozen=%s", home, getattr(sys, "frozen", False))
    setup_keys()

    import uvicorn

    from gridmap.server.app import app  # after GRIDMAP_HOME is set

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, reload=False,
                                           loop="asyncio", http="h11", ws="none", timeout_graceful_shutdown=3))
    th = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    th.start()
    t0 = time.time()
    while not server.started and th.is_alive() and time.time() - t0 < 60:
        time.sleep(0.05)
    if not server.started:
        log.error("server did not start")
        alert("GB Grid could not start its local server. See logs/app.log in the data folder.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    log.info("serving %s", url)

    if os.environ.get("GRIDMAP_NO_WINDOW") == "1":
        print(f"GB Grid serving {url}", flush=True)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        stop.wait()
    else:
        import webview
        webview.create_window(APP_NAME, url, width=1440, height=900, min_size=(900, 600))
        webview.start()  # blocks until the window is closed

    log.info("window closed; stopping server")
    server.should_exit = True
    th.join(timeout=10)
    logging.shutdown()
    os._exit(0)  # scheduler/sweep threads are daemons; do not wait for in-flight fetches


if __name__ == "__main__":
    raise SystemExit(main())
