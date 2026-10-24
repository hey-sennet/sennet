import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { chooseProject } from './projects'

// The e2e server's fake speech hears this whenever there is sound (playwright.config.ts),
// and Chromium's fake microphone plays a swelling tone.
const HEARD = 'add a health check'
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')

async function addProject(page: Page, name: string) {
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const response = await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })
  expect(response.ok()).toBe(true)
}

async function taskCount(page: Page) {
  return (await (await page.request.get('/api/state')).json()).tasks.length
}

test('hold to talk starts a task from what was said', async ({ page }, info) => {
  const name = `voice-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await expect(talk).toBeEnabled()

  await talk.hover()
  await page.mouse.down()
  await expect(page.locator('canvas[data-state="listening"]')).toBeVisible()
  await expect(page.getByRole('button', { name: /Listening/ })).toHaveAttribute('aria-pressed', 'true')
  // Microphone audio streams to the server meanwhile, and what it hears shows under the disc.
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await page.mouse.up()

  // The spoken request starts a task, which talking now follows up on, and the agent answers it.
  const stage = page.getByRole('main')
  await expect(stage.getByText(`You said: ${HEARD}`)).toBeVisible()
  await expect(stage.getByText('Following up')).toBeVisible()
})

test('a new task starts blank, without the words sent to the last one', async ({ page }, info) => {
  const name = `voice-blank-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  // A task that keeps working, so the words said to it linger while it does.
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder(`Ask codex to do something in ${name}`)
  await composer.fill('hang')
  await composer.press('Enter')
  const stage = page.getByRole('main')
  await expect(stage.getByRole('button', { name: 'Stop task' })).toBeVisible()

  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await talk.hover()
  await page.mouse.down()
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await page.mouse.up()
  await expect(page.locator('[data-speaker="user"]')).toHaveCount(0) // sent: the task's progress shows

  await page.getByTitle('New task (N)', { exact: true }).click()
  await expect(stage.getByText('New task', { exact: true })).toBeVisible()
  await expect(page.locator('[data-speaker="user"]')).toHaveCount(0)
  for (const task of (await (await page.request.get('/api/state')).json()).tasks) {
    if (task.title === 'hang') await page.request.post(`/api/tasks/${task.id}/cancel`, { headers: { 'X-Sennet': '1' } })
  }
})

test('tap to start and tap again to send a follow-up', async ({ page }, info) => {
  const name = `voice-tap-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder(`Ask codex to do something in ${name}`)
  await composer.fill('first')
  await composer.press('Enter')
  const stage = page.getByRole('main')
  await expect(stage.getByText('You said: first')).toBeVisible()

  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await talk.click() // a quick tap latches
  await expect(page.getByRole('button', { name: 'Listening: tap to send' })).toBeVisible()
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: 'Listening: tap to send' }).click()
  await expect(stage.getByText(`You said: ${HEARD}`)).toBeVisible() // a follow-up on this task
})

test('a conversation hears you without the button, and pressing sends it', async ({ page }, info) => {
  const name = `voice-conversation-${info.project.name}-${info.repeatEachIndex}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  const toggle = page.getByRole('button', { name: /Start a conversation/ })
  await toggle.click()
  await expect(page.getByRole('button', { name: 'End the conversation' })).toHaveAttribute('aria-pressed', 'true')

  // The fake microphone never falls quiet, so the server's turn stays open, captioned.
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await expect(page.locator('canvas[data-state="listening"]')).toBeVisible()
  // Holding the disc takes over that turn; letting go sends it.
  const talk = page.getByRole('button', { name: /Listening/ })
  await talk.hover()
  await page.mouse.down()
  await page.waitForTimeout(400)
  await page.mouse.up()
  await expect(page.getByRole('main').getByText(`You said: ${HEARD}`)).toBeVisible()

  await page.getByRole('button', { name: 'End the conversation' }).click()
  await expect(page.getByRole('button', { name: /Start a conversation/ })).toHaveAttribute('aria-pressed', 'false')
})

test('sliding off the button cancels', async ({ page }, info) => {
  const name = `voice-cancel-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await expect(talk).toBeEnabled()
  const before = await taskCount(page)
  const box = (await talk.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(800)
  await page.mouse.move(box.x + box.width / 2, box.y - 200)
  await page.mouse.up()
  await expect(page.getByRole('button', { name: 'Hold to talk' })).toBeVisible()
  await page.waitForTimeout(500)
  expect(await taskCount(page)).toBe(before)
})

test('the reply is spoken, and stop silences it', async ({ page }, info) => {
  const name = `voice-speak-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await expect(talk).toBeEnabled()
  await talk.hover()
  await page.mouse.down()
  await page.waitForTimeout(1000)
  await page.mouse.up()

  const stop = page.getByRole('button', { name: 'Stop speaking' })
  await expect(stop).toBeVisible()
  await stop.click()
  await expect(stop).toBeHidden()
})

/** The binary frames on the voice socket, each way. */
function audioFrames(page: Page) {
  const frames = { sent: [] as Buffer[], received: [] as Buffer[] }
  page.on('websocket', (ws) => {
    if (!ws.url().endsWith('/api/voice')) return
    ws.on('framesent', ({ payload }) => { if (typeof payload !== 'string') frames.sent.push(payload) })
    ws.on('framereceived', ({ payload }) => { if (typeof payload !== 'string') frames.received.push(payload) })
  })
  return frames
}

async function talkAndHearTheReply(page: Page, name: string) {
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await expect(talk).toBeEnabled()
  await talk.hover()
  await page.mouse.down()
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await page.mouse.up()
  await expect(page.getByRole('main').getByText(`You said: ${HEARD}`)).toBeVisible()
  // Shown while the reply's audio is playing, so it was decoded.
  await expect(page.getByRole('button', { name: 'Stop speaking' })).toBeVisible()
}

const kind = (frame: Buffer) => [frame[0], frame[1]].join(',')

test('voice goes as Opus both ways where the browser has WebCodecs', async ({ page }, info) => {
  const frames = audioFrames(page)
  await talkAndHearTheReply(page, `voice-opus-${info.project.name}`)
  expect(frames.sent.length).toBeGreaterThan(0)
  expect(new Set(frames.sent.map(kind))).toEqual(new Set(['1,2']))
  expect(new Set(frames.received.map(kind))).toEqual(new Set(['1,2']))
  // A few dozen bytes per 60 ms packet, where PCM would be 1,920.
  const packets = frames.sent.filter((f) => f.length > 8)
  expect(packets.reduce((sum, f) => sum + f.length, 0) / packets.length).toBeLessThan(400)
})

test('without WebCodecs, voice stays on PCM', async ({ page }, info) => {
  await page.addInitScript(() => {
    delete (window as Partial<typeof window>).AudioEncoder
    delete (window as Partial<typeof window>).AudioDecoder
  })
  const frames = audioFrames(page)
  await talkAndHearTheReply(page, `voice-pcm-${info.project.name}`)
  expect(frames.sent.every((f) => f.length % 2 === 0 && f.length <= 3200)).toBe(true)
  expect(new Set(frames.received.map(kind))).toEqual(new Set(['1,1']))
})
