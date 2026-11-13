# Server ↔ web contract

Server: FastAPI on `http://localhost:8787` (`uv run uvicorn gridmap.server.app:app --port 8787`).
Web (Vite dev, :5173) proxies `/api` and `/data` to it. All JSON. No auth (local only, bind 127.0.0.1).

## Map data files (versioned, immutable, cache forever)

`GET /data/<file>`. Range requests MUST work (PMTiles). Filenames carry a content hash; the manifest says which is current.

### `sites.<hash>.geojson` — every GSP/BSP/PRIMARY site (+ NESO GSPs). Point features. `id` = numeric feature id (for feature-state).
Properties (short keys, keep the file small; null = not published):

| key | type | meaning |
|---|---|---|
| `sid` | str | site_id |
| `n` | str | display name |
| `op` | str | operator (UKPN, NPG, ENW, SPEN, NGED, SSEN, NESO) |
| `k` | int | kind: 1 GSP, 2 BSP, 3 PRIMARY (NESO GSP = 1) |
| `kv` | num | highest voltage at site |
| `g` | num | generation headroom MW |
| `d` | num | demand headroom (MW, or MVA when `du`=1) |
| `du` | int | 1 if `d` is MVA |
| `fh` | num | fault break headroom kA (rating − 3ph break), min over busbars |
| `q` | num | queued MW (accepted+offered) linked to this site |
| `c` | num | connected MW (ECR) linked to this site |
| `rg` | str | operator's native gen RAG (R/A/G) |
| `p` | int | feature id of parent site (for schematic lines), null if unknown |

### `links.<hash>.geojson` — schematic parent→child straight lines. LineString features, properties `k` (child kind), `op`.

### `lv.<hash>.pmtiles` — secondary substations, layer `lv`, minzoom 11, maxzoom 14 (overzoom above).
Properties: `id` (asset_uid), `op`, `u` (utilisation %, nullable), `kva` (transformer kVA), `cu` (customers), plus
`mt` (mount type: `"g"` ground, `"p"` pole; absent if unknown), `mti` (`1` when `mt` is inferred from kVA, absent when
published), `mon` (`1` when the site carries an LV monitor of any vendor, absent otherwise) and `ms` (monitor source:
`"ENW PreSense"`, `"SPEN"`, `"NPG"`, `"NGED"`, `"NeRDA"`; present only with `mon`).
`mt` is published by ENW (`situation`), UKPN (`substationdesign` PMT/GMT), NPG (`site_type`), NGED and SSEN (`type`);
otherwise inferred from kVA (<= 100 kVA pole, > 200 kVA ground, between: unknown). SPEN publishes neither mount nor kVA.
`mon`: ENW/SPEN/NGED lists by id, NPG installed monitors and SSEN NeRDA LV-feeder secondaries by nearest lv_site within 40 m.
Built with tippecanoe `-Z11 -z14 --drop-densest-as-needed --extend-zooms-if-still-dropping -r1`; no names in tiles.


Same sites as `lv` with `mon=1`, national at low zoom. Properties: `id`, `op`, `mt`, `mti`, `ms`, `u`, `kva`, `cu`
(same meaning as above; `mon` is omitted as it is always 1). Built with `-Z4 -z14 -r1 --no-feature-limit
--maximum-tile-bytes=500000 --drop-densest-as-needed` (tiles are thinned only if they exceed 500 KB). Absent from the
manifest when no monitor list is available.

## `GET /api/manifest`
```json
{ "version": 17,
  "layers": { "sites": "/data/sites.ab12.geojson", "links": "/data/links.cd34.geojson", "lv": "/data/lv.ef56.pmtiles", "lvmon": "/data/lvmon.9a8b.pmtiles" },
  "built_at": "2026-11-12T12:30:00Z",
  "refreshing": false }
```

## `GET /api/events` (SSE)
Events: `manifest` (data = manifest JSON) when any layer changes; `live` (data = `{"feed": "outages"|"national"}`) when a live feed refreshes; `status` (data = `{"stage": "fetch"|"normalise"|"match"|"build"|"idle", "detail": str}`).

## `GET /api/site/{site_id}`
```json
{ "site": {...site row...},
  "parent": {"site_id","name","kind"} | null,
  "chain": [{"site_id","name","kind","lat","lon"}],   // feed path upwards, nearest first (parent → … → GSP)
  "children": [{"site_id","name","kind","g","d"}],
  "assets": [{ "asset_uid","name","kind","kv_hv","kv_lv","source_id","as_of",
               "capacity": {...capacity row...} | null }],
  "faults": [{...fault_level row...}],
  "queue": { "by_status": {"connected": MW, "accepted": MW, ...}, "by_tech": {...}, "projects": [ up to 50 largest ] },
  "sources": [{"source_id","operator","licence","attribution","as_of","fetched_at"}] }
```

## `GET /api/lv/{asset_uid}` — one LV site row + `upstream_site_id`, `upstream_how` ("published" | "nearest" = inferred nearest primary) and `chain` (feed path from that primary upwards).

