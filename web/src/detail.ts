// Right-hand detail drawer for a selected site or LV substation. Fetched lazily per click.

import { api, type PowerRoute, type Row, type SiteDetail } from './api'
import { ago, day, fmt, h } from './dom'
import { GLOSSARY, term } from './glossary'
import { nerdaReadings } from './extras'
import { C, setRoute } from './layers'
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
  setRoute(null)
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
  return h('nav', { class: 'breadcrumb', 'aria-label': 'Fed from' },
    h('div', { class: 'bc-title' }, term('parent', 'Fed from'),
      how === 'nearest' ? h('small', { class: 'muted' }, ' · LV link inferred') : null),
    h('div', { class: 'bc-row' }, ...(nodes.length ? nodes : [h('span', { class: 'muted' }, 'Not published')])))
}

/** "as of 3 Nov 2026", or "as of 1 Apr to 3 Nov 2026" when rows differ. Empty when no row is dated. */
function asOf(dates: unknown[]): string {
  const ds = [...new Set(dates.filter((x): x is string => typeof x === 'string' && x !== ''))].sort()
  if (!ds.length) return ''
  return ds.length === 1 || day(ds[0]) === day(ds.at(-1))
    ? `as of ${day(ds[0])}` : `as of ${day(ds[0])} to ${day(ds.at(-1))}`
}

function dated(title: Node | string, dates: unknown[]): HTMLElement {
  const when = asOf(dates)
  return h('h3', {}, title, when ? h('small', { class: 'muted' }, ` · ${when}`) : null)
}

/** One plain sentence answering "can I connect here?" for the current mode and threshold. */
function verdict(mode: 'gen' | 'dem', v: number | null, unit: string): HTMLElement {
  const what = mode === 'gen' ? 'generation' : 'demand'
  let text: string
  let cls: string
  if (v === null) { text = `Not published for ${what}.`; cls = 'v-none' }
  else if (v <= 0) { text = `No spare capacity for ${what}.`; cls = 'v-red' }
  else if (v < state.thr) { text = `Limited spare capacity for ${what}: ~${fmt(v)} ${unit}.`; cls = 'v-amber' }
  else { text = `Good spare capacity for ${what}: ~${fmt(v)} ${unit}.`; cls = 'v-green' }
  return h('p', { class: `verdict ${cls}` }, text)
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
    : h('p', { class: 'muted' }, s.operator === 'ENW' ? 'ENW publishes no fault levels.' : 'No fault levels.')

  const statusRows = Object.entries(d.queue.by_status).filter(([, v]) => v)
  const techRows = Object.entries(d.queue.by_tech).filter(([, v]) => v).sort((a, b) => b[1] - a[1]).slice(0, 6)
  const basis = caps.map((c) => c.basis).find(Boolean)
  // a row with no date of its own takes its source's date
  const srcDate: Record<string, unknown> = Object.fromEntries(d.sources.map((r) => [String(r.source_id), r.as_of ?? r.fetched_at]))
  const rowDate = (r: Row | null | undefined) => r?.as_of ?? srcDate[String(r?.source_id)]

  const here: [number, number] | null = typeof s.lon === 'number' && typeof s.lat === 'number' ? [s.lon, s.lat] : null
  drawHier(here, d.chain ?? [], d.children)
  shell(String(s.name ?? s.site_id),
    `${s.operator} · ${KIND[String(s.kind)] ?? s.kind} · ${String(s.kv_list ?? '').replaceAll(',', '/')} kV`,
    verdict(state.mode, state.mode === 'gen' ? g : (dm ?? dmva), state.mode === 'gen' ? 'MW' : (dm !== null ? 'MW' : 'MVA')),
    breadcrumb(d.chain ?? [], { name: String(s.name ?? s.site_id), kind: String(s.kind) }),
    h('div', { class: 'stats' },
      stat(term('gen_headroom'), g, 'MW'),
      dm !== null ? stat(term('dem_headroom'), dm, 'MW') : stat(term('dem_headroom'), dmva, 'MVA'),
      stat(term('fault_headroom', 'Fault headroom'), fh.length ? Math.min(...fh) : null, 'kA', false),
      stat(term('queue', 'Queued'), queued || null, 'MW', false)),
    h('p', { class: 'muted small' }, term('basis', 'Headroom basis'), ': ',
      basis && !String(basis).toLowerCase().startsWith('unknown') ? String(basis)
        : `not stated`,
      asOf(caps.map(rowDate)) ? `. Figures ${asOf(caps.map(rowDate))}.` : ''),
    h('div', { class: 'rel' },
      d.children.length ? h('details', {}, h('summary', {}, `Feeds ${d.children.length}`),
        h('ul', { class: 'kids' }, ...d.children.map((c) => h('li', {}, link(c),
          h('span', { class: 'muted' }, ` ${fmt(num(c, state.mode === 'gen' ? 'g' : 'd'))}`)))))
        : null),
    dated(h('span', {}, term('busbar', 'Busbars'), ' & capacity'), d.assets.map((a) => rowDate(a.capacity) ?? rowDate(a))), assetsTbl,
    dated(term('fault_level', 'Fault levels'), d.faults.map(rowDate)), faultsTbl,
    dated(term('queue', 'Connection queue'), d.queue.projects.map(rowDate)),
    statusRows.length
      ? h('div', {}, table(['Status', 'MW'], statusRows.map(([k, v]) => [GLOSSARY[k] ? term(k, k) : k, fmt(v)])),
        techRows.length ? table(['Technology', 'MW'], techRows.map(([k, v]) => [k, fmt(v)])) : null)
      : h('p', { class: 'muted' }, 'None linked.'),
    h('h3', {}, 'Sources'),
    h('ul', { class: 'srcs' }, ...d.sources.map((r) => h('li', {},
      h('span', {}, `${r.source_id}`), h('span', { class: 'muted' },
        [r.as_of ? `data ${day(r.as_of)}` : null, r.fetched_at ? `fetched ${ago(String(r.fetched_at))}` : null,
          r.licence ? String(r.licence) : null].filter(Boolean).map((x) => ` · ${x}`).join(''))))),
  )
}

