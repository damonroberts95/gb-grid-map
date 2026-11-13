# AI Gotchas — GB grid capacity data platform

Known ways an AI assistant goes wrong on this project. Read before starting any task,
re-check before calling anything done. Add an entry (with date) whenever a new mistake costs time.

---

## 1. Data sources and APIs

- **Never invent dataset IDs, field names or URLs.** Verify against the live catalog
  (`/api/explore/v2.1/catalog/datasets` for Opendatasoft, `/api/3/action/package_search` for CKAN)
  before writing a fetcher. Dataset IDs get renamed (e.g. ENW → `sp-enw-*` after the
  SP Energy Networks acquisition; WPD → NGED; NGESO → NESO).
- **Opendatasoft `/records` endpoint caps offset+limit at 10,000.** Use
  `/exports/{csv|parquet|geojson}` for full datasets. Do not paginate `/records` and assume you got it all.
- **CKAN resource URLs change on republish.** Resolve via `package_show` each run; never hardcode the file URL.
- **Anonymous API quotas exist** (Opendatasoft per-IP daily limits; some portals want a free API key).
  Cache raw downloads; never re-hit the API in tests.
- **UKPN: most core datasets are login-restricted (`data_visible: false`).** `/records` returns 403, but
  `/exports/*` returns **HTTP 200 with a header-only CSV**. A naive fetcher "succeeds" with 0 rows. Always assert
  row count > 0 and check `data_visible` in catalog metadata. Needs a real API key. (2026-11-12)
  Same pattern on NPG (54/101 open), ENW (7/148 open), SPEN (23/171 open): one API key per portal.
  Anonymous quotas: UKPN 10k/day, NPG/ENW/SPEN 5k/day per IP.
- **NPG CSV `latlong` is "lat, lon" order**; GeoJSON `geo_point_2d` is `{lat, lon}`; GeoJSON geometry is `[lon, lat]`.
- **SPEN ECR 50kW–1MW coordinates are rounded to 1 km and ~49% missing.** Don't use for site matching.
- **NPG national ECR copy has invalid coordinates.** Use per-DNO ECR, not aggregated copies.
- **ENW publishes no LTDS / fault-level datasets.** Fault-level coverage for ENW will be ~0; say so in UI, don't treat as bug.
- **Dataset IDs lie about vintage** (ENW `dfes-2023*` contains 2026 data). Read the as-of field / description.
- **"Last modified" on the portal ≠ data date.** Record both the fetch time and any as-of date inside the data.
- **Some "datasets" are just PDFs/XLSX inside a zip** (e.g. LTDS appendices). Check the resource format before
  assuming CSV.
- **Count rows after every fetch** and compare with the portal's `total_count` / previous snapshot. A silent
  drop to 0 or −50% means the schema or URL changed, not that substations disappeared.

- **NGED CKAN: `restricted_level: registered` resources show `url: "redacted"`** anonymously and 403 on
  `datastore_search`. Needs account API key.
- **NGED ECR: ~1,350 rows have easting > 700000 (some ×10 errors), ~1,160 blank lat/lon.** Validate per row.
- **NGED LTDS tables join on node codes (`BANR5_BB_1_2`), heatmap on `mRID`.** Different keys for the same site.
- **SSEN Distribution: use the CKAN backend `data-api.ssen.co.uk` directly.** Cloudflare 403s curl's *default*
  User-Agent only; an honest UA (`gridmap-local/0.1`) gets 200 on API + downloads, no key needed (SSEN offers no
  API token). Don't spoof a browser UA. Resource download filenames include dates (`...-march-2026.csv`): always
  take `url` from `package_show`, never guess (guessing returns a 404 *HTML* page). LV smart meter is GraphQL;
  queue is XLSX-only. (2026-11-12)
- **NESO TEC register has no coordinates**; `Connection Site` is free text. Match to substations by normalised name.
- **NESO "embedded register" is Scotland-only**, not a GB-wide ECR. GB queue = per-DNO ECRs + TEC.
- **National Chargepoint Registry closed Nov 2024.** Don't build against it.
- **Elexon BMRS has no per-GSP live data.** "Live" at substation level only comes from DNO outage/fault feeds.
- **Protomaps style fonts/sprites default to protomaps.github.io.** Vendor glyphs + sprites locally or the
  "offline" map silently breaks labels without internet.

