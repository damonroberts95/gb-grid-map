# Plan — GB Grid Map (local, live, light)

Status: **APPROVED 2026-11-12** — P0–P1 in progress. SSEN: user registering account. Keys: user creating UKPN, NPG, ENW, SPEN, NGED.
Source specs: `docs/sources/*.md`. Traps: `docs/AI_GOTCHAS.md`.

## Goals
- Whole-GB map of the network: GSP / BSP / Primary / Secondary-LV substations, with headroom, fault levels,
  firm capacity, connection queue, LV feeder usage, live outages and national live context.
- Runs on the laptop, offline basemap. **Live**: data refreshes on startup + per-source cadence, map updates in
  place with **no rebuild and no page reload**.
- **Light and fast**, especially when zoomed into LV (griddata.uk's weak point).
- Hook for the user's own LV monitor data later.

## Architecture

```
pipeline/ (Python 3.12, uv)          server/ (FastAPI, same venv)            web/ (Vite + TS + MapLibre)
 sources.yaml  ─ registry+cadence     scheduler: startup + per-source timers   basemap: Protomaps GB z0-14 PMTiles
 fetch/  opendatasoft | ckan | http  ─▶ fetch ▶ normalise ▶ match ▶ tiles     layers by zoom, filters = MapLibre exprs
 raw/    data/raw/<src>/<ts>/         atomic swap: layer-<hash>.pmtiles        click ▶ /api/site/:id (lazy)
 normalise/ per-DNO adapters ▶ DuckDB  /api/manifest  /api/events (SSE)  ─────▶ on SSE "layer X v2": setUrl, no reload
 match/  site clustering + id_map     /api/site/:id  /api/search            live panels poll /api/live/* (cached proxy)
 tiles/  tippecanoe per layer         /api/live/* (Elexon, carbon, outages)   overlay slot: /api/overlay/:name
```

- **Store:** one DuckDB file (`data/grid.duckdb`). Canonical tables:
  `substation` (id, dno, kind, name, name_norm, kv[], lat, lon, parent_id), `id_map` (source, source_id → substation_id,
  method, confidence), `capacity` (gen/demand headroom MW, firm MVA, basis, rag, source, as_of), `fault_level`
  (kv, 3ph/1ph make/break kA, ratings), `queue_project` (ECR/TEC, status, MW, tech), `lv_feeder_usage`, `lct`,
  `outage` (live), `national_ts` (live), `source_run` (fetch log: rows, status, as_of, licence).
- **Tiles:** one PMTiles per layer, built only for the layers whose source changed. Tile attributes are the minimum
  needed to render and filter: id, kind, kv, RAG, headroom band, numeric headroom, fault-level headroom, queued MW.
  - GSP / BSP: z4+.
  - Primary: z8+.
  - Secondary / LV: z12+ (shown as points until z14, with feeder data in the details only).
  - Polygons (GSP regions, primary service areas): their own layer, toggleable.
- **Live without rebuild:** the scheduler writes a new hashed tile file and updates `manifest.json`, then pushes an
  SSE event. The browser swaps the source URL and the old file is deleted after a grace period. Live point feeds
  (outages) are GeoJSON from the API, small enough not to need tiles.
- **Cadence (initial, tuned per source):** outages 5 min; Elexon FUELINST 5 min / frequency 30 s (proxied, browser
  polls only while the panel is open); carbon 30 min; heatmaps / ECR / queue daily check, using a hash of the portal
  `modified` so we only download on change; LTDS / DFES weekly check.

## Model allocation
- Haiku: scaffolding config, fixture snapshotting, sources.yaml entries, mechanical tests.
- Sonnet: per-DNO adapters (one agent per DNO, in parallel), fetchers, tile build, API endpoints.
- Opus (main): architecture, cross-DNO matching / clustering, headroom semantics, UI/UX design, perf review, final verification.

## Phases

### P0 Scaffold
- [ ] uv project (py3.12), ruff, pytest; `web/` Vite TS; Makefile (`make dev`, `make refresh`, `make test`); `.env.example`
- [ ] Download the Protomaps GB z0–14 extract; vendor glyphs and sprites; check the basemap renders fully offline

### P1 Fetch framework — DONE 2026-11-12 (181 sources, keyed runs ok; NGED key invalid → pending)
- [x] `sources.yaml` registry: id, portal, dataset, auth env var, cadence, licence, attribution
- [x] Opendatasoft fetcher: `/exports`, apikey, `data_visible` check, **assert rows > 0**, compare with `total_count`
- [x] CKAN fetcher: resolve via `package_show` each run, apikey header, datastore + file resources
- [ ] SSEN fetcher (pending decision below), PowerTrack faults API
- [x] Raw snapshot store, `source_run` log, change detection (modified hash)
- [x] Tests on recorded fixtures (no live calls)

### P2 Normalise — DONE 2026-11-12 (asset 7,988 / capacity 6,474 / fault 23,845 / queue 23,936 / lv 361,679; 83 tests)
- [ ] NPG, UKPN, ENW, SPEN, NGED, SSEN adapters into the canonical tables, each with a fixture test and a row-count assertion
- [ ] Coordinate validation (bounds, BNG→WGS84 via pyproj, lat/lon swap detection)

### P3 Matching — DONE 2026-11-12 (5,424 sites; parentage 74–100%; namesake relocation; NESO GSPs)
- [ ] Name normalisation, coordinate-assisted fuzzy match, cluster busbars into sites, GSP→BSP→Primary parentage
- [ ] Match-rate report per DNO per dataset (written to `data/reports/match.md`)

### P4 Tiles + server — DONE 2026-11-12 (sites 231 KB gz, lv 44 MB pmtiles ≤6 KB/tile z14 London, site API 6 ms, search 1.5 ms, refresh 19 s)
- [ ] tippecanoe build per layer, atomic swap, manifest, SSE, `/api/site`, `/api/search` (prebuilt compact index)
- [ ] Scheduler: startup refresh + per-source timers, running in the background so the UI is usable immediately on the last good data

### P5 Frontend MVP — DONE 2026-11-12 (visual QA in Chrome; 60 fps pan z11–16 central London w/ LV; FCP 116 ms dev; glossary tooltips per user request)
- [ ] Map with zoom-tiered layers, RAG colouring, Generation/Demand toggle, filters (DNO, kV, min headroom MW), detail panel
- [ ] Search, coverage/freshness panel (per source: last fetch, as-of, rows), per-source attribution
- [ ] Perf budget check (see below)

### P6 Fault levels + queue — DONE 2026-11-12 (fault link 72–95%, queue 85–99%, NPG LTDS app 6/7, SSEN ECR ≥1MW)
- [ ] LTDS fault-level adapters (UKPN, NPG, SPEN, NGED, SSEN-T ETYS); ENW has none, and the UI says so
- [ ] ECR (all DNOs) + TEC queue, aggregated per substation (status filter, de-duplicated against TEC)

### P7 LV — DONE 2026-11-12 (lv_monitored, lv_smart, lv_headroom_forecast, lct)
- [ ] Secondary substations (NPG 57k, UKPN, NGED, ENW, SPEN, SSEN), utilisation where published
- [ ] LV feeder smart-meter aggregates (UKPN, NGED, SPEN, ENW, SSEN GraphQL): on-demand detail, not in tiles

### P8 Live layers — DONE (outages 6 DNOs, national panel, carbon_regions live extra)
- [ ] Outages (UKPN NEO, NPG live power cuts, NGED, SSEN PowerTrack, others where open) → live layer
- [ ] National panel: Elexon gen mix / demand / frequency, regional carbon intensity

### P9 Context layers — DONE 2026-11-12 (primary_areas, gsp_regions, flex, anm, curtailment, dfes, ev, repd, ecr_projects, tec)
- [ ] GSP region polygons, primary service areas, DFES / LCT per substation, flexibility tender sites, EV chargers (if OCM key)

### P9b Network lines — DONE 2026-11-12 (OSM lines/cables/substations, DNO overhead 6.6–132 kV, LV overhead, SSEN-T, ETYS)
- [ ] OSM power=line/cable/minor_line GB extract (Geofabrik pbf → osmium tags-filter) → lines PMTiles, coloured by kV
- [ ] DNO GIS lines where published (SSEN gis_network_line_data; check NGED/UKPN/NPG/SPEN/ENW)
- [ ] Schematic flow lines GSP→BSP→Primary from parentage (dashed, clearly "schematic"); colour by loading where flows published (UKPN, NGED)

### P10 Overlay hook — PARKED by user 2026-11-12
- [ ] `/api/overlay/:name` + `overlays/` drop folder (GeoJSON/CSV with substation id or lat/lon), joined via `id_map`

## Perf budget (measured, not guessed)
- App JS (excl. maplibre) < 60 KB gz; first map paint < 1 s locally
- Each tile < 200 KB at any zoom; LV layer at z14 over central London keeps 60 fps while panning
- Detail panel open < 100 ms (local API)

## Follow-ups found
- [ ] Merge DNO GSP sites with co-located NESO GSPs (search shows both; UKPN Bramford BSP has no parent)
- [ ] elexon_frequency fetch params return data ending 00:00Z (stale frequency)
- [ ] ~120 ENW BSP/GSP sites have no name (number only); find a names source
- [ ] NGED queue/reform registers keyed by LTDS bus codes (24% linked); SPEN ECR linking (10–37%)
- [ ] Fault rows without asset_uid matched by exact name only (16k of 24k) — add fuzzy+near
- [ ] Register NPG LTDS appendices 3–7 and SSEN ECR ≥1MW file
- [ ] Schema: lv_site utilisation_band (UKPN), separate 1ph ratings

## Next
- [ ] REPD URL resolver (gov.uk content API) so quarterly updates are automatic
- [ ] OCM EV chargers fetch inside refresh (bbox recursion fetcher)
- [ ] NIE Networks key → ni_sites / ni_secondary (code ready, data gated)
- [ ] NGED supply-area polygons (only PDF published) — none available
- [ ] 44 ENW BSPs still number-only


## Done since (2026-11-12, later)
- Live: GSP flows (NGED 5-min, UKPN), PV_Live regions, Elexon PN generators, UKPN 1-min constraint readings, carbon regions
- SSEN smart meter (GraphQL dev tables, Sept 2024) into lv_smart; SPEN LV coords/utilisation from lv_monitor
- Offshore (TCE/CES/EMODnet/OSM), DESNZ postcode kWh, REPD via gov.uk resolver, address search (OS Open Names + Code-Point)
- UI: shapes per substation type, key, hierarchy breadcrumb + feed path, operator areas, dark mode, reset, clear-all,
  collapsible sidebar, LV monitoring mode (lvmon layer: 27,606 monitored LV subs; mount type g/p)
- Stats + Queue pages with griddata comparison (docs/reports/griddata_comparison.md)
- Infra: extras plug-in framework, CLI fetch pending-log when DB locked, snapshot retention, strict_rows, max_mb

## In progress
- [ ] SSEN NeRDA live layer + /api/nerda + background sweep (agent resumed after rate limit)

## Waiting on user
- [ ] NIE key (signup confirmation)
- [ ] SPEN restricted GIS datasets (access request) for full SPEN LV coverage
- [ ] Crown Estate licence decision (commercial-use restriction)

## Open decisions
- SSEN Distribution blocks plain curl (403). Options: SSEN account/API key; send a browser User-Agent; or skip SSEN distribution for now.
- API keys needed: UKPN, NPG, ENW, SPEN, NGED (+ optional OCM, MCS).

## Review
_(filled in at end of each phase)_

## 2026-11-25: NIE as a full operator + monitored-LV clarity (PLAN, awaiting OK)

Why: NIE is an add-on layer today. No search, no detail panel, no substation shapes, no LV in the one LV layer,
no kind filters. Other DNOs go through normalise -> match -> build. NIE should too.

NIE as operator
- [ ] fetch/models.py OPERATORS += NIE; sources.d/ni.yaml operator OTHER -> NIE
- [ ] normalise/nie.py: asset (GSP/BSP/Primary from sites table, Irish Grid -> WGS84), lv_site (secondary tx
      grouped per site: kVA, count). No capacity/queue/fault (NIE publishes none) -> sites grey "not published"
- [ ] ADAPTERS += NIE; match/build/search pick it up (check hard-coded operator lists)
- [ ] Retire extra/ni_network.py (layers replaced by core sites + LV)
- [ ] UI: NIE chip filters core layers like the others (already added); colour #999933
- [ ] Tests: adapter fixture from real rows; build includes NIE sites + LV

Monitored LV clarity (NGED/SSEN rings with no numbers)
- [ ] LV panel: say where the ring comes from (ms) and where the % comes from (annual max demand vs monitor)
- [ ] SSEN: carry NeRDA NRN onto the LV point; "Live readings" button in the LV panel
- [ ] NGED: fetch the 301 per-substation monitor CSVs (key) and show latest reading (separate, bigger step)

Done today (in working tree, not committed): pan bounds, NIE key + fetch, keys-file retry, updater skips
releases older than the app, text selection, NIE chip gating NI layers.
