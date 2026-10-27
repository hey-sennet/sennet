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

test('an approval is answered from its notification, unless it\'s destructive', async ({ page, context }, info) => {
  test.skip(info.project.name !== 'desktop', 'the service worker is the same on every device')
  const name = `push-answer-${info.project.name}`
  mkdirSync(path.join(home, name), { recursive: true })
  const project = await (await page.request.post('/api/projects', { data: { path: path.join(home, name), name }, headers })).json()
  const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text: 'run:npm test' }, headers })).json()
  let approvalId = 0
  await expect.poll(async () => {
    const approvals = (await (await page.request.get('/api/state')).json()).approvals
    approvalId = approvals.find((a: { task_id: number }) => a.task_id === task.id)?.id ?? 0
    return approvalId
  }).toBeGreaterThan(0)

  await page.goto('/')
  const [worker] = await Promise.all([context.waitForEvent('serviceworker'),
    page.evaluate(() => navigator.serviceWorker.register('/sw.js'))])
  await page.goto('about:blank') // no page showing, so the notification shows
  // Chromium can't show or tap a notification here: the worker's notifications are recorded, and
  // the push and the tap dispatched. The events aren't trusted, so waitUntil throws, after the
  // handler has started its work.
  await worker.evaluate(() => {
    const shown: { title: string; body?: string; actions: string[]; data: unknown }[] = []
    Object.assign(self, { shown })
    self.registration.showNotification = async (title, options = {}) => {
      shown.push({ title, body: options.body, actions: ((options as { actions?: { title: string }[] }).actions ?? []).map((a) => a.title), data: options.data })
    }
  })
  const show = (notice: object) => worker.evaluate(async (notice) => {
    const shown = (self as unknown as { shown: unknown[] }).shown
    const before = shown.length
    self.dispatchEvent(new PushEvent('push', { data: JSON.stringify(notice) }))
    for (let i = 0; i < 50 && shown.length === before; i++) await new Promise((resolve) => setTimeout(resolve, 50))
    return shown[before] ?? null
  }, notice)
  const tap = (action: string, data: object) => worker.evaluate(async ({ action, data }) => {
    const notification = { data, close() {} } as unknown as Notification
    self.dispatchEvent(Object.assign(new ExtendableEvent('notificationclick'), { notification, action }))
  }, { action, data })
  const lastShown = () => worker.evaluate(() => (self as unknown as { shown: { title: string }[] }).shown.at(-1)?.title)

  const notice = { title: 'Approval needed', body: 'Run npm test\nrun:npm test', task_id: task.id, kind: 'approval', approval_id: approvalId }
  const data = { task_id: task.id, approval_id: approvalId }
  expect(await show({ ...notice, answerable: false })).toMatchObject({ actions: ['Open'] })
  expect(await show({ ...notice, answerable: true })).toEqual({ title: 'Approval needed', body: 'Run npm test\nrun:npm test',
    actions: ['Approve', 'Decline'], data })
  await tap('approve', data)
  await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')

  // Answered already: it says so, and a tap opens the task.
  await tap('approve', data)
  await expect.poll(lastShown).toBe('Approval not answered')
})
