import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { crc32, deflateSync } from 'node:zlib'
import { chooseProject } from './projects'

// The e2e server's fake speech hears this whenever there is sound (playwright.config.ts).
const HEARD = 'add a health check'
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')

/** A real PNG, of one colour, that the browser can decode and shrink. */
function png(width: number, height: number): Buffer {
  const chunk = (kind: string, data: Buffer) => {
    const body = Buffer.concat([Buffer.from(kind), data])
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const check = Buffer.alloc(4)
    check.writeUInt32BE(crc32(body))
    return Buffer.concat([length, body, check])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header.set([8, 2, 0, 0, 0], 8)
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(width * 3, Buffer.from([0x2a, 0x6f, 0xdb]))])
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', header),
    chunk('IDAT', deflateSync(Buffer.concat(Array(height).fill(row)))), chunk('IEND', Buffer.alloc(0))])
}

async function addProject(page: Page, name: string) {
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const response = await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })
  expect(response.ok()).toBe(true)
}

async function userMessages(page: Page, name: string) {
  const state = await (await page.request.get('/api/state')).json()
  const project = state.projects.find((p: { name: string }) => p.name === name)
  const tasks = state.tasks.filter((t: { project_id: number }) => t.project_id === project.id)
  const details = await Promise.all(tasks.map(async (t: { id: number }) => (await page.request.get(`/api/tasks/${t.id}`)).json()))
  return details.flatMap((d) => d.messages.filter((m: { role: string }) => m.role === 'user'))
}

async function openTimeline(page: Page) {
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole('button', { name: 'Tasks', exact: true }).click()
    return page.getByRole('region', { name: 'Tasks' })
  }
  await page.getByRole('button', { name: 'Show task', exact: true }).click()
  return page.getByRole('complementary', { name: 'Task', exact: true })
}

test('an image chosen from the device goes with a typed message and shows in the timeline', async ({ page }, info) => {
  const name = `images-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)

  await page.getByRole('button', { name: 'Add an image' }).click()
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Choose images' }).click()
  await (await chooser).setFiles({ name: 'screenshot.png', mimeType: 'image/png', buffer: png(2400, 1200) })
  const tray = page.getByRole('group', { name: 'Images to send' })
  await expect(tray.locator('img')).toHaveCount(1)
  await expect(tray.getByText('Going with what you say or type next')).toBeVisible()

  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('what is wrong in this picture?')
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled() // uploaded
  await composer.press('Enter')
  await expect(page.getByRole('main').getByText('You said: what is wrong in this picture?')).toBeVisible()
  await expect(tray.locator('img')).toHaveCount(0)

  // Shrunk on the device: a long side of 1600, kept a PNG.
  const [sent] = await userMessages(page, name)
  expect(sent.attachments).toEqual([expect.objectContaining({ media_type: 'image/png', width: 1600, height: 800 })])

  const timeline = await openTimeline(page)
  const thumbnail = timeline.getByRole('button', { name: 'Open the image' })
  await expect(thumbnail.locator('img')).toHaveJSProperty('naturalWidth', 1600)
  await page.screenshot({ path: `test-results/${info.project.name}-image-timeline.png` })
  await thumbnail.click()
  await expect(page.getByRole('dialog', { name: 'Image' }).locator('img')).toBeVisible()
})

test('a photo taken in Sennet goes with what is said next', async ({ page, context }, info) => {
  await context.grantPermissions(['camera', 'microphone'])
  const name = `camera-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)

  await page.getByRole('button', { name: 'Add an image' }).click()
  await page.getByRole('button', { name: 'Take a photo' }).click()
  const camera = page.getByRole('dialog', { name: 'Take a photo' })
  await expect(camera.getByRole('button', { name: 'Take the photo' })).toBeEnabled()
  await page.screenshot({ path: `test-results/${info.project.name}-camera.png` })
  await camera.getByRole('button', { name: 'Take the photo' }).click()
  await expect(camera).toBeHidden()
  const tray = page.getByRole('group', { name: 'Images to send' })
  await expect(tray.locator('img')).toHaveCount(1)

  // While you talk, the waiting image lights up as going with it.
  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await talk.hover()
  await page.mouse.down()
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await expect(tray.getByText('Going with what you’re saying')).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-image-talking.png` })
  await page.mouse.up()

  await expect(page.getByRole('main').getByText(`You said: ${HEARD}`)).toBeVisible()
  await expect(tray.locator('img')).toHaveCount(0)
  const [sent] = await userMessages(page, name)
  expect(sent.text).toBe(HEARD)
  expect(sent.attachments).toEqual([expect.objectContaining({ media_type: 'image/jpeg' })])
})

test('an image can be taken out before it is sent', async ({ page }, info) => {
  const name = `images-out-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Add an image' }).click()
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Choose images' }).click()
  await (await chooser).setFiles({ name: 'a.png', mimeType: 'image/png', buffer: png(40, 40) })
  const tray = page.getByRole('group', { name: 'Images to send' })
  await tray.getByRole('button', { name: 'Take this image out' }).click()
  await expect(tray.locator('img')).toHaveCount(0)
  await page.getByRole('button', { name: 'Type instead' }).click()
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled() // nothing to send
})
