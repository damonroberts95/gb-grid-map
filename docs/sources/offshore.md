# Offshore extras (offshore_wind, offshore_cables)

Registered in `src/gridmap/sources.d/offshore.yaml`; OSM comes from `osm_gb_pbf` (lines.yaml), interconnector MW/status from `neso_interconnector_register`, REPD name fallback from `desnz_repd`.

| source id | what | licence | access |
|---|---|---|---|
| tce_wind_sites / tce_wind_cables / tce_r4_preferred / tce_r5_pdas / tce_r6_pdas | The Crown Estate ArcGIS Hub FeatureServers (services2.arcgis.com/PZklK9Q45mfMFuZs): wind site agreements (72), cable agreement corridors (66), R4/R5/R6 areas | The Crown Estate Open Data Licence (GIS) v1.1; attribution "Contains data provided by The Crown Estate that is protected by copyright and database rights." Non-commercial-leaning terms: check before commercial use | keyless, f=geojson |
| ces_offshore_wind / ces_scotwind_offers / ces_intog | Crown Estate Scotland FeatureServers (services3.arcgis.com/nGV4jiurzcahJ9LV): sites, OFTO and substation leases with phase and MW (58), ScotWind (20), INTOG (13). Updated monthly | OGL v3; "Contains public sector information, licensed under the Open Government Licence v3.0, from Crown Estate Scotland" | keyless. The Hub site itself (opendata-crownestatescotland.hub.arcgis.com) is 401 to API clients; use the FeatureServer URLs found via `arcgis.com/sharing/rest/search?q=owner:COReGISAdmin` |
| emodnet_windfarms | EMODnet Human Activities WFS `emodnet:windfarmspoly`, CQL `country='United Kingdom'` (213 polygons with MW, turbines, status) | EMODnet open data, attribution to EMODnet Human Activities | keyless |
| neso_ssep_offshore | NESO SSEP offshore publication zones (shapefile zip, BNG, 19 zones) via CKAN | NESO Open Data Licence | keyless |

Layers: `offshore_wind` (128 areas), `offshore_cable_zones` (77), `offshore_ssep` (19), `offshore_cables` (759 OSM subsea ways), `landfalls` (104 points).

Notes
- Crown Estate "Round 4/5/6" polygons are dropped when their representative point already lies in a named site, so each seabed area is drawn once. MW order: operator figure, else sum of EMODnet footprints inside, else REPD name match (>=92 token-set ratio). EMODnet sums can double-count overlapping footprints; treat as approximate.
- OSM subsea cables are classified by name (inter-array / export / interconnector / HVDC-island link). Landfalls = subsea cable endpoint equal to a land cable endpoint or `power=transition`. Many cables are unnamed.
- Not buildable now: Ofgem OFTO list (web page only, no data file; the CES `OFTO` rows and TCE "OFTO" cable agreements cover most assets), NESO Holistic Network Design / offshore coordination (PDF/report only; SSEP zones are the open spatial part), planned interconnectors (register gives only a connection-site name, no geometry).
