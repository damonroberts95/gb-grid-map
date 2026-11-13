// Left control panel: search, mode, threshold, operator + kind filters, layer toggles, legend.

import { Marker, type Map as MlMap } from 'maplibre-gl'
import { api, type SearchHit } from './api'
import { debounce, h } from './dom'
import { clearExtras, enabledExtras, extraTitle, extrasPanel, onExtrasChange } from './extras'
import { topbar } from './topbar'
import { showHelp } from './help'
import { term } from './glossary'
import { C, OP_COLOURS } from './layers'
import { KIND_SHAPE, shapeSvg } from './shapes'
import { OPERATORS, THRESHOLDS, set, state, onChange, type State } from './state'

const KIND_LABEL: Record<number, string> = { 1: 'GSP', 2: 'BSP', 3: 'Primary', 4: 'LV' }

function seg<T extends string | number>(
  options: readonly T[], value: () => T, label: (v: T) => string, pick: (v: T) => void, cls = '',
  termOf: (v: T) => string | undefined = () => undefined,
) {
  const wrap = h('div', { class: `seg ${cls}`, role: 'radiogroup' })
  const render = () => {
    wrap.replaceChildren(
      ...options.map((o) =>
        h('button', {
          type: 'button', role: 'radio', 'aria-checked': String(value() === o),
          class: value() === o ? 'on' : '', onclick: () => pick(o), 'data-term': termOf(o),
        }, label(o)),
      ),
    )
  }
  render()
  onChange(render)
  return wrap
}

function chips<T extends string | number>(options: readonly T[], selected: () => T[], label: (v: T) => string,
                                          update: (v: T[]) => void,
                                          termOf: (v: T) => string | undefined = () => undefined,
                                          swatch: (v: T) => string | undefined = () => undefined) {
  const wrap = h('div', { class: 'chips' })
  const render = () => {
    const sel = selected()
    wrap.replaceChildren(
      ...options.map((o) => {
        const on = sel.includes(o)
        return h('button', {
          type: 'button', 'aria-pressed': String(on), class: on ? 'chip on' : 'chip', 'data-term': termOf(o),
          onclick: (e: Event) => {
            if ((e as MouseEvent).shiftKey) return update([o])
            update(on ? sel.filter((x) => x !== o) : [...sel, o])
          },
        }, swatch(o) ? h('i', { class: 'swatch', style: `background:${swatch(o)}` }) : null, label(o))
      }),
    )
  }
  render()
  onChange(render)
  return wrap
}

function toggle(key: keyof Pick<State, 'links' | 'outages' | 'neso'>, text: string, termKey: string) {
  const input = h('input', { type: 'checkbox' }) as HTMLInputElement
  input.checked = state[key]
  input.addEventListener('change', () => set({ [key]: input.checked } as Partial<State>))
  onChange((s) => (input.checked = s[key]))
  return h('label', { class: 'toggle' }, input, term(termKey, text))
}

