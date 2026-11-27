// Data sources and layers on top of the basemap. All filtering/colouring is done with MapLibre
// expressions on feature properties: no per-feature JS, no framework state.

import type { ExpressionSpecification, GeoJSONSource, LayerSpecification, Map as MlMap, VectorTileSource } from 'maplibre-gl'
import type { Manifest, PowerRoute } from './api'
import { setExtrasSuppressed } from './extras'
import { addShapeImages } from './shapes'
import { type State } from './state'

// keep in sync with src/gridmap/extra/dno_areas.py OP_COLOURS
export const OP_COLOURS: Record<string, string> = {
  UKPN: '#0072B2', NPG: '#E69F00', ENW: '#CC79A7', SPEN: '#56B4E9', NGED: '#996633', SSEN: '#009E73',
  NIE: '#999933', // Northern Ireland: area = ONS NI outline
  ESB: '#882255', // Republic of Ireland: area = Tailte Éireann provinces, merged
}

// Point outlines by theme: dark rim on the light basemap, light rim on the dark one (contrast for every fill colour)
export const OUTLINE = { light: 'rgba(17,24,39,0.7)', dark: 'rgba(255,255,255,0.8)' }

/** Re-apply theme-dependent outlines to point layers (called on load and on theme change). */
export function applyOutlines(map: MlMap, theme: 'light' | 'dark') {
  const o = OUTLINE[theme]
  const sel = (fallback: string) => ['case', ['boolean', ['feature-state', 'selected'], false], C.ink,
    ['boolean', ['feature-state', 'hover'], false], C.ink, fallback]
  if (map.getLayer('sites')) map.setPaintProperty('sites', 'icon-halo-color', sel(o) as ExpressionSpecification)
  if (map.getLayer('lv')) map.setPaintProperty('lv', 'icon-halo-color', sel(o) as ExpressionSpecification)
  for (const l of map.getStyle().layers) {
    if (l.id.startsWith('x_') && l.id.endsWith('_pt') && l.type === 'circle') map.setPaintProperty(l.id, 'circle-stroke-color', o)
  }
}

export const C = {
  green: '#1a9850',
  amber: '#f29e2e',
  red: '#d73027',
  none: '#7b8590', // 'not published': darker so it still reads against the grey basemap
  ink: '#1d2733',
  halo: '#ffffff',
  link: '#5b7083',
  outage: '#6d28d9', // violet: must never read as 'no headroom' red
  hier: '#1f6feb', // selected site's connection hierarchy
}

const ATTR_DNO =
  'Network data © UKPN, NPG, SP Electricity North West, SPEN, NGED, SSEN, NESO (open licences, modified) · <a href="#" data-sources>sources</a>'

let current: Manifest['layers'] = {}

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

function headroom(s: State): ExpressionSpecification {
  return ['get', s.mode === 'gen' ? 'g' : 'd']
}

export function colourExpr(s: State): ExpressionSpecification {
  const v = headroom(s)
  return ['case', ['==', v, null], C.none, ['<=', v, 0], C.red, ['<', v, s.thr], C.amber, C.green]
}

function siteFilter(s: State): ExpressionSpecification {
  const ops = s.neso ? [...s.ops, 'NESO'] : s.ops
  return ['all', ['in', ['get', 'op'], ['literal', ops]], ['in', ['get', 'k'], ['literal', s.kinds]]]
}

// icon-size (1 = 32 px). Types are clearly separated in size as well as shape.
const iconSize: ExpressionSpecification = [
  'interpolate', ['linear'], ['zoom'],
  5, ['match', ['get', 'k'], 1, 0.42, 2, 0.28, 0.15],
  9, ['match', ['get', 'k'], 1, 0.7, 2, 0.5, 0.33],
  13, ['match', ['get', 'k'], 1, 1.0, 2, 0.8, 0.58],
]