## 2. Domain semantics (electricity networks)

- **Headroom definitions differ by DNO.** Generation vs demand; thermal-only vs incl. fault level; with or without
  the contracted/accepted queue; firm (N-1) vs intact. Store `headroom_basis` per source. Never compare raw numbers
  across DNOs without it.
- **MW vs MVA.** Firm capacity and transformer ratings are usually MVA; headroom often MW. Don't silently mix.
- **Fault levels:** three-phase vs single-phase (earth fault); make (peak, kA) vs break (RMS, kA); current vs
  switchgear rating. Headroom % = rating − fault level. Keep all four columns separate.
- **Voltage hierarchy:** GSP (transmission/distribution interface, 400/275 → 132 or 33 kV) → BSP (132 → 33 kV)
  → Primary (33 → 11/6.6 kV) → Secondary / LV (11 kV → 0.4 kV).
  **In Scotland 132 kV is transmission** (SPT, SHET), not distribution. In England/Wales 132 kV is DNO.
- **One physical substation has many busbars/voltages** and appears in several datasets (heatmap, LTDS, ECR, DFES)
  under different names/IDs. Matching is the hardest part. Never assume name equality = same site.
- **Name noise:** "S/S", "Sub", "Substation", "Primary", "BSP", "GSP", "33kV", "11KV", "T1/T2", "GT1", "Grid",
  "(Proposed)", trailing whitespace, ampersands, apostrophes. Normalise, then match with coordinates as a tie-breaker.
- **LV/secondary substations have no published generation headroom or fault levels** (some have utilisation only).
  Don't fabricate RAG for them; show "no data" honestly.
- **RAG colours mean different things per DNO** (thresholds vary, some are demand-based). Recompute RAG ourselves
  from numbers where possible and say which.
- **Null semantics:** "N/A", "-", "TBC", "", 0 and negative values all occur. 0 may be genuinely zero; negative
  headroom is real (over-subscribed). Never coerce unknown to 0.
- **Embedded Capacity Register (ECR):** two parts (≥1 MW and <1 MW) plus status (connected / accepted / offered /
  scoping). Queue totals must filter by status and avoid double counting the same project across ≥1 MW and TEC.
- **NESO TEC register** is transmission-connected; ECR is distribution. Embedded projects that hold TEC appear in both.

## 3. Geospatial

- **OSGB36 / BNG (EPSG:27700) vs WGS84 (EPSG:4326).** Many DNO files give eastings/northings. Some label them
  "lat/long". Some swap lat/lon. Validate: GB lat ∈ [49.8, 60.9], lon ∈ [−8.7, 1.8]; easting ∈ [0, 700000],
  northing ∈ [0, 1250000]. Reject or flag out-of-range points.
- Use a proper transform (pyproj with the OSTN15 grid if available), not a hand-rolled formula.
- **Grid references** like "SU 123 456" need conversion; don't parse as numbers.
- Points at (0,0) or the DNO head office are placeholders, not substations.

## 4. Performance (core design goal: light and fast)

- **Never ship whole-country GeoJSON to the browser.** ~140k sites / ~300k busbars, plus LV. Use vector tiles
  (PMTiles, built with tippecanoe) with zoom-dependent layers: GSP/BSP at low zoom, primaries mid, LV only at high zoom.
- **Put only the properties you render into tiles** (id, kV, RAG, headroom band, numeric headroom). Details go in
  per-site JSON fetched on click.
- **Don't route map features through framework reactivity** (Vue/React state of 100k objects). Use MapLibre
  sources/layers and `feature-state` for highlight/filter.
- **Filters = MapLibre filter expressions** on tile properties, not JS array filtering + re-render.
- **Search = prebuilt compact index** (e.g. a small sorted JSON or FlexSearch index) loaded lazily, not a scan of all features.
- Measure: bundle size, first contentful paint, tile size per zoom, frame rate at z14+ in the densest area (London/UKPN).
  "Feels fast" is not a measurement.

