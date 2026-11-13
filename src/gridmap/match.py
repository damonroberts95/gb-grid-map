"""P3 matching: cluster operator assets into physical sites, resolve parentage, link fault levels
and queue projects to sites. Pure polars over data/norm parquet; writes data/derived/*.parquet.

Outputs
  site           one row per physical site (site_id stable across runs: "<OP>:<name_norm>[~n]")
  asset_site     asset_uid -> site_id
  site_capacity  headline headroom per site (from the asset rows of the site's own kind)
  site_fault     worst-case fault level / headroom per site
  site_queue     queued / connected MW per site from ECR + queue sources
  queue_site     project_uid -> site_id (for the per-site project list)
  match_report   per operator/dataset link rates (also written as data/reports/match.md)
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import polars as pl
from rapidfuzz import fuzz

from .normalise.common import DATA_DIR

NORM = DATA_DIR / "norm"
OUT = DATA_DIR / "derived"
KIND_RANK = {"TRANSMISSION": 0, "GSP": 1, "BSP": 2, "PRIMARY": 3, "OTHER": 4}

SAME_NAME_KM = 3.0      # same normalised name within this distance (or no coords) -> same site
NEAR_M = 150.0          # different names this close ...
NEAR_FUZZ = 70          # ... and this similar -> same site

# ---------- name normalisation ----------

_STRIP = [
    r"\(\s*\d{6}\s*\)",                                   # ENW "(123456)"
    r"\([^)]*\bg?t\d[^)]*\)",                              # "(T1B & T2B)"
    r"\b\d+(\.\d+)?\s*(/\s*\d+(\.\d+)?)*\s*k\s*v\b",       # 33kV, 33/11kV, 132/33/11 KV
    r"\b\d+(\.\d+)?\s*kv\b",
    r"\bs\.?\s*g\.?\s*p\.?(?=\s|$)", r"\bs\.?\s*stn\b", r"\bs/stn\b", r"\bs/s\b", r"\bsub\s*station\b",
    r"\bsubstation\b", r"\bsub\b", r"\bprimary\b", r"\bpry\b", r"\bbsp\b", r"\bgsp\b", r"\bgrid\b",
    r"\bsupergrid\b", r"\bsupply point\b", r"\bbulk\b", r"\bg?t\d+[a-z]?\b", r"\bno\.?\s*\d+\b",
    r"\b(11|33|66|132|275|400|6\.6)\b",
]
_STRIP_RE = [re.compile(p) for p in _STRIP]


def name_norm(s: str | None) -> str:
    if not s:
        return ""
    t = s.lower().replace("&", " and ").replace("'", "")
    for r in _STRIP_RE:
        t = r.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"(\s+and)+\s*$", "", t)            # dangling 'and' after stripping
    return re.sub(r"\s+", " ", t).strip()


def base_name(t: str) -> str:
    """Drop a trailing single-letter section suffix (Finsbury Market B -> finsbury market) for ids only."""
    return re.sub(r"\s+[a-f]$", "", t)


_GENERIC = re.compile(r"\b(st|street|rd|road|ln|lane|ave|avenue|pl|place|way|north|south|east|west|"
                      r"upper|lower|new|old|park|green|hill|local|town|the|of|and|[a-z])\b")


def similar(a: str, b: str) -> int:
    """Name similarity ignoring generic street/direction words, so 'Victoria St' != 'Cannon St'."""
    a2, b2 = _GENERIC.sub(" ", a).split(), _GENERIC.sub(" ", b).split()
    if not a2 or not b2:
        return 0
    return int(fuzz.token_sort_ratio(" ".join(a2), " ".join(b2)) if len(a2) == len(b2)
               else max(fuzz.partial_ratio(" ".join(a2), " ".join(b2)) - 10, 0))


def km(a_lat, a_lon, b_lat, b_lon) -> float:
    x = math.radians(b_lon - a_lon) * math.cos(math.radians((a_lat + b_lat) / 2))
    y = math.radians(b_lat - a_lat)
    return 6371.0 * math.hypot(x, y)


# ---------- union-find ----------

class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


# ---------- loading ----------

def _read(table: str) -> pl.DataFrame:
    files = sorted(NORM.glob(f"*/{table}.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")


_ENW_SUFFIX = re.compile(r"\s+(pry|primary|bsp|gsp|grid|s/stn|sub)\.?$", re.IGNORECASE)
ENW_GSP_NESO_KM = 1.0   # an unnamed ENW GSP within this of a NESO GSP takes its name
ENW_BSP_PRIMARY_M = 120.0  # an unnamed ENW BSP this close to a named primary shares its plot and name


def _enw_names(queue: pl.DataFrame, site_names: pl.DataFrame | None = None,
               asset: pl.DataFrame | None = None) -> dict[str, str]:
    """ENW publishes numbers only. Names, best source first (asset-name -> display name):
      1. ECR fields 'NAME (123456)'
      2. flexibility-tender site lists (number + substation_name), via normalise.enw site_name table
      3. GSPs: nearest NESO GSP within ENW_GSP_NESO_KM (NESO's published GSP names)
      4. BSPs: a named primary within ENW_BSP_PRIMARY_M (same plot; ENW BSPs sit beside a same-named primary)
    Keys are 6-digit numbers (1, 2) or whole asset names such as '400240_400241' (3, 4)."""
    out: dict[str, str] = {}
    if not queue.is_empty():
        q = queue.filter(pl.col("operator") == "ENW")
        for col in ("primary_name", "bsp_name", "gsp_name", "connection_point"):
            for v in q[col].drop_nulls().unique().to_list():
                m = re.match(r"^(.*?)\s*\((\d{6})\)\s*$", v)
                if m and m.group(1).strip():
                    out.setdefault(m.group(2), m.group(1).strip().title())
    if site_names is not None and not site_names.is_empty():
        for num, name in site_names.select("number", "name").iter_rows():
            out.setdefault(num, _ENW_SUFFIX.sub("", name.strip()).title())
    if asset is None or asset.is_empty():
        return out

    def known(nm: str) -> str | None:
        return next((out[p] for p in re.findall(r"\d{6}", nm) if p in out), None)

    rows = [r for r in asset.filter((pl.col("operator") == "ENW") & pl.col("name").is_not_null()).to_dicts()]
    neso = [r for r in asset.filter(pl.col("operator") == "NESO").to_dicts() if r["lat"] is not None]
    for r in rows:
        if r["kind"] == "GSP" and r["lat"] is not None and known(r["name"]) is None and neso:
            best = min(neso, key=lambda c: km(r["lat"], r["lon"], c["lat"], c["lon"]))
            if km(r["lat"], r["lon"], best["lat"], best["lon"]) <= ENW_GSP_NESO_KM:
                out[r["name"]] = best["name"]
    named_pry = [(r, known(r["name"])) for r in rows if r["kind"] == "PRIMARY" and r["lat"] is not None]
    named_pry = [(r, n) for r, n in named_pry if n]
    for r in rows:
        if r["kind"] == "BSP" and r["lat"] is not None and known(r["name"]) is None and r["name"] not in out:
            near = [(km(r["lat"], r["lon"], p["lat"], p["lon"]) * 1000, n) for p, n in named_pry]
            if near and (d := min(near))[0] <= ENW_BSP_PRIMARY_M:
                out[r["name"]] = d[1]
    return out


# ---------- clustering ----------

def cluster_sites(asset: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    a = asset.filter(pl.col("kind") != "SECONDARY").with_row_index("i")
    rows = a.to_dicts()
    norms = [name_norm(r["display_name"]) for r in rows]
    dsu = DSU(len(rows))

    by_op_name: dict[tuple[str, str], list[int]] = defaultdict(list)
    grid: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for r, n in zip(rows, norms, strict=True):
        if n:
            by_op_name[(r["operator"], n)].append(r["i"])
        if r["lat"] is not None:
            grid[(r["operator"], int(r["lat"] * 100), int(r["lon"] * 60))].append(r["i"])  # ~1.1 km cells

    # 1) same operator + same normalised name, close together (or either lacks coords)
    for idx in by_op_name.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                p, q = rows[idx[x]], rows[idx[y]]
                if p["lat"] is None or q["lat"] is None or km(p["lat"], p["lon"], q["lat"], q["lon"]) <= SAME_NAME_KM:
                    dsu.union(idx[x], idx[y])

    # 2) same operator, very close, similar names (e.g. "X Grid" + "X Primary" on one plot)
    for (op, gy, gx), idx in grid.items():
        cand = [j for dy in (-1, 0, 1) for dx in (-1, 0, 1) for j in grid.get((op, gy + dy, gx + dx), [])]
        for i in idx:
            for j in cand:
                if j <= i:
                    continue
                p, q = rows[i], rows[j]
                if km(p["lat"], p["lon"], q["lat"], q["lon"]) * 1000 <= NEAR_M and (
                    similar(norms[i], norms[j]) >= NEAR_FUZZ
                ):
                    dsu.union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        groups[dsu.find(r["i"])].append(r["i"])

    sites, links, used_ids = [], [], defaultdict(int)
    for members in groups.values():
        ms = [rows[i] for i in members]
        top = min(ms, key=lambda r: (KIND_RANK.get(r["kind"], 9), r["display_name"] is None))
        op = top["operator"]
        bases = [base_name(name_norm(r["display_name"])) for r in ms if r["kind"] == top["kind"]]
        bases = [b for b in bases if b]
        base = (max(sorted(set(bases), key=len), key=bases.count) if bases
                else top["asset_uid"].split(":", 1)[1].lower())
        used_ids[(op, base)] += 1
        sid = f"{op}:{base}" if used_ids[(op, base)] == 1 else f"{op}:{base}~{used_ids[(op, base)]}"
        pts = [(r["lat"], r["lon"]) for r in ms if r["lat"] is not None]
        lat = sorted(p[0] for p in pts)[len(pts) // 2] if pts else None
        lon = sorted(p[1] for p in pts)[len(pts) // 2] if pts else None
        kvs = sorted({v for r in ms for v in (r["kv_hv"], r["kv_lv"]) if v}, reverse=True)
        sites.append({
            "site_id": sid, "operator": op, "licence_area": top["licence_area"],
            "name": top["display_name"] or sid, "kind": top["kind"],
            "kinds": ",".join(sorted({r["kind"] for r in ms}, key=lambda k: KIND_RANK.get(k, 9))),
            "kv_max": kvs[0] if kvs else None, "kv_min": kvs[-1] if kvs else None,
            "kv_list": ",".join(f"{v:g}" for v in kvs),
            "lat": lat, "lon": lon, "n_assets": len(ms),
            "upstream_gsp": next((r["upstream_gsp"] for r in ms if r["upstream_gsp"]), None),
            "upstream_bsp": next((r["upstream_bsp"] for r in ms if r["upstream_bsp"]), None),
        })
        links += [{"asset_uid": r["asset_uid"], "site_id": sid} for r in ms]
    return pl.DataFrame(sites), pl.DataFrame(links)


# ---------- loose name variants (fault / queue / parent text only) ----------

_JUNK_TEXT = {"", "redacted", "#n/a", "n/a", "na", "tbc", "none", "null", "-", "unknown"}
_LOOSE_DROP = re.compile(r"\b(sws|switching station|switchgear|swgr|sw stn|switchroom|switch room|"
                         r"gsp group|group|local|connection node [a-z]|sgts?|(33|11|132)\s*kv)\b")


def loose_variants(text: str) -> list[str]:
    """Normalised spellings of a free-text site reference with operator noise removed. Used only as a
    fallback after strict matching fails ('GIFFNOCK GSP A1' -> 'giffnock', 'Tynemouth 1' -> 'tynemouth',
    'LYTHAM (BASI)' -> 'lytham', 'West Melton 1 & 2' -> 'west melton')."""
    t = re.sub(r"\([^)]*\)", " ", text or "")
    t = re.sub(r"\bat or near\b.*$|\bx:\d+.*$", " ", t, flags=re.IGNORECASE)
    t = _LOOSE_DROP.sub(" ", t.lower())
    outs = []
    for v in (t, re.sub(r"\s+\d+(\s*(&|and|,)\s*\d+)*\s*$", " ", t), re.sub(r"\s+[a-z]\d?\s*$", " ", t)):
        n = name_norm(v)
        n = re.sub(r"\s+\d+(\s*(and|,)\s*\d+)*$", "", n)  # trailing numbers survive name_norm
        if n and n not in outs:
            outs.append(n)
    return outs


# ---------- name index for linking ----------

class NameIndex:
    """Resolve (operator, free-text name, optional kind) -> site_id."""

    FUZZY_MIN = 88
    FUZZY_KM = 60.0

    LOOSE_MIN = 92   # global (no coordinates) fuzzy threshold for resolve_loose

    def __init__(self, sites: pl.DataFrame, extra: dict[tuple[str, str], str] | None = None,
                 alias: dict[str, str] | None = None):
        self.by: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.by_op: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        for s in sites.to_dicts():
            n = name_norm(s["name"])
            self.by[(s["operator"], n)].append(s)
            self.by[(s["operator"], base_name(n))].append(s)
            self.by_op[s["operator"]].append((n, s))
        self.extra = extra or {}  # (operator, key) -> site_id for id-based aliases (ENW numbers)
        self.alias = alias or {}  # merged-away site_id (NESO GSP) -> surviving site_id
        self._loose_cache: dict[tuple, str | None] = {}

    def _pick(self, cands: list[dict], kinds: tuple[str, ...], near: tuple | None,
              exclude: str | None = None, strict_kind: bool = False) -> str | None:
        cands = [c for c in cands if self.alias.get(c["site_id"], c["site_id"]) != exclude]
        if kinds:
            of_kind = [c for c in cands if c["kind"] in kinds]
            cands = of_kind if (of_kind or strict_kind) else cands
        if not cands:
            return None
        if near and near[0] is not None and len(cands) > 1:
            cands = sorted(cands, key=lambda c: km(near[0], near[1], c["lat"], c["lon"]) if c["lat"] else 1e9)
        return self.alias.get(cands[0]["site_id"], cands[0]["site_id"])

    def resolve(self, op: str, text: str | None, kinds: tuple[str, ...] = (),
                near: tuple | None = None, exclude: str | None = None) -> str | None:
        """exclude: a site_id never returned (a site is not its own parent)."""
        if not text:
            return None
        if m := re.search(r"\((\d{6})\)\s*$", text) or re.fullmatch(r"\s*(\d{6})\s*", text):
            if (op, m.group(1)) in self.extra and self.extra[(op, m.group(1))] != exclude:
                return self.extra[(op, m.group(1))]
        elif re.fullmatch(r"[\d_ ]+", text) and (nums := re.findall(r"\d{6}", text)):   # '609600_609000'
            for n in nums:
                if (op, n) in self.extra and self.extra[(op, n)] != exclude:
                    return self.extra[(op, n)]
        n = name_norm(text)
        if not n:
            return None
        ops = (op, "NESO") if (not kinds or "GSP" in kinds) else (op,)
        # exact name: the right kind in any allowed operator first, then any kind
        for strict in (True, False):
            for o in ops:
                hit = self._pick(self.by.get((o, n), []) + self.by.get((o, base_name(n)), []), kinds, near,
                                 exclude, strict)
                if hit:
                    return hit
        # fuzzy fallback: same operator (or NESO for GSPs), right kind, within FUZZY_KM of the child
        if near is None or near[0] is None:
            return None
        best, best_score = None, self.FUZZY_MIN
        for o in ops:
            for cn, c in self.by_op.get(o, []):
                if kinds and c["kind"] not in kinds or c["lat"] is None:
                    continue
                if self.alias.get(c["site_id"], c["site_id"]) == exclude:
                    continue
                if km(near[0], near[1], c["lat"], c["lon"]) > self.FUZZY_KM:
                    continue
                sc = similar(n, cn)
                if sc > best_score:
                    best, best_score = c["site_id"], sc
        return self.alias.get(best, best) if best else None

    def resolve_loose(self, op: str, text: str | None, kinds: tuple[str, ...] = (),
                      near: tuple | None = None, exclude: str | None = None) -> str | None:
        """resolve(), then progressively looser: multi-name lists ('A T1 / B T2' -> first that resolves),
        noise-stripped variants (SWS, 'Switching Station', '(BASI)', trailing '1 & 2', 'A1'), then a
        no-coordinate fuzzy match at LOOSE_MIN. Cached; for fault/queue/parent text, not for site clustering."""
        key = (op, text, kinds, near, exclude)
        if key in self._loose_cache:
            return self._loose_cache[key]
        hit = self._resolve_loose(op, text, kinds, near, exclude)
        self._loose_cache[key] = hit
        return hit

    def _resolve_loose(self, op, text, kinds, near, exclude=None):
        if not text or text.strip().lower() in _JUNK_TEXT or text.lstrip().startswith("***"):
            return None
        if hit := self.resolve(op, text, kinds, near, exclude):
            return hit
        parts = [p for p in re.split(r"\s+/\s+", text) if p.strip()]
        if len(parts) > 1:
            for part in parts:
                if hit := self._resolve_loose(op, part, kinds, near, exclude):
                    return hit
            return None
        ops = (op, "NESO") if (not kinds or "GSP" in kinds) else (op,)
        for v in loose_variants(text):
            if v and v != name_norm(text):
                for o in ops:
                    if hit := self._pick(self.by.get((o, v), []) + self.by.get((o, base_name(v)), []), kinds, near,
                                         exclude, True):
                        return hit
        best, best_score, tie = None, self.LOOSE_MIN - 1, False
        for v in loose_variants(text) or [name_norm(text)]:
            if len(v) < 4:
                continue
            for o in ops:
                for cn, c in self.by_op.get(o, []):
                    if kinds and c["kind"] not in kinds:
                        continue
                    if self.alias.get(c["site_id"], c["site_id"]) == exclude:
                        continue
                    if near and near[0] is not None and c["lat"] is not None and \
                            km(near[0], near[1], c["lat"], c["lon"]) > self.FUZZY_KM:
                        continue
                    sc = similar(v, cn)
                    if sc > best_score:
                        best, best_score, tie = c["site_id"], sc, False
                    elif sc == best_score and best != c["site_id"]:
                        tie = True
        return None if (best is None or tie) else self.alias.get(best, best)


# ---------- DNO GSP <-> NESO GSP merge ----------

MERGE_KM = 2.0          # a DNO GSP/BSP this close to a NESO GSP with a matching name is the same plant
MERGE_NUMERIC_KM = 0.25  # nameless (ENW number-only) DNO sites merge on proximity alone
_MERGE_STOP = {"main", "local", "gis", "power", "station", "national", "site", "switching", "spen", "farm",
               "sgp", "wf", "wind", "solar", "park"}


def _gsp_tokens(name: str | None) -> set[str]:
    t = _GENERIC.sub(" ", name_norm(name) if name else "")
    return {w for w in t.split() if len(w) >= 3 and w not in _MERGE_STOP}


def _lower_name(name: str | None) -> str:
    # NESO adds the alternative name in brackets: 'Perivale (Willesden 66kV)' -> both tokens count
    return re.sub(r"[()]", " ", name or "")


def merge_neso_gsps(site: pl.DataFrame, asset_site: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Fold NESO GSP sites into the DNO site that is the same physical GSP, so search/map show one site.

    A DNO GSP or BSP matches a NESO GSP when within MERGE_KM and the names agree (equal, similar>=85, or a
    shared significant word incl. NESO's bracketed alternative name), or within MERGE_NUMERIC_KM when the
    DNO site has no real name. Best candidate: name score, then GSP-kind, then distance. Several NESO
    GSPs may fold into one DNO site (Willesden). The surviving site keeps the DNO id/name, gains
    kinds='GSP,...' and kind='GSP', and lists the NESO ids in alias_ids/alias_names.
    Returns (site, asset_site, alias table[alias_id, alias_name, site_id]); asset rows of merged NESO
    sites are re-pointed at the survivor."""
    rows = site.to_dicts()
    dno = [r for r in rows if r["operator"] != "NESO" and r["kind"] in ("GSP", "BSP") and r["lat"] is not None]
    cells: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for d in dno:
        cells[(int(d["lat"] * 50), int(d["lon"] * 30))].append(d)   # ~2.2 km cells
    alias: dict[str, dict] = {}
    for n in rows:
        if n["operator"] != "NESO" or n["lat"] is None:
            continue
        ntok = _gsp_tokens(_lower_name(n["name"]))
        nn = name_norm(n["name"])
        best, best_key = None, None
        cy, cx = int(n["lat"] * 50), int(n["lon"] * 30)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for d in cells.get((cy + dy, cx + dx), []):
                    dist = km(n["lat"], n["lon"], d["lat"], d["lon"])
                    if dist > MERGE_KM:
                        continue
                    dn = name_norm(d["name"])
                    numeric = not dn or bool(re.fullmatch(r"(enw )?[\d_ ]+", dn))
                    if dn and dn == nn:
                        score = 3
                    elif not numeric and (similar(nn, dn) >= 85 or ntok & _gsp_tokens(d["name"])):
                        score = 2
                    elif numeric and dist <= MERGE_NUMERIC_KM:
                        score = 1
                    else:
                        continue
                    key = (score, d["kind"] == "GSP", -dist)
                    if best_key is None or key > best_key:
                        best, best_key = d, key
        if best is not None:
            alias[n["site_id"]] = {"alias_id": n["site_id"], "alias_name": n["name"], "site_id": best["site_id"]}
    if not alias:
        return site, asset_site, pl.DataFrame(schema={"alias_id": pl.Utf8, "alias_name": pl.Utf8, "site_id": pl.Utf8})
    by_target: dict[str, list[dict]] = defaultdict(list)
    for a in alias.values():
        by_target[a["site_id"]].append(a)
    out = []
    for r in rows:
        if r["site_id"] in alias:
            continue
        al = by_target.get(r["site_id"])
        if al:
            kinds = sorted({*r["kinds"].split(","), "GSP"}, key=lambda k: KIND_RANK.get(k, 9))
            r = {**r, "kind": "GSP", "kinds": ",".join(kinds), "n_assets": r["n_assets"] + len(al)}
        r = {**r, "alias_ids": ",".join(a["alias_id"] for a in al) if al else None,
             "alias_names": " | ".join(a["alias_name"] for a in al) if al else None}
        out.append(r)
    remap = {k: v["site_id"] for k, v in alias.items()}
    links = asset_site.with_columns(pl.col("site_id").replace(remap))
    return pl.DataFrame(out, infer_schema_length=None), links, pl.DataFrame(list(alias.values()))


# ---------- fault-level linking ----------

# Operators whose LTDS node code is a verified cross-table key (UKPN: same code+kV in T3a/heatmap/T4a-b).
# NGED/SPEN nodes are reused for taps, poles and customer connections: not safe.
NODE_KEY_OPERATORS = {"UKPN"}

def link_faults(fault: pl.DataFrame, asset_site: pl.DataFrame, idx: NameIndex) -> pl.DataFrame:
    """Add site_id (+ link_tier) to fault rows. Fault tables carry no coordinates, so proximity cannot be
    used directly; tiers, strongest first:
      asset   asset_uid from the adapter is a known asset
      name    strict normalised-name match within the operator
      node    the same LTDS node code (e.g. 'SHER11') already linked to a site in another table
      loose   noise-stripped / fuzzy name match (resolve_loose), unique best only
    """
    f = fault.join(asset_site, on="asset_uid", how="left").with_columns(
        pl.when(pl.col("site_id").is_not_null()).then(pl.lit("asset")).alias("link_tier"))
    pairs = f.select("operator", "site_name").unique().to_dicts()
    strict = {(r["operator"], r["site_name"]): idx.resolve(r["operator"], r["site_name"]) for r in pairs}
    loose: dict[tuple[str, str], str | None] = {}

    def strict_hit(r: dict) -> str | None:
        return strict.get((r["operator"], r["site_name"]))

    rows = f.select("operator", "site_name", "ltds_node", "site_id", "link_tier").to_dicts()
    for r in rows:
        if r["site_id"] is None and (h := strict_hit(r)):
            r["site_id"], r["link_tier"] = h, "name"
    node_sites: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        if r["site_id"] and r["ltds_node"] and r["operator"] in NODE_KEY_OPERATORS:
            node_sites[(r["operator"], r["ltds_node"].strip().upper())].add(r["site_id"])
    for r in rows:
        if r["site_id"] is None and r["ltds_node"] and r["operator"] in NODE_KEY_OPERATORS:
            cand = node_sites.get((r["operator"], r["ltds_node"].strip().upper()), set())
            if len(cand) == 1:
                r["site_id"], r["link_tier"] = next(iter(cand)), "node"
    for r in rows:
        if r["site_id"] is None:
            k = (r["operator"], r["site_name"])
            if k not in loose:
                loose[k] = idx.resolve_loose(r["operator"], r["site_name"])
            if loose[k]:
                r["site_id"], r["link_tier"] = loose[k], "loose"
    return f.drop("site_id", "link_tier").with_columns(
        pl.Series("site_id", [r["site_id"] for r in rows], pl.Utf8),
        pl.Series("link_tier", [r["link_tier"] for r in rows], pl.Utf8))


# ---------- coordinate repair ----------

# Sources whose coordinates were geocoded by name and can land on a same-named namesake elsewhere.
RELOCATE_SOURCES = {"ukpn_heatmap"}
NAMESAKE_KM = 5.0


def relocate_namesakes(asset: pl.DataFrame) -> pl.DataFrame:
    """For RELOCATE_SOURCES rows, pick among same-named assets (other sources, same operator) the
    location nearest the row's upstream GSP/BSP. Fixes e.g. UKPN heatmap 'Bury Grid 33kV' placed in
    Edmonton although it is fed from Bramford (Bury St Edmunds)."""
    ref = asset.filter(~pl.col("source_id").is_in(RELOCATE_SOURCES) & pl.col("lat").is_not_null())
    by_name: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for r in ref.select("operator", "display_name", "lat", "lon").iter_rows():
        by_name[(r[0], base_name(name_norm(r[1])))].append((r[2], r[3]))
    loc = {k: v[0] for k, v in by_name.items()}  # first location per name, used to place parents

    def parent_loc(op: str, txt: str | None):
        return loc.get((op, base_name(name_norm(txt)))) or loc.get(("NESO", base_name(name_norm(txt))))

    lats, lons, quals, moved = [], [], [], 0
    for r in asset.select("operator", "display_name", "lat", "lon", "coord_quality", "source_id",
                          "upstream_bsp", "upstream_gsp").iter_rows(named=True):
        la, lo, q = r["lat"], r["lon"], r["coord_quality"]
        if r["source_id"] in RELOCATE_SOURCES:
            cands = by_name.get((r["operator"], base_name(name_norm(r["display_name"]))), [])
            distinct = []
            for c in cands:
                if all(km(c[0], c[1], d[0], d[1]) > NAMESAKE_KM for d in distinct):
                    distinct.append(c)
            anchor = parent_loc(r["operator"], r["upstream_bsp"]) or parent_loc(r["operator"], r["upstream_gsp"])
            if distinct and anchor:
                best = min(distinct, key=lambda c: km(c[0], c[1], anchor[0], anchor[1]))
                d_best = km(best[0], best[1], anchor[0], anchor[1])
                d_cur = km(la, lo, anchor[0], anchor[1]) if la is not None else 1e9
                # move only when the namesake is clearly the better fit for this row's upstream feed
                if km(la or 0, lo or 0, best[0], best[1]) > NAMESAKE_KM and d_cur > 20 and d_cur > 2 * d_best:
                    la, lo, q, moved = best[0], best[1], "relocated_namesake", moved + 1
        lats.append(la)
        lons.append(lo)
        quals.append(q)
    print(f"relocate_namesakes: moved {moved} rows")
    return asset.with_columns(pl.Series("lat", lats, pl.Float64), pl.Series("lon", lons, pl.Float64),
                              pl.Series("coord_quality", quals, pl.Utf8))


# Queue sources that restate projects already in an ECR (aggregated per substation/fuel): linked and
# reported, but kept out of site_queue totals and per-site project lists so MW is not counted twice.
QUEUE_SUPPLEMENTARY = {"npg_ltds_app7_generation"}


# ---------- main ----------

def run() -> dict[str, int]:
    asset, capacity = _read("asset"), _read("capacity")
    fault, queue = _read("fault_level"), _read("queue_project")

    enw = _enw_names(queue, _read("site_name"), asset)
    enw_display = [enw.get(n) or next((enw[p] for p in re.findall(r"\d{6}", n or "") if p in enw), None)
                   for n in asset["name"].to_list()]
    asset = asset.with_columns(
        pl.when(pl.col("operator") == "ENW")
        .then(pl.Series(enw_display, dtype=pl.Utf8))
        .otherwise(pl.col("name"))
        .alias("display_name")
    )
    asset = relocate_namesakes(asset)
    site_all, asset_site = cluster_sites(asset)
    site, asset_site, site_alias = merge_neso_gsps(site_all, asset_site)
    alias_map = dict(site_alias.select("alias_id", "site_id").iter_rows())

    # id aliases: ENW 6-digit numbers (and compound '400240_400241' names) -> site
    num = asset.filter(pl.col("operator") == "ENW").select("asset_uid", "name").join(asset_site, on="asset_uid")
    extra = {("ENW", p): s for n, s in num.select("name", "site_id").iter_rows()
             for p in re.findall(r"\d{6}", n or "")}
    # the index keeps the pre-merge NESO sites (name lookups) and maps them to the surviving DNO site
    idx = NameIndex(site_all, extra, alias_map)

    # parentage: PRIMARY -> BSP (else GSP), BSP -> GSP
    def parent(r: dict, me: str) -> str | None:
        near = (r["lat"], r["lon"])
        bsp = r["upstream_bsp"]
        if bsp and "/" in bsp:  # SPEN lists several feeding GTs: "AINTREE GT1 / FORMBY GT2A"
            bsp = bsp.split("/")[0]
        if r["kind"] == "PRIMARY":
            return idx.resolve_loose(r["operator"], bsp, ("BSP",), near, me) or idx.resolve_loose(
                r["operator"], r["upstream_gsp"], ("GSP", "BSP"), near, me)
        if r["kind"] in ("BSP", "GSP") and r["operator"] != "NESO":
            return idx.resolve_loose(r["operator"], r["upstream_gsp"], ("GSP",), near, me)
        return None

    def parent_of(r: dict) -> str | None:
        return parent(r, r["site_id"])   # a site (e.g. a BSP merged with its own GSP) is never its own parent

    site = site.with_columns(pl.Series("parent_site_id", [parent_of(r) for r in site.to_dicts()], pl.Utf8))

    # headline capacity per site: asset rows whose kind == site kind; prefer most recent as_of, then most fields
    cap = (capacity.join(asset.select("asset_uid", "kind", "kv_lv"), on="asset_uid")
           .join(asset_site, on="asset_uid")
           .join(site.select("site_id", pl.col("kind").alias("site_kind")), on="site_id"))
    cap = cap.with_columns(
        (pl.col("kind") == pl.col("site_kind")).alias("own_kind"),
        pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int8) for c in
                           ("gen_headroom_mw", "demand_headroom_mw", "demand_headroom_mva", "firm_capacity_mva")]
                          ).alias("_filled"),
    )
    site_capacity = (
        cap.sort(["own_kind", "as_of", "_filled", "kv_lv"], descending=[True, True, True, False], nulls_last=True)
        .group_by("site_id", maintain_order=True)
        .agg(
            pl.col("gen_headroom_mw").drop_nulls().first(),
            pl.col("demand_headroom_mw").drop_nulls().first(),
            pl.col("demand_headroom_mva").drop_nulls().first(),
            pl.col("firm_capacity_mva").drop_nulls().first(),
            pl.col("gen_rag_native").drop_nulls().first(),
            pl.col("demand_rag_native").drop_nulls().first(),
            pl.col("upstream_constraint").drop_nulls().first(),
            pl.col("basis").drop_nulls().first(),
            pl.col("source_id").first(),
            pl.col("as_of").max(),
        )
    )

    # fault levels -> site (by asset_uid, else by site_name within operator)
    if not fault.is_empty():
        f = link_faults(fault, asset_site, idx)
        f = f.with_columns(
            (pl.col("rating_break_ka") - pl.col("fl_3ph_break_ka")).alias("break_headroom_ka"),
            (pl.col("rating_make_ka") - pl.col("fl_3ph_make_ka")).alias("make_headroom_ka"),
        )
        site_fault = (f.filter(pl.col("site_id").is_not_null()).group_by("site_id").agg(
            pl.col("fl_3ph_break_ka").max(), pl.col("fl_3ph_make_ka").max(),
            pl.col("fl_1ph_break_ka").max(), pl.col("rating_break_ka").max(), pl.col("rating_make_ka").max(),
            pl.col("break_headroom_ka").min(), pl.col("make_headroom_ka").min(),
        ))
        fault_linked = f.group_by("operator", "source_id").agg(
            pl.len().alias("rows"), pl.col("site_id").is_not_null().mean().alias("linked"))
        fault_tiers = f.group_by("operator", "link_tier").len().rename({"len": "rows"})
    else:
        site_fault, fault_linked, fault_tiers = pl.DataFrame(), pl.DataFrame(), pl.DataFrame()

    # queue -> site at each level present
    q = queue.to_dicts()
    for r in q:
        op = r["operator"]
        r["primary_site"] = idx.resolve_loose(op, r["primary_name"], ("PRIMARY",)) or (
            idx.resolve_loose(op, r["connection_point"], ("PRIMARY",)) if not r["bsp_name"] else None)
        r["bsp_site"] = idx.resolve_loose(op, r["bsp_name"], ("BSP",))
        r["gsp_site"] = idx.resolve_loose(op, r["gsp_name"], ("GSP",))
        r["any_site"] = (r["primary_site"] or r["bsp_site"] or r["gsp_site"]
                         or idx.resolve_loose(op, r["connection_point"]))
    qd = pl.DataFrame(q, infer_schema_length=None)
    qd_main = qd.filter(~pl.col("source_id").is_in(QUEUE_SUPPLEMENTARY))   # no double counting in totals
    site_queue = (qd_main.filter(pl.col("any_site").is_not_null()).group_by(pl.col("any_site").alias("site_id")).agg(
        pl.col("export_mw").filter(pl.col("status").is_in(["accepted", "offered"])).sum().alias("queued_mw"),
        pl.col("export_mw").filter(pl.col("status") == "connected").sum().alias("connected_mw"),
        pl.col("export_mw").filter(pl.col("status").is_in(["scoping", "other"])).sum().alias("other_mw"),
        pl.col("status").is_in(["accepted", "offered"]).sum().alias("queued_projects"),
        pl.len().alias("projects"),
    ))
    queue_linked = qd.group_by("operator", "source_id").agg(
        pl.len().alias("rows"), pl.col("any_site").is_not_null().mean().alias("linked"))

    # parentage report
    par = site.filter(pl.col("kind").is_in(["PRIMARY", "BSP"])).group_by("operator", "kind").agg(
        pl.len().alias("sites"), pl.col("parent_site_id").is_not_null().mean().alias("with_parent"),
        pl.col("lat").is_not_null().mean().alias("with_coords"))

    OUT.mkdir(parents=True, exist_ok=True)
    tables = {"site": site, "site_alias": site_alias, "asset_site": asset_site, "site_capacity": site_capacity,
              "site_fault": site_fault, "site_queue": site_queue,
              "queue_site": qd_main.filter(pl.col("any_site").is_not_null()).select(
                  "project_uid", pl.col("any_site").alias("site_id"))}
    for name, df in tables.items():
        if not df.is_empty():
            df.write_parquet(OUT / f"{name}.parquet")
    _report(asset, site, par, fault_linked, queue_linked, site_capacity, fault_tiers, site_alias)
    return {k: v.height for k, v in tables.items()}


