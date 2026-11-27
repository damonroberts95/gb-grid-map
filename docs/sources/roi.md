# Republic of Ireland (added 2026-11-27)

The project is non-commercial. Sources without an open licence are used as published and say so in attribution.
Registry: `src/gridmap/sources.d/roi.yaml`.

## ESB Networks capacity heatmap (operator `ESB`)
- Page: esbnetworks.ie/our-services/tools---maps/generator-availability-capacity-map. It links one XLSX
  (`customer-heatmap-download-<month>-<year>.xlsx?sfvrsn=...`). The name changes per release.
- Fetch: portal `page_link` reads the page and takes the first href matching `params.pattern`. The full link
  (with the version query) is the change key. No licence stated: "ESB Networks website terms".
- Sheet "Heatmap data", header on row 2. One row per transformer group. ~46.5k rows. WGS84 lat/lon.
  Footer rows hold "Report compiled 12/12/2025": used as `as_of`.
- Columns: Station Name ("GARRYOWEN (067000) 38 kV STATION"), Transformer GroupID ("Garryowen [T41,T42]"),
  Primary kV, Voltage Class (secondary: 110 kV, 38 kV, MV, LV), Transformer Configuration
  ("2x15 MVA @ 10 kV" or "400 kVA :Kiosk Substation"), Installed Capacity MVA, Demand FirmCapacity MVA,
  Demand Available MVA, Demand Parent Constraint, Gen Firm/NonFirm Capacity MW, Generation Total Committed MW,
  Gen Available Firm/NonFirm MW, Generation Parent Constraint, Parent Feeder, Parent Station, TSO Interface Station.
- Parent Station names the feeding transformer group exactly (or "Transmission Source").

### Mapping (normalise/esb.py)
ESB topology: 220/110 kV transmission, 110/38 kV, 38 kV/MV, MV/LV. In Ireland 110 kV is mostly transmission.
- Group fed from "Transmission Source" (220/110, 110/38, 110/MV at the TSO/DSO interface) -> `GSP`.
- Other group with a 110 or 38 kV secondary (Dublin DSO 110 kV, 220/38 beside a GSP group) -> `BSP`.
- Group with an MV (10/20 kV) secondary -> `PRIMARY`.
- Voltage Class LV -> `lv_site`. Several rows at one name+location are summed.
- One asset per group (`ESB:<station no>:<group>`). Match clusters groups of one station into one site.
- Upstream: the parent group's station name, as `upstream_gsp` (parent is GSP) or `upstream_bsp`.
- Capacity: `demand_headroom_mva` = Demand Available MVA, `gen_headroom_mw` = Gen Available Firm MW,
  `firm_capacity_mva`, `contracted_gen_mw` = Total Committed. Both headrooms already include the parent
  constraint. The "Constrained, otherwise capacity available = X" note goes to `upstream_constraint`.
- LV: `transformer_kva` from Installed MVA. `utilisation_pct` is derived: 1 - own available / firm, where own
  available is the "otherwise" figure when the parent constrains it. Mount from the structure: Pole -> pole,
  Kiosk/Building -> ground. LV names are part-redacted by ESB ("MV/LV Substation BA***002") and not unique.

### Counts (2026-11-27, July 2026 file compiled 2025-12-12)
- 657 HV groups -> 548 sites: 115 GSP, 9 BSP, 424 primary. All have coordinates and both headrooms.
- Parent found: 100% BSP, 99.8% primary. Parent distance p50 6.8 km, p95 24 km, max 46 km.
- Gen headroom > 0: 73% GSP, 68% primary. Demand headroom > 0: 83% GSP, 46% primary.
- 45,868 LV sites (22,893 pole). All have kVA and utilisation.

## ESB PowerCheck outages (`esb_powercheck_outages`)
- GET api.esb.ie/esbn/powercheck/v1.0/outages with header `api-subscription-key` (registry `auth_header`).
  Key in `ESB_POWERCHECK_KEY`. Gated: `needs_key` without it.
- 2026-11-27: the key supplied returns HTTP 401 ("invalid subscription key ... active subscription"), also as
  a query parameter and with the Ocp-Apim header. No real response seen yet.
- The parser (`live._esb_powercheck`) follows the NIE PowerCheck shape (`outageMessage`, `outageId`,
  `outageType`, `point.coordinates`, `startTime`, `estRestoreTime`, `numCustAffected`). A point may be
  "lat,lon" or ITM (EPSG:2157). Check it against a real response once the key works.

## EirGrid transmission (`eirgrid_transmission`, extra `eirgrid_transmission`)
- ArcGIS FeatureServer `TDP_2024_Web_Map_PUBLIC` (services-eu1.arcgis.com/1VGs4Se8lewgdzfE). No licence stated.
- Portal `arcgis` pages each layer 2,000 at a time (`resultOffset`) into `layer_<id>.geojson`.
- Layers: 38 stations (161), 42 stations with committed works (153), 40 overhead spans (24,952),
  41 overhead spans with committed works (6,887), 39 cables (1,607), 43 cables with committed works (123),
  4/6/7 Celtic, Greenlink and EWIC.
- 41/43 are not repeats of 40/39. Spans with works are left out of 40, so most 400 kV lines are only in 41.
  Read all four, dedupe on G3E_FID.
- Layer 7 has `FID`, not `OBJECTID`: do not send orderByFields=OBJECTID.
- Built: spans merged per circuit section -> 616 line features (1.7 MB pmtiles, minzoom by voltage) and
  314 stations (265 x 110 kV, 43 x 220 kV, 1 x 275 kV, 5 x 400 kV; 153 flagged "works planned").

## ROI live national (EirGrid Smart Grid Dashboard, region=ROI)
- Same API as NI. Fields: SYSTEM_DEMAND, WIND_ACTUAL, SOLAR_ACTUAL, INTER_EWIC, INTER_GRNLK, INTER_NET_ROI,
  CO2_INTENSITY. INTER_NET_ROI = EWIC + Greenlink (checked). Positive = import into Ireland.
- Empty `Rows` happen often (demand and co2 both empty on the first try today). `_eirgrid_last` falls back.
- `/api/national` -> `roi`. The web shows an "Ireland:" line under the NI line.

## ROI outline (`tailte_roi_provinces`)
- Tailte Éireann "Province Boundaries Generalised 100m" FeatureServer, CC BY 4.0. Queried with outSR=4326 and
  maxAllowableOffset=0.003 (~0.7 MB, 4 provinces). No country-level layer was found.
- `dno_areas.dissolve` drops edges shared by two provinces and re-chains the rest (no shapely in deps).
  The provinces share exact vertices, so it closes. If it ever fails the provinces are kept as parts.

## Basemap
- BBOX west edge moved from -8.7 to -11.0. `gb.pmtiles.bbox` records the bbox used. An install without it (or
  with another bbox) re-extracts once in the background, then swaps the file. pmtiles.js sees the new ETag.

## Not available
- ESB MV/LV line routes, a machine-readable connection queue (PDF only), LV monitoring.
- Irish towns and Eircodes are not in the place search (OS Open Names is GB only). ESB station names are.