## 5. Process / tooling

- **System Python is 3.9 (macOS).** Use a project venv (uv) with a pinned modern Python. Don't `pip install` globally.
- **Downloaded files are untrusted data.** Keep raw downloads in `data/raw/<source>/<date>/`, scripts elsewhere,
  run Python with `-I` when reading them.
- **Sample real data before writing a parser.** Field mappings written from a dataset's description are wrong half the time.
- **Snapshot small fixtures** from real responses for tests; never hit live APIs in tests.
- **Subagent output is a claim, not a fact.** Verify row counts, file existence and test runs yourself before reporting done.
- **Don't scrape griddata.uk.** Build from the primary open sources only; their derived data is theirs.
- **Attribution is a licence condition.** Every row keeps `source`, `licence` and `fetched_at`; the UI shows per-source
  attribution. Licences: CC BY 4.0 (UKPN, SPEN, ENW, SSEN), NGED Open Data Licence, NPG Open Data Licence v1.0,
  NESO Open Data Licence, OGL v3 (ONS/OS-derived).
- **Coverage claims need numbers.** Report "X of Y sites have generation headroom" from a query, not an estimate.

## 6. Fetch framework findings (added 2026-11-12, P1)

- **Opendatasoft CSV exports are `;`-delimited with a UTF-8 BOM.** Read with delimiter `;` and `utf-8-sig`, never assume `,`.
- **Catalog `data_visible` depends on the caller:** with a valid portal key it flips to true for gated datasets
  (UKPN/NPG/ENW/SPEN all did), so the same registry works keyed or keyless; only ENW `sp-enw-live-neop` stayed false with a key.
- **Catalog `metas.default.modified` is the change-detection key; `records_count` is the row check.** Row counts that differ
  >10% from `records_count` are reported `empty_or_gated`. Heatmap JSON attachments (UKPN) are readable even when `data_visible` is false.
- **Some tiny-row datasets are huge on disk:** `ukpn-connections-reform-local-authority` is 133 rows but 51 MB (polygons per row).
  Check bytes, not just rows, before assuming a source is light.
- **Test environment has no pytest-asyncio.** Drive async code with `asyncio.run` inside sync tests.

## 7. Concurrency

- **DuckDB is single-writer per file across processes.** A second process opening `data/grid.duckdb` read-write
  fails with `Could not set lock on file`. The server/scheduler must be the only writer; CLI tools either go
  through the server or open read-only. Don't run two fetch processes at once. (2026-11-12)

## 8. Learned in P2 adapters (2026-11-12)

- **Never print, echo or slice API keys in shell commands**, not even a fragment for "verification". Load them inside
  Python via dotenv and print only booleans/status codes. (Auto-mode blocked this once.)
- **CKAN ≥2.10 API tokens are JWTs (`eyJ…`, two dots).** A non-JWT value in `NGED_API_KEY` = wrong field copied; API returns 403 on `user_show`.
- **Same native id ≠ same asset.** NGED/ENW/NPG reuse one number for BSP and Primary at a site; NPG `substation_id` repeats
  across GSP/BSP/Primary levels. Include kind (or name slug) in `asset_uid`.
- **IDs that look shared often aren't:** UKPN T3a `substation_mrid` has a leading `_` vs heatmap `mRID`; NGED CIM mRIDs ≠ heatmap mRIDs.
- **Headroom tables publish one row per busbar voltage and/or per coordinate variant** (SPEN SPM). Dedupe before counting sites.
- **Two NPG headroom figures disagree by design:** `heatmapdatatable` genhr/demhr are pre-constraint; LTDS available capacity is post-constraint (0 where fault level red). Use LTDS.
- **Units:** NPG sites_list maximum_demand is kW, LTDS demandmaximum MVA. SSEN/NGED/SPEN demand headroom is MVA.
- **Placeholders:** NPG Kielder fault level 1.0 kA everywhere; zero make values. UKPN utilisation/headroom are text bands ("20-40%").
- **Malformed files:** SPEN ECR 50kW–1MW has 908 column-shifted rows; NGED reform register has ~1M trailing blank rows;
  SSEN ECR has BOM + newlines in headers. NGED LTDS table 4 has one row per branch end (node repeats).
