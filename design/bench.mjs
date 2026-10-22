// Measure Strata's frame cost across point and gradient-stop counts, with the CPU throttled to
// stand in for slower phones, and save a frozen frame of each setting to compare the look.
// Headless Chromium paints the canvas in software, so throttling inflates painting cost; the
// draw times (script) carry over to phones, the frame rates only roughly. Check on a real phone.
//   python3 -m http.server 8731 --bind 127.0.0.1 --directory design
//   node design/bench.mjs          (writes design/out/bench/)
import { createRequire } from 'node:module'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const here = import.meta.dirname
const require = createRequire(path.join(here, '../frontend/package.json'))
const { chromium } = require('@playwright/test')

const SETTINGS = [[120, 24], [60, 12], [40, 8], [60, 12, 'flat'], [60, 12, 'nofill']] // [points, stops, switch]
const THROTTLE = [1, 4, 6] // CPU slowdown factors
const SECONDS = 6
const base = 'http://127.0.0.1:8731/strata.html?state=listening&shot'
const out = path.join(here, 'out', 'bench')
mkdirSync(out, { recursive: true })

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE })
const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const cdp = await page.context().newCDPSession(page)

const rows = []
for (const [points, stops, toggle] of SETTINGS) {
  const query = `&points=${points}&stops=${stops}${toggle ? `&${toggle}` : ''}`
  await page.goto(`${base}&t=3.3${query}`)
  await page.waitForSelector('body[data-ready]')
  const box = await page.locator('.orb-slot').boundingBox()
  await page.screenshot({
    path: path.join(out, `p${points}-s${stops}${toggle ? `-${toggle}` : ''}.png`),
    clip: { x: box.x - 12, y: box.y - 12, width: box.width + 24, height: box.height + 24 },
  })

  for (const rate of THROTTLE) {
    await page.goto(`${base}&perf${query}`)
    await cdp.send('Emulation.setCPUThrottlingRate', { rate })
    await page.waitForTimeout(1000)
    await page.evaluate(() => window.perfStats())
    await page.waitForTimeout(SECONDS * 1000)
    const s = await page.evaluate(() => window.perfStats())
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
    rows.push({ points, stops, switch: toggle ?? '', cpu: `${rate}x`, frames: s.frames, fps: +s.fps.toFixed(0), 'frame p95': +s.gapP95.toFixed(1),
      'draw p50': +s.drawP50.toFixed(1), 'draw p95': +s.drawP95.toFixed(1) })
  }
}
await browser.close()
console.table(rows)
console.log(`frozen frames in ${out}`)
