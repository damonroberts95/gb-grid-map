# PyInstaller spec for "GB Grid.app" (macOS, Apple Silicon, unsigned, onedir windowed).
# Build with packaging/build_app.sh (it stages packaging/bin/* and packaging/keys.env first).
#   no downloaded data is bundled: the app builds <home>/data on first run.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
PKG_DIR = ROOT / "packaging"
SRC = ROOT / "src"

datas, binaries, hiddenimports = [], [], []
for pkg in ("duckdb", "polars", "osmium", "webview"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("pyproj")  # proj.db and grids
hiddenimports += collect_submodules("pyproj")
hiddenimports += collect_submodules("gridmap")  # adapters / extra layers are imported by name at run time
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["h11", "yaml", "dotenv", "rapidfuzz", "openpyxl", "httpx"]

# our package data (sources.yaml, sources.d/*.yaml) next to the gridmap modules
datas += [(str(SRC / "gridmap" / "sources.yaml"), "gridmap"), (str(SRC / "gridmap" / "sources.d"), "gridmap/sources.d")]
# built web app (npm run build)
datas += [(str(ROOT / "web" / "dist"), "web/dist")]
# native tools (copied from /opt/homebrew/bin by build_app.sh; only system libraries)
for tool in ("tippecanoe", "pmtiles"):
    p = PKG_DIR / "bin" / tool
    if not p.is_file():
        raise SystemExit(f"missing {p}: run packaging/build_app.sh")
    datas.append((str(p), "bin"))
# API keys: copied from the developer's .env at build time; never committed (gitignored)
keys = PKG_DIR / "keys.env"
if keys.is_file():
    datas.append((str(keys), "."))
else:
    print("WARNING: packaging/keys.env missing; the app will have no API keys")

a = Analysis(
    [str(PKG_DIR / "launch.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "respx", "pyarrow", "pandas", "IPython", "PyQt5", "PyQt6",
              "PySide2", "PySide6", "qtpy", "gi", "gtk"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="GB Grid", console=False, target_arch="arm64",
          codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GB Grid")
app = BUNDLE(
    coll,
    name="GB Grid.app",
    icon=str(PKG_DIR / "icon.icns") if (PKG_DIR / "icon.icns").is_file() else None,
    bundle_identifier="uk.gridata.gbgrid",
    info_plist={
        "CFBundleName": "GB Grid",
        "CFBundleDisplayName": "GB Grid",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
