// Live wiring: server-sent events (manifest swaps, live feeds, refresh status) + national strip.

import { api, type Manifest, type National } from './api'
import { $, ago, fmt, h } from './dom'

interface Handlers {
  manifest: (m: Manifest) => void
  live: (feed: string) => void
}

let builtAt: string | null = null
let stage = 'idle'

// Thin progress bar under the top bar while data updates (polls /api/progress only while busy).
const bar = h('div', { class: 'progress', hidden: true, role: 'progressbar', 'aria-label': 'Updating data',
  'aria-valuemin': '0', 'aria-valuemax': '100' }, h('i', {}))
document.body.append(bar)
let polling: number | undefined
let lastPct = 0

async function pollProgress() {
  try {
    const p = await (await fetch('/api/progress')).json() as { active: boolean; pct: number; stage: string }
    if (!p.active) { stopProgress(); return }
    lastPct = p.pct
    bar.hidden = false
    bar.setAttribute('aria-valuenow', String(p.pct))
    ;(bar.firstChild as HTMLElement).style.width = `${Math.max(3, p.pct)}%`
    paintStatus(true)
  } catch { /* server restarting: keep last value */ }
}

function startProgress() {
  if (polling) return
  void pollProgress()
  polling = window.setInterval(pollProgress, 2000)
}

function stopProgress() {
  clearInterval(polling)
  polling = undefined
  bar.hidden = true
  lastPct = 0
}

function paintStatus(connected: boolean) {
  const el = $('#status')
  if (!el) return
  const busy = stage !== 'idle'
  el.className = `status ${connected ? (busy ? 'busy' : 'ok') : 'down'}`
  el.textContent = !connected ? 'Offline' : busy ? `Updating data ${lastPct}%` : `Data updated ${ago(builtAt)}`
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
      if (stage !== 'idle') startProgress()
      else stopProgress()
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

/** Interconnector flow text: positive = import into the region ('EWIC: 250 MW to IE'). */
function link(name: string, mw: number | undefined, to: string): string | null {
  if (mw == null) return null
  const m = Math.round(Math.abs(mw))
  return m === 0 ? `${name} idle` : `${name}: ${m} MW ${mw > 0 ? 'to' : 'from'} ${to}`
}

/** One compact line for the NI or Ireland figures (EirGrid dashboard). */
function regionText(label: string, r: NonNullable<National['ni']> & NonNullable<National['roi']>, to: string): string {
  const parts: (string | null)[] = []
  if (r.demand_mw != null) parts.push(`demand ${Math.round(r.demand_mw)} MW`)
  if (r.wind_mw != null) parts.push(`wind ${Math.round(r.wind_mw)}`)
  if (r.solar_mw != null) parts.push(`solar ${Math.round(r.solar_mw)}`)
  parts.push(link('Moyle', r.moyle_mw, to), link('EWIC', r.ewic_mw, to), link('Greenlink', r.greenlink_mw, to))
  if (r.co2_g_kwh != null) parts.push(`${Math.round(r.co2_g_kwh)} g/kWh`)
  return `${label}: ${parts.filter((x) => x).join(' · ')}`
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
      ...(n.ni ? [h('div', { class: 'mix muted small', title: 'Northern Ireland, EirGrid dashboard' }, regionText('NI', n.ni, 'NI'))] : []),
      ...(n.roi ? [h('div', { class: 'mix muted small', title: 'Republic of Ireland, EirGrid dashboard' }, regionText('Ireland', n.roi, 'IE'))] : []),
    )
  } catch {
    box.replaceChildren(h('div', { class: 'muted small' }, 'No national data'))
  }
}
