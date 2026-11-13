// Plain-English definitions for technical terms. One shared tooltip element, shown on hover or keyboard
// focus of any [data-term] element, so it costs nothing per term.

import { h } from './dom'
import { extraDescription } from './extras'

export const GLOSSARY: Record<string, { t: string; d: string }> = {
  headroom: { t: 'Spare capacity', d: 'Extra load a substation can take before a network limit. Operator estimate. A guide only.' },
  gen_headroom: { t: 'Generation headroom', d: 'Extra generation that can connect, in MW. Each operator counts it differently.' },
  dem_headroom: { t: 'Demand headroom', d: 'Extra demand the substation can supply. Some operators give MVA.' },
  threshold: { t: 'Green threshold', d: 'Green at or above this. Amber between 0 and this. Red at 0 or below.' },
  fault_level: { t: 'Fault level', d: 'Current (kA) in a short circuit here. New generation adds to it.' },
  fault_headroom: { t: 'Fault headroom', d: 'Breaker rating minus 3-phase fault level (kA). Low means new generators may be blocked.' },
  break: { t: 'Break (RMS)', d: 'Fault current a breaker must stop. "Make" is the peak on closing, about 2.5 times break.' },
  rating: { t: 'Switchgear rating', d: 'Most fault current the breakers can stop (break) or close onto (make).' },
  three_phase: { t: '3-phase fault', d: 'Short circuit across all three phases. Usually the largest.' },
  firm: { t: 'Firm capacity', d: 'Capacity left with the largest transformer or circuit out (N-1).' },
  mva: { t: 'MVA vs MW', d: 'MVA is apparent power. MW is real power. MW is about MVA × 0.9 to 0.95.' },
  kv: { t: 'kV', d: 'Kilovolts. GB distribution runs at 132, 66, 33, 11 and 6.6 kV.' },
  busbar: { t: 'Busbar', d: 'Conductor that circuits connect to. A site can have several.' },
  gsp: { t: 'Grid Supply Point (GSP)', d: 'Where transmission feeds the local network. Rings show NESO’s list.' },
  bsp: { t: 'Bulk Supply Point (BSP)', d: 'Steps 132 kV down to 33 kV (66 kV in parts). Feeds primaries.' },
  primary: { t: 'Primary substation', d: 'Steps 33 kV down to 11 kV (or 6.6 kV).' },
  lv: { t: 'LV substation', d: 'Street or pole transformer. 11 or 6.6 kV down to 400/230 V.' },
  utilisation: { t: 'Utilisation', d: 'Peak load as a share of transformer rating. UKPN gives 20% bands. We show the middle.' },
  transmission_gsp: { t: 'Transmission GSPs', d: 'NESO’s grid supply points. Rings. No headroom data.' },
  schematic: { t: 'Schematic feeds', d: 'Straight lines to the feeding substation. Not real routes.' },
  outage: { t: 'Live power cuts', d: 'Filled: unplanned fault. Hollow: planned work. Refreshes every few minutes.' },
  queue: { t: 'Connection queue', d: 'Projects in the Embedded Capacity Register. Queued = accepted + offered. Matched by name.' },
  accepted: { t: 'Accepted', d: 'Offer accepted. Capacity reserved. Not built.' },
  offered: { t: 'Offered', d: 'Offer made. Not accepted yet.' },
  connected: { t: 'Connected', d: 'Built and running.' },
  scoping: { t: 'Scoping / other', d: 'Early enquiry or unknown status.' },
  rag: { t: 'RAG', d: 'Red, amber, green. We work it out from headroom and your threshold.' },
  frequency: { t: 'Grid frequency', d: 'Nominal 50 Hz. Must stay within 49.5 to 50.5. Source: Elexon.' },
  demand_gw: { t: 'National demand', d: 'GB demand on the transmission grid (Elexon). Excludes small local solar and wind.' },
  carbon: { t: 'Carbon intensity', d: 'Grams of CO₂ per kWh now. Source: NESO Carbon Intensity API.' },
  basis: { t: 'Headroom basis', d: 'How the operator defines headroom. Compare operators with care.' },
  more_layers: { t: 'More layers', d: 'Extra public datasets. Hover a name for details.' },
  lv_mode: { t: 'LV monitoring mode', d: 'Only LV substations that operators say have a monitor.' },
  lv_monitored_note: { t: 'Coverage', d: 'ENW, SPEN, NGED, NPG and SSEN NeRDA only. UKPN publishes no list.' },
  parent: { t: 'Fed from', d: 'Upstream substation: Primary, then BSP, then GSP.' },
}

const tip = h('div', { class: 'tip', role: 'tooltip', id: 'tip', hidden: true })
let showFor: HTMLElement | null = null

/** Wrap a label so hovering or focusing it explains the term. */
export function term(key: string, label?: string, tag = 'span'): HTMLElement {
  const g = GLOSSARY[key]
  const el = h(tag, { class: 'term', 'data-term': key, tabindex: '0', 'aria-describedby': 'tip' }, label ?? g?.t ?? key)
  return el
}

function place(el: HTMLElement) {
  const g = el.dataset.xdesc ? extraDescription(el.dataset.xdesc) : GLOSSARY[el.dataset.term ?? '']
  if (!g) return
  tip.replaceChildren(h('strong', {}, g.t), h('div', {}, g.d))
  tip.hidden = false
  const r = el.getBoundingClientRect()
  const tw = Math.min(300, window.innerWidth - 24)
  tip.style.maxWidth = `${tw}px`
  const below = r.bottom + 8 + tip.offsetHeight < window.innerHeight
  const left = Math.max(12, Math.min(r.left, window.innerWidth - tw - 12))
  tip.style.left = `${left}px`
  tip.style.top = `${below ? r.bottom + 6 : r.top - tip.offsetHeight - 6}px`
  showFor = el
}

function clear(el: HTMLElement | null) {
  if (el && el !== showFor) return
  tip.hidden = true
  showFor = null
}

export function initGlossary() {
  document.body.append(tip)
  const find = (e: Event) => (e.target as HTMLElement | null)?.closest?.('[data-term],[data-xdesc]') as HTMLElement | null
  document.addEventListener('mouseover', (e) => { const t = find(e); if (t) place(t) })
  document.addEventListener('mouseout', (e) => { const t = find(e); if (t) clear(t) })
  document.addEventListener('focusin', (e) => { const t = find(e); if (t) place(t) })
  document.addEventListener('focusout', (e) => clear(find(e)))
  document.addEventListener('keydown', (e) => e.key === 'Escape' && clear(null))
  window.addEventListener('scroll', () => clear(null), true)
}
