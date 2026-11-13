"""Offline street / place / postcode search over data/derived/places.parquet (built by gridmap.build_places).

Loaded once into plain Python lists: names sorted by normalised key (bisect prefix range, then a token filter on
the remaining words as context), postcodes in a dict (exact) plus a sorted compact list (partial prefix)."""

from __future__ import annotations

import re
from bisect import bisect_left
from pathlib import Path

ABBR = {"rd": "road", "st": "street", "ave": "avenue", "av": "avenue", "ln": "lane", "dr": "drive", "cl": "close",
        "ct": "court", "gdns": "gardens", "gdn": "garden", "cres": "crescent", "pl": "place", "sq": "square",
        "ter": "terrace", "grn": "green", "pk": "park", "hts": "heights", "bvd": "boulevard", "mt": "mount",
        "gr": "grove", "wy": "way", "cr": "crescent"}
PC_FULL = re.compile(r"^([a-z]{1,2}\d[a-z\d]?)(\d[a-z]{2})$")
PC_LIKE = re.compile(r"^[a-z]{1,2}\d[a-z\d]?\d?[a-z]{0,2}$")
SCAN_CAP = 4000  # candidates examined per prefix range
_NON = re.compile(r"[^a-z0-9]+")


def norm_key(s: str) -> str:
    """Lowercase, punctuation-free, abbreviation-expanded key (apostrophes dropped: St John's -> st johns)."""
    s = s.lower().replace("'", "").replace("’", "")
    return " ".join(ABBR.get(t, t) for t in _NON.sub(" ", s).split())


def pc_compact(s: str) -> str:
    return _NON.sub("", s.lower())


def pc_display(compact: str) -> str:
    c = compact.upper()
    return c[:-3] + " " + c[-3:]


class Places:
    def __init__(self, path: Path):
        import polars as pl
        df = pl.read_parquet(path)
        pcs = df.filter(pl.col("kind") == "postcode")
        names = df.filter(pl.col("kind") != "postcode")  # file is sorted by (key, rank): order is preserved
        self.n_by_kind = {k: v for k, v in df.group_by("kind").len().iter_rows()}
        self.keys = names["key"].to_list()
        self.rows = list(zip(names["id"].to_list(), names["name"].to_list(), names["kind"].to_list(),
                             names["lat"].to_list(), names["lon"].to_list(), names["context"].to_list(),
                             names["ckey"].to_list(), names["rank"].to_list(), strict=True))
        pk = pcs["key"].to_list()
        order = sorted(range(len(pk)), key=pk.__getitem__)
        pn, pla, plo, pctx = (pcs[c].to_list() for c in ("name", "lat", "lon", "context"))
        self.pc_keys = [pk[i] for i in order]
        self.pc_rows = [(pn[i], pla[i], plo[i], pctx[i]) for i in order]
        self.pc_index = {k: i for i, k in enumerate(self.pc_keys)}

    def _pc(self, i: int) -> dict:
        name, lat, lon, ctx = self.pc_rows[i]
        return {"type": "place", "id": "pc:" + self.pc_keys[i], "name": name, "op": None, "kind": "postcode",
                "lat": lat, "lon": lon, "context": ctx}

    def _place(self, r) -> dict:
        return {"type": "place", "id": r[0], "name": r[1], "op": None, "kind": r[2], "lat": r[3], "lon": r[4],
                "context": r[5]}

    def search(self, q: str, limit: int = 8) -> list[dict]:
        q = q.strip()
        if len(q) < 2 or limit <= 0:
            return []
        compact = pc_compact(q)
        out: list[dict] = []
        parts = q.split()
        if PC_LIKE.match(compact) and len(parts) <= 2:
            i = self.pc_index.get(compact)
            if i is not None:  # exact full postcode
                out.append(self._pc(i))
            elif len(compact) >= 3:  # partial: "OX1 4" or "ox1"
                j, end = bisect_left(self.pc_keys, compact), 0
                outward = len(pc_compact(parts[0])) if len(parts) == 2 else 0  # "m1 1" must not match M11 1AA
                while (j < len(self.pc_keys) and self.pc_keys[j].startswith(compact) and len(out) < limit
                       and end < SCAN_CAP):
                    if not outward or len(self.pc_keys[j]) - 3 == outward:
                        out.append(self._pc(j))
                    j, end = j + 1, end + 1
            if len(out) >= limit or (out and PC_FULL.match(compact)):
                return out[:limit]
        toks = norm_key(q).split()
        while len(toks) > 1 and re.fullmatch(r"\d+[a-z]?", toks[0]):  # house numbers: no address-level data, ignore
            toks.pop(0)
        if not toks:
            return out
        seen: set[str] = set()
        for split in range(len(toks), 0, -1):  # longest name prefix first, the rest is context words
            if len(out) >= limit:
                break
            prefix, ctx = " ".join(toks[:split]), toks[split:]
            lo = bisect_left(self.keys, prefix)
            hi = bisect_left(self.keys, prefix + "￿", lo)
            cands = []
            for k in range(lo, min(hi, lo + SCAN_CAP)):
                r = self.rows[k]
                if ctx:
                    words = r[6].split()
                    if not all(any(w.startswith(c) for w in words) for c in ctx):
                        continue
                cands.append((self.keys[k] != prefix, r[7], len(self.keys[k]), k))
            cands.sort()
            for *_, k in cands:
                r = self.rows[k]
                if r[0] not in seen:
                    seen.add(r[0])
                    out.append(self._place(r))
                    if len(out) >= limit:
                        break
        return out


_cache: tuple[float, Places] | None = None


def get(path: Path) -> Places | None:
    """Process-wide Places, loaded on first use and reloaded when the parquet changes; None if not built."""
    global _cache
    try:
        m = path.stat().st_mtime
    except OSError:
        return None
    if _cache is None or _cache[0] != m:
        _cache = (m, Places(path))
    return _cache[1]
