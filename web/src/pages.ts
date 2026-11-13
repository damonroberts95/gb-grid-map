// Shared bits for the stats and queue pages: theme (same data-theme + 'gridmap.theme' key as the map), nav, bars.
import './pages.css'
import { h } from './dom'
import { topbar } from './topbar'

const KEY = 'gridmap.theme'

function initialTheme(): 'light' | 'dark' {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'light' || t === 'dark') return t
  } catch { /* storage unavailable */ }
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function shell(active: 'stats' | 'queue' | 'keys'): HTMLElement {
  const root = document.documentElement
  root.dataset.theme = initialTheme()
  const btn = h('button', { class: 'tb-btn', type: 'button', title: 'Toggle dark mode', 'aria-label': 'Toggle dark mode' }, '◐')
  btn.addEventListener('click', () => {
    const t = root.dataset.theme === 'dark' ? 'light' : 'dark'
    root.dataset.theme = t
    try { localStorage.setItem(KEY, t) } catch { /* ignore */ }
  })
  const main = h('main', { class: 'pg' })
  document.getElementById('app')!.append(topbar(active, [btn]), main)
  return main
}

/** Percentage cell with an inline-width bar; null shows an em dash. */
export function cov(p: number | null | undefined): HTMLElement {
  if (p === null || p === undefined) return h('span', { class: 'dim' }, '—')
  return h('span', { class: 'cov' }, h('i', { style: `width:${Math.max(0, Math.min(100, p))}%` }), h('span', {}, `${p.toFixed(1)}%`))
}

export const num = (n: number | null | undefined) => (n === null || n === undefined ? '—' : n.toLocaleString('en-GB'))

export function table(head: string[], body: (Node | string | number | null)[][], rowAttrs?: (i: number) => Record<string, string | number | boolean | EventListener | undefined>): HTMLElement {
  return h('div', { class: 'tbl-wrap' }, h('table', { class: 't' },
    h('thead', {}, h('tr', {}, ...head.map((x) => h('th', {}, x)))),
    h('tbody', {}, ...body.map((r, i) => h('tr', rowAttrs?.(i) ?? {}, ...r.map((c) => h('td', {}, c)))))))
}

export async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
  return r.json() as Promise<T>
}
