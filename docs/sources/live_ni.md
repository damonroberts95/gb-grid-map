# Live national context + Northern Ireland (recon 2026-11-12)

## Carbon intensity by region (built: `carbon_regions`)
- `carbon_regional` (api.carbonintensity.org.uk/regional, CC BY 4.0, 30 min): 14 DNO regions (ids 1-14) + England/Scotland/Wales/GB aggregates (15-18, ignored).
- Joined to `neso_dno_licence_areas` (GeoJSON in **EPSG:27700**, despite no coordinate hint in properties; 14 polygons, 3 MB). Mapping regionid -> polygon `ID` is in `live_carbon.REGION_TO_AREA`. Reprojected with pyproj, tippecanoe pmtiles (0.23 MB).
- Intensity `forecast` can be 0 for wind-only regions; that is a real zero.

## NESO embedded wind & solar forecast
- CKAN `embedded-wind-and-solar-forecasts` (datastore resource db6c038f-...): **national only** (EMBEDDED_WIND/SOLAR_FORECAST and _CAPACITY, MW, half-hourly, 14 days). No regional split, so no layer. `neso_regional_carbon_forecast` (136k rows) is a regional forecast of intensity only.
- The dated CSV download url 302s; use datastore_search.

## NIE Networks (Northern Ireland) - GATED
- portal nienetworks.opendatasoft.com, 20 datasets, CC BY 4.0, all `data_visible=false` anonymously: `/records` 403, `/exports/csv` header-only. Needs a free account key -> `NIE_API_KEY` (not in .env). Sources registered in `sources.d/ni.yaml` as gated; fetch reports `needs_key`.
- Datasets: sites (GSP, BSP, primary, combined `grid-bulk-and-primary-sites`), secondary transformers (has `nominal_rating` + unit, geopoint, Irish Grid x/y), primary/transmission transformers, switchgear etc, `network-faults` (live outages, ~30 min; columns outageid, outagetype, starttime, estrestorefulldatetime, postcode, numcustaffected, statusmessage, updatedtimestamp: **postcode only, no coordinates**).
- No capacity/headroom/heatmap dataset exists on the portal. NIE's capacity map is not published as data here.
- `ni_network.py` builds `ni_sites` + `ni_secondary` once a snapshot exists (until then it builds nothing). Column names verified from headers; row values untested against real data.
- `ni_faults` not built: would need postcode geocoding (ONSPD not NI-complete / not fetched).

## SONI
- Dashboard at soni.ltd.uk/how-the-grid-works/system-data-dashboard/ ; robots.txt disallows `/api/`; no open API/dataset found. Skipped.
