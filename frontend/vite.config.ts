/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { licenses } from './licenses.ts'

export default defineConfig({
  plugins: [react(), tailwindcss(), licenses({
    root: __dirname, css: path.resolve(__dirname, 'src/index.css'), notices: path.resolve(__dirname, '../NOTICES'),
  })],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  // The Python package serves the built client; see src/sennet/server.py.
  build: { outDir: '../src/sennet/web', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:7710', ws: true } } },
  // Unit tests sit beside the code; tests/ holds the Playwright suites.
  test: { include: ['src/**/*.test.ts'] },
})
