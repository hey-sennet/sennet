import { defineConfig, devices } from '@playwright/test'

const port = 7799
// The server is the built Python app with a scripted fake Codex; run `npm run build` first.
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'phone', use: { ...devices['Pixel 7'] } },
  ],
  workers: 1,
  webServer: {
    command: `cd .. && uv run python tests/e2e_server.py ${port} .e2e-home`,
    url: `http://127.0.0.1:${port}/api/state`,
    reuseExistingServer: false,
  },
})
