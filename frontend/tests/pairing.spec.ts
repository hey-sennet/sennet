import { expect, test, type Browser, type TestInfo } from '@playwright/test'

// Every other suite starts paired (playwright.config.ts). These start a browser that isn't.
async function unpaired(browser: Browser, info: TestInfo) {
  const context = await browser.newContext({ ...info.project.use, storageState: { cookies: [], origins: [] } })
  return context.newPage()
}

test('pair a browser with the code a paired one shows, then unpair it', async ({ page, browser }, info) => {
  const stranger = await unpaired(browser, info)
  await stranger.goto('/')
  await expect(stranger.getByRole('heading', { name: 'Pair this device' })).toBeVisible()
  await stranger.screenshot({ path: `test-results/${info.project.name}-pair.png` })
  await stranger.getByLabel('Pairing code').fill('WRONG-CODE')
  await stranger.getByRole('button', { name: 'Pair' }).click()
  await expect(stranger.getByRole('alert')).toContainText("That code didn't work")

  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Pair a device' }).click()
  const code = await dialog.getByLabel('Pairing code').textContent()
  expect(code).toMatch(/^[0-9A-Z]{4}-[0-9A-Z]{4}$/)
  await dialog.screenshot({ path: `test-results/${info.project.name}-pairing-code.png` })

  await stranger.getByLabel('Pairing code').fill(code!.toLowerCase())
  await stranger.getByRole('button', { name: 'Pair' }).click()
  await expect(stranger.getByTitle('Connected')).toBeVisible()

  // The paired device hears about it, and lists the new one.
  // Named from the browser's user agent: the desktop project's says Windows.
  const name = info.project.name === 'phone' ? 'Chrome on Android' : 'Chrome on Windows'
  await expect(page.getByText(`Paired ${name}`, { exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Pair a device' })).toBeVisible()
  const unpair = dialog.getByRole('button', { name: `Unpair ${name}` }).last()
  page.once('dialog', (question) => question.accept())
  await unpair.click()
  await expect(stranger.getByRole('heading', { name: 'Pair this device' })).toBeVisible()
})

test('a pairing link pairs straight away, once', async ({ page, browser }, info) => {
  const shown = await (await page.request.post('/api/devices/pairing', { headers: { 'X-Sennet': '1' } })).json()
  const link = new URL(shown.link)
  const stranger = await unpaired(browser, info)
  await stranger.goto(link.pathname + link.hash)
  await expect(stranger.getByTitle('Connected')).toBeVisible()
  expect(new URL(stranger.url()).hash).toBe('')

  const another = await unpaired(browser, info)
  await another.goto(link.pathname + link.hash)
  await expect(another.getByRole('alert')).toContainText("That code didn't work")
})
