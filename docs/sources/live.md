# Live and near-live sources (recon + build 2026-11-12, ~16:45 UTC)

Layers (group "Live", `live=True`, default off): `live_gsp` (99 points, 20 KB), `live_pv` (14 polygons, 0.21 MB pmtiles),
`live_generators` (274 points, 70 KB). Existing: `carbon_regions` (30 min), outages endpoint (`server/live.py`).
Sources: `src/gridmap/sources.d/live.yaml`. Popups carry the data timestamp ("Data as of (UTC)").

## Measured freshness (age of newest record at request time)
| Source | Newest record | Age | Used |
|---|---|---|---|
| NGED `live-gsp-data-*` (55 GSPs, 4 packages, public CSV, 5-min rows) | 16:40 | ~6 min | `live_gsp` |
| NGED `live-data` (4 licence areas, 5-min; `nged_live_area_data`, already registered) | 16:40 | ~6 min | not mapped (GSP layer covers it) |
| UKPN `ukpn-super-grid-transformer-header` (per GSP x SGT, 30-min, needs key) | 16:30 | ~17 min | `live_gsp` (AGGREGATE rows) |
| UKPN `ukpn-constraints-real-time-meter-readings` (1-min amps/utilisation at constraint nodes) | 16:41 | ~5 min | **blocked**, see below |
| Elexon PN (Physical Notifications, all BM units) | current minute | 0 | `live_generators` |
| Elexon B1610 actual per-unit output | empty for 9-12 Nov | >3 days | no |
| Sheffield Solar PV_Live `pes/{id}` | 16:30 | ~17 min | `live_pv` |
| ENW `sp-enw-live-system-demand` (one network-wide number, ~11 min cadence) | 16:31 | ~15 min | no location, not mapped |
| NPG `live-primary-operational-metering-14-day` / 33/66/132 kV circuits / BSP | 15:30 / 15:30 / 14:30 | 75-135 min | no (>1 h and whole-dataset export is ~40 MB per fetch) |
| SPEN `network-flow-dataset-...` / `network_flows_sp_boundaries` | 8 Nov 03:00 / 31 Oct | 4-12 days | no |
| NGED `live-primary-data-*` / `live-bsp-data-*` (registered users) | modified 09:05-13:55 | hours | no |
| SSEN NeRDA | unknown (3-min SCADA, per docs) | gated | no key, see below |
| NESO embedded wind/solar forecast, demand forecasts | 16:27 | forecast, national only | no regional split: nothing to map |
| Outages: UKPN (2 feeds), NPG, ENW, SPEN, NGED, SSEN | minutes | existing endpoint | all six DNOs already covered; NIE: see below |

## SSEN NeRDA (closest public analogue to LV monitoring): needs a free account
- Portal moved to **https://nerda.ssen.co.uk** (nerda.opengrid.com 301s there). Content: SCADA PowerOn BSP-primary 33 kV
  feeder line current every 3 min, HV feeder (primary-to-secondary 11 kV) switch position / current / voltage / P / Q every
  3 min, secondary substation kVA / current / voltage, **LV feeder-head readings from installed LV monitors** (current, voltage,
  THD, phase angle, frequency), load-model forecasts. Substations are BSP, primary or secondary, keyed by NRN.
- API (from the API guide PDF): `https://nerda.opengrid.com/api/nerdastatic-SEPD/{NRN}` (static: lines, location, measurement ids),
  `.../nerdart_after-SEPD?measurement={nerda_measurement_id}&after=YYYY-MM-DDThh:mm:ss.SSSZ`, `nerdart_between-...`. The SPA's
  config shows the real backend is an Azure app `nerda-prod-apis-v2.azurewebsites.net/api/` behind Azure B2C (scope `nerda.read`).
  Anonymous calls 404 (`nerdastatic-SEPD` path not found without auth), so granularity beyond the docs is **unverified**.
