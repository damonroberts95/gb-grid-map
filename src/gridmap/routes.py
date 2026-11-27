"""Power routes along real overhead lines.

Build: per operator and voltage band, turn the published overhead line routes (extra/lines.py readers) into a graph.
Nodes are line ends and shared vertices snapped to ~5 m. Edges keep their polyline. Dangling ends near a dangling end
of another graph piece get a straight "jump" edge, because that is where a line went underground (cables are not
published). Saved as parquet under data/derived/routes/.

Query: shortest path between two points on one graph (A*). The ends snap to nearby nodes. Jumps cost 3x their length
so the path follows real lines and only jumps over gaps. With no graph or no path the answer is one straight jump.
Used by GET /api/route (server/app.py)."""

from __future__ import annotations

import heapq
import json
import logging
import math
import threading
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import polars as pl

from .normalise.common import latest_snapshot, snapshot_files

log = logging.getLogger("gridmap.routes")

BANDS = ("hv", "ehv", "132")
JUMP_COST = 3.0       # straight jumps cost this times their length
JUMP_R = 3000.0       # max gap (m) bridged between dangling ends of two graph pieces
JUMP_K = 3            # bridges per dangling end (nearest per other piece)
JOIN_M = 10.0         # shorter bridges are the same line with ends a few metres apart: drawn and costed as line
END_R = 2000.0        # an end point may join the graph this far away (beyond its snap limit it costs JUMP_COST)
END_K = 8             # nearest nodes tried per end point
MAX_POP = 400_000     # A* safety cap
SNAP = (20000, 12000)  # key cells per degree (lat, lon): ~5.5 m x ~5 m in GB


def band(kv: float) -> str | None:
    """Voltage band of a line: hv 1-25 kV (and unknown voltage), ehv 25-100 kV, 132 kV and up. LV -> None."""
    if 0 < kv < 1:
        return None
    return "132" if kv >= 100 else "ehv" if kv >= 25 else "hv"


