// Right-hand detail drawer for a selected site or LV substation. Fetched lazily per click.

import { api, type Row, type SiteDetail } from './api'
import { fmt, h } from './dom'
import { GLOSSARY, term } from './glossary'
import { C } from './layers'
import { state } from './state'
import { KIND_SHAPE, shapeSvg } from './shapes'

const KIND_NUM: Record<string, number> = { GSP: 1, BSP: 2, PRIMARY: 3 }

/** Called with hierarchy geometry whenever a site/LV detail loads (main draws it on the map). */
export type HierFn = (from: [number, number] | null, chain: Row[], children: Row[], how?: string) => void
let drawHier: HierFn = () => {}
export function onHierarchy(fn: HierFn) { drawHier = fn }

const KIND: Record<string, string> = { GSP: 'Grid supply point', BSP: 'Bulk supply point', PRIMARY: 'Primary',
  SECONDARY: 'Secondary', TRANSMISSION: 'Transmission' }

const drawer = h('aside', { class: 'panel drawer', 'aria-live': 'polite', hidden: true })
let ctl: AbortController | undefined
let onClose: () => void = () => {}
let openSite: (id: string) => void = () => {}

export function initDetail(close: () => void, navigate: (siteId: string) => void) {
  onClose = close
  openSite = navigate
  document.body.append(drawer)
  document.addEventListener('keydown', (e) => e.key === 'Escape' && hide())
}

export function hide() {
  drawer.hidden = true
  drawHier(null, [], [])
  ctl?.abort()
  onClose()
}

function shell(title: string, sub: string, ...body: (Node | null)[]) {
  drawer.replaceChildren(
    h('header', { class: 'd-head' },
      h('div', {}, h('h2', {}, title), h('div', { class: 'd-sub' }, sub)),
      h('button', { type: 'button', class: 'close', 'aria-label': 'Close', onclick: hide }, '×')),
    ...body.filter(Boolean) as Node[],
  )
  drawer.hidden = false
}

function tone(v: number | null | undefined): string {
  if (v === null || v === undefined) return C.none
  return v <= 0 ? C.red : v < state.thr ? C.amber : C.green
}

function stat(label: string | Node, v: number | null | undefined, unit: string, colour = true) {
  return h('div', { class: 'stat' },
    h('div', { class: 'stat-v', style: colour ? `color:${tone(v)}` : '' }, fmt(v, 1), h('small', {}, ` ${unit}`)),
    h('div', { class: 'stat-l' }, label))
}

function num(r: Row | null | undefined, k: string): number | null {
  const v = r?.[k]
  return typeof v === 'number' ? v : null
}

function table(head: (string | Node)[], rows: (string | number | null | Node)[][]) {
  return h('table', {},
    h('thead', {}, h('tr', {}, ...head.map((x) => h('th', {}, x)))),
    h('tbody', {}, ...rows.map((r) => h('tr', {}, ...r.map((c) => h('td', {}, c === null ? '—' : c instanceof Node ? c : String(c)))))))
}

function link(row: Row | null) {
  if (!row) return null
  return h('button', { type: 'button', class: 'linklike', onclick: () => openSite(String(row.site_id)) },
    String(row.name ?? row.site_id))
}

/** GSP › BSP › Primary › [this]: the connection hierarchy, top-down, each level clickable. */
function breadcrumb(chain: Row[], self: { name: string; kind: string } | null, how?: string | null) {
  const top = [...chain].reverse()
  const nodes: Node[] = []
  top.forEach((c, i) => {
    if (i) nodes.push(h('span', { class: 'bc-sep' }, '›'))
    nodes.push(h('button', { type: 'button', class: 'bc-item', title: String(c.kind ?? ''), onclick: () => openSite(String(c.site_id)) },
      shapeSvg(KIND_SHAPE[KIND_NUM[String(c.kind)] ?? 3] ?? 'circle', 'var(--muted)', 12),
      h('span', {}, String(c.name ?? c.site_id))))
  })
  if (self) {
    if (nodes.length) nodes.push(h('span', { class: 'bc-sep' }, '›'))
    nodes.push(h('span', { class: 'bc-item self' }, self.kind === 'LV'
      ? h('i', { class: 'bc-lv' }) : shapeSvg(KIND_SHAPE[KIND_NUM[self.kind] ?? 3] ?? 'circle', 'var(--accent)', 12),
    h('span', {}, self.name)))
  }
  return h('nav', { class: 'breadcrumb', 'aria-label': 'Connection hierarchy' },
    h('div', { class: 'bc-title' }, term('parent', 'Connection hierarchy'),
      how === 'nearest' ? h('small', { class: 'muted' }, ' · LV link inferred (nearest primary)') : null),
    h('div', { class: 'bc-row' }, ...(nodes.length ? nodes : [h('span', { class: 'muted' }, 'Upstream not published')])))
}