- **Queue double counting:** SPEN gsp-queue-position duplicates ECR 1MW+ (784/787); NPG queue has no status/id and overlaps ECR.
- **Parallel agents share the scratchpad.** Use unique filenames per agent.

## 9. Matching (2026-11-12)

- **Same name ≠ same substation, even within one operator.** UKPN has three "Bury Grid" (Bury St Edmunds, Ramsey,
  Edmonton) and two "Water Ln". UKPN heatmap rows carry the *namesake's* coordinates. `match.relocate_namesakes`
  moves them to the namesake nearest their upstream feed. Always sanity-check parent→child distances (p95 ~20–45 km;
  Scottish islands legitimately ~80–90 km).
- **Upstream GSPs are transmission sites absent from DNO asset lists.** Resolve them against NESO's GSP list
  (`neso_fes_gsp_info`, 373 with lat/lon), not just the operator's own assets.
- **ENW publishes numbers, not names.** Names come from ECR fields "NAME (123456)" (~82% of primaries, ~28% of BSPs).
- **SPEN SPM upstream fields list several GTs** ("AINTREE GT1 / FORMBY GT2A"). Split before resolving.
- **A dir and a module with the same name** (`gridmap/match/` vs `match.py`): the package wins and `-m` fails. Don't pre-create package dirs speculatively.

## 10. Frontend / runtime (2026-11-12)

- **MapLibre 6 + Vite dev: "Worker failed to load".** MapLibre finds its module worker via
  `new URL('./maplibre-gl-worker.mjs', import.meta.url)`; Vite pre-bundling moves it. Fix: `optimizeDeps.exclude: ['maplibre-gl']`.
  A successful `npm run build` does NOT prove the map renders — always load it in a browser and read the console.
- **MapLibre `["zoom"]` must be the outermost expression** (top-level interpolate/step). Nesting it inside `case`
  makes `addLayer` throw and the layer silently never appears; every later setFilter then errors.
- **Fuzzy name matching: generic words dominate.** `token_set_ratio("victoria st", "cannon st bloomberg")` is high
  because of "st". Strip street/direction words before comparing (`match.similar`).
- **Code edits don't reach a running server.** Run uvicorn with `--reload --reload-dir src`; refresh re-runs stages
  whose code is newer than their outputs. Kill the old server before starting another: the DuckDB lock is still held
  for a moment after the process starts exiting.
- **Never show internal notes as user-facing text** (adapter `basis` strings like "unknown: … recon …"). Map
  "unknown" to a plain "not stated in <operator>'s published data".
- **Vector tile features need an id for feature-state (selection/hover).** tippecanoe output has none; use
  `promoteId: { <layer>: 'id' }` on the source. Guard `setFeatureState` against undefined ids.
- **URLSearchParams percent-encodes `/` and `,`**, corrupting MapLibre's `#map=z/lat/lon`. Use `hashGet/hashSet` (dom.ts).
- **Changing only the URL hash doesn't reload the page** (browser automation `navigate`); add a dummy query (`?r=n`) to force a reload when testing initial state.
- **Browser automation screenshots are scaled** relative to CSS pixels (e.g. 1568 vs 1920 wide). Convert coordinates from `map.project()` before clicking, or click via `find` refs.
- **Theme swap:** basemap flavours share layer ids; remove those ids and re-add the other flavour *before the first non-basemap layer*, then `setSprite`. Vendor every flavour's sprites (grayscale + dark) for offline use.

## 11. Public data extras (2026-11-12)

- **Opendatasoft CSV exports are `;`-delimited with a BOM**; geopoints are `"lat, lon"` text (NPG curtailment, others).
- **Coordinate oddities:** SPEN smart-meter locations are text `"(E, N)"` BNG; REPD NI rows are Irish Grid (drop);
  SSEN shapefile dbf fields truncated to 10 chars and in BNG; NESO ETYS boundaries in EPSG:3857.
