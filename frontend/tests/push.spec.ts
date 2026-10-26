import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')
const headers = { 'X-Sennet': '1' }

test.use({ permissions: ['microphone', 'notifications'] })

test('turn notifications on for this device, choose what notifies, and turn them off', async ({ page }) => {
  await page.request.delete('/api/push', { headers })
  // Headless Chromium has no push service to subscribe with: stand in for the browser's.
  await page.addInitScript(() => {
    let current: PushSubscription | null = null
    PushManager.prototype.subscribe = async function () {
      const keys = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'])
      const raw = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey))
      const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
      current = {
        endpoint: 'https://push.example.com/send/e2e',
        options: { applicationServerKey: null, userVisibleOnly: true },
        toJSON: () => ({ endpoint: 'https://push.example.com/send/e2e', keys: { p256dh: b64(raw), auth: b64(new Uint8Array(16)) } }),
        unsubscribe: async () => ((current = null), true),
      } as unknown as PushSubscription
      return current
    }
    PushManager.prototype.getSubscription = async () => current
  })
  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')

  await dialog.getByLabel('Notify this device').click()
  await expect(dialog.getByLabel('Notify this device')).toBeChecked()
  await expect(dialog.getByLabel('fails')).toBeChecked()
  const stored = async () => (await (await page.request.get('/api/push')).json()).subscription
  expect(await stored()).toEqual({ endpoint: 'https://push.example.com/send/e2e', kinds: ['approval', 'finished', 'failed'] })
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.active?.scriptURL))
    .toMatch(/\/sw\.js$/)

  await dialog.getByRole('button', { name: 'Send a test' }).click()
  await expect(page.getByText('Sent a test notification')).toBeVisible()

  await dialog.getByLabel('finishes').click()
  await expect(dialog.getByLabel('finishes')).not.toBeChecked()
  expect((await stored()).kinds).toEqual(['approval', 'failed'])

  await dialog.getByLabel('Notify this device').click()
  await expect(dialog.getByLabel('fails')).toBeHidden()
  expect(await stored()).toBeNull()
})

test('notifications blocked for the site can\'t be turned on, and it says how to allow them', async ({ page }) => {
  await page.request.delete('/api/push', { headers })
  await page.addInitScript(() => {
    const query = navigator.permissions.query.bind(navigator.permissions)
    navigator.permissions.query = async (descriptor) =>
      descriptor.name === 'notifications' ? ({ state: 'denied', addEventListener() {}, removeEventListener() {} } as unknown as PermissionStatus) : query(descriptor)
  })
  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByLabel('Notify this device')).toBeDisabled()
  await expect(dialog.getByText(/blocked for this site\. Allow them in its site settings/)).toBeVisible()
})

test("a notification's link opens its task", async ({ page }, info) => {
  const name = `push-${info.project.name}`
  mkdirSync(path.join(home, name), { recursive: true })
  const project = await (await page.request.post('/api/projects', { data: { path: path.join(home, name), name }, headers })).json()
  const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text: `ping ${name}` }, headers })).json()
  await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')

  await page.goto(`/?task=${task.id}`)
  await expect(page.getByText(`You said: ping ${name}`).first()).toBeVisible()
  expect(new URL(page.url()).search).toBe('')
})
