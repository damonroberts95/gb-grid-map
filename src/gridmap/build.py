"""Build the versioned map files in data/public/ and the manifest (docs/api.md).

  sites.<hash>.geojson  every GSP/BSP/PRIMARY site with coordinates (short property keys)
  links.<hash>.geojson  schematic parent -> child lines
  lv.<hash>.pmtiles     secondary substations (tippecanoe, layer `lv`, z11-14)
  lvmon.<hash>.pmtiles  monitored secondary substations only (layer `lvmon`, z4-14)

A layer is rebuilt only when the hash of its input files changes. Files are written under a temp name and
renamed into place; the manifest is renamed last, so readers never see a manifest that points at a missing file.
Superseded files are deleted once they are older than 10 minutes (clients may still hold the old manifest).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from .normalise.common import DATA_DIR
from .tools import tool

BUILD_VERSION = "2"  # bump when the output format changes, to force a rebuild
KIND_NUM = {"GSP": 1, "BSP": 2, "PRIMARY": 3}
STALE_SECONDS = 600
TIPPECANOE = ["-l", "lv", "-Z11", "-z14", "--drop-densest-as-needed", "--extend-zooms-if-still-dropping", "-r1",
              "--force", "-q"]
# monitored LV only (national, low zoom): -r1 = no rate-based dropping; tiles over 500 KB are thinned.
TIPPECANOE_LVMON = ["-l", "lvmon", "-Z4", "-z14", "-r1", "--no-feature-limit", "--maximum-tile-bytes=500000",
                    "--drop-densest-as-needed", "--force", "-q"]


def _hash_files(files: list[Path], salt: str) -> str:
    h = hashlib.sha256(f"{BUILD_VERSION}:{salt}".encode())
    for f in sorted(files):
        h.update(f.name.encode())
        with open(f, "rb") as fh:
            while chunk := fh.read(1 << 20):
                h.update(chunk)
    return h.hexdigest()[:10]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"tmp_{path.name}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _num(v: float | None, nd: int = 3) -> float | None:
    return None if v is None else round(v, nd)


def _read_opt(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.exists() else None


def site_frames(derived: Path) -> pl.DataFrame:
    """site + capacity + fault + queue, one row per site with coordinates, sorted by site_id (stable feature ids)."""
    site = pl.read_parquet(derived / "site.parquet")
    df = site.filter(pl.col("kind").is_in(list(KIND_NUM)) & pl.col("lat").is_not_null()).sort("site_id")
    for name, cols in (
        ("site_capacity", ["site_id", "gen_headroom_mw", "demand_headroom_mw", "demand_headroom_mva",
                           "gen_rag_native"]),
        ("site_fault", ["site_id", "break_headroom_ka"]),
        ("site_queue", ["site_id", "queued_mw", "connected_mw"]),
    ):
        other = _read_opt(derived / f"{name}.parquet")
        if other is not None:
            df = df.join(other.select(cols), on="site_id", how="left")
    for c in ("gen_headroom_mw", "demand_headroom_mw", "demand_headroom_mva", "gen_rag_native", "break_headroom_ka",
              "queued_mw", "connected_mw"):
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8 if c.endswith("native") else pl.Float64).alias(c))
    return df.with_row_index("fid")


def build_sites_links(derived: Path) -> tuple[bytes, bytes]:
    df = site_frames(derived)
    fid = dict(zip(df["site_id"].to_list(), df["fid"].to_list(), strict=True))
    coord = {r["site_id"]: (round(r["lon"], 5), round(r["lat"], 5)) for r in df.select("site_id", "lat", "lon").to_dicts()}
    feats, lines = [], []
    for r in df.to_dicts():
        mva = r["demand_headroom_mw"] is None and r["demand_headroom_mva"] is not None
        parent = r["parent_site_id"]
        props = {
            "sid": r["site_id"], "n": r["name"], "op": r["operator"], "k": KIND_NUM[r["kind"]],
            "kv": _num(r["kv_max"]), "g": _num(r["gen_headroom_mw"]),
            "d": _num(r["demand_headroom_mva"] if mva else r["demand_headroom_mw"]), "du": 1 if mva else 0,
            "fh": _num(r["break_headroom_ka"]), "q": _num(r["queued_mw"]), "c": _num(r["connected_mw"]),
            "rg": r["gen_rag_native"], "p": fid.get(parent),
        }
        feats.append({"type": "Feature", "id": r["fid"], "geometry": {"type": "Point", "coordinates": coord[r["site_id"]]},
                      "properties": props})
        if parent in coord:
            lines.append({"type": "Feature", "geometry": {"type": "LineString",
                                                          "coordinates": [coord[r["site_id"]], coord[parent]]},
                          "properties": {"k": KIND_NUM[r["kind"]], "op": r["operator"]}})
    dump = lambda f: json.dumps({"type": "FeatureCollection", "features": f}, separators=(",", ":")).encode()
    return dump(feats), dump(lines)


def lv_frame(norm: Path, data_dir: Path | None = None) -> pl.DataFrame:
    """lv_site rows with coordinates + tile props: mt/mti (mount type, inferred flag), mon (1) and ms (monitor source)."""
    from .extra.lv_common import with_mount
    from .extra.lv_monitored import monitored
    files = sorted(norm.glob("*/lv_site.parquet"))
    df = (pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
          .filter(pl.col("lat").is_not_null() & pl.col("lon").is_not_null()))
    df = with_mount(df)
    try:
        mon = monitored(data_dir)
    except Exception as e:  # noqa: BLE001 - monitor lists are optional enrichment
        print(f"build_lv: monitored() failed, no mon flags: {e!r}")
        mon = pl.DataFrame(schema={"asset_uid": pl.Utf8, "ms": pl.Utf8})
    return (df.join(mon, on="asset_uid", how="left").with_columns(pl.col("ms").is_not_null().alias("mon"))
            .select("asset_uid", "operator", "lat", "lon", "utilisation_pct", "transformer_kva", "customers",
                    "mt", "mti", "mon", "ms"))


def _lv_geojsonl(df: pl.DataFrame, out: Path, only_mon: bool) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".geojsonl", delete=False, dir=out.parent, prefix="tmp_lv_") as tf:
        for uid, op, lat, lon, u, kva, cu, mt, mti, mon, ms in df.iter_rows():
            if only_mon and not mon:
                continue
            props = {"id": uid, "op": op, "u": _num(u, 1), "kva": None if kva is None else int(kva), "cu": cu,
                     "mt": mt, "mti": mti, "mon": 1 if mon else None, "ms": ms}
            if only_mon:
                props.pop("mon")
            tf.write(json.dumps({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                                 "properties": {k: v for k, v in props.items() if v is not None}},
                                separators=(",", ":")) + "\n")
        return tf.name


def build_lv(norm: Path, out: Path, only_mon: bool = False, df: pl.DataFrame | None = None) -> None:
    """tippecanoe over lv_site rows that have coordinates. Writes `out` (a .pmtiles path).
    only_mon=True builds the `lvmon` layer (monitored sites only, z4-14)."""
    df = lv_frame(norm) if df is None else df
    src = _lv_geojsonl(df, out, only_mon)
    tmp = out.with_name(f"tmp_{out.name}")
    try:
        subprocess.run([tool("tippecanoe"), "-P", "-o", str(tmp), *(TIPPECANOE_LVMON if only_mon else TIPPECANOE), src],
                       check=True)
        os.replace(tmp, out)
    finally:
        Path(src).unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def read_manifest(public: Path) -> dict:
    try:
        return json.loads((public / "manifest.json").read_text())
    except (FileNotFoundError, ValueError):
        return {"version": 0, "layers": {}, "built_at": None, "refreshing": False}


def cleanup(public: Path, keep: set[str], now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    gone = []
    for f in public.iterdir():
        if f.name in keep or f.name == "manifest.json" or not f.is_file():
            continue
        if f.name.startswith(("sites.", "links.", "lv.", "lvmon.", "x_", "tmp_")) and now - f.stat().st_mtime > STALE_SECONDS:
            f.unlink(missing_ok=True)
            gone.append(f.name)
    return gone


def build_all(data_dir: Path | None = None, public: Path | None = None, only_live: bool = False) -> dict:
    """Rebuild changed layers, write the manifest. Returns {"changed": [layers], "manifest": {...}, "sizes": {...}}."""
    data_dir = data_dir or DATA_DIR
    derived, norm = data_dir / "derived", data_dir / "norm"
    public = public or data_dir / "public"
    public.mkdir(parents=True, exist_ok=True)
    old = read_manifest(public)
    layers = dict(old.get("layers", {}))
    changed: list[str] = []

    site_in = [derived / f"{n}.parquet" for n in ("site", "site_capacity", "site_fault", "site_queue")]
    link_in = [derived / "site.parquet"]
    from .extra.lv_monitored import input_files
    lv_in = sorted(norm.glob("*/lv_site.parquet")) + input_files(data_dir)  # + monitor lists feeding mon/ms
    plan = {
        "sites": ([f for f in site_in if f.exists()], "geojson"),
        "links": (link_in, "geojson"),
        "lv": (lv_in, "pmtiles"),
        "lvmon": (lv_in, "pmtiles"),
    }
    lv_df = None
    sites_b = links_b = None
    for layer, (inputs, ext) in ({} if only_live else plan).items():
        if not inputs or (layer not in ("lv", "lvmon") and not (derived / "site.parquet").exists()):
            continue
        h = _hash_files(inputs, layer)
        name = f"{layer}.{h}.{ext}"
        if layers.get(layer) == f"/data/{name}" and (public / name).exists():
            continue
        if layer in ("sites", "links"):
            if sites_b is None:
                sites_b, links_b = build_sites_links(derived)
            _atomic_write(public / name, sites_b if layer == "sites" else links_b)
        else:
            if lv_df is None:
                lv_df = lv_frame(norm, data_dir)
            if layer == "lvmon" and not lv_df["mon"].any():
                layers.pop("lvmon", None)  # no monitor lists available: no layer (tippecanoe rejects empty input)
                continue
            build_lv(norm, public / name, only_mon=layer == "lvmon", df=lv_df)
        layers[layer] = f"/data/{name}"
        changed.append(layer)

    import os

    from .extra import build_extras  # local import: extras import normalise helpers
    if os.environ.get("GRIDMAP_NO_EXTRAS") == "1":  # tests: extras read the real data dir
        extras, x_changed, x_errors = old.get("extras", {}), [], {}
    else:
        extras, x_changed, x_errors = build_extras(data_dir, public, old.get("extras", {}), only_live=only_live)
    changed += [f"x:{x}" for x in x_changed]

    manifest = old
    if changed or not (public / "manifest.json").exists():
        manifest = {"version": int(old.get("version", 0)) + 1, "layers": layers, "extras": extras,
                    "built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "refreshing": False}
        _atomic_write(public / "manifest.json", json.dumps(manifest, indent=1).encode())
    cleanup(public, {Path(p).name for p in layers.values()} | {Path(e["url"]).name for e in extras.values()})
    sizes = {k: (public / Path(v).name).stat().st_size for k, v in layers.items() if (public / Path(v).name).exists()}
    return {"changed": changed, "manifest": manifest, "sizes": sizes, "extra_errors": x_errors}


if __name__ == "__main__":
    res = build_all()
    print("rebuilt:", res["changed"] or "nothing")
    for k, v in res["sizes"].items():
        print(f"  {k:6} {v / 1e6:8.2f} MB  {res['manifest']['layers'][k]}")
