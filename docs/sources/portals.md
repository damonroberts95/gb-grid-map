# Other open-data portals

Registered in `src/gridmap/sources.d/portals.yaml`. Built: `portal_consumption` (layer `elec_postcode_kwh`).

| portal | useful datasets | URL, licence, format, auth, cadence | verdict |
|---|---|---|---|
| DESNZ sub-national electricity consumption | postcode (2015-2024), LSOA, MSOA domestic electricity meters, total and mean/median kWh | gov.uk collection `/government/collections/sub-national-electricity-consumption-data`; OGL v3; CSV (postcode, 80 MB) and XLSX (LSOA 50 MB); keyless via gov.uk content API; yearly, December (2025 edition due Dec 2026, new slug) | BUILT: 1.29M postcodes, 50 MB pmtiles. LSOA/MSOA XLSX worth adding later as polygons (needs ONS boundaries) |
| Code-Point Open (OS) | postcode centroids | already `os_codepoint`, OGL v3 | used for the join |
| NESO data portal (api.neso.energy, CKAN) | `gis-boundaries-for-gb-generation-charging-zones` (TNUoS zone polygons), TNUoS tariffs CSV, `thermal-constraint-costs` (CSV per year), `constraint-breakdown`, `24-months-ahead-constraint-limits`, `capacity-market-register`, `national-demand-balancing-mechanism-units`, `embedded-wind-and-solar-forecasts`, SSEP onshore/economic zones | NESO Open Data Licence; CSV/GeoJSON/zip; keyless; daily to yearly | WORTH ADDING: generation charging zones + TNUoS tariffs (zone polygons coloured by tariff), constraint costs by boundary, CMR (capacity-market units with locations). No dataset named CMZ exists (CMZs are DNO flexibility zones, already in area extras). SSEP offshore zones BUILT |
| Open Net Zero (Icebreaker One) | catalogue/aggregator that links to DNO datasets (e.g. UKPN ECR, smart-meter volumes) | opennetzero.org, Next.js site, no public CKAN API found (api paths 404); licences per dataset | SKIP: pointer service only, the underlying DNO portals are already covered |
| ENA | Open Networks / connections data | energynetworks.org returns 403 to API clients, data.ena.energy does not resolve | SKIP: no machine-readable portal reachable; DNO portals cover the content |
| data.gov.uk | CKAN at ckan.publishing.service.gov.uk, e.g. MCS certified battery installation statistics, smart meter installations | API keyless, mixed licences (mostly OGL), XLSX/CSV | WORTH ADDING selectively: search per topic; nothing else is grid-geometry |
| Ofgem | data portal (ofgem.gov.uk/news-and-insight/data/data-portal), OFTO and price-control tables | mostly XLSX/PDF on web pages, OGL | SKIP for now: no stable machine feed, no coordinates |
| MCS installation data | per-installation database (heat pumps, solar, batteries) | mcscertified.com data dashboard; bulk data via request form; gov.uk battery statistics XLSX (April 2024 - March 2026) | SKIP bulk (request form needed); battery XLSX is national totals only |
| Weave (weave.energy / smart-meter-data) | aggregated half-hourly smart meter consumption per LV feeder from 4 DNOs | CSV/API, MIT-licensed repo; same data the DNOs (SSEN, NGED, SPEN, NPG, UKPN) publish | SKIP: duplicates lv_smart sources already built |
| Scotland / Wales | Scottish spatial hub (data.spatialhub.scot), DataMapWales | reachable, per-dataset OGL | WORTH ADDING later: local energy/heat maps; nothing grid-specific checked |

Gotcha: DESNZ publishes a new gov.uk slug per year (`postcode-level-electricity-statistics-2024`); bump the source `dataset` when the 2025 edition appears.