/** One plain line on how much of the drawn route follows published lines. */
function routeNote(r: PowerRoute): HTMLElement {
  const km = (m: number) => `${fmt(m / 1000, 1, '')} km`
  const to = (r.hops.at(-1)?.hop.split('-')[1] ?? 'bsp').toUpperCase()
  const text = r.summary.line_m > 0
    ? `Route to ${to}: ${km(r.summary.line_m)} on published lines. ${km(r.summary.jump_m)} drawn straight where no line data.`
    : `Route to ${to}: no published lines here. Drawn straight.`
  return h('p', { class: 'muted small route-note', title: 'Overhead lines only. Cables are not published.' }, text)
}

/** Fetch and draw the power route for the open item (q: `lv=<uid>` or `site=<id>`). Quiet on failure. */
async function loadRoute(q: string, signal: AbortSignal) {
  try {
    const r = await api.route(q, signal)
    if (signal.aborted || !r.features.length) return
    setRoute(r)
    const at = drawer.querySelector('nav.breadcrumb') ?? drawer.querySelector('.d-head')
    drawer.querySelector('.route-note')?.remove()
    at?.after(routeNote(r))
  } catch { /* the straight hierarchy links stay */ }
}

export async function showSite(id: string) {
  ctl?.abort()
  ctl = new AbortController()
  const signal = ctl.signal
  shell('Loading…', '')
  try {
    render(await api.site(id, signal))
    void loadRoute(`site=${encodeURIComponent(id)}`, signal)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') shell('Could not load', String((e as Error).message))
  }
}

/** Where the ring and the % come from, in a few words. */
const RING_SRC: Record<string, string> = { NeRDA: 'SSEN NeRDA', 'ENW PreSense': 'ENW monitor list' }
const PCT_SRC: Record<string, string> = {
  NGED: '% is annual max demand over rating. Not a live monitor.',
  ENW: '% is published peak load over rating.',
  SPEN: '% is max utilisation from the SPEN monitor, latest month.',
  UKPN: '% is the middle of a published band.',
}

