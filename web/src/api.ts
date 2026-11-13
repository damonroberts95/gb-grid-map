// Typed access to the local server (contract: docs/api.md).

export interface Manifest {
  version: number
  layers: { sites?: string; links?: string; lv?: string; lvmon?: string }
  extras?: Record<string, import('./extras').ExtraMeta>
  built_at: string | null
  refreshing: boolean
}

export interface SearchHit {
  type: 'site' | 'lv' | 'place'
  id: string
  name: string
  op: string | null
  context?: string
  kind: string | number | null
  lat: number
  lon: number
}

export interface National {
  fuel_mix?: { fuel: string; mw: number }[]
  demand_mw?: number | null
  frequency_hz?: number | null
  frequency_at?: string | null
  carbon?: { national?: number | null; regions?: { region: string; intensity: number; index: string }[] }
  as_of?: string | null
}

export interface SourceRow {
  source_id: string
  operator: string
  category: string
  status: string
  rows: number | null
  as_of: string | null
  fetched_at: string | null
  cadence: string
  licence: string
  attribution: string
}

async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal })
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
  return (await r.json()) as T
}

export const api = {
  manifest: () => get<Manifest>('/api/manifest'),
  site: (id: string, signal?: AbortSignal) => get<SiteDetail>(`/api/site/${encodeURIComponent(id)}`, signal),
  lv: (id: string, signal?: AbortSignal) => get<Record<string, unknown>>(`/api/lv/${encodeURIComponent(id)}`, signal),
  search: (q: string, signal?: AbortSignal) =>
    get<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}&limit=12`, signal),
  national: () => get<National>('/api/live/national'),
  sources: () => get<SourceRow[]>('/api/sources'),
}

export type Row = Record<string, unknown>

export interface SiteDetail {
  site: Row
  parent: Row | null
  chain: Row[]
  children: Row[]
  assets: (Row & { capacity: Row | null })[]
  faults: Row[]
  queue: { by_status: Record<string, number>; by_tech: Record<string, number>; projects: Row[] }
  sources: Row[]
}
