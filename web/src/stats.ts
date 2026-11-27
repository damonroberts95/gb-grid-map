import { h } from './dom'
import { cov, getJson, num, shell, table } from './pages'
import { state } from './state'

interface Row {
  operator: string; busbars: number; sites: number; coords_pct: number | null; gen_pct: number | null
  demand_pct: number | null; fault_pct: number | null; switchgear_pct: number | null
  constraint_pct: number | null; rag_pct: number | null
}
interface Stats {
  generated_at: string
  headline: Record<string, number | null>
  dno: Row[]; to: Row[]; note: string
  coord_quality: { quality: string; n: number }[]
  headroom_sources: { generation: { source_id: string; n: number }[]; demand: { source_id: string; n: number }[] }
  lv: { sites: number; coords_pct: number | null; utilisation_pct: number | null
        by_operator: { operator: string; sites: number; coords_pct: number | null; utilisation_pct: number | null }[] }
  freshness: { by_operator: { operator: string; sources: number; ok: number; fetched_at: string | null; as_of: string | null }[] }
  transmission_assets_held: string[]
}

interface LevelRow { level: string; operator: string; total: number; green: number; amber: number; red: number; none: number }
interface Levels { mode: string; thr: number; ops: string[]; rows: LevelRow[] }

const LEVELS: [string, string][] = [['GSP', 'Grid supply points (GSP)'], ['BSP', 'Bulk supply points (BSP)'],
  ['PRIMARY', 'Primaries'], ['LV', 'LV substations']]
const KIND_OF: Record<string, number> = { GSP: 1, BSP: 2, PRIMARY: 3, LV: 4 }
const BAND = { green: '#1a9850', amber: '#f29e2e', red: '#d73027', none: '#7b8590' }
const BANDS = ['green', 'amber', 'red', 'none'] as const

/** Count with its share of the row, coloured like the map dot. */
function band(n: number, total: number, colour: string): HTMLElement {
  return h('span', { class: 'band' }, h('i', { class: 'dot', style: `background:${colour}` }),
    h('b', {}, num(n)), h('small', { class: 'dim' }, total ? ` ${Math.round((100 * n) / total)}%` : ''))
}

function sum(rows: LevelRow[]): Omit<LevelRow, 'level' | 'operator'> {
  const z = { total: 0, green: 0, amber: 0, red: 0, none: 0 }
  for (const r of rows) for (const k of Object.keys(z) as (keyof typeof z)[]) z[k] += r[k]
  return z
}

/** Substations the map shows, per level, in the map's colours, using the map's current filters. */
function onMap(L: Levels): HTMLElement[] {
  const what = L.mode === 'gen' ? 'generation' : 'demand'
  const thr = `${L.thr} MW`
  const shown = LEVELS.filter(([k]) => state.kinds.includes(KIND_OF[k]))
  const off = LEVELS.filter(([k]) => !state.kinds.includes(KIND_OF[k])).map(([, t]) => t)
  const hidden = ['UKPN', 'NPG', 'ENW', 'SPEN', 'NGED', 'SSEN', 'NIE', 'ESB'].filter((o) => !L.ops.includes(o))
  const row = ([k, title]: [string, string]) => {
    const t = sum(L.rows.filter((r) => r.level === k))
    return [title, h('b', {}, num(t.total)), ...BANDS.map((b) => band(t[b], t.total, BAND[b]))]
  }
  const ops = [...new Set(L.rows.map((r) => r.operator))].sort()
  return [
    h('p', { class: 'sub' }, `Same filters as the map: ${what} headroom, green from ${thr}. `,
      hidden.length ? `Operators hidden: ${hidden.join(', ')}. ` : 'All operators. ',
      off.length ? `Levels hidden: ${off.join(', ')}. ` : '',
      h('a', { href: `/${location.hash}` }, 'Change on the map')),
    h('div', { class: 'cards' }, ...shown.map(([k, title]) => card(title, num(sum(L.rows.filter((r) => r.level === k)).total)))),
    table(['Level', 'On map', 'Green', 'Amber', 'Red', 'Not published'], shown.map(row)),
    h('p', { class: 'note' }, `GSP, BSP and primary: green has ${thr} or more ${what} headroom, amber has some but less, red has none. `
      + 'LV: green under 60% utilised, amber 60 to 85%, red 85% or more. Not published: the operator gives no figure.'),
    h('h2', {}, 'By operator'),
    table(['Operator', ...shown.map(([, t]) => t)], ops.map((o) => [o, ...shown.map(([k]) => {
      const r = L.rows.find((x) => x.operator === o && x.level === k)
      if (!r || !r.total) return h('span', { class: 'dim' }, '—')
      return h('span', {}, num(r.total), h('small', { class: 'dim' }, ` · ${Math.round((100 * (r.total - r.none)) / r.total)}% with figures`))
    })])),
  ]
}

interface MonRow { operator: string; published: number; confirmed: number; estimate: number; lv_sites: number
  coverage_pct: number | null; has_plan: boolean; steps: string[]; notes: string[]; sources: { title: string; url: string }[] }
interface Monitors { as_of: string; rows: MonRow[]; assumptions: string[]; total: { published: number; confirmed: number; estimate: number } }

