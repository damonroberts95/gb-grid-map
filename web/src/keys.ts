// API keys page: one card per provider, a paste box per key, Save + Test. Key values are only ever sent to the
// local server (PUT body); they are never put in the URL, logs or localStorage, and the server only returns a mask.
import './keys.css'
import { ago, h } from './dom'
import { getJson, shell } from './pages'

interface Step { text: string; url: string | null; link: string | null }
interface Field { env: string; label: string; hint: string }
interface Provider { id: string; name: string; unlocks: string; optional: boolean; steps: Step[]; keys: Field[]; success: string; gotchas: string[]; test: string }
interface Test { ok: boolean; at: string; detail: string }
interface KeyRow { id: string; provider: string; set: boolean; masked: string; last_test: Test | null }
interface Updates { enabled: boolean; current_sha: string | null; fetched_at: string | null; last_check: string | null; last_error: string | null }

async function send(method: string, url: string, body?: unknown): Promise<{ ok: boolean; data: any }> {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined })
  let data: any = null
  try { data = await r.json() } catch { /* empty body */ }
  return { ok: r.ok, data }
}

function status(p: Provider, rows: Map<string, KeyRow>): 'ok' | 'bad' | 'saved' | 'unset' {
  const rs = p.keys.map((k) => rows.get(k.env))
  if (rs.some((r) => !r?.set)) return 'unset'
  const t = rs.map((r) => r!.last_test).find((x) => x)
  return t ? (t.ok ? 'ok' : 'bad') : 'saved'
}

/** NAME=value lines from a keys file, forgiving: BOM, Windows line ends, 'export ', quotes, spaces around '='.
 * Rich text (TextEdit's default) is refused with a hint, since its markup would end up in the values. */
export function parseKeys(text: string): { pairs: [string, string][]; rich: boolean } {
  const t = text.replace(/^\uFEFF/, '')
  if (/^\s*\{\\rtf/.test(t)) return { pairs: [], rich: true }
  const pairs: [string, string][] = []
  for (const raw of t.split(/\r?\n|\r/)) {
    const line = raw.trim().replace(/^export\s+/, '')
    if (!line || line.startsWith('#')) continue
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)$/.exec(line)
    if (!m) continue
    let v = m[2].trim()
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1)
    else v = v.replace(/\s+#.*$/, '')
    if (v) pairs.push([m[1].toUpperCase(), v])
  }
  return { pairs, rich: false }
}