/** One plain sentence answering "can I connect here?" for the current mode and threshold. */
function verdict(mode: 'gen' | 'dem', v: number | null, unit: string): HTMLElement {
  const what = mode === 'gen' ? 'new generation (e.g. solar, batteries)' : 'new demand (e.g. heat pumps, EV charging)'
  let text: string
  let cls: string
  if (v === null) { text = `The operator doesn’t publish spare capacity for ${what} here.`; cls = 'v-none' }
  else if (v <= 0) { text = `No spare capacity for ${what}: new connections are likely to need network reinforcement.`; cls = 'v-red' }
  else if (v < state.thr) { text = `Limited spare capacity for ${what}: about ${fmt(v)} ${unit}.`; cls = 'v-amber' }
  else { text = `Good spare capacity for ${what}: about ${fmt(v)} ${unit}.`; cls = 'v-green' }
  return h('p', { class: `verdict ${cls}` }, text,
    h('span', { class: 'muted small' }, ' Indicative only: a formal application decides.'))
}

function render(d: SiteDetail) {
  const s = d.site
  // headline figures: first asset carrying each value
  const caps = d.assets.map((a) => a.capacity).filter(Boolean) as Row[]
  const first = (k: string) => caps.map((c) => num(c, k)).find((v) => v !== null) ?? null
  const g = first('gen_headroom_mw')
  const dm = first('demand_headroom_mw')
  const dmva = first('demand_headroom_mva')
  const fh = d.faults.map((f) => num(f, 'break_headroom_ka') ??
    ((num(f, 'rating_break_ka') ?? NaN) - (num(f, 'fl_3ph_break_ka') ?? NaN)))
    .filter((v) => Number.isFinite(v))
  const queued = (d.queue.by_status.accepted ?? 0) + (d.queue.by_status.offered ?? 0)

  const assetsTbl = d.assets.length
    ? table([term('busbar', 'Busbar'), term('kv', 'kV'), term('gen_headroom', 'Gen MW'), term('dem_headroom', 'Demand'),
      term('firm', 'Firm MVA'), 'Source'],
      d.assets.map((a) => [
        String(a.name ?? ''), [a.kv_hv, a.kv_lv].filter((x) => x !== null && x !== undefined).join('/'),
        fmt(num(a.capacity, 'gen_headroom_mw')),
        num(a.capacity, 'demand_headroom_mw') !== null ? fmt(num(a.capacity, 'demand_headroom_mw'))
          : num(a.capacity, 'demand_headroom_mva') !== null ? `${fmt(num(a.capacity, 'demand_headroom_mva'))} MVA` : '—',
        fmt(num(a.capacity, 'firm_capacity_mva')), String(a.source_id ?? ''),
      ]))
    : null

  const faultsTbl = d.faults.length
    ? table(['Node', term('kv', 'kV'), term('break', '3ph break kA'), term('rating', 'Rating kA'),
      term('fault_headroom', 'Headroom kA')],
      d.faults.slice(0, 30).map((f) => [
        String(f.ltds_node ?? f.site_name ?? ''), fmt(num(f, 'kv'), 1), fmt(num(f, 'fl_3ph_break_ka'), 2),
        fmt(num(f, 'rating_break_ka'), 2),
        fmt((num(f, 'rating_break_ka') ?? NaN) - (num(f, 'fl_3ph_break_ka') ?? NaN), 2),
      ]))
    : h('p', { class: 'muted' }, s.operator === 'ENW' ? 'ENW does not publish fault levels.' : 'No fault level data published.')

  const statusRows = Object.entries(d.queue.by_status).filter(([, v]) => v)
  const techRows = Object.entries(d.queue.by_tech).filter(([, v]) => v).sort((a, b) => b[1] - a[1]).slice(0, 6)
  const basis = caps.map((c) => c.basis).find(Boolean)

  const here: [number, number] | null = typeof s.lon === 'number' && typeof s.lat === 'number' ? [s.lon, s.lat] : null
  drawHier(here, d.chain ?? [], d.children)
  shell(String(s.name ?? s.site_id),
    `${s.operator} · ${KIND[String(s.kind)] ?? s.kind} · ${String(s.kv_list ?? '').replaceAll(',', '/')} kV`,
    verdict(state.mode, state.mode === 'gen' ? g : (dm ?? dmva), state.mode === 'gen' ? 'MW' : (dm !== null ? 'MW' : 'MVA')),
    breadcrumb(d.chain ?? [], { name: String(s.name ?? s.site_id), kind: String(s.kind) }),
    h('div', { class: 'stats' },
      stat(term('gen_headroom'), g, 'MW'),
      dm !== null ? stat(term('dem_headroom'), dm, 'MW') : stat(term('dem_headroom'), dmva, 'MVA'),
      stat(term('fault_headroom', 'Fault headroom (min)'), fh.length ? Math.min(...fh) : null, 'kA', false),
      stat(term('queue', 'Queued generation'), queued || null, 'MW', false)),
    h('p', { class: 'muted small' }, term('basis', 'Headroom basis'), ': ',
      basis && !String(basis).toLowerCase().startsWith('unknown') ? String(basis)
        : `not stated in ${String(s.operator)}’s published data`),
    h('div', { class: 'rel' },
      d.children.length ? h('details', {}, h('summary', {}, `Feeds ${d.children.length} substations`),
        h('ul', { class: 'kids' }, ...d.children.map((c) => h('li', {}, link(c),
          h('span', { class: 'muted' }, ` ${fmt(num(c, state.mode === 'gen' ? 'g' : 'd'))}`)))))
        : null),
    h('h3', {}, term('busbar', 'Busbars'), ' & capacity'), assetsTbl,
    h('h3', {}, term('fault_level', 'Fault levels')), faultsTbl,
    h('h3', {}, term('queue', 'Connection queue')),
    statusRows.length
      ? h('div', {}, table(['Status', 'MW'], statusRows.map(([k, v]) => [GLOSSARY[k] ? term(k, k) : k, fmt(v)])),
        techRows.length ? table(['Technology', 'MW'], techRows.map(([k, v]) => [k, fmt(v)])) : null)
      : h('p', { class: 'muted' }, 'No register entries linked to this site.'),
    h('h3', {}, 'Sources'),
    h('ul', { class: 'srcs' }, ...d.sources.map((r) => h('li', {},
      h('span', {}, `${r.source_id}`), h('span', { class: 'muted' }, ` · as of ${r.as_of ?? '?'} · ${r.licence ?? ''}`)))),
  )
}

