// One top bar for every page (map, stats, queue): brand, page tabs, and page-specific actions on the right.
// No maplibre import here, so the light pages can use it too.
import './topbar.css'
import { h } from './dom'

export type Page = 'map' | 'stats' | 'queue' | 'keys'

export function topbar(active: Page, right: Node[] = []): HTMLElement {
  const tab = (href: string, label: string, page: Page, title: string) =>
    h('a', { href, class: active === page ? 'on' : undefined, 'aria-current': active === page ? 'page' : undefined, title }, label)
  return h('header', { class: 'topbar', role: 'banner' },
    h('a', { class: 'tb-brand', href: '/' }, 'GB Grid'),
    h('nav', { class: 'tb-nav', 'aria-label': 'Pages' },
      tab('/', 'Map', 'map', 'Interactive grid map'),
      tab('/stats.html', 'Stats', 'stats', 'Data coverage statistics'),
      tab('/queue.html', 'Queue', 'queue', 'Connection queue by substation'),
      tab('/keys.html', 'Keys', 'keys', 'Set up API keys for locked datasets')),
    h('div', { class: 'tb-right' }, ...right))
}
