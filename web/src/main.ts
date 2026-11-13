import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import { addProtocol, Map as MlMap, type MapGeoJSONFeature, type StyleSpecification } from 'maplibre-gl'
import { Protocol } from 'pmtiles'
import { api, type SearchHit } from './api'
import { buildControls } from './controls'
import { hide, initDetail, onHierarchy, showLv, showSite } from './detail'
import { h } from './dom'
import { initGlossary } from './glossary'
import { maybeShowWelcome } from './help'
import { applyExtras, extraLayerIds, extraPopup, resetExtras } from './extras'
import { addShapeImages } from './shapes'
import { addLayers, applyManifest, applyOutlines, applyState, refreshOutages, setHierarchy, type HierNode } from './layers'
import { connectLive, refreshNational, setBuiltAt } from './live'
import { onChange, reset as resetState, state } from './state'
import { applyThemeToDocument, basemap, INK, initialTheme, onTheme, setTheme, theme } from './theme'

const pmtiles = new Protocol()
addProtocol('pmtiles', pmtiles.tile)

applyThemeToDocument(initialTheme())
const base = basemap(initialTheme())
const style: StyleSpecification = {
  version: 8,
  glyphs: '/basemap/fonts/{fontstack}/{range}.pbf',
  sprite: base.sprite,
  sources: {
    protomaps: {
      type: 'vector',
      url: `pmtiles://${location.origin}/basemap/gb.pmtiles`,
      attribution: '© OpenStreetMap contributors · Protomaps',
    },
  },
  layers: base.layers,
}

const map = new MlMap({
  container: 'map',
  style,
  center: [-2.5, 54.5],
  zoom: 5.3,
  maxBounds: [[-11, 49], [4, 61.5]],
  hash: 'map',
  attributionControl: { compact: true },
  fadeDuration: 0,
})
if (import.meta.env.DEV) (window as unknown as { __map: MlMap }).__map = map // perf checks
map.touchZoomRotate.disableRotation()
map.dragRotate.disable()

// ---- selection (feature-state, no re-render of data) ----
let selected: { source: string; sourceLayer?: string; id: string | number } | null = null
let hovered: string | number | null = null

function select(f: { source: string; sourceLayer?: string; id: string | number | undefined } | null) {
  if (selected) map.setFeatureState(selected, { selected: false })
  // features without an id (e.g. tiles built before promoteId) can't hold feature-state
  selected = f && f.id !== undefined && f.id !== null ? (f as typeof selected) : null
  if (selected) map.setFeatureState(selected, { selected: true })
}

initDetail(() => select(null), (siteId) => openSiteById(siteId, true))

function openSiteById(siteId: string, fly: boolean) {
  const f = map.querySourceFeatures('sites', { filter: ['==', ['get', 'sid'], siteId] })[0]
  if (f?.id !== undefined) {
    select({ source: 'sites', id: f.id })
    if (fly && f.geometry.type === 'Point') {
      map.flyTo({ center: f.geometry.coordinates as [number, number], zoom: Math.max(map.getZoom(), 10) })
    }
  }
  void showSite(siteId)
}

function onSearch(hit: SearchHit) {
  if (hit.type === 'site') {
    map.once('idle', () => openSiteById(hit.id, false))
  } else {
    void showLv(hit.id, { op: hit.op })
  }
}

// images added at runtime (substation shape icons) are dropped when the style reloads (theme switch): re-add
map.on('styleimagemissing', (e) => { if (e.id.startsWith('shape-')) addShapeImages(map) })

onHierarchy((from, chain, children, how) =>
  setHierarchy(map, from, chain as unknown as HierNode[], children as unknown as HierNode[], how))

// "Zoom in to see local substations" hint (LV tiles start at z11; newcomers otherwise think LV is missing)
const zoomHint = h('div', { class: 'zoom-hint', hidden: true }, 'Zoom in to see local (street-level) substations')
document.body.append(zoomHint)
const updateHint = () => { zoomHint.hidden = !(state.lv && !state.simple && map.getZoom() < 11) }
map.on('zoomend', updateHint)
onChange(updateHint)

