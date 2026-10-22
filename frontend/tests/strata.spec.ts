import { expect, test, type Page } from '@playwright/test'

// Frozen frames of each state (driven by the synthetic voice) against reviewed baselines.
// After an intended change to the drawing, review and refresh them with
// `npx playwright test strata --update-snapshots`.
const STATES = ['idle', 'listening', 'thinking', 'speaking', 'approval']

async function frozen(page: Page, state: string) {
  await page.goto(`/?strata=${state}&t=3.3`)
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('data-ready', 'true')
  return canvas
}

for (const state of STATES) {
  test(`strata ${state}`, async ({ page }) => {
    await expect(await frozen(page, state)).toHaveScreenshot(`strata-${state}.png`, { maxDiffPixelRatio: 0.01 })
  })
}

test('strata under reduced motion only breathes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const listening = await frozen(page, 'listening')
  await expect(listening).toHaveScreenshot('strata-reduced-motion.png', { maxDiffPixelRatio: 0.01 })
})

test('strata pauses while the page is hidden', async ({ page }) => {
  await page.goto('/')
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('data-running', 'true')
  const setVisibility = (state: string) => page.evaluate((state) => {
    Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
  }, state)
  await setVisibility('hidden')
  await expect(canvas).toHaveAttribute('data-running', 'false')
  await setVisibility('visible')
  await expect(canvas).toHaveAttribute('data-running', 'true')
})