async function main() {
  const root = shell('keys')
  const summary = h('div', { class: 'k-summary' })
  const cards = h('div', {})
  const foot = h('p', { class: 'note k-foot' })
  // import a whole keys file (e.g. a .env a colleague sent): drop it here or pick it; values go only to this app
  const importMsg = h('div', { class: 'k-msg', role: 'status' })
  const picker = h('input', { type: 'file', accept: '.env,.txt,text/plain,*/*', hidden: true }) as HTMLInputElement
  const drop = h('div', { class: 'k-import', tabindex: '0' },
    h('b', {}, 'Have a keys file?'), ' Drop it here, or ',
    h('button', { type: 'button', onclick: () => picker.click() }, 'Choose file'),
    h('span', { class: 'note' }, ' Any name works: .env, env.txt, keys.txt. Plain text, one NAME=value per line.'),
    picker, importMsg)
  root.append(h('h1', {}, 'API keys'),
    h('p', { class: 'sub' }, 'Free keys unlock more data. Stored only on this computer.'),
    drop, summary, cards, foot)

  let guide: Provider[] = []
  let rows = new Map<string, KeyRow>()
  const load = async () => { rows = new Map((await getJson<KeyRow[]>('/api/keys')).map((r) => [r.id, r])) }

  function render() {
    const working = guide.filter((p) => status(p, rows) === 'ok').length
    summary.replaceChildren(h('span', {}, `${working} of ${guide.length} keys working`),
      h('span', { class: 'k-bar', 'aria-hidden': 'true' }, h('i', { style: `width:${(100 * working) / Math.max(1, guide.length)}%` })))
    cards.replaceChildren(...guide.map(card))
  }

  function card(p: Provider): HTMLElement {
    const st = status(p, rows)
    const msg = h('div', { class: 'k-msg', role: 'status' })
    const inputs = new Map<string, HTMLInputElement>()
    const fields = p.keys.map((k) => {
      const row = rows.get(k.env)
      const input = h('input', { type: 'password', autocomplete: 'off', spellcheck: 'false', id: `k-${k.env}`,
        placeholder: row?.set ? `saved ${row.masked}`.trim() : 'paste here' }) as HTMLInputElement
      inputs.set(k.env, input)
      const show = h('button', { type: 'button', 'aria-label': `Show or hide ${k.label}` }, 'Show')
      let shown = '' // saved value put in the box by Show; cleared again by Hide
      show.addEventListener('click', async () => {
        if (input.type === 'password') {
          if (!input.value && row?.set) {
            const r = await fetch(`/api/keys/${k.env}/value`, { headers: { 'X-GB-Grid': '1' } })
            if (r.ok) input.value = shown = String(((await r.json()) as { value?: string }).value ?? '')
          }
          input.type = 'text'
          show.textContent = 'Hide'
        } else {
          if (shown && input.value === shown) input.value = ''
          shown = ''
          input.type = 'password'
          show.textContent = 'Show'
        }
      })
      return h('div', { class: 'k-field' }, h('label', { for: `k-${k.env}` }, k.label), input, show,
        k.hint ? h('span', { class: 'note' }, k.hint) : null)
    })
    const save = h('button', { type: 'button', class: 'primary' }, 'Save') as HTMLButtonElement
    const test = h('button', { type: 'button' }, 'Test') as HTMLButtonElement
    const say = (text: string, cls = '') => { msg.className = `k-msg ${cls}`; msg.textContent = text }
    save.addEventListener('click', async () => {
      const todo = [...inputs].filter(([, i]) => i.value.trim())
      if (!todo.length) return say('Paste a key first.', 'bad')
      save.disabled = true
      for (const [env, input] of todo) {
        const r = await send('PUT', `/api/keys/${env}`, { value: input.value })
        input.value = ''
        if (!r.ok) { save.disabled = false; return say(r.data?.detail ?? 'Could not save.', 'bad') }
      }
      await load()
      const complete = p.keys.every((k) => rows.get(k.env)?.set)
      render()
      if (complete) void runTest(p.id)
    })
    async function runTest(id: string) {
      const target = p.keys[0].env
      const r = await send('POST', `/api/keys/${target}/test`)
      await load()
      render()
      const m = document.getElementById(`msg-${id}`)
      if (m && r.data) { m.className = `k-msg ${r.data.ok ? 'ok' : 'bad'}`; m.textContent = r.data.detail }
    }
    test.addEventListener('click', async () => {
      test.disabled = true
      say('Testing…')
      await runTest(p.id)
    })
    msg.id = `msg-${p.id}`
    const t = p.keys.map((k) => rows.get(k.env)?.last_test).find((x) => x)
    if (t) { msg.className = `k-msg ${t.ok ? 'ok' : 'bad'}`; msg.textContent = `${t.detail} (tested ${ago(t.at)})` }
    const chip = st === 'ok' ? ['Working', 'ok'] : st === 'bad' ? ['Failing', 'bad'] : st === 'saved' ? ['Checking…', ''] : ['Not set', '']
    return h('section', { class: 'k-card' },
      h('h2', {}, p.name, h('span', { class: `k-chip ${chip[1]}` }, chip[0]), p.optional ? h('span', { class: 'k-opt' }, 'optional') : null),
      h('p', { class: 'k-unlocks' }, p.unlocks),
      h('ol', {}, ...p.steps.map((s) => h('li', {}, s.text, s.url ? ' ' : null, s.url ? h('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer' }, s.link ?? 'Open') : null))),
      ...fields,
      h('div', { class: 'k-field' }, save, test),
      msg,
      p.gotchas.length ? h('details', {}, h('summary', {}, 'Notes'), h('ul', {}, ...p.gotchas.map((g) => h('li', {}, g)))) : null)
  }

  const sayImport = (text: string, cls = '') => { importMsg.className = `k-msg ${cls}`; importMsg.textContent = text }
  async function importFile(f: File) {
    const { pairs, rich } = parseKeys(await f.text())
    if (rich) return sayImport('That file is rich text. In TextEdit choose Format, Make Plain Text, save, then try again.', 'bad')
    const known = new Set(guide.flatMap((p) => p.keys.map((k) => k.env)))
    const use = pairs.filter(([k]) => known.has(k))
    const skipped = pairs.filter(([k]) => !known.has(k)).map(([k]) => k)
    if (!use.length) return sayImport(`No known keys found in ${f.name}. Each line should look like UKPN_API_KEY=abc123.`, 'bad')
    const failed: string[] = []
    for (const [k, v] of use) if (!(await send('PUT', `/api/keys/${k}`, { value: v })).ok) failed.push(k)
    await load()
    render()
    sayImport([`Imported ${use.length - failed.length} key${use.length - failed.length === 1 ? '' : 's'} from ${f.name}.`,
      failed.length ? `Rejected: ${failed.join(', ')}.` : '', skipped.length ? `Not used: ${skipped.join(', ')}.` : '',
      'Testing them now.'].filter(Boolean).join(' '), failed.length ? 'bad' : 'ok')
    for (const p of guide) if (status(p, rows) === 'saved') { await send('POST', `/api/keys/${p.keys[0].env}/test`).catch(() => null); await load(); render() }
  }
  picker.addEventListener('change', () => { if (picker.files?.[0]) void importFile(picker.files[0]); picker.value = '' })
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over') })
  drop.addEventListener('dragleave', () => drop.classList.remove('over'))
  drop.addEventListener('drop', (e) => {
    e.preventDefault()
    drop.classList.remove('over')
    const f = (e as DragEvent).dataTransfer?.files?.[0]
    if (f) void importFile(f)
  })

  try {
    guide = await getJson<Provider[]>('/api/keys/guide')
    await load()
  } catch (e) {
    cards.replaceChildren(h('p', { class: 'err' }, `Could not load keys: ${e}`))
    return
  }
  render()
  // check every saved key once on open, so the status is real (tests run one at a time to be gentle on portals)
  for (const p of guide) {
    if (status(p, rows) !== 'saved') continue
    await send('POST', `/api/keys/${p.keys[0].env}/test`).catch(() => null)
    await load()
    render()
  }
  try {
    const u = await getJson<Updates>('/api/updates')
    foot.textContent = u.current_sha
      ? `Config updated ${ago(u.fetched_at)} (${u.current_sha.slice(0, 7)}).`
      : u.enabled ? 'Config: bundled version. Checks every 6 hours.' : 'Config updates off (dev).'
  } catch { /* optional footer */ }
}

void main()