export async function showSite(id: string) {
  ctl?.abort()
  ctl = new AbortController()
  shell('Loading…', '')
  try {
    render(await api.site(id, ctl.signal))
  } catch (e) {
    if ((e as Error).name !== 'AbortError') shell('Could not load site', String((e as Error).message))
  }
}

export async function showLv(id: string, props: Record<string, unknown>, at?: [number, number]) {
  ctl?.abort()
  ctl = new AbortController()
  const u = typeof props.u === 'number' ? props.u : null
  shell(`LV substation ${id.split(':').pop()}`, `${props.op ?? ''} · secondary`,
    h('div', { class: 'stats' },
      h('div', { class: 'stat' }, h('div', { class: 'stat-v' }, fmt(u, 0), h('small', {}, ' %')),
        h('div', { class: 'stat-l' }, term('utilisation'))),
      stat('Transformer', typeof props.kva === 'number' ? props.kva : null, 'kVA', false),
      stat('Customers', typeof props.cu === 'number' ? props.cu : null, '', false)),
    h('p', { class: 'muted small' }, 'Loading details…'))
  try {
    const r = await api.lv(id, ctl.signal)
    const chain = (r.chain as Row[] | undefined) ?? []
    const pos: [number, number] | null = at ?? (typeof r.lon === 'number' && typeof r.lat === 'number' ? [r.lon, r.lat] : null)
    drawHier(pos, chain, [], String(r.upstream_how ?? 'published'))
    drawer.querySelector('.d-head')?.after(breadcrumb(chain, { name: `LV ${id.split(':').pop()}`, kind: 'LV' }, r.upstream_how as string))
    const rows = Object.entries(r).filter(([k, v]) => v !== null && v !== '' && typeof v !== 'object' && !k.startsWith('upstream_'))
    drawer.querySelector('p.small')?.replaceWith(table(['Field', 'Value'], rows.map(([k, v]) => [k, v as string])))
  } catch (e) {
    if ((e as Error).name !== 'AbortError') drawer.querySelector('p.small')?.replaceWith(
      h('p', { class: 'muted' }, 'Details unavailable.'))
  }
}
