import { existsSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import type { Plugin } from 'vite'

/**
 * Writes the client's license notices next to it at build time (docs/m6-packaging.md):
 * `licenses.txt`, every npm package the bundle contains with its license text, found from
 * the bundle itself; and `notices.txt`, a copy of the repository's NOTICES. Settings → About
 * links to both, so they ship wherever the client does. Workers are bundled on their own, before
 * the page: `worker` goes in the config's worker plugins, so their packages are listed too.
 */
export function licenses({ root, css, notices }: { root: string; css: string; notices: string }): { page: Plugin; worker: Plugin } {
  const sources = new Set<string>()
  const collect = (bundle: Record<string, { type: string; moduleIds?: string[]; originalFileNames?: string[] }>) => {
    for (const file of Object.values(bundle)) {
      file.moduleIds?.forEach((id) => sources.add(id))
      if (file.type === 'asset') file.originalFileNames?.forEach((name) => sources.add(path.resolve(root, name)))
    }
  }
  const worker: Plugin = { name: 'sennet-licenses-worker', apply: 'build', generateBundle: (_, bundle) => collect(bundle) }
  const page: Plugin = {
    name: 'sennet-licenses',
    apply: 'build',
    generateBundle(_, bundle) {
      collect(bundle)
      // Tailwind inlines the stylesheet's own @imports, so they never show up as modules.
      for (const [, name] of readFileSync(css, 'utf8').matchAll(/@import\s+["']([^"'.][^"']*)["']/g)) {
        sources.add(path.join(root, 'node_modules', name))
      }
      const packages = new Map<string, string>()  // name → directory
      for (const source of sources) {
        const found = [...source.matchAll(/node_modules\/((?:@[^/]+\/)?[^/]+)/g)].at(-1)
        if (!found) continue
        const directory = source.slice(0, found.index + found[0].length)
        if (existsSync(path.join(directory, 'package.json'))) packages.set(found[1], directory)
      }
      const entries = [...packages].sort(([a], [b]) => a.localeCompare(b)).map(([name, directory]) => entry(name, directory))
      this.emitFile({
        type: 'asset', fileName: 'licenses.txt',
        source: 'Third-party software in the Sennet web client\n'
          + `${'='.repeat(51)}\n\n${entries.length} packages, each with its license.\n\n${entries.join('\n\n')}\n`,
      })
      this.emitFile({ type: 'asset', fileName: 'notices.txt', source: readFileSync(notices, 'utf8') })
    },
  }
  return { page, worker }
}

function entry(name: string, directory: string): string {
  const manifest = JSON.parse(readFileSync(path.join(directory, 'package.json'), 'utf8'))
  const license = typeof manifest.license === 'string' ? manifest.license : manifest.license?.type ?? 'unknown'
  const file = readdirSync(directory).find((f) => /^(licen[cs]e|copying)(\.|-|$)/i.test(f))
  const text = file ? readFileSync(path.join(directory, file), 'utf8').trim() : `(${name} ships no license file; its package.json says ${license}.)`
  return `${'-'.repeat(72)}\n${name} ${manifest.version} (${license})\n${'-'.repeat(72)}\n\n${text}`
}