function lvNotes(props: Record<string, unknown>): HTMLElement | null {
  const ms = typeof props.ms === 'string' ? props.ms : null
  const src = ms ? `Monitor fitted. Source: ${RING_SRC[ms] ?? `${ms} monitor list`}.` : null
  const pct = typeof props.u === 'number' ? PCT_SRC[String(props.op)] ?? null : null
  if (!src && !pct) return null
  return h('p', { class: 'muted note' }, [src, pct].filter(Boolean).join(' '))
}

interface LvMonitor { t: string; bus?: LvBox; fd?: Record<string, LvBox>; temp?: number }
interface LvBox { i?: (number | null)[]; kw?: number; v?: (number | null)[] }

/** NGED 10-minute monitor reading: busbar and feeders, current per phase and power. */
function monitorTable(m: LvMonitor): HTMLElement {
  const row = (name: string, b: LvBox) => [name, ...[0, 1, 2].map((k) => fmt(b.i?.[k] ?? null, 0, '')), fmt(b.kw ?? null, 1, '')]
  const rows = [...(m.bus?.i ? [row('Busbar', m.bus)] : []),
    ...Object.entries(m.fd ?? {}).sort(([a], [b]) => Number(a) - Number(b)).map(([f, b]) => row(`Feeder ${f}`, b))]
  const v = m.bus?.v?.filter((x): x is number => typeof x === 'number' && x > 100)
  return h('div', {},
    h('h3', {}, 'Monitor reading'),
    table(['', 'L1 A', 'L2 A', 'L3 A', 'kW'], rows),
    h('div', { class: 'muted small' }, [`Taken ${m.t.replace('T', ' ').slice(0, 16)}.`,
      v?.length ? `Volts ${fmt(v.reduce((a, b) => a + b, 0) / v.length, 0, '')}.` : '',
      typeof m.temp === 'number' ? `Transformer ${fmt(m.temp, 0, '')} C.` : ''].filter(Boolean).join(' ')))
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
    lvNotes(props),
    typeof props.nrn === 'string' ? nerdaReadings(props.nrn) : null,
    h('p', { class: 'muted small' }, 'Loading details…'))
  try {
    const signal = ctl.signal
    const r = await api.lv(id, signal)
    const chain = (r.chain as Row[] | undefined) ?? []
    const pos: [number, number] | null = at ?? (typeof r.lon === 'number' && typeof r.lat === 'number' ? [r.lon, r.lat] : null)
    drawHier(pos, chain, [], String(r.upstream_how ?? 'published'))
    void loadRoute(`lv=${encodeURIComponent(id)}`, signal)
    // SSEN publishes a number, not a name: add the locality so the title reads as a place
    const loc = (r.native_ids as Record<string, unknown> | null)?.locality
    const short = typeof r.name === 'string' && r.name ? r.name : String(id.split(':').pop())
    const place = typeof loc === 'string' && loc ? ` · ${loc.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())}` : ''
    const title = drawer.querySelector('.d-head h2')
    if (title) title.textContent = `LV substation ${short}${place}`
    drawer.querySelector('.d-head')?.after(breadcrumb(chain, { name: `LV ${short}`, kind: 'LV' }, r.upstream_how as string))
    const rows = Object.entries(r).filter(([k, v]) => v !== null && v !== '' && typeof v !== 'object' && !k.startsWith('upstream_'))
    const mon = r.monitor as LvMonitor | null | undefined
    const when = day(r.as_of)
    drawer.querySelector('p.small')?.replaceWith(
      ...(when ? [h('p', { class: 'muted small' }, `Data as of ${when}${r.source_id ? ` (${String(r.source_id)})` : ''}.`)] : []),
      table(['Field', 'Value'], rows.map(([k, v]) => [k, v as string])))
    if (mon?.t) drawer.querySelector('.stats')?.after(monitorTable(mon))
    // opened from search: no tile props, so take the ring note and live-readings button from the API
    if (typeof props.ms !== 'string' && typeof r.ms === 'string') {
      const extra = [lvNotes({ ...props, op: r.operator, ms: r.ms }), typeof r.nrn === 'string' ? nerdaReadings(r.nrn) : null]
      drawer.querySelector('.stats')?.after(...extra.filter((x): x is HTMLElement => x !== null))
    }
  } catch (e) {
    if ((e as Error).name !== 'AbortError') drawer.querySelector('p.small')?.replaceWith(
      h('p', { class: 'muted' }, 'No details.'))
  }
}
