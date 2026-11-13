// Generic renderer for "extra" layers declared by server-side plug-ins (manifest.extras).
// Each extra carries its own styling hints, popup fields and description, so new public datasets
// appear in the UI without frontend changes.

import type { ExpressionSpecification, GeoJSONSource, LayerSpecification, Map as MlMap, VectorTileSource } from 'maplibre-gl'
import { fmt, h, hashGet, hashSet } from './dom'
import { FRIENDLY_GROUP, FRIENDLY_TITLE, POPULAR } from './friendly'

export interface ExtraMeta {
  id: string
  title: string
  group: string
  type: 'geojson' | 'pmtiles'
  geom: 'point' | 'line' | 'polygon'
  description: string
  source_layer?: string | null
  minzoom: number
  default_on: boolean
  color: string | { prop: string; type: 'categorical' | 'steps'; stops: [string | number, string][]; default?: string }
  size: number | { prop: string; stops: [number, number][] }
  opacity: number
  popup: [string, string, string | null][]
  legend: [string, string][]
  attribution: string
  url: string
  live?: boolean
  label?: string | null
  label_maxzoom?: number
}

const GROUP_ORDER = ['Live', 'Demand & LV', 'Network', 'Generation & planning', 'Areas'] // live data is the priority
let showAll = (() => { try { return localStorage.getItem('gridmap.showAllLayers') === '1' } catch { return false } })()
const title = (m: ExtraMeta) => FRIENDLY_TITLE[m.id] ?? m.title
let metas: Record<string, ExtraMeta> = {}
const urls: Record<string, string> = {}
let enabled = new Set<string>(readHash())
// default-on layers the user explicitly switched off (hash 'xo'); everything else default_on starts on
const switchedOff = new Set<string>((hashGet('xo') ?? '').split(',').filter(Boolean))
const changeListeners: (() => void)[] = []

function readHash(): string[] {
  const v = hashGet('x')
  return v ? v.split(',').filter(Boolean) : []
}

function writeHash() {
  hashSet({ x: [...enabled].join(','), xo: [...switchedOff].join(',') })
}

function colorExpr(c: ExtraMeta['color']): string | ExpressionSpecification {
  if (typeof c === 'string') return c
  const fallback = c.default ?? '#9aa3ad'
  if (c.type === 'categorical') {
    return ['match', ['to-string', ['get', c.prop]], ...c.stops.flatMap(([v, col]) => [String(v), col]), fallback] as ExpressionSpecification
  }
  // steps: stops sorted ascending [[threshold, colour]]; first colour applies below the 2nd threshold
  const [first, ...rest] = c.stops
  return ['case', ['!', ['has', c.prop]], fallback,
    ['step', ['to-number', ['get', c.prop]], first[1], ...rest.flatMap(([v, col]) => [Number(v), col])]] as ExpressionSpecification
}

function sizeExpr(s: ExtraMeta['size'], base: number): number | ExpressionSpecification {
  const zoomed = (v: number | ExpressionSpecification): ExpressionSpecification =>
    ['interpolate', ['linear'], ['zoom'], 5, ['*', v, 0.6], 10, v, 15, ['*', v, 1.8]] as ExpressionSpecification
  if (typeof s === 'number') return zoomed(s || base)
  return zoomed(['interpolate', ['linear'], ['coalesce', ['to-number', ['get', s.prop]], 0],
    ...s.stops.flat()] as ExpressionSpecification)
}

