// Light/dark theme: swaps basemap flavour + sprite in place (our data layers stay), UI tokens via
// [data-theme] on <html>, and label colours. Choice remembered per browser (localStorage, best effort).

import type { LayerSpecification, Map as MlMap } from 'maplibre-gl'
import { layers, namedFlavor } from '@protomaps/basemaps'

export type Theme = 'light' | 'dark'
const KEY = 'gridmap.theme'

export function initialTheme(): Theme {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'light' || t === 'dark') return t
  } catch { /* storage unavailable: fall through */ }
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function basemap(theme: Theme): { sprite: string; layers: LayerSpecification[] } {
  const flavor = theme === 'dark' ? 'dark' : 'grayscale'
  return {
    sprite: `${location.origin}/basemap/sprites/${flavor}`,
    layers: layers('protomaps', namedFlavor(flavor), { lang: 'en' }) as LayerSpecification[],
  }
}

export const INK = { light: { text: '#1d2733', halo: '#ffffff' }, dark: { text: '#e8edf2', halo: '#0f1419' } }

let current: Theme = initialTheme()
const listeners: ((t: Theme) => void)[] = []

export function theme(): Theme {
  return current
}

export function onTheme(l: (t: Theme) => void) {
  listeners.push(l)
}

export function applyThemeToDocument(t: Theme) {
  document.documentElement.dataset.theme = t
}

/** Switch basemap flavour via setStyle + transformStyle: MapLibre diffs/reloads the style properly (labels,
 * sprites, glyph placement), and our own sources/layers are carried across unchanged. */
export function setTheme(map: MlMap, t: Theme) {
  current = t
  try { localStorage.setItem(KEY, t) } catch { /* ignore */ }
  applyThemeToDocument(t)
  const b = basemap(t)
  // basemap ids from BOTH flavours: dark has layers grayscale lacks (e.g. POIs); missing one here leaves
  // dark-only labels behind after switching back ("Manchester Piccadilly" bug)
  const baseIds = new Set([...basemap('light').layers, ...basemap('dark').layers].map((l) => l.id))
  map.setStyle({ ...map.getStyle(), sprite: b.sprite, layers: b.layers }, {
    diff: false,
    transformStyle: (prev, next) => ({
      ...next,
      sources: { ...(prev?.sources ?? {}), ...next.sources },
      layers: [...next.layers, ...(prev?.layers ?? []).filter((l) => !baseIds.has(l.id))],
    }),
  })
  map.once('style.load', () => { for (const fn of listeners) fn(t) })
}
