import { writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const port = 7799
// The server is the built Python app with a scripted fake Codex and fake speech, which hears
// HEARD whenever there is sound; `npm test` builds the client first.
const HEARD = 'add a health check' // tests/voice.spec.ts expects this
// A paired device's token (tests/e2e_server.py); every browser starts with it as its cookie, except
// where a test pairs a browser of its own.
export const TOKEN = 'e2e-device-token'

// Chromium's fake microphone plays this file: a tone that swells four times a second, loud
// enough for the fake voice detector and varied enough to pass noise suppression.
const micFile = path.join(tmpdir(), 'sennet-fake-mic.wav')
writeFileSync(micFile, wav(48000, 2, (t) => 0.5 * Math.sin(2 * Math.PI * 220 * t) * (0.6 + 0.4 * Math.sin(2 * Math.PI * 4 * t))))

function wav(rate: number, seconds: number, sample: (t: number) => number) {
  const n = rate * seconds
  const data = Buffer.alloc(44 + n * 2)
  data.write('RIFF', 0); data.writeUInt32LE(36 + n * 2, 4); data.write('WAVEfmt ', 8)
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22)
  data.writeUInt32LE(rate, 24); data.writeUInt32LE(rate * 2, 28); data.writeUInt16LE(2, 32); data.writeUInt16LE(16, 34)
  data.write('data', 36); data.writeUInt32LE(n * 2, 40)
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(sample(i / rate) * 32767), 44 + i * 2)
  return data
}
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${micFile}`],
    },
    permissions: ['microphone'],
    storageState: {
      cookies: [{ name: 'sennet_device', value: TOKEN, domain: '127.0.0.1', path: '/', expires: -1,
        httpOnly: true, secure: false, sameSite: 'Strict' }],
      origins: [],
    },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'phone', use: { ...devices['Pixel 7'] } },
  ],
  workers: 1,
  webServer: {
    command: `cd .. && SENNET_FAKE_SPEECH='${HEARD}' SENNET_E2E_TOKEN=${TOKEN} uv run python tests/e2e_server.py ${port} .e2e-home`,
    url: `http://127.0.0.1:${port}/api/state`,
    reuseExistingServer: false,
  },
})
