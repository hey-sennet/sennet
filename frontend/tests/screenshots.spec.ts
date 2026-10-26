import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { chooseProject } from './projects'

// The README's screenshot, on a desktop, from the fake agent's scenes (tests/fake_codex.py SCENES). Only on asking:
//   SCREENSHOTS=1 npx playwright test screenshots
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')
const out = path.resolve(import.meta.dirname, '../../docs/images')
const headers = { 'X-Sennet': '1' }

test.skip(!process.env.SCREENSHOTS, 'makes the README screenshots: SCREENSHOTS=1')

test('README screenshot', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop')
  await page.setViewportSize({ width: 1440, height: 900 })
  const name = 'webshop'
  const directory = path.join(home, `${name}-${info.project.name}`)
  mkdirSync(directory, { recursive: true })
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  for (const text of ['why is the dashboard slow to load', 'add a health check endpoint that says whether the database is up']) {
    const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text }, headers })).json()
    await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')
  }

  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await chooseProject(page, name)
  // Started here, so it asks here.
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('the upload test keeps failing on CI, can you find out why and fix it')
  await composer.press('Enter')
  await composer.press('Escape')
  const typeInstead = page.getByRole('button', { name: 'Type instead' })
  // Closed once the type bar has shrunk back to the glyph.
  await expect.poll(async () => (await typeInstead.locator('xpath=ancestor::form').boundingBox())?.width).toBe(44)
  const stage = page.getByRole('main')
  await expect(stage.getByText(`${name} wants to run a command`)).toBeVisible()

  await page.getByRole('button', { name: 'Show tasks' }).click()
  await stage.getByRole('button', { name: 'Fix the flaky upload test' }).click()
  await expect(page.getByRole('complementary', { name: 'Task', exact: true }).getByText('Wants to')).toBeVisible()
  await page.waitForTimeout(1500) // the words and panels settle
  await page.screenshot({ path: path.join(out, 'desktop.png') })
})