/** LV monitors per operator: confirmed, estimated, and every step behind the estimate. */
function monitors(M: Monitors): HTMLElement[] {
  return [
    h('h1', { class: 'h-detail' }, 'LV monitors installed (estimate)'),
    h('p', { class: 'sub' }, `As of ${M.as_of}. Confirmed: the larger of the operator's published monitor list and the `
      + 'latest figure it has stated. Estimate: that figure carried forward to today along its own published plan. '
      + 'Open an operator below to see each step and its sources.'),
    h('div', { class: 'cards' },
      card('confirmed, all operators', num(M.total.confirmed)), card('estimated today', num(M.total.estimate)),
      card('in published monitor lists', num(M.total.published))),
    table(['Operator', 'In published list', 'Confirmed', 'Estimate today', 'LV substations', 'Coverage'],
      M.rows.map((r) => [r.operator, num(r.published), num(r.confirmed), h('b', {}, num(r.estimate)), num(r.lv_sites),
        cov(r.coverage_pct)])),
    h('h2', {}, 'How each figure was worked out'),
    ...M.rows.map((r) => h('details', { class: 'mon-how' },
      h('summary', {}, `${r.operator}: estimate ${num(r.estimate)}${r.has_plan ? '' : ' (no plan found)'}`),
      h('ol', {}, ...r.steps.map((x) => h('li', {}, x))),
      r.notes.length ? h('ul', { class: 'note' }, ...r.notes.map((x) => h('li', {}, x))) : null,
      r.sources.length ? h('p', { class: 'note' }, 'Sources: ', ...r.sources.flatMap((x, i) => [
        i ? ' · ' : '', h('a', { href: x.url, target: '_blank', rel: 'noopener' }, x.title)])) : null)),
    h('h2', {}, 'Assumptions'),
    h('ul', { class: 'note' }, ...M.assumptions.map((x) => h('li', {}, x))),
  ]
}

const pc = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`)
const day = (s: string | null) => (s ? s.slice(0, 16).replace('T', ' ') : '—')

function card(label: string, value: string): HTMLElement {
  return h('div', { class: 'card' }, h('b', {}, value), h('span', {}, label))
}

function coverage(rows: Row[]): HTMLElement {
  return table(['Operator', 'Records', 'Sites', 'Coords', 'Gen headroom', 'Demand headroom', 'Fault levels', 'Switchgear', 'Constraints', 'RAG'],
    rows.map((r) => [r.operator, num(r.busbars), num(r.sites), cov(r.coords_pct), cov(r.gen_pct), cov(r.demand_pct),
      cov(r.fault_pct), cov(r.switchgear_pct), cov(r.constraint_pct), cov(r.rag_pct)]))
}

function sources(title: string, rows: { source_id: string; n: number }[]): HTMLElement {
  const max = Math.max(1, ...rows.map((r) => r.n))
  return h('div', {}, h('h2', {}, title), table(['Source', 'Records'],
    rows.map((r) => [r.source_id, h('span', { class: 'cov' }, h('i', { style: `width:${(100 * r.n) / max}%` }), h('span', {}, num(r.n)))])))
}

async function main() {
  const root = shell('stats')
  root.append(h('h1', {}, 'Grid data coverage'), h('p', { class: 'sub' }, 'Loading…'))
  let s: Stats
  let L: Levels
  let M: Monitors | null = null
  try {
    const ops = [...state.ops, ...(state.neso ? ['NESO'] : [])].join(',')
    M = await getJson<Monitors>('/api/stats/lv-monitors').catch(() => null)
    ;[s, L] = await Promise.all([getJson<Stats>('/api/stats'), getJson<Levels>(
      `/api/stats/levels?mode=${state.mode}&thr=${state.thr}&ops=${encodeURIComponent(ops)}&kinds=${state.kinds.join(',')}`)])
  } catch (e) {
    root.replaceChildren(h('h1', {}, 'Grid data coverage'), h('p', { class: 'err' }, String(e)))
    return
  }
  const H = s.headline
  root.replaceChildren(
    h('h1', {}, 'Substations on the map'),
    ...onMap(L),
    ...(M ? monitors(M) : []),
    h('h1', { class: 'h-detail' }, 'Data detail'),
    h('p', { class: 'sub' }, `Generated ${day(s.generated_at)} UTC. Every operator, not filtered. Records are the rows each operator `
      + 'publishes: one per substation voltage. Percentages are the share of records with that figure.'),
    h('h2', {}, 'Distribution network operators'), coverage(s.dno),
    h('h2', {}, 'Transmission'), coverage(s.to),
    h('p', { class: 'note' }, `Transmission records held: ${s.transmission_assets_held.join(', ') || 'none'}. SSEN-T, NGET and SPT records are not held.`),
    h('h2', {}, 'LV / secondary substations'),
    table(['Operator', 'Substations', 'Coords', 'Utilisation'], [
      ['All', num(s.lv.sites), cov(s.lv.coords_pct), cov(s.lv.utilisation_pct)],
      ...s.lv.by_operator.map((r) => [r.operator, num(r.sites), cov(r.coords_pct), cov(r.utilisation_pct)])]),
    h('p', { class: 'note' }, `Coordinates including LV: ${pc(H.coords_pct_incl_lv)}.`),
    h('h2', {}, 'Coordinate quality (records)'),
    table(['Quality', 'Records'], s.coord_quality.map((r) => [r.quality, num(r.n)])),
    sources('Generation headroom sources', s.headroom_sources.generation),
    sources('Demand headroom sources', s.headroom_sources.demand),
    h('h2', {}, 'Freshness by operator'),
    table(['Operator', 'Sources ok', 'Last fetched (UTC)', 'Latest as-of'],
      s.freshness.by_operator.map((f) => [f.operator, `${f.ok} / ${f.sources}`, day(f.fetched_at), day(f.as_of)])))
}

void main()
