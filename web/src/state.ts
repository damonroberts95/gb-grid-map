// UI state, mirrored into the URL hash so a reload keeps the view.

import { hashGet, hashSet } from './dom'

export type Mode = 'gen' | 'dem'
export const OPERATORS = ['UKPN', 'NPG', 'ENW', 'SPEN', 'NGED', 'SSEN', 'NIE'] as const
// operators an old link's 'o' list could name; any operator added since starts on
const LEGACY_OPS = ['UKPN', 'NPG', 'ENW', 'SPEN', 'NGED', 'SSEN']
export const THRESHOLDS = [1, 2, 5, 10, 20] as const

export interface State {
  mode: Mode
  thr: number // MW: headroom at/above = green
  ops: string[]
  kinds: number[] // 1 GSP, 2 BSP, 3 PRIMARY, 4 LV
  links: boolean
  outages: boolean
  neso: boolean
  simple: boolean // 'LV monitoring' mode: only monitored LV substations
  mounts: string[] // g ground, p pole, u unknown
}

const DEFAULT: State = {
  mode: 'dem', thr: 5, ops: [...OPERATORS], kinds: [1, 2, 3, 4],
  links: false, outages: true, neso: true, simple: false, mounts: ['g', 'p', 'u'],
}

type Listener = (s: State) => void
const listeners: Listener[] = []
export let state: State = load()

export function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  save()
  for (const l of listeners) l(state)
}

/** Back to the default filters/mode (layers + map position are reset by their owners). */
export function reset() {
  set({ ...DEFAULT })
}

export function onChange(l: Listener) {
  listeners.push(l)
}

function save() {
  hashSet({
    m: state.mode, t: String(state.thr), o: null, ox: OPERATORS.filter((o) => !state.ops.includes(o)).join(','), k: state.kinds.join(','),
    l: [state.links && 'links', state.outages && 'out', state.neso && 'neso'].filter(Boolean).join(','),
    s: state.simple ? '1' : '0', mt: state.mounts.join(','),
  })
}

function load(): State {
  const p = { get: hashGet, has: (k: string) => hashGet(k) !== null }
  // partial hashes (e.g. a shared '#s=1' link) override just those keys
  if (!p.has('m')) return { ...DEFAULT }
  const layers = (p.get('l') ?? '').split(',')
  const thr = Number(p.get('t'))
  return {
    mode: p.get('m') === 'gen' ? 'gen' : 'dem',
    thr: THRESHOLDS.includes(thr as (typeof THRESHOLDS)[number]) ? thr : DEFAULT.thr,
    // hidden operators ('ox'), so a new operator starts on; old links listed the shown ones ('o')
    ops: (() => {
      const off = p.has('ox') ? (p.get('ox') ?? '').split(',')
        : LEGACY_OPS.filter((o) => !(p.get('o') ?? '').split(',').includes(o))
      return OPERATORS.filter((o) => !off.includes(o))
    })(),
    // old links had a separate LV toggle ('l=lv'): fold it into kind 4
    kinds: [...(p.get('k') ?? '').split(',').map(Number).filter((k) => [1, 2, 3, 4].includes(k)),
      ...(layers.includes('lv') && !(p.get('k') ?? '').split(',').includes('4') ? [4] : [])],
    links: layers.includes('links'),
    outages: layers.includes('out'),
    neso: layers.includes('neso'),
    simple: false, // LV monitoring tab removed (one LV view; monitored sites are ringed)
    mounts: p.get('mt') !== null ? (p.get('mt') ?? '').split(',').filter((m) => ['g', 'p', 'u'].includes(m)) : ['g', 'p', 'u'],
  }
}

// Map position lives in the same hash (MapLibre `hash: 'map'` option).
