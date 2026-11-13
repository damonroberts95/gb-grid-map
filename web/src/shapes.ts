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
    map.addImage(`shape-${shape}`, { width: PX, height: PX, data: new Uint8Array(img.data.buffer) }, { sdf: true, pixelRatio: 2 })
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