map.on('load', async () => {
  updateHint()
  // start with the attribution collapsed to the (i) button; it lists many sources
  document.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
  addLayers(map, state)
  const ink = () => {
    const c = INK[theme()]
    map.setPaintProperty('site-labels', 'text-color', c.text)
    map.setPaintProperty('site-labels', 'text-halo-color', c.halo)
    applyOutlines(map, theme())
    // extra-layer casings and label halos follow the theme (white glow on dark looks wrong)
    for (const l of map.getStyle().layers) {
      if (l.id.endsWith('_casing')) map.setPaintProperty(l.id, 'line-color', theme() === 'dark' ? 'rgba(15,20,25,0.8)' : 'rgba(255,255,255,0.7)')
      if (l.id.startsWith('x_') && l.id.endsWith('_label')) map.setPaintProperty(l.id, 'text-halo-color', c.halo)
    }
  }
  ink()
  onTheme(ink)
  window.addEventListener('gridmap:layers', ink)
  window.addEventListener('gridmap:reset', () => {
    hide()
    resetState()
    resetExtras(map)
    document.querySelectorAll('.maplibregl-popup').forEach((p) => p.remove())
    map.flyTo({ center: [-2.5, 54.5], zoom: 5.3, bearing: 0, pitch: 0 })
  })
  window.addEventListener('gridmap:toggle-theme', () => setTheme(map, theme() === 'dark' ? 'light' : 'dark'))
  onChange((s) => applyState(map, s))

  map.on('mousemove', 'sites', (e) => {
    const f = e.features?.[0]
    if (!f || f.id === undefined) return
    if (hovered !== null && hovered !== f.id) map.setFeatureState({ source: 'sites', id: hovered }, { hover: false })
    hovered = f.id
    map.setFeatureState({ source: 'sites', id: hovered }, { hover: true })
    map.getCanvas().style.cursor = 'pointer'
  })
  map.on('mouseleave', 'sites', () => {
    if (hovered !== null) map.setFeatureState({ source: 'sites', id: hovered }, { hover: false })
    hovered = null
    map.getCanvas().style.cursor = ''
  })

  map.on('click', (e) => {
    const xl = extraLayerIds().filter((id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none')
    const vis = (id: string) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none'
    const hits = map.queryRenderedFeatures(e.point, { layers: ['sites', 'lv', 'lvmon', 'outages', ...xl].filter((id) => vis(id)) })
    const site = hits.find((f) => f.layer.id === 'sites')
    const lv = hits.find((f) => f.layer.id === 'lv' || f.layer.id === 'lvmon')
    const out = hits.find((f) => f.layer.id === 'outages')
    const x = hits.find((f) => f.layer.id.startsWith('x_'))
    if (site) {
      select({ source: 'sites', id: site.id as number })
      void showSite(String(site.properties.sid))
    } else if (lv) {
      select({ source: lv.layer.id, sourceLayer: lv.layer.id, id: lv.id as string })
      void showLv(String(lv.properties.id), lv.properties,
        lv.geometry.type === 'Point' ? (lv.geometry.coordinates as [number, number]) : undefined)
    } else if (out) {
      outagePopup(out)
    } else if (x) {
      const body = extraPopup(x.layer.id, x.properties)
      if (body) void import('maplibre-gl').then(({ Popup }) =>
        new Popup({ maxWidth: '280px' }).setLngLat(e.lngLat).setDOMContent(body).addTo(map))
    } else {
      hide()
    }
  })

  try {
    const m = await api.manifest()
    applyManifest(map, m, state)
    applyExtras(map, m.extras)
    applyState(map, state)
    window.dispatchEvent(new Event('gridmap:layers'))
    setBuiltAt(m.built_at)
  } catch {
    setBuiltAt(null)
  }
  refreshOutages(map)
  connectLive({
    manifest: (m) => { applyManifest(map, m, state); applyExtras(map, m.extras); applyState(map, state); window.dispatchEvent(new Event('gridmap:layers')) },
    live: (feed) => {
      if (feed === 'outages') refreshOutages(map)
      if (feed === 'national') void refreshNational()
    },
  })
})

initGlossary()
buildControls(map, onSearch)
maybeShowWelcome()
void refreshNational()
setInterval(refreshNational, 60_000)

function outagePopup(f: MapGeoJSONFeature) {
  const p = f.properties
  void import('maplibre-gl').then(({ Popup }) => {
    if (f.geometry.type !== 'Point') return
    new Popup({ closeButton: true, maxWidth: '260px' })
      .setLngLat(f.geometry.coordinates as [number, number])
      .setDOMContent(h('div', { class: 'popup' },
        h('strong', {}, `${p.op ?? ''} ${p.kind === 'planned' ? 'planned work' : 'power cut'}`),
        h('div', {}, `Ref ${p.ref ?? '—'} · ${p.status ?? ''}`),
        p.cust ? h('div', {}, `${p.cust} customers affected`) : null,
        p.start ? h('div', { class: 'muted' }, `Since ${new Date(String(p.start)).toLocaleString()}`) : null,
        p.etr ? h('div', { class: 'muted' }, `Est. restore ${new Date(String(p.etr)).toLocaleString()}`) : null))
      .addTo(map)
  })
}

// Attribution "sources" link -> freshness/licence table
document.addEventListener('click', async (e) => {
  const a = (e.target as HTMLElement).closest('[data-sources]')
  if (!a) return
  e.preventDefault()
  const dlg = h('dialog', { class: 'panel sources' }) as HTMLDialogElement
  dlg.append(h('h2', {}, 'Data sources'), h('p', { class: 'muted' }, 'Loading…'))
  document.body.append(dlg)
  dlg.addEventListener('close', () => dlg.remove())
  dlg.showModal()
  try {
    const rows = await api.sources()
    dlg.replaceChildren(
      h('header', { class: 'd-head' }, h('h2', {}, 'Data sources'),
        h('button', { type: 'button', class: 'close', 'aria-label': 'Close', onclick: () => dlg.close() }, '×')),
      h('div', { class: 'scroll' }, h('table', {},
        h('thead', {}, h('tr', {}, ...['Source', 'Operator', 'Status', 'Rows', 'Data as of', 'Fetched', 'Licence']
          .map((x) => h('th', {}, x)))),
        h('tbody', {}, ...rows.map((r) => h('tr', { title: r.attribution },
          h('td', {}, r.source_id), h('td', {}, r.operator), h('td', { class: `st-${r.status}` }, r.status),
          h('td', {}, r.rows?.toLocaleString() ?? '—'), h('td', {}, r.as_of?.slice(0, 10) ?? '—'),
          h('td', {}, r.fetched_at?.slice(0, 16).replace('T', ' ') ?? '—'), h('td', {}, r.licence)))))),
    )
  } catch {
    dlg.replaceChildren(h('p', {}, 'Could not load sources.'))
  }
})
