"""OSM extraction with pyosmium (replaces the `osmium tags-filter` + `osmium export -f geojsonseq` CLI pair, so the
packaged app needs no osmium binary). Output lines match `osmium export` closely enough for the extras:
one GeoJSON Feature per line, `properties` = the OSM tags, geometries WGS84.

    export_geojsonseq(pbf, out, {"power": {"substation"}}, nodes=True, ways=True, areas=True)

nodes -> Point; ways -> LineString (every matching way); areas -> MultiPolygon (closed ways and multipolygon
relations, like osmium export). The input may have any file name (Geofabrik snapshots are stored as `file`).
"""

from __future__ import annotations

import json
from pathlib import Path

import osmium
import osmium.filter
import osmium.geom


def pbf_in(files: list[Path]) -> Path:
    """The OSM extract inside a raw snapshot (stored as `file`, or `*.pbf`): the biggest file."""
    return max(files, key=lambda p: p.stat().st_size)


def export_geojsonseq(pbf: Path, out: Path, want: dict[str, set[str]], *, nodes: bool = False, ways: bool = False,
                      areas: bool = False) -> int:
    """Write matching OSM objects as GeoJSONSeq (one Feature per line, no RS). `want` = {key: allowed values}
    (an object matches if any key has an allowed value). Returns the number of features written."""
    fp = osmium.FileProcessor(osmium.io.File(str(pbf), "pbf"))
    fp = fp.with_locations()
    if areas:
        fp = fp.with_areas(osmium.filter.TagFilter(*[(k, v) for k, vs in want.items() for v in vs]))
    fp = fp.with_filter(osmium.filter.KeyFilter(*want))
    gj = osmium.geom.GeoJSONFactory()
    n = 0
    with out.open("w") as f:
        for o in fp:
            tags = o.tags
            if not any(tags.get(k) in vs for k, vs in want.items()):
                continue
            try:
                if o.is_node():
                    if not nodes:
                        continue
                    geom = gj.create_point(o)
                elif o.is_way():
                    if not ways:
                        continue
                    geom = gj.create_linestring(o)
                elif o.is_area():
                    if not areas:
                        continue
                    geom = gj.create_multipolygon(o)
                else:
                    continue
            except (RuntimeError, osmium.InvalidLocationError):
                continue  # geometry could not be built (missing node / broken ring), as osmium export skips them
            f.write('{"type":"Feature","properties":' + json.dumps({t.k: t.v for t in tags}, separators=(",", ":"),
                    ensure_ascii=False) + ',"geometry":' + geom + "}\n")
            n += 1
    return n