function siteColour(s: State): ExpressionSpecification {
  return ['case', ['==', ['get', 'op'], 'NESO'], C.ink, colourExpr(s)]
}

export function addLayers(map: MlMap, s: State) {
  map.addSource('sites', { type: 'geojson', data: EMPTY, attribution: ATTR_DNO })
  map.addSource('links', { type: 'geojson', data: EMPTY })
  map.addSource('outages', { type: 'geojson', data: EMPTY })

  map.addLayer({
    id: 'links', type: 'line', source: 'links', minzoom: 6,
    layout: { visibility: s.links ? 'visible' : 'none', 'line-cap': 'round' },
    paint: {
      'line-color': C.link,
      'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.4, 12, 1.4],
      'line-opacity': 0.55,
      'line-dasharray': [2, 2],
    },
  })

  // LV layer is added lazily once a pmtiles URL exists (see applyManifest).

  addShapeImages(map)
  // hierarchy of the selected site: feed path upwards (bold) + what it feeds (thin). Filled by setHierarchy().
  map.addSource('hier', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'hier-down', type: 'line', source: 'hier', filter: ['==', ['get', 'dir'], 'down'],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': C.hier, 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1, 12, 2], 'line-opacity': 0.75 },
  })
  // line-dasharray isn't data-driven: inferred links (LV -> nearest primary) get their own dashed layer
  map.addLayer({
    id: 'hier-up', type: 'line', source: 'hier',
    filter: ['all', ['==', ['get', 'dir'], 'up'], ['!=', ['get', 'how'], 'nearest']],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': C.hier, 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 12, 5] },
  })
  map.addLayer({
    id: 'hier-up-inferred', type: 'line', source: 'hier',
    filter: ['all', ['==', ['get', 'dir'], 'up'], ['==', ['get', 'how'], 'nearest']],
    paint: { 'line-color': C.hier, 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 12, 5],
      'line-dasharray': [1.5, 1.2] },
  })
  // power route of the selected item along published lines (solid, voltage colour) and straight jumps (dashed grey)
  routeMap = map
  map.addSource('route', { type: 'geojson', data: EMPTY })
  map.addLayer({
    id: 'route-jump', type: 'line', source: 'route', filter: ['==', ['get', 'kind'], 'jump'],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': '#8c8c8c', 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2, 12, 4],
      'line-dasharray': [1.5, 1.5] },
  })
  map.addLayer({
    id: 'route-line', type: 'line', source: 'route', filter: ['==', ['get', 'kind'], 'line'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    // same voltage colours as the dno_lines layer (src/gridmap/extra/lines.py)
    paint: { 'line-color': ['step', ['get', 'kv'], '#cc79a7', 30, '#009e9e', 100, '#2c7bb6'],
      'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 6] },
  })

  // Substation type = shape (◆ GSP, ■ BSP, ● Primary, ◇ transmission GSP); colour = headroom.
  map.addLayer({
    id: 'sites', type: 'symbol', source: 'sites',
    filter: siteFilter(s),
    layout: {
      'icon-image': ['case', ['==', ['get', 'op'], 'NESO'], 'shape-diamond-ring',
        ['match', ['get', 'k'], 1, 'shape-diamond', 2, 'shape-square', 'shape-circle']],
      'icon-size': iconSize,
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'symbol-sort-key': ['-', 4, ['get', 'k']],
      'symbol-z-order': 'source',
    },
    paint: {
      'icon-color': siteColour(s),
      'icon-halo-color': [
        'case',
        ['boolean', ['feature-state', 'selected'], false], C.ink,
        ['boolean', ['feature-state', 'hover'], false], C.ink,
        OUTLINE.light,
      ],
      'icon-halo-width': [
        'case',
        ['boolean', ['feature-state', 'selected'], false], 2.5,
        ['boolean', ['feature-state', 'hover'], false], 2,
        1.3,
      ],
    },
  })

  map.addLayer({
    id: 'site-labels', type: 'symbol', source: 'sites',
    filter: siteFilter(s),
    minzoom: 8,
    layout: {
      'text-field': ['get', 'n'],
      'text-font': ['Noto Sans Medium'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 8, ['match', ['get', 'k'], 3, 0, 10], 11, 11, 14, 12.5],
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-optional': true,
      'symbol-sort-key': ['get', 'k'],
    },
    paint: { 'text-color': C.ink, 'text-halo-color': C.halo, 'text-halo-width': 1.4 },
  })

  map.addLayer({
    id: 'outages', type: 'circle', source: 'outages',
    layout: { visibility: s.outages ? 'visible' : 'none' },
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 2.2, 9, 3.5, 13, 6],
      'circle-color': ['match', ['get', 'kind'], 'planned', '#ffffff', C.outage],
      'circle-stroke-color': ['match', ['get', 'kind'], 'planned', C.outage, '#ffffff'],
      'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 12, 1.8],
      'circle-opacity': 0.9,
    },
  })
}

