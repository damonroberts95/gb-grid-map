// No API keys set: small dismissible banner linking to the Keys page.
import { h } from './dom'

export async function nudgeKeys() {
  try {
    if (localStorage.getItem('gridmap.keysNudgeDismissed') === '1') return
  } catch { /* show anyway */ }
  let rows: { set: boolean }[] = []
  try { rows = await (await fetch('/api/keys')).json() } catch { return }
  if (!Array.isArray(rows) || rows.some((r) => r.set)) return
  const bar = h('div', { class: 'keys-nudge', role: 'status' },
    h('span', {}, 'No API keys. Some data is missing. '),
    h('a', { href: '/keys.html' }, 'Add free keys'),
    h('button', { type: 'button', 'aria-label': 'Dismiss', onclick: () => {
      try { localStorage.setItem('gridmap.keysNudgeDismissed', '1') } catch { /* ignore */ }
      bar.remove()
    } }, '×'))
  document.body.append(bar)
}