def dist(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Metres, equirectangular (good to <0.5% over a few tens of km)."""
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * 111320 * k, (lat2 - lat1) * 110574)


def _key(c: list[float]) -> tuple[int, int]:
    return round(c[1] * SNAP[0]), round(c[0] * SNAP[1])


# --------------------------------------------------------------------------- build
def _lines() -> Iterator[tuple[str, float, list[list[list[float]]]]]:
    """(operator, kV, parts) for every HV/EHV overhead line we hold."""
    from .extra.lines import GEOJSON_HV, _geojson_feats, _ssen_feats

    def parts(g: dict) -> list:
        return [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]

    for sid, op in GEOJSON_HV:
        snap = latest_snapshot(sid)
        f = next((x for x in snapshot_files(snap) if x.suffix == ".geojson"), None) if snap else None
        if f is not None:
            for feat in _geojson_feats(f, op, False):
                yield op, feat["properties"]["kv"], parts(feat["geometry"])
    snap = latest_snapshot("ssen_overhead_lines_routes")
    if snap is not None:
        for feat, is_lv in _ssen_feats(snap):
            if not is_lv:
                yield "SSEN", feat["properties"]["kv"], parts(feat["geometry"])


def inputs() -> dict[str, str | None]:
    """Snapshot dir name per line source; the graphs are stale when this changes."""
    from .extra.lines import GEOJSON_HV
    out = {}
    for sid in [s for s, _ in GEOJSON_HV] + ["ssen_overhead_lines_routes"]:
        snap = latest_snapshot(sid)
        out[sid] = snap.name if snap else None
    return out


def stale(data_dir: Path) -> bool:
    from .refresh import code_newer_than
    m = data_dir / "derived" / "routes" / "manifest.json"
    if not m.exists() or code_newer_than(m, "routes.py", "extra/lines.py"):
        return True
    try:
        return json.loads(m.read_text()).get("inputs") != inputs()
    except ValueError:
        return True


def graph_frames(lines: list[tuple[float, list[list[list[float]]]]]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Lines of one operator and band -> (nodes, edges) frames. Edge kv -1 marks a jump (no geometry)."""
    count: dict[tuple[int, int], int] = defaultdict(int)
    for _, parts in lines:
        for p in parts:
            if len(p) < 2:
                continue
            count[_key(p[0])] += 2
            count[_key(p[-1])] += 2
            for c in p[1:-1]:
                count[_key(c)] += 1
    ids: dict[tuple[int, int], int] = {}
    nlon: list[float] = []
    nlat: list[float] = []

    def node(c: list[float]) -> int:
        k = _key(c)
        i = ids.get(k)
        if i is None:
            i = ids[k] = len(nlon)
            nlon.append(c[0])
            nlat.append(c[1])
        return i

    eu, ev, em, ekv, exs, eys = [], [], [], [], [], []
    for kv, parts in lines:
        for p in parts:
            if len(p) < 2:
                continue
            start = 0
            for j in range(1, len(p)):
                if j < len(p) - 1 and count[_key(p[j])] < 2:
                    continue
                seg = p[start:j + 1]
                u, v = node(seg[0]), node(seg[-1])
                m = sum(dist(*seg[i], *seg[i + 1]) for i in range(len(seg) - 1))
                if u != v and m > 0:
                    eu.append(u), ev.append(v), em.append(m), ekv.append(kv)
                    exs.append([c[0] for c in seg]), eys.append([c[1] for c in seg])
                start = j
    # pieces (union-find) and dangling ends
    parent = list(range(len(nlon)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    deg = [0] * len(nlon)
    for u, v in zip(eu, ev, strict=True):
        deg[u] += 1
        deg[v] += 1
        parent[find(u)] = find(v)
    dangling = [i for i, d in enumerate(deg) if d == 1]
    cell = JUMP_R / 111000
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i in dangling:
        grid[(int(nlat[i] // cell), int(nlon[i] // (cell * 1.7)))].append(i)
    seen: set[tuple[int, int]] = set()
    for i in dangling:
        gy, gx = int(nlat[i] // cell), int(nlon[i] // (cell * 1.7))
        ci = find(i)
        best: dict[int, tuple[float, int]] = {}
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for j in grid.get((gy + dy, gx + dx), ()):
                    cj = find(j)
                    if cj == ci:
                        continue
                    d = dist(nlon[i], nlat[i], nlon[j], nlat[j])
                    if d <= JUMP_R and (cj not in best or d < best[cj][0]):
                        best[cj] = (d, j)
        for d, j in sorted(best.values())[:JUMP_K]:
            pair = (min(i, j), max(i, j))
            if pair not in seen:
                seen.add(pair)
                eu.append(i), ev.append(j), em.append(d), ekv.append(-1.0), exs.append([]), eys.append([])
    nodes = pl.DataFrame({"lon": nlon, "lat": nlat}, schema={"lon": pl.Float64, "lat": pl.Float64})
    edges = pl.DataFrame({"u": eu, "v": ev, "m": em, "kv": ekv, "xs": exs, "ys": eys},
                         schema={"u": pl.Int32, "v": pl.Int32, "m": pl.Float32, "kv": pl.Float32,
                                 "xs": pl.List(pl.Float64), "ys": pl.List(pl.Float64)})
    return nodes, edges


def build(data_dir: Path) -> dict[str, int]:
    """Write data/derived/routes/{op}_{band}.{nodes,edges}.parquet + manifest.json. Returns edge counts."""
    out = data_dir / "derived" / "routes"
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    groups: dict[str, list] = defaultdict(list)

    def flush(op: str) -> None:  # one operator at a time keeps memory down
        for b, lines in sorted(groups.items()):
            nodes, edges = graph_frames(lines)
            nodes.write_parquet(out / f"{op}_{b}.nodes.parquet")
            edges.write_parquet(out / f"{op}_{b}.edges.parquet")
            counts[f"{op}_{b}"] = edges.height
        groups.clear()

    cur = None
    for op, kv, parts in _lines():  # sources come grouped by operator
        if op != cur and cur is not None:
            flush(cur)
        cur = op
        if (b := band(kv)) is not None:
            groups[b].append((kv, parts))
    if cur is not None:
        flush(cur)
    for f in out.glob("*.parquet"):  # an operator/band that no longer has lines
        if f.name.split(".")[0] not in counts:
            f.unlink()
    (out / "manifest.json").write_text(json.dumps({"inputs": inputs(), "edges": counts}, indent=1))
    log.info("route graphs built: %s", counts)
    return counts


# --------------------------------------------------------------------------- query
class Graph:
    def __init__(self, nodes: pl.DataFrame, edges: pl.DataFrame):
        self.lon: list[float] = nodes["lon"].to_list()
        self.lat: list[float] = nodes["lat"].to_list()
        self.kv: list[float] = edges["kv"].to_list()
        self.m: list[float] = edges["m"].to_list()
        self.eu: list[int] = edges["u"].to_list()
        self.ev: list[int] = edges["v"].to_list()
        self.xs = edges["xs"]
        self.ys = edges["ys"]
        self.adj: list[list[tuple[int, float, int]]] = [[] for _ in self.lon]
        for i, (u, v) in enumerate(zip(self.eu, self.ev, strict=True)):
            c = self.m[i] * (JUMP_COST if self.kv[i] < 0 and self.m[i] >= JOIN_M else 1.0)
            self.adj[u].append((v, c, i))
            self.adj[v].append((u, c, i))
        self.cell = 500 / 111000
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (x, y) in enumerate(zip(self.lon, self.lat, strict=True)):
            self.grid[self._cell(x, y)].append(i)

    def _cell(self, lon: float, lat: float) -> tuple[int, int]:
        return int(lat // self.cell), int(lon // (self.cell * 1.7))

    def near(self, lon: float, lat: float, r: float, k: int) -> list[tuple[float, int]]:
        """Up to k nearest nodes within r metres, as (metres, node)."""
        gy, gx = self._cell(lon, lat)
        n = int(r // 500) + 1
        out = []
        for dy in range(-n, n + 1):
            for dx in range(-n, n + 1):
                for i in self.grid.get((gy + dy, gx + dx), ()):
                    d = dist(lon, lat, self.lon[i], self.lat[i])
                    if d <= r:
                        out.append((d, i))
        return heapq.nsmallest(k, out)

    def coords(self, e: int, frm: int) -> list[list[float]]:
        xs, ys = self.xs[e], self.ys[e]
        if not len(xs):  # jump
            to = self.ev[e] if self.eu[e] == frm else self.eu[e]
            return [[self.lon[frm], self.lat[frm]], [self.lon[to], self.lat[to]]]
        pts = [[x, y] for x, y in zip(xs, ys, strict=True)]
        return pts if self.eu[e] == frm else pts[::-1]


def _jump(a: tuple[float, float], b: tuple[float, float]) -> dict:
    return {"kind": "jump", "kv": None, "coords": [list(a), list(b)], "m": dist(*a, *b)}


def route(g: Graph | None, a: tuple[float, float], b: tuple[float, float], snap_a: float, snap_b: float) -> list[dict]:
    """Path from a to b (lon, lat) as segments {kind: line|jump, kv, coords, m}, merged by kind and kV."""
    direct = dist(*a, *b)
    if g is None or direct < 1:
        return [_jump(a, b)] if direct >= 1 else []

    def end_cost(d: float, snap: float) -> float:
        return d if d <= snap else d * JUMP_COST

    start = g.near(*a, END_R, END_K)
    goal = {i: end_cost(d, snap_b) for d, i in g.near(*b, END_R, END_K)}
    best, best_node = direct * JUMP_COST, None
    gs: dict[int, float] = {}
    prev: dict[int, tuple[int, int]] = {}  # node -> (previous node or -1, edge or -1)
    heap: list[tuple[float, float, int]] = []
    for d, i in start:
        c = end_cost(d, snap_a)
        if c < gs.get(i, math.inf):
            gs[i] = c
            prev[i] = (-1, -1)
            heapq.heappush(heap, (c + dist(g.lon[i], g.lat[i], *b), c, i))
    pops = 0
    while heap and pops < MAX_POP:
        f, c, n = heapq.heappop(heap)
        if f >= best:
            break
        if c > gs.get(n, math.inf):
            continue
        pops += 1
        if n in goal and c + goal[n] < best:
            best, best_node = c + goal[n], n
        for nb, w, e in g.adj[n]:
            c2 = c + w
            if c2 < gs.get(nb, math.inf):
                gs[nb] = c2
                prev[nb] = (n, e)
                heapq.heappush(heap, (c2 + dist(g.lon[nb], g.lat[nb], *b), c2, nb))
    if best_node is None:
        return [_jump(a, b)]
    steps = []
    n = best_node
    while True:
        p, e = prev[n]
        if p < 0:
            break
        steps.append((p, e))
        n = p
    steps.reverse()
    first = (g.lon[n], g.lat[n])
    last = (g.lon[best_node], g.lat[best_node])
    segs = [_jump(a, first)] if dist(*a, *first) >= 1 else []
    for p, e in steps:
        kv = g.kv[e]
        s = {"kind": "jump", "kv": None, "coords": g.coords(e, p), "m": g.m[e]} if kv < 0 else \
            {"kind": "line", "kv": kv, "coords": g.coords(e, p), "m": g.m[e]}
        if kv < 0 and s["m"] < JOIN_M and segs and segs[-1]["kind"] == "line":
            s["kind"], s["kv"] = "line", segs[-1]["kv"]  # a few metres between two ends of one line
        if segs and segs[-1]["kind"] == s["kind"] and segs[-1]["kv"] == s["kv"]:
            segs[-1]["coords"] += s["coords"][1:]
            segs[-1]["m"] += s["m"]
        else:
            segs.append(s)
    if dist(*last, *b) >= 1:
        j = _jump(last, b)
        if segs and segs[-1]["kind"] == "jump":
            segs[-1]["coords"].append(list(b))
            segs[-1]["m"] += j["m"]
        else:
            segs.append(j)
    return segs


class Router:
    """Lazy per-(operator, band) graph cache over data/derived/routes. Reloads after a rebuild."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "derived" / "routes"
        self.lock = threading.Lock()
        self.graphs: dict[tuple[str, str], Graph | None] = {}
        self.version = 0.0

    def graph(self, op: str, b: str) -> Graph | None:
        with self.lock:
            m = self.dir / "manifest.json"
            v = m.stat().st_mtime if m.exists() else 0.0
            if v != self.version:
                self.graphs.clear()
                self.version = v
            if (op, b) not in self.graphs:
                n, e = self.dir / f"{op}_{b}.nodes.parquet", self.dir / f"{op}_{b}.edges.parquet"
                self.graphs[(op, b)] = Graph(pl.read_parquet(n), pl.read_parquet(e)) if n.exists() and e.exists() \
                    else None
            return self.graphs[(op, b)]

    def hop(self, op: str, bands: tuple[str, ...], a: tuple[float, float], b: tuple[float, float],
            snap_a: float, snap_b: float) -> list[dict]:
        """Route on the first band that has any line on the path; else the straight jump."""
        segs: list[dict] = []
        for bd in bands:
            segs = route(self.graph(op, bd), a, b, snap_a, snap_b)
            if any(s["kind"] == "line" for s in segs):
                return segs
        return segs