- **Steps (user, ~5 min):** 1) open https://nerda.ssen.co.uk, "Register", enter email, enter the emailed verification code;
  2) accept the Terms and Conditions on first login; 3) top-right three-bar menu -> "Generate Long-Term API Key" (NOT the short
  one; shown once, copy it); 4) put it in `.env` as `NERDA_API_KEY=...` and `NERDA_USERNAME=<account email>`; 5) the auth header
  name is not documented in the guide (the HV Data Tool .exe uses `Username` + `LongTermApiKey`): confirm it with
  `SSENSmart@sse.com` or by watching the browser Network tab on the "View APIs and Manage Keys" console, then code `ssen_nerda`
  (SSEN's terms: separate NeRDA T&Cs for the data tool; check redistribution before publishing).
- Not coded: no key, unknown header and unsampled payload (AI_GOTCHAS: sample before parsing).

## UKPN constraints (blocked by a fetcher rule, not by data)
`ukpn-constraints-real-time-meter-readings` is fresh (1-min) and keyed-readable (258 rows) but the catalog `records_count` says
986, so `fetch/opendatasoft.py` flags every fetch `empty_or_gated` (>10 % row mismatch) and discards the snapshot (that is why
`ukpn_constraints_meter` in areas.yaml only has a hand-placed file). Needs a per-source row-tolerance opt-out in the fetcher
(out of scope here). Once fetchable: constraint ids start with the site code (`0C1068` = Bramford); descriptions carry the site
name ("BRAMFORD GRID 132kV") so points can be placed on UKPN grid sites; colour by `utilisation_percentage`, flag `breach_flag`.

## NIE Networks
`nie-networks-network-faults` (14 live rows, `data_visible=false`, key pending) has postcodes but no coordinates; already
registered gated in `ni.yaml`. Not added to the outages endpoint (no geocoder); `server/live.py` untouched.

## How the layers work
- **`live_gsp`**: NGED rows = newest of each GSP CSV (name from the slugged file name: `upper-boat-132_swales.csv`). `Import` is the
  draw from transmission; `Net Demand` = Import + Generation (verified on 5 rows). UKPN rows = `sgt == AGGREGATE` with a power
  reading (6 of 55 are blank at times). Located by name on the operator's own GSP list (`derived/site.parquet`), else NESO FES
  GSP lat/lon: 99 of 104 located; unlocated: Stayleybridge, Barking C (EPN/LPN), Wimbledon 1&2 and 3&4 (namesakes km apart).
  Rows >3 h older than the newest row are dropped (dead feed). NGED timestamps carry no zone; assumed UTC (matches sunset
  in November); **unverified in BST**, check in summer.
- **`live_pv`**: PV_Live has no bulk GSP call (one request per GSP id, ~330), so 14 `pes/{10..23}` calls every 15 min give
  region level, painted on the DNO licence areas (PES id -> GSP group letter -> carbon region id -> licence-area polygon, mapping
  in `live_pv.PES_REGION`, checked against `pes_list`). Colour = % of installed PV MWp. Newest value is an early estimate. Terms:
  api.pvlive.uk "Terms & Conditions" (not retrievable as text); attribution string is ours, confirm before publishing.
- **`live_generators`**: PN level interpolated to the fetch minute. BM units from `reference/bmunits/all` (types T/E/M, >= 5 MW;
  aggregator types 2__/C__/V_/I/G/S have no site). Placed by name + capacity match (rapidfuzz, >= 90) to REPD operational sites
  (158) and NESO TEC "Built" projects at their connection substation (116, so approximate). Technology must agree.
  **Match rate: 274 of 598 candidate units (46 %); 37.0 GW of 95.7 GW registered capacity (39 %); 11.8 of 35.0 GW positive notified
  output (34 %).** Unmatched are mostly big conventional plant and sites whose BM name is a code. PN is the plan, not metering.

## Gotchas
- Elexon `datasets/PN/stream` needs `from`/`to` (no `publishDateTime*`), returns every interval overlapping the window (~570 KB, 2,500 units);
  `bmUnit` is null for 56 rows, join on `nationalGridBmUnit`. Registry cadence 15 m; snapshots are never pruned (~55 MB/day).
- UKPN ODS live datasets: anonymous `/exports` returns header-only (key needed) and some have `records_count` > public rows.
- NGED `live-gsp-data-*` resources are public (no `restricted_level`); the primary/BSP ones are `registered` and refresh only hourly+.
- BMRS `B1610` (actual per-unit output) is empty for the last 3+ days; do not plan "live actuals" on it.
- Elexon BM unit names can contain U+FFFD (`Walney�1`); stripped in `live_generators`.