function layerSpecs(m: ExtraMeta): LayerSpecification[] {
  const src = `x_${m.id}`
  const common = {
    source: src, minzoom: m.minzoom,
    layout: { visibility: enabled.has(m.id) ? 'visible' : 'none' },
    ...(m.type === 'pmtiles' ? { 'source-layer': m.source_layer ?? m.id } : {}),
  } as const
  const labels: LayerSpecification[] = m.label ? [{
    ...common, id: `${src}_label`, type: 'symbol', maxzoom: m.label_maxzoom ?? 24,
    filter: ['==', ['geometry-type'], 'Point'],
    layout: {
      ...common.layout, 'text-field': ['get', m.label], 'text-font': ['Noto Sans Medium'],
      'text-size': ['interpolate', ['linear'], ['zoom'], 4, 11, 8, 15], 'text-allow-overlap': false,
      'text-letter-spacing': 0.04, 'text-max-width': 9,
    },
    paint: { 'text-color': colorExpr(m.color), 'text-halo-color': 'rgba(255,255,255,0.9)', 'text-halo-width': 1.6 },
  } as LayerSpecification] : []
  if (m.geom === 'polygon') {
    const poly = ['==', ['geometry-type'], 'Polygon'] as ExpressionSpecification
    const width = typeof m.size === 'number' && m.size > 0 ? m.size : 0.8
    return [
      { ...common, id: `${src}_fill`, type: 'fill', filter: poly,
        // operator areas are context only: keep the tint faint so points stay readable
        paint: { 'fill-color': colorExpr(m.color), 'fill-opacity': m.opacity * (m.id === 'dno_areas' ? 0.25 : 0.45) } } as LayerSpecification,
      // light casing under the border so boundaries read on both light and dark basemaps
      ...(width >= 1.5 ? [{ ...common, id: `${src}_casing`, type: 'line', filter: poly,
        paint: { 'line-color': 'rgba(255,255,255,0.7)', 'line-width': width + 2 } } as LayerSpecification] : []),
      { ...common, id: `${src}_line`, type: 'line', filter: poly,
        paint: { 'line-color': colorExpr(m.color), 'line-width': width, 'line-opacity': Math.max(m.opacity, 0.85) } } as LayerSpecification,
      ...labels,
    ]
  }
  if (m.geom === 'line') {
    return [{ ...common, id: `${src}_line`, type: 'line', layout: { ...common.layout, 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': colorExpr(m.color), 'line-width': sizeExpr(m.size, 1.2), 'line-opacity': m.opacity } } as LayerSpecification]
  }
  if (m.id.startsWith('lv_monitored')) {
    // overlay on LV dots: hollow ring so the underlying utilisation colour stays visible
    return [{ ...common, id: `${src}_pt`, type: 'circle',
      paint: {
        'circle-color': 'rgba(0,0,0,0)', 'circle-radius': sizeExpr(m.size, 6),
        'circle-stroke-color': colorExpr(m.color), 'circle-stroke-width': 2.5, 'circle-stroke-opacity': 1,
      } } as LayerSpecification]
  }
  return [{ ...common, id: `${src}_pt`, type: 'circle',
    paint: {
      'circle-color': colorExpr(m.color), 'circle-radius': sizeExpr(m.size, 3), 'circle-opacity': m.opacity,
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.6,
    } } as LayerSpecification]
}

/** Polygons go under everything, lines under substations, points just under substations. */
function beforeId(map: MlMap, m: ExtraMeta): string | undefined {
  const target = m.geom === 'point' ? 'sites' : 'links'
  return map.getLayer(target) ? target : undefined
}

export function extraLayerIds(): string[] {
  return Object.values(metas).flatMap((m) => layerSpecs(m).map((l) => l.id)).filter(Boolean)
}

export function applyExtras(map: MlMap, extras: Record<string, ExtraMeta> | undefined) {
  if (!extras) return
  for (const [id, m] of Object.entries(extras)) {
    const src = `x_${id}`
    const abs = m.type === 'pmtiles' ? `pmtiles://${location.origin}${m.url}` : m.url
    if (!map.getSource(src)) {
      if (m.type === 'pmtiles') map.addSource(src, { type: 'vector', url: abs, attribution: m.attribution })
      else map.addSource(src, { type: 'geojson', data: m.url, attribution: m.attribution })
      if (m.default_on && !switchedOff.has(id)) enabled.add(id)
      for (const l of layerSpecs(m)) map.addLayer(l, beforeId(map, m))
    } else if (urls[id] !== m.url) {
      if (m.type === 'pmtiles') (map.getSource(src) as VectorTileSource).setUrl(abs)
      else (map.getSource(src) as GeoJSONSource).setData(m.url)
    }
    urls[id] = m.url
  }
  metas = { ...extras }
  for (const l of changeListeners) l()
}

function setEnabled(map: MlMap, id: string, on: boolean) {
  if (on) enabled.add(id)
  else enabled.delete(id)
  if (metas[id]?.default_on) {
    if (on) switchedOff.delete(id)
    else switchedOff.add(id)
  }
  const m = metas[id]
  if (m) for (const l of layerSpecs(m)) if (map.getLayer(l.id)) map.setLayoutProperty(l.id, 'visibility', on ? 'visible' : 'none')
  writeHash()
}

/** 'Clear all': every extra off, including default-on ones (remembered as switched off). */
export function clearExtras(map: MlMap) {
  for (const id of Object.keys(metas)) setEnabled(map, id, false)
  for (const l of changeListeners) l()
}

/** Hide/restore all extras without changing the user's choices (LV monitoring mode keeps only operator areas). */
export function setExtrasSuppressed(map: MlMap, suppressed: boolean, keep: string[] = ['dno_areas']) {
  for (const m of Object.values(metas)) {
    const on = enabled.has(m.id) && (!suppressed || keep.includes(m.id))
    for (const l of layerSpecs(m)) if (map.getLayer(l.id)) map.setLayoutProperty(l.id, 'visibility', on ? 'visible' : 'none')
  }
}

/** Turn every extra layer off (used by 'reset view'). */
export function resetExtras(map: MlMap) {
  for (const id of Object.keys(metas)) {
    switchedOff.delete(id)
    setEnabled(map, id, !!metas[id].default_on)
  }
  for (const l of changeListeners) l()
}

const openGroups = new Map<string, boolean>()

function isOpen(g: string, items: ExtraMeta[]): boolean {
  const remembered = openGroups.get(g)
  if (remembered !== undefined) return remembered
  return g === 'Live' || items.some((m) => enabled.has(m.id)) // first render: Live + groups with active layers
}

/** Panel section listing extras grouped, with toggles, hover descriptions and mini legends. */
export function extrasPanel(map: MlMap): HTMLElement {
  const box = h('div', { class: 'group extras' })
  const render = () => {
    const item = (m: ExtraMeta) => {
      const input = h('input', { type: 'checkbox' }) as HTMLInputElement
      input.checked = enabled.has(m.id)
      input.addEventListener('change', () => { setEnabled(map, m.id, input.checked); render() })
      return h('div', { class: 'xitem' },
        h('label', { class: 'toggle' }, input,
          h('span', { class: 'term', 'data-xdesc': m.id, tabindex: '0' }, title(m)),
          m.live ? h('small', { class: 'live-tag' }, ' live') : null,
          m.minzoom > 6 ? h('small', { class: 'muted' }, ' (zoom in)') : null),
        enabled.has(m.id) && m.legend.length
          ? h('div', { class: 'xleg' }, ...m.legend.map(([a, b]) => {
            const [c, t] = /^#|^rgb/.test(a) ? [a, b] : [b, a]
            return h('span', {}, h('i', { style: `background:${c}` }), t)
          }))
          : null)
    }
    const all = Object.values(metas)
    const toggleAll = h('button', { type: 'button', class: 'linklike small', onclick: () => {
      showAll = !showAll
      try { localStorage.setItem('gridmap.showAllLayers', showAll ? '1' : '0') } catch { /* ignore */ }
      render()
    } }, showAll ? 'Show fewer layers' : `Show all ${all.length} layers`)
    if (!showAll) {
      // newcomers: a short list of the most useful layers (+ anything already switched on)
      const pick = all.filter((m) => POPULAR.includes(m.id) || enabled.has(m.id))
        .sort((a, b) => (POPULAR.indexOf(a.id) + 99) % 99 - (POPULAR.indexOf(b.id) + 99) % 99)
      box.replaceChildren(...pick.map(item), toggleAll)
      return
    }
    const groups = new Map<string, ExtraMeta[]>()
    for (const m of all) groups.set(m.group, [...(groups.get(m.group) ?? []), m])
    const ordered = [...groups.keys()].sort((a, b) => (GROUP_ORDER.indexOf(a) + 99) % 99 - (GROUP_ORDER.indexOf(b) + 99) % 99)
    box.replaceChildren(...ordered.map((g) =>
      h('details', {
        class: 'xgroup', open: isOpen(g, groups.get(g) ?? []),
        ontoggle: (e: Event) => { openGroups.set(g, (e.currentTarget as HTMLDetailsElement).open) },
      },
      h('summary', { class: 'label' }, FRIENDLY_GROUP[g] ?? g, g === 'Live' ? h('span', { class: 'live-dot', 'aria-label': 'live' }) : null,
        h('span', { class: 'count' }, ` ${groups.get(g)?.length ?? 0}`)),
      ...(groups.get(g) ?? []).sort((a, b) => title(a).localeCompare(title(b))).map(item))), toggleAll)
  }
  changeListeners.push(render)
  render()
  return box
}

export function extraDescription(id: string): { t: string; d: string } | null {
  const m = metas[id]
  return m ? { t: title(m), d: `${m.description}${title(m) !== m.title ? ` (Source layer: ${m.title}.)` : ''}` } : null
}

/** Popup content for a clicked extra feature, or null if the feature isn't from an extra. */
export function extraPopup(layerId: string, props: Record<string, unknown>): HTMLElement | null {
  const id = layerId.replace(/^x_/, '').replace(/_(pt|line|fill|casing|label)$/, '')
  const m = metas[id]
  if (!m) return null
  const rows = m.popup.length ? m.popup : Object.keys(props).slice(0, 8).map((k) => [k, k, null] as [string, string, null])
  const [titleRow, ...rest] = rows
  const val = (k: string, unit: string | null) => {
    const v = props[k]
    if (v === null || v === undefined || v === '') return null
    return typeof v === 'number' ? fmt(v, 1, unit ?? '') : `${String(v)}${unit ? ` ${unit}` : ''}`
  }
  return h('div', { class: 'popup' },
    h('div', { class: 'muted small' }, m.title),
    h('strong', {}, val(titleRow[0], titleRow[2]) ?? m.title),
    ...rest.map(([k, label, unit]) => {
      const v = val(k, unit)
      return v === null ? null : h('div', {}, h('span', { class: 'muted' }, `${label}: `), v)
    }),
    id === 'nerda_sites' && props.nrn ? nerdaReadings(String(props.nrn)) : null)
}

interface NerdaFamily { v: number; unit: string; ts: string | null }
interface NerdaLine { line: string; readings: Record<string, NerdaFamily> }

/** "Live readings" button for NeRDA substations: fetches /api/nerda/<nrn> (can take ~30 s: one request per measurement). */
function nerdaReadings(nrn: string): HTMLElement {
  const out = h('div', { class: 'nerda-live' })
  const btn = h('button', { type: 'button', class: 'btn' }, 'Live readings')
  btn.onclick = async () => {
    btn.setAttribute('disabled', '')
    out.replaceChildren(h('p', { class: 'muted small' }, 'Fetching from NeRDA (up to ~30 s)…'))
    try {
      const r = await fetch(`/api/nerda/${encodeURIComponent(nrn)}`)
      if (!r.ok) throw new Error(String(((await r.json()) as { error?: string }).error ?? r.status))
      const d = (await r.json()) as { lines: NerdaLine[]; fetched: string }
      const cols: [string, string][] = [['LineCurrent', 'A'], ['PhaseVoltage', 'V'], ['ActivePower', 'kW'], ['ReactivePower', 'kVAr'],
        ['ApparentPower', 'kVA'], ['PowerFactor', 'PF'], ['THD', 'THD %'], ['Frequency', 'Hz']]
      const used = cols.filter(([k]) => d.lines.some((l) => l.readings[k]))
      out.replaceChildren(
        h('table', { class: 'small' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Feeder'), ...used.map(([, u]) => h('th', {}, u)))),
          h('tbody', {}, ...d.lines.map((l) => h('tr', {}, h('td', {}, l.line.split('_').pop() ?? l.line),
            ...used.map(([k]) => h('td', {}, l.readings[k] ? fmt(l.readings[k].v, k === 'PowerFactor' ? 2 : 1, '') : '–')))))),
        h('div', { class: 'muted small' }, `Latest values from the last 15 min (UTC ${d.fetched.slice(11, 16)}). Internal-use data.`))
    } catch (e) {
      out.replaceChildren(h('p', { class: 'muted small' }, `Readings unavailable: ${(e as Error).message}`))
    }
    btn.removeAttribute('disabled')
  }
  return h('div', {}, btn, out)
}