def _report(asset, site, par, fault_linked, queue_linked, site_capacity, fault_tiers, site_alias) -> None:
    rep = DATA_DIR / "reports" / "match.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    a = asset.filter(pl.col("kind") != "SECONDARY").group_by("operator").len().rename({"len": "assets"})
    s = site.group_by("operator").agg(pl.len().alias("sites"))
    c = site.join(site_capacity, on="site_id", how="left").group_by("operator").agg(
        pl.col("gen_headroom_mw").is_not_null().mean().round(3).alias("gen_headroom"),
        (pl.col("demand_headroom_mw").is_not_null() | pl.col("demand_headroom_mva").is_not_null())
        .mean().round(3).alias("demand_headroom"))
    pl.Config.set_tbl_formatting("MARKDOWN")
    pl.Config.set_tbl_rows(100)
    with pl.Config(tbl_hide_dataframe_shape=True, tbl_hide_column_data_types=True):
        txt = "\n\n".join([
            "# Match report", "## Assets -> sites", str(a.join(s, on="operator").join(c, on="operator").sort("operator")),
            "## Parentage", str(par.sort("operator", "kind").with_columns(pl.col(pl.Float64).round(3))),
            "## Fault levels linked", str(fault_linked.sort("operator", "source_id").with_columns(pl.col("linked").round(3))),
            "## Queue linked", str(queue_linked.sort("operator", "source_id").with_columns(pl.col("linked").round(3))),
            "## Fault link tiers (rows)", str(fault_tiers.sort("operator", "link_tier", nulls_last=True)),
            "## NESO GSPs merged into DNO sites\n\n" + f"{site_alias.height} of "
            + f"{site.filter(pl.col('operator') == 'NESO').height + site_alias.height} NESO GSPs",
        ])
    rep.write_text(txt + "\n")


if __name__ == "__main__":
    for k, v in run().items():
        print(f"{k:14} {v:,}")
    print(Path(DATA_DIR / "reports" / "match.md").read_text())