## `GET /api/search?q=text&limit=20`
Matches site names (and LV names when q >= 4 chars), then offline streets / places / postcodes. Returns
`[{"type":"site"|"lv","id","name","op","kind","lat","lon"} | {"type":"place","id","name","op":null,"kind":"road"|"postcode"|"place","lat","lon","context"}]`.
Substations come first; up to 5 slots are always reserved for places. Must be < 50 ms (places typically < 5 ms).

Place results come from `data/derived/places.parquet` (OS Open Names roads/places + Code-Point Open postcode units;
build with `uv run python -I -m gridmap.build_places`, search in `server/places.py`). Absent parquet = no place results.
- `id`: `on:<OS id>` (road/place) or `pc:<compactpostcode>` (e.g. `pc:ox14sz`); `context` is "place, postcode district" for roads,
  "borough, county" for places, the town for postcodes.
- Postcodes: full ("OX1 4SZ", "ox14sz") resolve exactly; partial ("OX1 4") prefix-match.
- Roads/places: prefix on the name, trailing words filter on context ("oxford rd manchester"); abbreviations (rd, st, ave...)
  are expanded; leading house numbers are ignored (no address-level data). Exact name matches rank first, then city > town > village > road.
- Attribution: "Contains OS data (c) Crown copyright and database right" (shown in the search dropdown).

## `GET /api/live/outages`
GeoJSON points of current outages across operators (UKPN NEO, NPG, NGED, SPEN, SSEN PowerTrack...). Properties: `op`, `ref`, `status`, `start`, `etr` (estimated restore), `cust` (customers affected), `kind` (planned|unplanned).

## `GET /api/live/national`
`{ "fuel_mix": [{"fuel","mw"}], "demand_mw", "frequency_hz", "frequency_at", "carbon": {"national": gCO2/kWh, "regions": [{"region","intensity","index"}]}, "as_of" }`

## `GET /api/sources`
Freshness table: latest `source_run` per source: id, operator, category, status, rows, as_of, fetched_at, cadence, licence, attribution.

## Overlay hook (P10)
`GET /api/overlay` lists overlays from `overlays/*.geojson|csv`; `GET /api/overlay/{name}` returns GeoJSON; rows carrying `site_id` or `asset_uid` are joined to our coordinates.

## `GET /api/stats`
Coverage statistics (our equivalent of griddata.uk/stats). Cached in memory, invalidated when the manifest `version` changes.
```json
{ "generated_at", "note",
  "headline": { "sites", "busbars", "coords_pct", "gen_pct", "demand_pct", "fault_pct", "switchgear_pct",
                "constraint_pct", "rag_pct", "coords_pct_incl_lv" },
  "dno": [ { "operator", "busbars", "sites", "coords_pct", "gen_pct", "demand_pct", "fault_pct", "switchgear_pct",
             "constraint_pct", "rag_pct", "site_coords_pct", "site_gen_pct", "site_demand_pct", "site_fault_pct" } ],
  "to": [ ...same row shape, operators that are not DNOs (currently NESO GSPs) ],
  "coord_quality": [ {"quality", "n"} ],
  "headroom_sources": { "generation": [ {"source_id", "n"} ], "demand": [ {"source_id", "n"} ] },
  "lv": { "sites", "coords_pct", "utilisation_pct", "by_operator": [ {"operator","sites","coords_pct","utilisation_pct"} ] },
  "freshness": { "by_operator": [ {"operator","sources","ok","fetched_at","as_of"} ], "sources": [ latest source_run per source ] },
  "transmission_assets_held": ["NESO"] }
```
`busbars` = `asset` rows, excluding LV. `*_pct` are percentages of busbars (asset rows); `site_*_pct` are of sites.
Fault and switchgear coverage link each asset to its matched site's `site_fault` (or a direct `fault_level` row).

## `GET /api/queue?op=&limit=100&scoping=false`
Substations ranked by queued MW. `queued` = DNO accepted+offered (from `site_queue`) + NESO TEC **Direct Connection**
projects that are under construction / consented / awaiting consent, matched to sites by name (reuses
`extra/gen_planning.match_site`). Embedded TEC rows are excluded because they are already in a DNO ECR. `connected` = DNO connected +
TEC Built. Scoping MW (DNO `other` + TEC Scoping) is excluded unless `scoping=true`. `op` filters by site operator.
```json
{ "op", "limit", "scoping", "tec_included",
  "totals": { "queued_gw", "connected_gw", "projects", "sites" },      // over the returned (top `limit`) sites
  "totals_all": { ... },                                               // over every ranked site
  "operators": ["ENW", ...],
  "sites": [ { "site_id","name","op","kind","kv","lat","lon","queued_mw","connected_mw","scoping_mw",
               "tec_queued_mw","tec_built_mw","projects","total_queued_mw","total_connected_mw" } ] }
```
