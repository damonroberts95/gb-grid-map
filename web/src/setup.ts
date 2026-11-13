// First-run setup screen. The packaged app starts with no data: the server downloads the basemap, fetches the
// sources and builds the map layers (GET /api/setup reports progress). main.ts awaits gateOnSetup() before it
// creates the map, so when setup finishes the map simply starts, with no page reload.
// If /api/setup is unreachable (dev server not running, old server) we do not block.

import { h } from './dom'

type Stage = { id: string; label: string; state: 'done' | 'active' | 'pending'; pct: number | null }
type Setup = { stage: string; detail: string; pct: number; ready: boolean; stages?: Stage[]; error?: string }

const CSS = `
.setup{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;padding:24px;
  background:var(--bg,#fff);color:var(--ink,#1d2733);font:15px/1.5 system-ui,-apple-system,sans-serif}
.setup-card{width:min(520px,100%)}
.setup h1{margin:0 0 4px;font-size:22px}
.setup .sub{margin:0 0 22px;color:var(--muted,#5f6b78)}
.setup-bar{height:10px;border-radius:5px;background:var(--bg-soft,#eef1f4);overflow:hidden;margin-bottom:6px}
.setup-bar i{display:block;height:100%;width:0;background:var(--accent,#1f6feb);transition:width .6s ease}
.setup-total{display:flex;justify-content:space-between;color:var(--muted,#5f6b78);font-size:13px;margin-bottom:18px}
.setup ul{list-style:none;margin:0 0 20px;padding:0}
.setup li{display:flex;align-items:center;gap:10px;padding:6px 0;color:var(--muted,#5f6b78)}
.setup li.active{color:var(--ink,#1d2733);font-weight:600}
.setup li.done{color:var(--ink,#1d2733)}
.setup li .dot{width:18px;text-align:center}
.setup li .p{margin-left:auto;font-variant-numeric:tabular-nums;font-size:13px}
.setup .note{padding:12px 14px;border-radius:8px;background:var(--bg-soft,#eef1f4);color:var(--muted,#5f6b78);font-size:13px}
.setup .err{color:#b42318;margin:0 0 12px;font-size:13px}
`

async function fetchSetup(): Promise<Setup | null> {
  try {
    const r = await fetch('/api/setup', { cache: 'no-store' })
    if (!r.ok) return null
    return (await r.json()) as Setup
  } catch {
    return null
  }
}

export async function gateOnSetup(): Promise<void> {
  let s = await fetchSetup()
  if (!s || s.ready) return

  document.head.append(h('style', {}, CSS))
  const bar = h('i')
  const pctText = h('span', {}, '0%')
  const detail = h('span', {}, '')
  const list = h('ul')
  const err = h('p', { class: 'err', hidden: true })
  const root = h('div', { class: 'setup', role: 'status' },
    h('div', { class: 'setup-card' },
      h('h1', {}, 'Setting up GB Grid (10–20 min)'),
      h('p', { class: 'sub' }, 'Downloading map and grid data.'),
      h('div', { class: 'setup-bar' }, bar),
      h('div', { class: 'setup-total' }, detail, pctText),
      err, list,
      h('div', { class: 'note' },
        'One time only. Safe to quit. Setup resumes.')))
  document.body.append(root)

  const render = (x: Setup) => {
    bar.style.width = `${Math.max(2, Math.min(100, x.pct))}%`
    pctText.textContent = `${Math.round(x.pct)}%`
    detail.textContent = x.detail
    err.hidden = !x.error
    err.textContent = x.error ?? ''
    list.replaceChildren(...(x.stages ?? []).map((st) =>
      h('li', { class: st.state },
        h('span', { class: 'dot' }, st.state === 'done' ? '✓' : st.state === 'active' ? '●' : '○'),
        h('span', {}, st.label),
        h('span', { class: 'p' }, st.pct === null ? '' : `${st.pct}%`))))
  }

  while (s && !s.ready) {
    render(s)
    await new Promise((r) => setTimeout(r, 1500))
    s = (await fetchSetup()) ?? s
  }
  root.remove()
}
