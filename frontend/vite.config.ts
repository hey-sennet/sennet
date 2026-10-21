import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  // The Python package serves the built client; see src/agent_talk/server.py.
  build: { outDir: '../src/agent_talk/web', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:7710', ws: true } } },
})
