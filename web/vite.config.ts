import { createReadStream, promises as fsp } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const PMTILES_URL = '/basemap/gb.pmtiles'
const PMTILES_FILE = path.resolve(here, '../data/basemap/gb.pmtiles')

// Serves the PMTiles archive with HTTP Range support (required by the pmtiles client).
// Only the exact archive path is handled; everything else falls through to Vite.
async function servePmtilesRange(req: IncomingMessage, res: ServerResponse, next: () => void) {
  const pathname = req.url?.split('?')[0]
  if (pathname !== PMTILES_URL) return next()
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.statusCode = 405
    res.setHeader('Allow', 'GET, HEAD')
    return res.end()
  }

  let size: number
  try {
    size = (await fsp.stat(PMTILES_FILE)).size
  } catch {
    res.statusCode = 404
    res.setHeader('Content-Type', 'text/plain')
    return res.end(`gb.pmtiles not found at ${PMTILES_FILE}`)
  }

  const base = {
    'Content-Type': 'application/octet-stream',
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'no-cache',
  }
  const range = req.headers.range

  if (!range) {
    res.writeHead(200, { ...base, 'Content-Length': size })
    if (req.method === 'HEAD') return res.end()
    return pipe(res, createReadStream(PMTILES_FILE))
  }

  const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim())
  if (!m || (m[1] === '' && m[2] === '')) {
    res.writeHead(416, { 'Content-Range': `bytes */${size}` })
    return res.end()
  }

  let start: number
  let end: number
  if (m[1] === '') {
    // Suffix range: last N bytes.
    start = Math.max(0, size - Number(m[2]))
    end = size - 1
  } else {
    start = Number(m[1])
    end = m[2] === '' ? size - 1 : Math.min(Number(m[2]), size - 1)
  }
  if (start >= size || start > end) {
    res.writeHead(416, { 'Content-Range': `bytes */${size}` })
    return res.end()
  }

  res.writeHead(206, {
    ...base,
    'Content-Range': `bytes ${start}-${end}/${size}`,
    'Content-Length': end - start + 1,
  })
  if (req.method === 'HEAD') return res.end()
  return pipe(res, createReadStream(PMTILES_FILE, { start, end }))
}

function pipe(res: ServerResponse, stream: ReturnType<typeof createReadStream>) {
  stream.on('error', () => res.destroy())
  res.on('close', () => stream.destroy())
  stream.pipe(res)
}

function pmtilesRangePlugin(): Plugin {
  return {
    name: 'serve-pmtiles-range',
    configureServer(server) {
      server.middlewares.use((req, res, next) => void servePmtilesRange(req, res, next))
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => void servePmtilesRange(req, res, next))
    },
  }
}

const apiTarget = 'http://127.0.0.1:8787'
const proxy = {
  '/api': { target: apiTarget, changeOrigin: true },
  '/data': { target: apiTarget, changeOrigin: true },
}

export default defineConfig({
  plugins: [pmtilesRangePlugin()],
  // MapLibre 6 locates its module worker via new URL('./maplibre-gl-worker.mjs', import.meta.url);
  // pre-bundling moves the file into .vite/deps and breaks that path.
  optimizeDeps: { exclude: ['maplibre-gl'] },
  worker: { format: 'es' },
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(here, 'index.html'),
        stats: path.resolve(here, 'stats.html'),
        queue: path.resolve(here, 'queue.html'),
      },
    },
  },
  server: { proxy },
  preview: { proxy },
})