/** LV substations: one tileset, both views. Shape = mount type (■ ground, ● pole, ◆ unknown); colour = utilisation;
 * monitored sites get a ring. Monitored sites are in the tiles from z4, the rest from z11. */
export const lvVisible = (s: State) => s.simple || s.kinds.includes(4)

export function lvFilter(s: State): ExpressionSpecification {
  const ops: ExpressionSpecification = ['in', ['get', 'op'], ['literal', s.ops]]
  if (!s.simple) return ops
  return ['all', ops, ['==', ['get', 'mon'], 1], ['in', ['coalesce', ['get', 'mt'], 'u'], ['literal', s.mounts]]]
}

const lvUtilColour: ExpressionSpecification = [
  'case', ['!', ['has', 'u']], C.none,
  ['<', ['to-number', ['get', 'u']], 60], C.green, ['<', ['to-number', ['get', 'u']], 85], C.amber, C.red,
]

function addLv(map: MlMap, url: string, s: State) {
  // promoteId: tile features carry their id as a property; without this, feature-state (selection) can't attach
  map.addSource('lv', { type: 'vector', url: `pmtiles://${location.origin}${url}`, promoteId: { lv: 'id' } })
  map.addLayer({
    id: 'lv-ring', type: 'circle', source: 'lv', 'source-layer': 'lv',
    filter: ['all', lvFilter(s), ['==', ['get', 'mon'], 1]],
    layout: { visibility: lvVisible(s) && !s.simple ? 'visible' : 'none' },
    paint: {
      'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': C.hier, 'circle-stroke-width': 1.8,
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 3.5, 11, 6.5, 14, 10, 18, 15],
    },
  } as LayerSpecification, 'links')
  map.addLayer({
    id: 'lv', type: 'symbol', source: 'lv', 'source-layer': 'lv',
    filter: lvFilter(s),
    layout: {
      visibility: lvVisible(s) ? 'visible' : 'none',
      'icon-image': ['match', ['get', 'mt'], 'g', 'shape-square', 'p', 'shape-circle', 'shape-diamond'],
      'icon-size': ['interpolate', ['linear'], ['zoom'], 4, 0.16, 9, 0.24, 11, 0.28, 14, 0.42, 18, 0.7],
      'icon-allow-overlap': true, 'icon-ignore-placement': true,
      'symbol-sort-key': ['coalesce', ['to-number', ['get', 'u']], 0],
    },
    paint: {
      'icon-color': lvUtilColour,
      'icon-halo-color': ['case', ['boolean', ['feature-state', 'selected'], false], C.ink, OUTLINE.light],
      'icon-halo-width': ['case', ['boolean', ['feature-state', 'selected'], false], 2.5, 1],
    },
  } as LayerSpecification, 'links')
}

