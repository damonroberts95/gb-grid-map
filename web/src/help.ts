// "What am I looking at?" card: shown once on first visit, and from the ? Help button any time.
import { h } from './dom'

const SEEN = 'gridmap.helpSeen'

export function showHelp() {
  document.querySelector('dialog.help')?.remove()
  const dlg = h('dialog', { class: 'panel help', 'aria-labelledby': 'help-title' }) as HTMLDialogElement
  const close = () => { try { localStorage.setItem(SEEN, '1') } catch { /* ignore */ } dlg.close() }
  dlg.append(
    h('header', { class: 'd-head' }, h('h2', { id: 'help-title' }, 'How to use this map'),
      h('button', { type: 'button', class: 'close', 'aria-label': 'Close', onclick: close }, '×')),
    h('ul', {},
      h('li', {}, h('strong', {}, 'Shapes'), ': ◆ grid supply point, ■ bulk supply point, ● primary.'),
      h('li', {}, h('strong', {}, 'Colours'), ': green has room, amber a little, red none, grey not published.'),
      h('li', {}, h('strong', {}, 'Search'), ' a street, postcode or substation.'),
      h('li', {}, h('strong', {}, 'Click'), ' a dot for details and what feeds it.'),
      h('li', {}, h('strong', {}, 'LV monitoring'), ' shows street substations with a monitor.')),
    h('p', { class: 'muted small' }, 'Hover dotted words for definitions. ⟲ Reset undoes changes.'),
    h('div', { class: 'help-actions' }, h('button', { type: 'button', class: 'tb-btn primary', onclick: close }, 'Got it')),
  )
  dlg.addEventListener('close', () => dlg.remove())
  document.body.append(dlg)
  dlg.showModal()
}

export function maybeShowWelcome() {
  let seen = false
  try { seen = localStorage.getItem(SEEN) === '1' } catch { /* show */ }
  if (!seen) showHelp()
}
