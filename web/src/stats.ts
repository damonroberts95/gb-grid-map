import { h } from './dom'
import { cov, getJson, num, shell, table } from './pages'

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

const pc = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v.toFixed(1)}%`)
const day = (s: string | null) => (s ? s.slice(0, 16).replace('T', ' ') : '—')

function card(label: string, value: string): HTMLElement {
  return h('div', { class: 'card' }, h('b', {}, value), h('span', {}, label))
}

function coverage(rows: Row[]): HTMLElement {
  return table(['Operator', 'Busbars', 'Sites', 'Coords', 'Gen headroom', 'Demand headroom', 'Fault levels', 'Switchgear', 'Constraints', 'RAG'],
    rows.map((r) => [r.operator, num(r.busbars), num(r.sites), cov(r.coords_pct), cov(r.gen_pct), cov(r.demand_pct),
      cov(r.fault_pct), cov(r.switchgear_pct), cov(r.constraint_pct), cov(r.rag_pct)]))
}

function sources(title: string, rows: { source_id: string; n: number }[]): HTMLElement {
  const max = Math.max(1, ...rows.map((r) => r.n))
  return h('div', {}, h('h2', {}, title), table(['Source', 'Busbars'],
    rows.map((r) => [r.source_id, h('span', { class: 'cov' }, h('i', { style: `width:${(100 * r.n) / max}%` }), h('span', {}, num(r.n)))])))
}

async function main() {
  const root = shell('stats')
  root.append(h('h1', {}, 'Grid data coverage'), h('p', { class: 'sub' }, 'Loading…'))
  let s: Stats
  try {
    s = await getJson<Stats>('/api/stats')
  } catch (e) {
    root.replaceChildren(h('h1', {}, 'Grid data coverage'), h('p', { class: 'err' }, String(e)))
    return
  }
  const H = s.headline
  root.replaceChildren(
    h('h1', {}, 'Grid data coverage'),
    h('p', { class: 'sub' }, `Generated ${day(s.generated_at)} UTC. ${s.note}`),
    h('div', { class: 'cards' },
      card('sites', num(H.sites)), card('busbars (asset rows)', num(H.busbars)), card('with coordinates', pc(H.coords_pct)),
      card('generation headroom', pc(H.gen_pct)), card('demand headroom', pc(H.demand_pct)), card('fault levels', pc(H.fault_pct)),
      card('switchgear ratings', pc(H.switchgear_pct)), card('constraint data', pc(H.constraint_pct)), card('RAG status', pc(H.rag_pct)),
      card('LV substations', num(s.lv.sites))),
    h('h2', {}, 'Distribution network operators'), coverage(s.dno),
    h('h2', {}, 'Transmission'), coverage(s.to),
    h('p', { class: 'note' }, `Transmission busbars held: ${s.transmission_assets_held.join(', ') || 'none'}. SSEN-T, NGET and SPT busbars are not in the asset table.`),
    h('h2', {}, 'LV / secondary substations'),
    table(['Operator', 'Substations', 'Coords', 'Utilisation'], [
      ['All', num(s.lv.sites), cov(s.lv.coords_pct), cov(s.lv.utilisation_pct)],
      ...s.lv.by_operator.map((r) => [r.operator, num(r.sites), cov(r.coords_pct), cov(r.utilisation_pct)])]),
    h('p', { class: 'note' }, `Coordinates including LV: ${pc(H.coords_pct_incl_lv)}.`),
    h('h2', {}, 'Coordinate quality (busbars)'),
    table(['Quality', 'Busbars'], s.coord_quality.map((r) => [r.quality, num(r.n)])),
    sources('Generation headroom sources', s.headroom_sources.generation),
    sources('Demand headroom sources', s.headroom_sources.demand),
    h('h2', {}, 'Freshness by operator'),
    table(['Operator', 'Sources ok', 'Last fetched (UTC)', 'Latest as-of'],
      s.freshness.by_operator.map((f) => [f.operator, `${f.ok} / ${f.sources}`, day(f.fetched_at), day(f.as_of)])))
}

void main()
