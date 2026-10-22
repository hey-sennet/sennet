// Film Strata through you talking, the agent replying, then rest, and save stills plus a
// clip to design/out/ (ignored by git).
//   python3 -m http.server 8731 --bind 127.0.0.1 --directory design
//   node design/capture.mjs        (needs ffmpeg and a Chromium for Playwright)
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'
import { mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'

const here = import.meta.dirname
const require = createRequire(path.join(here, '../frontend/package.json'))
const { chromium } = require('@playwright/test')

const FPS = 30
const SCRIPT = [[0, 'listening'], [6, 'speaking'], [12, 'idle'], [16, null]] // [start s, state]
const out = path.join(here, 'out')
const frames = path.join(out, 'frames')
rmSync(frames, { recursive: true, force: true })
mkdirSync(frames, { recursive: true })

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE })
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
await page.goto('http://127.0.0.1:8731/strata.html?state=listening&t=0&shot')
await page.waitForSelector('body[data-ready]')
const box = await page.locator('.orb-slot').boundingBox()
const clip = { x: box.x - 12, y: box.y - 12, width: box.width + 24, height: box.height + 24 }

const end = SCRIPT.at(-1)[0]
let n = 0
for (let i = 0; i < end * FPS; i++) {
  const t = i / FPS
  const cue = SCRIPT.find(([at, state]) => state && Math.abs(at - t) < 0.5 / FPS)
  if (cue) await page.evaluate((s) => window.setState(s), cue[1])
  await page.evaluate((t) => window.renderAt(t), t)
  await page.screenshot({ path: path.join(frames, `${String(n++).padStart(4, '0')}.png`), clip })
  if (i % (FPS / 2) === 0) await page.screenshot({ path: path.join(out, `t${t.toFixed(1).padStart(4, '0')}.png`) })
}
await browser.close()
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', path.join(frames, '%04d.png'),
  '-vf', 'scale=480:-2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', path.join(out, 'strata.mp4')])
rmSync(frames, { recursive: true, force: true })
console.log('wrote', path.join(out, 'strata.mp4'))
