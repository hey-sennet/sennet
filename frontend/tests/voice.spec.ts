import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

// The e2e server's fake speech hears this whenever there is sound (playwright.config.ts),
// and Chromium's fake microphone plays a swelling tone.
const HEARD = 'add a health check'
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')

async function addProject(page: Page, name: string) {
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const response = await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Agent-Talk': '1' } })
  expect(response.ok()).toBe(true)
}

async function taskCount(page: Page) {
  return (await (await page.request.get('/api/state')).json()).tasks.length
}

test('hold to talk starts a task from what was said', async ({ page }, info) => {
  const name = `voice-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await page.getByLabel('Project').first().selectOption({ label: name })
  const talk = page.getByRole('button', { name: 'Hold to talk' }).first()
  await expect(talk).toBeEnabled()

  await talk.hover()
  await page.mouse.down()
  await expect(page.locator('canvas[data-state="listening"]').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /Listening/ }).first()).toHaveAttribute('aria-pressed', 'true')
  // Microphone audio streams to the server meanwhile, and what it hears shows under the disc.
  await expect(page.locator('[data-speaker="user"]', { hasText: HEARD })).toBeVisible()
  await page.mouse.up()

  // The spoken request opens its task, as a typed one does, and the agent answers it.
  await expect(page.getByRole('heading', { name: HEARD })).toBeVisible()
  await expect(page.getByText(`You said: ${HEARD}`)).toBeVisible()
})

test('tap to start and tap again to send a follow-up', async ({ page }, info) => {
  const name = `voice-tap-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await page.getByLabel('Project').first().selectOption({ label: name })
  const composer = page.getByPlaceholder(`Ask codex to do something in ${name}`)
  await composer.fill('first')
  await composer.press('Enter')
  await expect(page.getByText('You said: first')).toBeVisible()

  const talk = page.getByRole('button', { name: 'Hold to talk' })
  await talk.click() // a quick tap latches
  await expect(page.getByRole('button', { name: 'Listening: tap to send' })).toBeVisible()
  await page.waitForTimeout(1000)
  await page.getByRole('button', { name: 'Listening: tap to send' }).click()
  await expect(page.getByText(`You said: ${HEARD}`)).toBeVisible() // a follow-up on this task
})

test('sliding off the button cancels', async ({ page }, info) => {
  const name = `voice-cancel-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  const talk = page.getByRole('button', { name: 'Hold to talk' }).first()
  await expect(talk).toBeEnabled()
  const before = await taskCount(page)
  const box = (await talk.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(800)
  await page.mouse.move(box.x + box.width / 2, box.y - 200)
  await page.mouse.up()
  await expect(page.getByRole('button', { name: 'Hold to talk' }).first()).toBeVisible()
  await page.waitForTimeout(500)
  expect(await taskCount(page)).toBe(before)
})

test('the reply is spoken, and stop silences it', async ({ page }, info) => {
  const name = `voice-speak-${info.project.name}`
  await addProject(page, name)
  await page.goto('/')
  await page.getByLabel('Project').first().selectOption({ label: name })
  const talk = page.getByRole('button', { name: 'Hold to talk' }).first()
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
