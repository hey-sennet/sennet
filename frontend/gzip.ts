import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { gzipSync } from 'node:zlib'
import type { Plugin } from 'vite'

const TEXT = /\.(js|css|html|svg|json|txt)$/

/**
 * Writes a `.gz` beside each built text file, which the server sends to browsers that take
 * gzip (src/sennet/server.py), so it spends no CPU compressing them (docs/performance.md, step 1).
 */
export function gzip(): Plugin {
  return {
    name: 'sennet-gzip',
    apply: 'build',
    writeBundle({ dir }, bundle) {
      for (const file of Object.values(bundle)) {
        if (!dir || !TEXT.test(file.fileName)) continue
        const source = file.type === 'chunk' ? file.code : file.source
        if (source.length < 1024) continue
        writeFileSync(path.join(dir, `${file.fileName}.gz`), gzipSync(source, { level: 9 }))
      }
    },
  }
}
