# Demand, LCT and forecasts (extras: dem_lct, dem_dfes, dem_ev)

## Layers
| id | type | features | notes |
|---|---|---|---|
| lct | pmtiles (3.1 MB) | 38,211 LV substations (UKPN 23,966, ENW 14,245) | minzoom 9; ENW is MCS installs only (no EV) |
| lct_primary | geojson (0.65 MB) | 2,625 (NGED 887, UKPN 779, SPEN 629, NPg 330 postal districts) | SPEN kW ADMD only, no counts |
| dfes | geojson (0.46 MB) | 2,135 primaries (SPEN 706, UKPN 616, NPg 483, ENW 330) | peak demand 2025 -> 2035, Holistic Transition |
| ev_chargers | pmtiles (4.3 MB) | ~27k Open Charge Map POIs, minzoom 8 | CC BY 4.0, manual fetch |

## DFES metric
Peak demand growth 2025-2035, each operator's Holistic Transition scenario (ENW `2-HOLISTIC_TRANSITION`, SPEN `HT`,
NPg `NG FES - Holistic Transition`, UKPN `Holistic Transition`). UKPN publishes only demand *headroom*, so peak =
firm capacity - headroom with firm capacity = headroom_mw / headroom_percent (derived, flagged in popup).
ENW is MVA, others MW. NGED and SSEN DFES are per local authority only (no primary level): not on the layer.
Growth % is withheld when the 2025 baseline is under 0.5 MW.

## Sources
Registered in sources.d/demand.yaml: `dem_spen_dfes_spd/spm` (own non-large copies, streamed with polars scan_csv).
Other inputs reuse ids from sources.yaml: ukpn_lct_secondary/primary, enw_mcs_lct_per_substation, nged_lct_connections,
spen_lct_spd/spm, npg_lct_postal_district, ukpn_dfes_network_headroom, npg_dfes_wide, enw_dfes_primary.
Skipped: SSEN low_carbon_technologies and SSEN/NGED DFES (local-authority granularity only); NPg per-substation LCT (none published).

## Open Charge Map
Not in sources.yaml: needs `X-API-Key` header and bounding-box recursion (no offset). Run
`uv run python -I -m gridmap.extra.dem_ev --fetch` (reads OCM_API_KEY from .env). Snapshot lands in
data/raw/ocm_gb_poi/<ts>/. Records with status not-operational/planned/removed/duplicate and Ireland/NI are dropped.
Attribution: "Data from Open Charge Map and its contributors, licensed under CC BY 4.0".
