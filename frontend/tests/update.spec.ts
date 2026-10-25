import { expect, test, type WebSocketRoute } from '@playwright/test'

// After `sennet upgrade` the server restarts with a new version, and an open page reconnects to
// it with the old client. It should offer to reload.
test('a page offers to reload when the server comes back newer', async ({ page }) => {
  let version: string | undefined
  let events: WebSocketRoute | undefined
  let connections = 0
  // The state loaded after the socket's second connection comes from the "upgraded" server.
  await page.route('**/api/state', async (route) => {
    const response = await route.fetch()
    const state = await response.json()
    version ??= state.version
    await route.fulfill({ response, json: { ...state, version: connections > 1 ? '99.0.0' : state.version } })
  })
  await page.routeWebSocket('**/api/events', (ws) => {
    ws.connectToServer()
    connections++
    events ??= ws
  })

  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await page.getByRole('button', { name: 'Settings' }).click()
  await expect(page.getByRole('dialog')).toContainText(`Sennet ${version}`)
  for (const [name, file] of [['Notices', '/notices.txt'], ['licenses', '/licenses.txt']]) {
    await expect(page.getByRole('link', { name, exact: true })).toHaveAttribute('href', file)
    expect((await page.request.get(file)).status()).toBe(200)
  }
  await page.keyboard.press('Escape')
  await expect(page.getByText('Sennet was updated')).toHaveCount(0)

  await events!.close({ code: 1012, reason: 'restarting' })
  await expect(page.getByText('Sennet was updated')).toBeVisible()
  await page.getByRole('button', { name: 'Reload' }).click()
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
})
