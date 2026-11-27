// Satellite imagery under our layers: Esri World Imagery tiles (online only). Sits above the basemap's
// fills and roads but under its labels and all our data. Choice remembered per browser (best effort).

import type { Map as MlMap } from 'maplibre-gl'

const KEY = 'gridmap.satellite'
const SRC = 'satellite'
const URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

let on = false
try { on = localStorage.getItem(KEY) === '1' } catch { /* default off */ }

export const satelliteOn = () => on

/** First basemap label layer: imagery goes just under it. */
function firstLabel(map: MlMap): string | undefined {
  return map.getStyle().layers.find((l) => l.type === 'symbol' && 'source' in l && l.source === 'protomaps')?.id
}

/** Add or re-place the imagery layer. Call after load and after each style swap (theme change). */
export function applySatellite(map: MlMap) {
  if (!map.getSource(SRC)) {
    map.addSource(SRC, {
      type: 'raster', tiles: [URL], tileSize: 256, maxzoom: 19,
      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, GIS User Community',
    })
  }
  if (!map.getLayer(SRC)) map.addLayer({ id: SRC, type: 'raster', source: SRC }, firstLabel(map))
  else map.moveLayer(SRC, firstLabel(map))
  map.setLayoutProperty(SRC, 'visibility', on ? 'visible' : 'none')
}

export function toggleSatellite(map: MlMap) {
  on = !on
  try { localStorage.setItem(KEY, on ? '1' : '0') } catch { /* ignore */ }
  applySatellite(map)
  window.dispatchEvent(new Event('gridmap:satellite'))
}