/** The key: substation type (shape) is primary data, then headroom colour, then lines/markers. */
/** Key: only what is on the map now. Rebuilds on filter, mode or layer changes. */
function legend() {
  const box = h('section', { class: 'key', 'aria-label': 'Map key' })
  const row = (sw: Node, label: Node | string) => h('div', { class: 'key-row' }, h('span', { class: 'key-sw' }, sw), h('span', {}, label))
  const sq = (c: string) => h('i', { class: 'key-col', style: `background:${c}` })
  const block = (title: Node | string, ...rows: Node[]) => h('div', { class: 'key-block' }, h('div', { class: 'key-sub' }, title), ...rows)
  const render = () => {
    const s = state
    const hv = !s.simple && s.kinds.some((k) => k <= 3)
    const lv = s.simple || s.kinds.includes(4)
    const unit = s.mode === 'gen' ? 'MW' : 'MW/MVA'
    const blocks: Node[] = []
    if (hv) {
      blocks.push(block('Substations',
        ...(s.kinds.includes(1) ? [row(shapeSvg(KIND_SHAPE[1], 'var(--muted)', 18), term('gsp', 'GSP'))] : []),
        ...(s.kinds.includes(2) ? [row(shapeSvg(KIND_SHAPE[2], 'var(--muted)', 16), term('bsp', 'BSP'))] : []),
        ...(s.kinds.includes(3) ? [row(shapeSvg(KIND_SHAPE[3], 'var(--muted)', 13), term('primary', 'Primary'))] : []),
        ...(s.neso ? [row(shapeSvg('diamond-ring', 'none', 16), term('transmission_gsp', 'Transmission'))] : [])))
      blocks.push(block(term(s.mode === 'gen' ? 'gen_headroom' : 'dem_headroom', s.mode === 'gen' ? 'Spare capacity (gen)' : 'Spare capacity (demand)'),
        row(sq(C.green), `≥ ${s.thr} ${unit}`), row(sq(C.amber), `0 – ${s.thr}`), row(sq(C.red), 'None'), row(sq(C.none), 'Not published')))
    }
    if (lv) {
      blocks.push(block(term('lv', s.simple ? 'Monitored LV' : 'LV substations'),
        row(shapeSvg('square', 'var(--muted)', 12), 'Ground'),
        row(shapeSvg('circle', 'var(--muted)', 12), 'Pole'),
        row(shapeSvg('diamond', 'var(--muted)', 12), 'Mount unknown'),
        ...(!s.simple ? [row(h('i', { class: 'key-ring' }), term('lv_monitored_note', 'Monitored'))] : [])))
      blocks.push(block(term('utilisation', 'LV utilisation'),
        row(sq(C.green), '< 60%'), row(sq(C.amber), '60 – 85%'), row(sq(C.red), '≥ 85%'), row(sq(C.none), 'Not published')))
    }
    const marks: Node[] = []
    if (!s.simple && s.outages) marks.push(row(h('i', { class: 'key-out' }), term('outage', 'Power cut')))
    if (!s.simple && s.links) marks.push(row(h('i', { class: 'key-line dashed' }), term('schematic', 'Feeds')))
    marks.push(row(h('i', { class: 'key-line', style: `background:${C.hier}` }), term('parent', 'Feed path')))
    blocks.push(block('Lines', ...marks))
    for (const m of enabledExtras()) {
      if (!m.legend.length || (s.simple && m.id !== 'dno_areas')) continue
      blocks.push(block(extraTitle(m), ...m.legend.map(([a, b]) => {
        const [c, t] = /^#|^rgb/.test(a) ? [a, b] : [b, a]
        return row(sq(c), t)
      })))
    }
    box.replaceChildren(h('div', { class: 'key-grid' }, ...blocks))
  }
  render()
  onChange(render)
  onExtrasChange(render)
  return box
}

function search(map: MlMap, select: (hit: SearchHit) => void) {
  const input = h('input', {
    type: 'search', placeholder: 'Search place or substation', 'aria-label': 'Search substations, streets and postcodes', autocomplete: 'off',
  }) as HTMLInputElement
  const list = h('ul', { class: 'results', role: 'listbox' })
  let ctl: AbortController | undefined
  let hits: SearchHit[] = []
  let pin: Marker | undefined
  let pinTimer: ReturnType<typeof setTimeout> | undefined
  const clearPin = () => {
    clearTimeout(pinTimer)
    pin?.remove()
    pin = undefined
  }
  const go = (hit: SearchHit) => {
    list.replaceChildren()
    clearPin()
    input.value = hit.name
    if (hit.type === 'place') {
      map.flyTo({ center: [hit.lon, hit.lat], zoom: hit.kind === 'place' ? 13 : 16 })
      pin = new Marker({ scale: 0.7 }).setLngLat([hit.lon, hit.lat]).addTo(map)
      pinTimer = setTimeout(clearPin, 20_000)
      return
    }
    map.flyTo({ center: [hit.lon, hit.lat], zoom: Math.max(map.getZoom(), hit.type === 'lv' ? 15 : 12) })
    select(hit)
  }
  const run = debounce(async (q: string) => {
    ctl?.abort()
    if (q.trim().length < 2) return list.replaceChildren()
    ctl = new AbortController()
    try {
      hits = await api.search(q.trim(), ctl.signal)
      clearPin()
      list.replaceChildren(
        ...hits.map((r) =>
          h('li', { role: 'option', tabindex: '0', onclick: () => go(r), onkeydown: (e: Event) => {
            if ((e as KeyboardEvent).key === 'Enter') go(r)
          } },
          h('span', { class: 'r-name' }, r.name),
          h('span', { class: 'r-meta' }, r.type === 'place'
            ? `${r.kind}${r.context ? ` · ${r.context}` : ''}`
            : `${r.op} · ${r.type === 'lv' ? 'LV' : (KIND_LABEL[Number(r.kind)] ?? r.kind ?? '')}`)),
        ),
        ...(hits.some((r) => r.type === 'place')
          ? [h('li', { class: 'empty', style: 'font-size:11px;opacity:.7' }, 'Contains OS data © Crown copyright and database right 2026')]
          : []),
      )
      if (!hits.length) list.replaceChildren(h('li', { class: 'empty' }, 'No matches'))
    } catch (e) {
      if ((e as Error).name !== 'AbortError') list.replaceChildren(h('li', { class: 'empty' }, 'Search failed'))
    }
  }, 120)
  input.addEventListener('input', () => run(input.value))
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && hits[0]) go(hits[0])
    if (e.key === 'Escape') list.replaceChildren()
  })
  return h('div', { class: 'search' }, input, list)
}

