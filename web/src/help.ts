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
    h('p', {}, 'This map shows Britain’s electricity network from public data: substations, how much ',
      h('strong', {}, 'spare capacity'), ' they have, and live readings where operators publish them.'),
    h('h3', {}, 'Reading the map'),
    h('ul', {},
      h('li', {}, h('strong', {}, 'Shapes'), ' are substation types: ◆ big grid supply points, ■ bulk supply points, ● local primaries. Zoom in for the small street-level (LV) substations.'),
      h('li', {}, h('strong', {}, 'Colours'), ' show spare capacity: green = room to connect, amber = a little, red = none, grey = not published.'),
      h('li', {}, 'Coloured outlines are the six network operators’ areas.')),
    h('h3', {}, 'Try this'),
    h('ol', {},
      h('li', {}, 'Search for a street, postcode or substation in the box on the left.'),
      h('li', {}, 'Click any dot to see its details and which bigger substation feeds it.'),
      h('li', {}, 'Switch to ', h('strong', {}, 'LV monitoring'), ' to see only street substations that have a monitor fitted.')),
    h('p', { class: 'muted small' }, 'Hover any dotted-underlined word for a plain-English explanation. ⟲ Reset puts everything back. Data comes from the operators’ open data portals and is refreshed automatically.'),
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
