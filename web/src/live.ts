// Live wiring: server-sent events (manifest swaps, live feeds, refresh status) + national strip.

import { api, type Manifest } from './api'
import { $, ago, fmt, h } from './dom'

interface Handlers {
  manifest: (m: Manifest) => void
  live: (feed: string) => void
}

let builtAt: string | null = null
let stage = 'idle'

function paintStatus(connected: boolean) {
  const el = $('#status')
  if (!el) return
  const busy = stage !== 'idle'
  el.className = `status ${connected ? (busy ? 'busy' : 'ok') : 'down'}`
  el.textContent = !connected ? 'Offline' : busy ? 'Updating data…' : `Data updated ${ago(builtAt)}`
  el.title = builtAt ? `Map data built ${new Date(builtAt).toLocaleString()}` : ''
}

export function connectLive(on: Handlers) {
  let es: EventSource | undefined
  let connected = false
  const open = () => {
    es = new EventSource('/api/events')
    es.onopen = () => { connected = true; paintStatus(true) }
    es.onerror = () => { connected = false; paintStatus(false) } // EventSource retries by itself
    es.addEventListener('manifest', (e) => {
      const m = JSON.parse((e as MessageEvent).data) as Manifest
      builtAt = m.built_at
      on.manifest(m)
      paintStatus(true)
    })
    es.addEventListener('live', (e) => on.live(JSON.parse((e as MessageEvent).data).feed))
    es.addEventListener('status', (e) => {
      stage = JSON.parse((e as MessageEvent).data).stage
      paintStatus(true)
    })
  }
  open()
  setInterval(() => paintStatus(connected), 30_000)
}

export function setBuiltAt(iso: string | null) {
  builtAt = iso
  paintStatus(true)
}

export async function refreshNational() {
  const box = $('#national')
  if (!box || document.hidden) return
  try {
    const n = await api.national()
    const total = (n.fuel_mix ?? []).reduce((a, f) => a + Math.max(0, f.mw), 0)
    const top = [...(n.fuel_mix ?? [])].sort((a, b) => b.mw - a.mw).slice(0, 4)
    box.replaceChildren(
      h('div', { class: 'nat' },
        h('div', { class: 'term', 'data-term': 'frequency', tabindex: '0' },
          h('b', {}, fmt(n.frequency_hz ?? null, 3)), h('small', {}, ' Hz')),
        h('div', { class: 'term', 'data-term': 'demand_gw', tabindex: '0' },
          h('b', {}, fmt(n.demand_mw != null ? n.demand_mw / 1000 : null, 1)), h('small', {}, ' GW')),
        h('div', { class: 'term', 'data-term': 'carbon', tabindex: '0' },
          h('b', {}, fmt(n.carbon?.national ?? null, 0)), h('small', {}, ' gCO₂/kWh'))),
      ...(total ? [h('div', { class: 'mix muted small' },
        top.map((f) => `${f.fuel} ${Math.round((100 * f.mw) / total)}%`).join(' · '))] : []),
    )
  } catch {
    box.replaceChildren(h('div', { class: 'muted small' }, 'No national data'))
  }
}
