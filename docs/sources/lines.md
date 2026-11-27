# Network lines and transmission (extras: lines, osm_power, transmission)

Recon 2026-11-12. All counts are from the fetched snapshots.

## Used
| Source id(s) | What | Rows | Licence | Layer |
|---|---|---|---|---|
| `osm_gb_pbf` | Geofabrik GB extract (2.2 GB), osmium-filtered to power=line/minor_line/cable/substation | 223,789 line/cable ways, 1,734 substations >=132 kV | ODbL | `power_lines`, `power_cables`, `power_substations` |
| `ukpn_oh_132kv/66kv/33kv/hv`, `ukpn_oh_lv` | UKPN overhead lines (CC BY; key-visible) | 685 / 4 / 6,081 / 103,421 / 215,301 | CC BY 4.0 | `dno_lines`, `lv_overhead` |
| `npg_oh_ehv/hv/lv` | NPG overhead feeders | 853 / 21,266 / 27,441 | NPG Open Data Licence v1.0 | same |
| `enw_oh_132kv/33kv/11kv/66kv/lv` | ENW overhead conductors (66kv id = 6.6 kV) | 4,182 / 2,882 / 36,004 / 4,700 / 33,292 | CC BY 4.0 | same |
| `ssen_overhead_lines_routes` | SSEN `ssen_overhead_lines` route shapefiles (5 zips, BNG): 2-132 kV existing overhead | 140,000 segments (125k 11 kV, 13k 33 kV, 1.1k 132 kV) | CC BY 4.0 | `dno_lines` |
| `ssent_sites_supergrid/grid` + `ssent_etys_fault_levels_winter_y1` | SSEN-T site polygons (centroid) + ETYS fault levels | 162 sites, 125 matched to fault levels | CC BY 4.0 | `ssent_substations` |
| `neso_etys_boundaries` | ETYS study boundary lines (EPSG:3857 shapefile) | 34 | NESO Open Data Licence | `etys_boundaries` |

## Not used
- SPEN `spm/spd_gis_shapefiles_*_line_assets` (2.9M/3.2M lines): form-request, shared licence, `data_visible: false`.
- UKPN `*-underground-cables` (33/132 kV, HV, LV 2M rows), NPG `lv-underground-cables`, `npg-hv-underground-cables`, `npg-ehv-underground-feeders`, ENW `sp-enw-gis-conductors-<LA>` (~50 sets): "Shared Data Licence", gated even with key.
- SSEN `gis_network_line_data`: shared classification, no resources (PDF only).
- NGED `*kv_poles_full_nged_area_*`: WPD Shared Data Licence, registered-only; points not lines.
- UKPN poles/towers (CC BY points, 282k HV, 370k LV, 53k 33 kV, 7k 132 kV): not built, could be a "poles" layer.
- National Grid ET / SPT: no open GIS found (NESO has only ETYS boundaries); covered via OSM substations.
- SSEN-T `overhead-line-grid`, `tower-grid`, TEC/embedded registers: `visibility: domain`, empty with the key.

## Format notes
- ODS geojson exports: NPG geometries are MultiLineString, ENW LV has 2 null geometries, UKPN LV has no voltage.
- NPG voltage is volts as text ("66000.0"); LV = 230. ENW voltage strings "11kV"/"415V"/"6.6kV". SSEN "11.000 kV".
- SSEN shapefiles: dbf field names truncated to 10 chars (`nominal_vo`), BNG; ETYS boundaries are Web Mercator.
- No shapefile library is installed; `extra/lines.py` has a small pure-Python polyline reader (`read_shp_zip`).

## Power routes (`gridmap/routes.py`, `GET /api/route`)
- Refresh builds one graph per operator and band (hv 1-25 kV and unknown, ehv 25-100 kV, 132 kV) from the same
  readers as `dno_lines`. Output: `data/derived/routes/{op}_{band}.{nodes,edges}.parquet` + `manifest.json`
  (snapshot names per source). Rebuilt when a line snapshot or `routes.py` / `extra/lines.py` changes. ~12 s, ~24 MB.
- Nodes are line ends and shared vertices snapped to ~5 m. Dangling ends get straight bridges (up to 3 km) to the
  dangling ends of other graph pieces: that is where a line went underground.
- Query: A* with jumps costing 3x their length, so the path follows lines and jumps only over gaps. Each end may
  join the graph within 2 km (LV 300 m / substation 800 m at 1x, further at 3x). Bridges under 10 m are drawn as line.
- Hops: LV -> primary on hv; primary -> BSP on ehv (then hv); a selected BSP -> GSP on 132 kV (then ehv).
  Only upward links are followed. SPEN, NGED and NIE publish no lines, so their routes are straight.
- Real data (2026-11-27, random sample of 30 LV sites per DNO, mean share of route length on lines): NPG 55%,
  SSEN 52%, ENW 43%, UKPN 26%, NGED/SPEN 0%. Town centres are ~0% (all cable). First call per operator loads its
  graphs in ~50-200 ms; later calls take ~3 ms.
- The route is a plausible path on published overhead lines, not the operator's feeder record.
