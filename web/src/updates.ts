// Update banner. The packaged app downloads updates in the background (GET /api/updates). When one is ready we offer a
// restart; when this app is too old for the newest version we say so. Nothing shows in dev or when up to date.

import { h } from './dom'

type Updates = { state: string; available_sha?: string | null }

const CSS = `
.upd{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:9000;display:flex;align-items:center;gap:12px;
  padding:9px 14px;border-radius:var(--r,10px);background:var(--bg,#fff);color:var(--ink,#1d2733);
  border:1px solid var(--line,#dde2e7);box-shadow:var(--shadow);font:14px/1.4 system-ui,-apple-system,sans-serif}
.upd button{font:inherit;padding:4px 12px;border-radius:6px;border:0;background:var(--accent,#1f6feb);color:#fff;cursor:pointer}
.upd .x{background:none;color:var(--muted,#5f6b78);padding:4px 6px}
`

let banner: HTMLElement | null = null
let dismissed = ''

function show(text: string, restart: boolean, key: string) {
  if (dismissed === key) return
  if (banner?.dataset.key === key) return
  banner?.remove()
  const kids: (HTMLElement | string)[] = [text]
  if (restart) {
    kids.push(
      h('button', {
        onclick: async (e: Event) => {
          const b = e.target as HTMLButtonElement
          b.disabled = true
          b.textContent = 'Restarting…'
          await fetch('/api/updates/apply', { method: 'POST' }).catch(() => undefined)
        },
      }, 'Restart'),
    )
  }
  kids.push(h('button', { class: 'x', title: 'Hide', 'aria-label': 'Hide', onclick: () => { dismissed = key; banner?.remove(); banner = null } }, '×'))
  banner = h('div', { class: 'upd', role: 'status', 'data-key': key }, ...kids)
  document.body.append(banner)
}

async function poll() {
  try {
    const r = await fetch('/api/updates', { cache: 'no-store' })
    if (!r.ok) return
    const u = (await r.json()) as Updates
    if (u.state === 'ready_to_restart') show('Update ready. Restart to apply.', true, `r${u.available_sha}`)
    else if (u.state === 'needs_new_app') show('A new version of the app is needed. Ask for the latest download.', false, `n${u.available_sha}`)
    else if (banner) { banner.remove(); banner = null }
  } catch {
    /* server busy or restarting: try again next time */
  }
}

export function initUpdates() {
  document.head.append(h('style', {}, CSS))
  void poll()
  setInterval(poll, 60_000)
}
