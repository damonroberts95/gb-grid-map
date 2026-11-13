// Plain-English definitions for technical terms. One shared tooltip element, shown on hover or keyboard
// focus of any [data-term] element, so it costs nothing per term.

import { h } from './dom'
import { extraDescription } from './extras'

export const GLOSSARY: Record<string, { t: string; d: string }> = {
  headroom: {
    t: 'Spare capacity (headroom)',
    d: 'How much more can connect at a substation before it hits a network limit (thermal, voltage or fault level), per the operator’s latest published assessment. Indicative only: a formal connection application decides.',
  },
  gen_headroom: {
    t: 'Generation headroom',
    d: 'Estimated extra generation or export (MW) that could connect here before a network limit is reached. Each operator calculates it differently, e.g. with or without projects already in the queue.',
  },
  dem_headroom: {
    t: 'Demand headroom',
    d: 'Estimated extra load (e.g. heat pumps, EV charging, new buildings) the substation can supply before its firm capacity is exceeded. Some operators publish it in MVA rather than MW.',
  },
  threshold: {
    t: 'Green threshold',
    d: 'Sites with at least this much headroom show green; between zero and this, amber; zero or negative, red. Pick the size of connection you care about.',
  },
  fault_level: {
    t: 'Fault level',
    d: 'The current (kA) that would flow into a short circuit at this point. Switchgear must safely break and make that current. New generation, especially rotating machines, adds fault current.',
  },
  fault_headroom: {
    t: 'Fault headroom',
    d: 'Switchgear break rating minus the prospective three-phase break fault level (kA). Low or negative values commonly block new synchronous generation even when thermal headroom exists.',
  },
  break: {
    t: 'Break (RMS)',
    d: 'The RMS fault current a circuit breaker must interrupt. "Make" is the peak current when closing onto a fault, typically about 2.5× the break value.',
  },
  rating: {
    t: 'Switchgear rating',
    d: 'The maximum fault current the installed circuit breakers are rated to interrupt (break) or close onto (make).',
  },
  three_phase: {
    t: '3-phase fault',
    d: 'A short circuit across all three phases: usually the largest fault current and the one switchgear is sized for. Single-phase (earth) faults are listed separately.',
  },
  firm: {
    t: 'Firm capacity',
    d: 'Capacity still available with the largest single transformer or circuit out of service (N-1 security). Demand headroom is usually firm capacity minus peak demand.',
  },
  mva: {
    t: 'MVA vs MW',
    d: 'MVA is apparent power; MW is real power. MW ≈ MVA × power factor (typically 0.9–0.95). Transformer ratings and some demand headroom figures are in MVA.',
  },
  kv: { t: 'kV', d: 'Kilovolts: the voltage level. GB distribution runs at 132, 66, 33, 11 and 6.6 kV, then 400/230 V at LV.' },
  busbar: {
    t: 'Busbar',
    d: 'The conductor inside a substation that circuits connect to. One site often has several busbars at different voltages, each with its own headroom.',
  },
  gsp: {
    t: 'Grid Supply Point (GSP)',
    d: 'Where the transmission network (400/275 kV, or 132 kV in Scotland) feeds the distribution network. Ring markers are NESO’s list of GSPs.',
  },
  bsp: {
    t: 'Bulk Supply Point (BSP)',
    d: 'Substation stepping 132 kV down to 33 kV (66 kV in parts of the north-east and London). Feeds a group of primaries.',
  },
  primary: {
    t: 'Primary substation',
    d: 'Steps 33 kV down to 11 kV (or 6.6 kV) for the local high-voltage feeders that supply secondary substations.',
  },
  lv: {
    t: 'LV / secondary substation',
    d: 'The local transformer (11 or 6.6 kV to 400/230 V) on a street or pole that supplies homes and businesses. Coloured by utilisation where published.',
  },
  utilisation: {
    t: 'Utilisation',
    d: 'Peak load as a share of transformer rating. Above ~85% there is little room for new heat pumps, EV chargers or solar export. UKPN publishes 20%-wide bands; we show the band midpoint.',
  },
  transmission_gsp: {
    t: 'Transmission GSPs',
    d: 'NESO’s published grid supply points (transmission to distribution). Shown as rings: no headroom data, but they anchor the hierarchy.',
  },
  schematic: {
    t: 'Schematic feeds',
    d: 'Straight lines from each substation to the one feeding it. Shows the network hierarchy, not real cable or line routes.',
  },
  outage: {
    t: 'Live power cuts',
    d: 'Current unplanned faults (filled) and planned work (hollow) from operator feeds, refreshed every few minutes.',
  },
  queue: {
    t: 'Connection queue',
    d: 'Generation and storage projects in the operator’s Embedded Capacity Register (≥1 MW and smaller). Queued = accepted + offered. Linked to substations by name, so coverage varies.',
  },
  accepted: { t: 'Accepted', d: 'Customer has accepted a connection offer; capacity is reserved but not yet built.' },
  offered: { t: 'Offered', d: 'Operator has issued a connection offer that is not yet accepted.' },
  connected: { t: 'Connected', d: 'Already built and operating.' },
  scoping: { t: 'Scoping / other', d: 'Enquiry or early-stage application; not yet a firm offer. Status unknown also lands here.' },
  rag: {
    t: 'RAG',
    d: 'Red/amber/green rating. Colours here are computed from the headroom number and your threshold, not taken from the operator’s own RAG.',
  },
  frequency: {
    t: 'Grid frequency',
    d: 'Nominal 50 Hz. Falls when demand exceeds generation and rises when there is surplus; must stay within 49.5–50.5 Hz. Source: Elexon.',
  },
  demand_gw: {
    t: 'National demand',
    d: 'Transmission-metered GB demand (Elexon). Excludes embedded solar and wind, which reduce what the transmission system sees.',
  },
  carbon: {
    t: 'Carbon intensity',
    d: 'Grams of CO₂ emitted per kWh of electricity consumed right now (Carbon Intensity API, NESO).',
  },
  basis: {
    t: 'Headroom basis',
    d: 'How the operator defines its headroom figure: firm or non-firm, thermal only or including fault level, before or after queued projects. Compare operators with care.',
  },
  more_layers: { t: 'More layers', d: 'Additional public datasets: generation projects, lines, LV usage, forecasts, areas and more. Hover a layer name for what it shows and where it comes from.' },
  lv_mode: { t: 'LV monitoring mode', d: 'Shows only LV (secondary) substations that operators publish as having an LV monitor, by mount type and utilisation, at every zoom level. Everything else is hidden.' },
  lv_monitored_note: { t: 'Coverage', d: 'Only monitors that operators publish are shown: ENW (PreSense), SPEN, NGED, NPG and SSEN NeRDA. UKPN publishes no monitored-site list. Vendor is rarely published.' },
  parent: { t: 'Fed from', d: 'The upstream substation supplying this one (Primary → BSP → GSP), resolved from operator data.' },
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
