// Substation-type shapes as SDF icons (one white shape each; MapLibre tints them with icon-color, so headroom
// colour and type shape combine). Also exported as inline SVG for the key, so map and key always match.

import type { Map as MlMap } from 'maplibre-gl'

export type Shape = 'diamond' | 'square' | 'circle' | 'diamond-ring'

/** kind -> shape. 1 GSP, 2 BSP, 3 Primary. NESO (transmission) GSPs use the hollow diamond. */
export const KIND_SHAPE: Record<number, Shape> = { 1: 'diamond', 2: 'square', 3: 'circle' }
export const KIND_NAME: Record<number, string> = { 1: 'Grid supply point (GSP)', 2: 'Bulk supply point (BSP)', 3: 'Primary substation' }

const PX = 64 // icon canvas size; icon-size 1 = 32 css px at pixelRatio 2

function path(ctx: CanvasRenderingContext2D, shape: Shape, s: number, inset: number) {
  const c = s / 2
  const r = c - inset
  ctx.beginPath()
  if (shape === 'circle') ctx.arc(c, c, r, 0, Math.PI * 2)
  else if (shape === 'square') ctx.rect(c - r * 0.82, c - r * 0.82, r * 1.64, r * 1.64)
  else { ctx.moveTo(c, c - r); ctx.lineTo(c + r, c); ctx.lineTo(c, c + r); ctx.lineTo(c - r, c); ctx.closePath() }
}

const SDF_RADIUS = 8 // px of falloff each side of the edge: room for the halo
const SDF_CUTOFF = 0.25 // edge sits at alpha 0.75, as MapLibre's SDF shader expects

/** Signed distance field from a drawn shape (alpha mask). A plain on/off mask has no falloff, so the halo
 * fills the whole icon square at some sizes ("flickering grey squares"). */
function toSdf(rgba: Uint8ClampedArray): Uint8Array {
  const n = PX * PX
  const inside = new Uint8Array(n)
  for (let i = 0; i < n; i++) inside[i] = rgba[i * 4 + 3] > 127 ? 1 : 0
  // edge pixels: inside pixels with an outside 4-neighbour, and the reverse
  const edges: [number, number, number][] = []
  for (let y = 0; y < PX; y++) for (let x = 0; x < PX; x++) {
    const v = inside[y * PX + x]
    const nb = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]
    if (nb.some(([a, b]) => a >= 0 && b >= 0 && a < PX && b < PX && inside[b * PX + a] !== v)) edges.push([x, y, v])
  }
  const out = new Uint8Array(n * 4)
  for (let y = 0; y < PX; y++) for (let x = 0; x < PX; x++) {
    const v = inside[y * PX + x]
    let best = Infinity
    for (const [ex, ey, ev] of edges) {
      if (ev === v) continue
      const d = (ex - x) ** 2 + (ey - y) ** 2
      if (d < best) best = d
    }
    const dist = (Math.sqrt(best) - 0.5) * (v ? -1 : 1)
    const a = Math.max(0, Math.min(255, Math.round(255 - 255 * (dist / SDF_RADIUS + SDF_CUTOFF))))
    const i = (y * PX + x) * 4
    out[i] = out[i + 1] = out[i + 2] = 255
    out[i + 3] = a
  }
  return out
}

export function addShapeImages(map: MlMap) {
  for (const shape of ['diamond', 'square', 'circle', 'diamond-ring'] as Shape[]) {
    if (map.hasImage(`shape-${shape}`)) continue
    const cv = document.createElement('canvas')
    cv.width = cv.height = PX
    const ctx = cv.getContext('2d')!
    ctx.fillStyle = '#fff'
    ctx.strokeStyle = '#fff'
    if (shape === 'diamond-ring') {
      path(ctx, 'diamond', PX, 10)
      ctx.lineWidth = 7
      ctx.stroke()
    } else {
      path(ctx, shape, PX, 10)
      ctx.fill()
    }
    const img = ctx.getImageData(0, 0, PX, PX)
    map.addImage(`shape-${shape}`, { width: PX, height: PX, data: toSdf(img.data) }, { sdf: true, pixelRatio: 2 })
  }
}

/** Inline SVG swatch for the key (same geometry as the map icons). */
export function shapeSvg(shape: Shape, fill: string, size = 16, stroke = 'var(--ink)'): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(ns, 'svg')
  svg.setAttribute('width', String(size))
  svg.setAttribute('height', String(size))
  svg.setAttribute('viewBox', '0 0 16 16')
  svg.setAttribute('aria-hidden', 'true')
  let el: SVGElement
  if (shape === 'circle') {
    el = document.createElementNS(ns, 'circle')
    el.setAttribute('cx', '8'); el.setAttribute('cy', '8'); el.setAttribute('r', '5.5')
  } else if (shape === 'square') {
    el = document.createElementNS(ns, 'rect')
    el.setAttribute('x', '3'); el.setAttribute('y', '3'); el.setAttribute('width', '10'); el.setAttribute('height', '10')
  } else {
    el = document.createElementNS(ns, 'polygon')
    el.setAttribute('points', '8,1.5 14.5,8 8,14.5 1.5,8')
  }
  const hollow = shape === 'diamond-ring'
  el.setAttribute('fill', hollow ? 'none' : fill)
  el.setAttribute('stroke', hollow ? stroke : 'var(--bg)')
  el.setAttribute('stroke-width', hollow ? '2' : '1')
  svg.append(el)
  return svg
}
