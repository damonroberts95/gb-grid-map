// Plain-English names for layers and groups (newcomer-friendly). Server titles stay in the hover help.
export const FRIENDLY_TITLE: Record<string, string> = {
  anm_zones: 'Areas where generation can be curtailed',
  flex_zones: 'Areas buying flexibility',
  gsp_regions: 'Grid supply areas',
  offshore_ssep: 'Offshore planning zones',
  dno_areas: 'Network operator areas',
  primary_areas: 'Local areas by spare capacity',
  ev_chargers: 'EV charge points',
  dfes: 'Demand growth forecast (to 2035)',
  elec_postcode_kwh: 'Home electricity use by postcode',
  lv_headroom_forecast: 'Local substation capacity forecast (NW England)',
  lv_monitored: 'Monitored local substations',
  lv_overhead: 'Local overhead lines',
  lv_smart: 'Smart-meter peak demand',
  lct: 'Heat pumps, EVs & solar (local substations)',
  lct_primary: 'Heat pumps, EVs & solar (by area)',
  ecr_projects: 'Generation & storage projects (local)',
  offshore_wind: 'Offshore wind sites',
  repd: 'Renewable projects (planned & built)',
  tec: 'Large projects on the transmission grid',
  carbon_regions: 'How green the electricity is now',
  live_constraints: 'Network constraints now (East & London)',
  curtailment: 'Generation switched off for constraints',
  live_gsp: 'Power flow at grid supply points now',
  live_generators: 'Big power stations: output now',
  nerda_sites: 'Live substation readings (South England)',
  live_pv: 'Solar output now',
  landfalls: 'Where subsea cables come ashore',
  etys_boundaries: 'Transmission bottlenecks',
  power_substations: 'Big grid substations',
  offshore_cable_zones: 'Offshore cable routes',
  dno_lines: 'Overhead power lines (local network)',
  power_lines: 'Power lines',
  ssent_substations: 'Transmission substations (North Scotland)',
  offshore_cables: 'Subsea cables & interconnectors',
  power_cables: 'Underground cables',
}

export const FRIENDLY_GROUP: Record<string, string> = {
  'Demand & LV': 'Local network & demand',
  'Generation & planning': 'Generation & new projects',
  Network: 'Power lines & big substations',
  Areas: 'Areas',
  Live: 'Live now',
}

/** Shown by default for newcomers; everything else behind "Show all layers". */
export const POPULAR = ['primary_areas', 'lv_monitored', 'live_pv', 'carbon_regions', 'repd', 'ev_chargers']