- **Voltage strings vary:** NPG volts as text, ENW `"415V"`, SSEN `"11.000 kV"` → `lines.parse_kv`.
- **Units:** ENW DFES max demand is MVA; UKPN DFES publishes demand *headroom* only; UKPN curtailment energy is
  estimated kWh, NPG's is outage MWh: not comparable.
- **Smart-meter peaks only count smart-metered customers** → understate substation load. UKPN smart-meter datasets cap at 30k rows.
- **Elexon FREQ needs both From and To**, else returns a day ending 00:00Z. http params support `{now-15m}`/`{now}`.
- **Join keys:** UKPN `ltds_node`+kV is a verified cross-table key; NGED/SPEN nodes include taps/poles/customer sites (don't join).
  NESO GSP names appear inside DNO names ("Perivale (Willesden 66kV)").
- **NPG LTDS appendix 7 restates ECR projects** (excluded from queue totals via `match.QUEUE_SUPPLEMENTARY`).
- **REPD's gov.uk asset URL changes every quarter** (hashed path). Needs a gov.uk content-API resolver, not a fixed URL.
- **Open Charge Map has no offset pagination**: recurse bounding boxes. OCM fetch currently runs via `extra.dem_ev --fetch` (outside refresh).
- **Big downloads:** set `max_mb` on the source (OSM GB pbf 2.2 GB); default cap is 200 MB.
- **`osmium export` can emit RS-prefixed GeoJSONSeq**: use `-x print_record_separator=false`.
- **Extras input hash covers only the module file + its sources**: editing a shared helper (e.g. `lv_common.py`) needs the module touched to rebuild.
- **Missing ≠ absent network:** DNO line layers are overhead-only (underground cables are gated/shared-licence). Say so in descriptions.
- **NIE Networks Opendatasoft datasets are login-gated** (catalog public, data not).
- **SSEN smart meter (GraphQL, gql.internal.datopian.com/v1/graphql):** `ssen_smart_meter_prod_*` tables return
  BigQuery 403; `ssen_smart_meter_dev_*` work but stop at 2024-09-05 and have null geo. Base URL `/v1/` 404s; use
  `/v1/graphql`. Date-typed columns reject ISO timestamps. Feeder ids don't join to SSEN substation numbers: locate via
  lookup postcodes (Code-Point Open) and snap to nearest lv_site. (2026-11-12)

## 12. Offshore and portals (2026-11-12)

- **Crown Estate Scotland's Hub (opendata-crownestatescotland.hub.arcgis.com) answers 401 to the Hub search API**; find FeatureServers via `arcgis.com/sharing/rest/search?q=owner:COReGISAdmin`. ArcGIS `f=geojson` queries cap at `maxRecordCount` (1000-2000): check `returnCountOnly`.
- **Crown Estate licence is not OGL**: attribution string is mandatory and the terms restrict commercial gain from the data.
- **EMODnet WFS needs CQL via `CQL_FILTER` and `outputFormat=application/json`**; MW footprints overlap, so summing double-counts.
- **No numpy in the venv**: polars `.to_numpy()` panics; pass `.to_list()` to pyproj. polars 2 names header-less columns `column_0`.. ; select by position.
- **OSM subsea cables: names like "inter array" dominate** (430 of 759); pipelines/telecoms also carry `location=underwater`, so require `power=cable` and drop by name.
- **SPEN LV locations:** `spm/spd_gis_shapefiles_point_assets` and `secondary_substation_polygons` return 403 even with
  an API key (per-dataset access request needed). The only open SPEN LV coordinates are `lv_monitor.geo_points`
  (~19k monitored transformers, "lat, lon" text); join on `transformer_enid`. (2026-11-12)
- **Pipeline runs outside the server don't reach the DB:** after normalising by CLI, touch the adapter so the server's
  refresh re-runs normalise → load → build (it detects code newer than outputs).
- **SSEN NeRDA API (verified 2026-11-12):** base `https://nerda-prod-apis-v2.azurewebsites.net/api/`. Auth = **GET with a
  JSON body** `{"username": <email>, "apiKey": <long-term key>}` + `Content-Type: application/json` (no auth header;
  Authorization header → 403, missing body → 400 "Invalid API key body."). Endpoints: `ApiNerdaStatic` (no params = all
  4,628 SEPD substations, 133 MB, ~60 s; `?substation=<uuid>` = one), `ApiNerdaAfter?measurement=&after=`,
  `ApiNerdaBetween?measurement=&after=&before=`, `POST GenerateApiKey` (rotate; key lasts 90 days). Old docs'
  `nerda.opengrid.com/api/nerdastatic-SEPD/{NRN}` paths are dead (301 to SPA). No bulk time-series endpoint: one request
  per measurement → poll politely. Measurement ids repeat within a line and one id can appear under two
  measurementTypes; dedupe by (id, type). NRN `6100_007_120_00` = primary/HV feeder/secondary/LV feeder (same scheme as
  SSEN smart-meter dataset_id). Portal-only routes (getrttx, getsub...) need the B2C session token — don't use.
- **http fetcher file naming:** with no `path`, files were saved as `file` (extras look for `.pbf` etc. by suffix).
  Now named from the URL. Snapshot dirs without meta.json (killed mid-download) are pruned after 1 h. (2026-11-12)
- **Hand-fetched snapshots + due check:** a source with no `source_run` row was treated as never fetched → the 2.2 GB
  OSM pbf re-downloaded on every server start and got killed by reloads. `due_sources` now trusts an on-disk snapshot.
- **`--reload` restarts background threads** (scheduler, NeRDA sweep). A long sweep never finishes while code is being
  edited; avoid editing src/ during long-running sweeps, or expect partial cycles.
- **Python logging is off by default under uvicorn** for our `gridmap.*` loggers: sweep/refresh INFO lines were silently
  dropped, hiding a stalled sweep. app.py now attaches a handler. Check `grep gridmap. <server log>` for health.

## 13. Updates (2026-11-13)

- **Updatable code can only import what the .app already bundles.** A new third-party import passes tests in dev and breaks in the frozen app. Dependency changes change `deps_hash` and need `make app`; old apps then show "needs new app".
- **Never import `gridmap` in `packaging/launch.py` before `use_payload()`.** It would load the bundled copy and the payload would be ignored. The launcher's `-c` branch (used by the updater's validation) runs before payload selection so it does not touch `current.json`.
- **In the frozen app `sys.executable` is the GUI shell.** Run subprocess checks as `sys.executable -I -c <code>`; the launcher execs the code instead of opening a window.
- **MapLibre worker in production builds:** `optimizeDeps.exclude` fixes dev only. Vite can't see MapLibre's runtime
  `new URL(e, import.meta.url)`, so `vite build` emits no worker and the packaged app shows a blank map. Import it
  with `?worker&url` and call `setWorkerUrl()`. Always test the production build (`/` on :8787), not just Vite dev.

## 14. Fresh-install parity (2026-11-13)

- **Everything an adapter or extra reads must be fetchable by the scheduler.** Hand-fetched snapshots and one-off scripts hide gaps until a fresh `GRIDMAP_HOME` (packaged app) lacks them. Test with an empty temp home: `GRIDMAP_HOME=<tmp> uv run python -I -m gridmap.refresh`. New portals: `ssen_gql`, `smart_agg` (NGED/SPEN smart-meter aggregation), `ocm`. `large: true` is for sources nothing reads.
- **Snapshot file names follow the URL** (`downloads`, not `file`): never open `snap / "file"`; find zips by `zipfile.is_zipfile`. build_places did and only worked on the dev Mac's old snapshots.
- **First-run set is `refresh.is_core`**: category core, or `core: true` on the source (the lv_monitored inputs).
- **Address search index** (`derived/places.parquet`) is rebuilt by refresh when os_open_names / os_codepoint change.
- **A feed that is missing can leave a sibling feed's aggregate half-empty** (`area_constraints`: breaches without curtailment events crashed `min([])`). Guard empties in extras.
