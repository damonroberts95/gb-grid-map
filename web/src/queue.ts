import { h } from './dom'
import { getJson, num, shell, table } from './pages'

interface Site {
  site_id: string; name: string; op: string; kind: string | null; kv: number | null; lat: number | null; lon: number | null
  total_queued_mw: number; total_connected_mw: number; projects: number; tec_queued_mw: number
}
interface Totals { queued_gw: number; connected_gw: number; projects: number; sites: number }
interface Queue { totals: Totals; totals_all: Totals; operators: string[]; sites: Site[]; tec_included: boolean }

const mw = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(2)} GW` : v > 0 ? `${Math.round(v)} MW` : '—')
const card = (label: string, value: string) => h('div', { class: 'card' }, h('b', {}, value), h('span', {}, label))

async function main() {
  const root = shell('queue')
  let op = new URLSearchParams(location.search).get('op') ?? ''
  const body = h('div', {})
  root.append(h('h1', {}, 'Connection queue'),
    h('p', { class: 'sub' }, 'Queued MW by substation. DNO accepted and offered, plus NESO TEC consented or building. No scoping.'),
    body)

  async function load() {
    let q: Queue
    try {
      q = await getJson<Queue>(`/api/queue?limit=100${op ? `&op=${encodeURIComponent(op)}` : ''}`)
    } catch (e) {
      body.replaceChildren(h('p', { class: 'err' }, String(e)))
      return
    }
    const max = Math.max(1, ...q.sites.map((s) => s.total_queued_mw))
    const chip = (v: string, label: string) => {
      const b = h('button', { type: 'button', class: op === v ? 'on' : undefined }, label)
      b.addEventListener('click', () => {
        op = v
        history.replaceState(null, '', v ? `?op=${encodeURIComponent(v)}` : location.pathname)
        void load()
      })
      return b
    }
    body.replaceChildren(
      h('div', { class: 'cards' }, card('queued (top 100)', `${q.totals.queued_gw.toFixed(2)} GW`),
        card('connected (top 100)', `${q.totals.connected_gw.toFixed(2)} GW`), card('projects (top 100)', num(q.totals.projects)),
        card('queued (all sites)', `${q.totals_all.queued_gw.toFixed(1)} GW`)),
      h('div', { class: 'filters' }, chip('', 'All'), ...q.operators.map((o) => chip(o, o))),
      table(['#', 'Site', 'kV', 'DNO/TO', 'Type', 'Queued', 'Connected', 'Projects'],
        q.sites.map((s, i) => [i + 1, h('span', { class: 'l' }, s.name),
          s.kv ? String(s.kv) : '—', s.op, s.kind ?? '—',
          h('span', { class: 'cov' }, h('i', { style: `width:${(100 * s.total_queued_mw) / max}%` }), h('span', {}, mw(s.total_queued_mw))),
          mw(s.total_connected_mw), num(s.projects)]),
        (i) => {
          const s = q.sites[i]
          return s.lat === null || s.lon === null ? {} : {
            class: 'link', title: 'Open on the map',
            onclick: () => { location.href = `/#map=12/${s.lat}/${s.lon}` },
          }
        }),
      h('p', { class: 'note' }, 'Click a row to open it on the map.'))
  }
  await load()
}

void main()