/** Swap data sources in place when the server publishes a new manifest. No page reload. */
export function applyManifest(map: MlMap, m: Manifest, s: State) {
  const L = m.layers
  if (L.sites && L.sites !== current.sites) (map.getSource('sites') as GeoJSONSource).setData(L.sites)
  if (L.links && L.links !== current.links) (map.getSource('links') as GeoJSONSource).setData(L.links)
  if (L.lv && L.lv !== current.lv) {
    const src = map.getSource('lv') as VectorTileSource | undefined
    if (src) src.setUrl(`pmtiles://${location.origin}${L.lv}`)
    else addLv(map, L.lv, s)
  }
  current = { ...L }
}

export function refreshOutages(map: MlMap) {
  ;(map.getSource('outages') as GeoJSONSource | undefined)?.setData(`/api/live/outages?t=${Date.now()}`)
}

export function applyState(map: MlMap, s: State) {
  document.body.classList.toggle('simple', s.simple)
  const vis = (id: string, on: boolean) => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none') }
  if (map.getLayer('lv')) {
    map.setFilter('lv', lvFilter(s))
    map.setFilter('lv-ring', ['all', lvFilter(s), ['==', ['get', 'mon'], 1]])
  }
  vis('lv', lvVisible(s))
  vis('lv-ring', lvVisible(s) && !s.simple)
  setExtrasSuppressed(map, s.simple)
  if (s.simple) {
    // LV monitoring mode: only monitored LV substations (+ operator areas and the selected item's hierarchy)
    for (const id of ['sites', 'site-labels', 'links', 'outages']) vis(id, false)
    return
  }
  vis('sites', true)
  vis('site-labels', true)
  const f = siteFilter(s)
  map.setFilter('sites', f)
  map.setFilter('site-labels', f)
  map.setPaintProperty('sites', 'icon-color', siteColour(s))
  map.setLayoutProperty('links', 'visibility', s.links ? 'visible' : 'none')
  map.setLayoutProperty('outages', 'visibility', s.outages ? 'visible' : 'none')

}

export interface HierNode { site_id?: string; name?: string; kind?: string; lat: number | null; lon: number | null }

/** Draw the selected item's connection hierarchy: `from` up through `chain` (nearest first), and down to `children`. */
type HierFeat = { type: 'Feature'; properties: Record<string, string | number>; geometry: { type: 'LineString'; coordinates: [number, number][] } }
let hierFeats: HierFeat[] = []
let routeMap: MlMap | undefined

export function setHierarchy(map: MlMap, from: [number, number] | null, chain: HierNode[], children: HierNode[], how = 'published') {
  const feats: HierFeat[] = []
  let prev = from
  let i = 0
  for (const n of chain) {
    if (n.lat == null || n.lon == null) continue
    const cur: [number, number] = [n.lon, n.lat]
    if (prev) feats.push({ type: 'Feature', properties: { dir: 'up', how: feats.length === 0 ? how : 'published', i: i++ },
      geometry: { type: 'LineString', coordinates: [prev, cur] } })
    prev = cur
  }
  if (from) for (const c of children) {
    if (c.lat == null || c.lon == null) continue
    feats.push({ type: 'Feature', properties: { dir: 'down' }, geometry: { type: 'LineString', coordinates: [from, [c.lon, c.lat]] } })
  }
  hierFeats = feats
  ;(map.getSource('hier') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: feats })
  ;(map.getSource('route') as GeoJSONSource | undefined)?.setData(EMPTY)
}

/** Draw the selected item's power route (null clears it). The straight hierarchy links it covers are hidden. */
export function setRoute(r: PowerRoute | null) {
  const map = routeMap
  if (!map) return
  ;(map.getSource('route') as GeoJSONSource | undefined)?.setData(r ?? EMPTY)
  const n = r?.features.length ? r.hops.length : 0
  ;(map.getSource('hier') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection',
    features: hierFeats.filter((f) => f.properties.dir !== 'up' || Number(f.properties.i) >= n) })
}
