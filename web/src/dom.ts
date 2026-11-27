// Tiny DOM helpers. All data strings go through textContent: operator data is untrusted.

type Child = Node | string | number | null | undefined | false
type Attrs = Record<string, string | number | boolean | EventListener | undefined>

export function h(tag: string, attrs: Attrs = {}, ...children: Child[]): HTMLElement {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === false) continue
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v)
    else if (k === 'class') el.className = String(v)
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue
    el.append(c instanceof Node ? c : document.createTextNode(String(c)))
  }
  return el
}

export const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T

export function fmt(v: number | null | undefined, digits = 1, unit = ''): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const s = Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(digits)
  return unit ? `${s} ${unit}` : s
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 90) return 'just now'
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 129600) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

/** "27 Nov 2026", or "27 Nov 2026 09:45" when the value has a time. Raw text if it is not a date. */
export function day(iso: unknown): string {
  if (typeof iso !== 'string' || !iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  return iso.length > 10 ? `${date} ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : date
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let t: number | undefined
  return (...a: A) => {
    clearTimeout(t)
    t = window.setTimeout(() => fn(...a), ms)
  }
}

/** Read/update one key in the URL hash without percent-encoding '/' and ',' (MapLibre's map=z/lat/lon must stay raw). */
export function hashGet(key: string): string | null {
  for (const part of location.hash.slice(1).split('&')) {
    const i = part.indexOf('=')
    if (i > 0 && part.slice(0, i) === key) return decodeURIComponent(part.slice(i + 1))
    if (part === key) return '' // 'ox' with no '=' (some browsers drop it): empty, not missing
  }
  return null
}

/** null removes the key (e.g. a key replaced by a newer one). */
export function hashSet(values: Record<string, string | null>) {
  const parts = location.hash.slice(1).split('&').filter(Boolean)
  const map = new Map(parts.map((p) => { const i = p.indexOf('='); return [i > 0 ? p.slice(0, i) : p, i > 0 ? p.slice(i + 1) : ''] }))
  for (const [k, v] of Object.entries(values)) {
    if (v === null) map.delete(k)
    else map.set(k, encodeURIComponent(v).replace(/%2F/g, '/').replace(/%2C/g, ','))
  }
  history.replaceState(null, '', `#${[...map].map(([k, v]) => `${k}=${v}`).join('&')}`)
}
