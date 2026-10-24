/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { gzip } from './gzip.ts'
import { licenses } from './licenses.ts'

/**
 * The ONNX runtime's WebAssembly (14 MB) stays out of the build: setup downloads it, pinned, and
 * the server hands it to the wake word worker (src/voice/wakeword.worker.ts sets wasmPaths).
 */
const withoutOrtWasm: Plugin = {
  name: 'sennet-without-ort-wasm',
  apply: 'build',
  generateBundle(_, bundle) {
    for (const name of Object.keys(bundle)) if (/^assets\/ort-wasm-.*\.wasm$/.test(name)) delete bundle[name]
  },
}

const notices = licenses({
  root: __dirname, css: path.resolve(__dirname, 'src/index.css'), notices: path.resolve(__dirname, '../NOTICES'),
})

export default defineConfig({
  plugins: [react(), tailwindcss(), notices.page, gzip()],
  // The wake word worker (src/voice/wakeword.worker.ts) is a module worker.
  worker: { format: 'es', plugins: () => [notices.worker, withoutOrtWasm, gzip()] },
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  // The Python package serves the built client; see src/sennet/server.py.
  build: { outDir: '../src/sennet/web', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:7710', ws: true } } },
  // Unit tests sit beside the code; tests/ holds the Playwright suites.
  test: { include: ['src/**/*.test.ts'] },
})