/** Collapsible sidebar section; open/closed remembered per browser. Optional header actions (e.g. Clear all). */
function section(id: string, title: Node | string, actions: HTMLElement[], ...body: (Node | null)[]) {
  let open = true
  try { open = localStorage.getItem(`gridmap.sec.${id}`) !== '0' } catch { /* default open */ }
  const d = h('details', { class: 'sec', open, 'data-sec': id,
    ontoggle: (e: Event) => { try { localStorage.setItem(`gridmap.sec.${id}`, (e.currentTarget as HTMLDetailsElement).open ? '1' : '0') } catch { /* ignore */ } },
  },
  h('summary', { class: 'sec-h' }, h('span', { class: 'sec-t' }, title),
    h('span', { class: 'sec-a', onclick: (e: Event) => e.preventDefault() }, ...actions)),
  h('div', { class: 'sec-b' }, ...body.filter(Boolean) as Node[]))
  return d
}

function action(label: string, title: string, fn: () => void) {
  return h('button', { type: 'button', class: 'sec-btn', title, onclick: (e: Event) => { e.preventDefault(); e.stopPropagation(); fn() } }, label)
}

export function buildControls(map: MlMap, select: (hit: SearchHit) => void) {
  const ops = () => state.ops as (typeof OPERATORS)[number][]
  const opChips = () => chips(OPERATORS, ops, (v) => v, (v) => set({ ops: v }), () => undefined, (v) => OP_COLOURS[v])
  const opActions = () => [action('All', 'Show all operators', () => set({ ops: [...OPERATORS] })),
    action('None', 'Hide all operators', () => set({ ops: [] }))]

  const panel = h('section', { class: 'panel controls', 'aria-label': 'Map controls' },
    search(map, select),
    section('key', 'Key', [], legend()),

    // ---- full map ----
    h('div', { class: 'full-only' },
      section('headroom', term('headroom', 'Spare capacity'), [],
        seg(['dem', 'gen'] as const, () => state.mode, (v) => (v === 'gen' ? 'Generation' : 'Demand'),
          (v) => set({ mode: v }), '', (v) => (v === 'gen' ? 'gen_headroom' : 'dem_headroom')),
        h('div', { class: 'label sub' }, term('threshold', 'Green from')),
        seg(THRESHOLDS, () => state.thr as (typeof THRESHOLDS)[number], (v) => `${v} MW`, (v) => set({ thr: v }), 'small')),
      section('filters', 'Filter', opActions(),
        opChips(),
        h('div', { class: 'label sub' }, 'Substations'),
        chips([1, 2, 3, 4] as const, () => state.kinds as (1 | 2 | 3 | 4)[], (v) => KIND_LABEL[v], (v) => set({ kinds: v }),
          (v) => ({ 1: 'gsp', 2: 'bsp', 3: 'primary', 4: 'lv' })[v])),
      section('layers', 'Layers', [
        action('Clear all', 'Hide all layers', () => {
          set({ outages: false, links: false, neso: false })
          clearExtras(map)
        })],
      h('div', { class: 'toggles' },
        toggle('outages', 'Live power cuts', 'outage'),
        toggle('links', 'Schematic feeds', 'schematic'),
        toggle('neso', 'Transmission GSPs', 'transmission_gsp'))),
      section('more', term('more_layers', 'More layers'), [action('Clear all', 'Hide all extras', () => clearExtras(map))],
        extrasPanel(map))),
    section('gbnow', 'GB now', [], h('div', { id: 'national', class: 'national' })),
  )
  document.body.prepend(topbar('map', [
    h('span', { id: 'status', class: 'status' }),
    h('button', { type: 'button', class: 'tb-btn', title: 'Help', onclick: showHelp }, '? Help'),
    h('button', {
      type: 'button', class: 'tb-btn', 'aria-label': 'Reset view', title: 'Reset view',
      onclick: () => window.dispatchEvent(new Event('gridmap:reset')),
    }, '⟲ Reset'),
    h('button', {
      type: 'button', class: 'tb-btn', 'aria-label': 'Toggle dark mode', title: 'Toggle dark mode',
      onclick: () => window.dispatchEvent(new Event('gridmap:toggle-theme')),
    }, '◐'),
  ]))
  document.body.append(panel)
}
