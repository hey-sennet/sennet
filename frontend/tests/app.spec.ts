import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

// Created by tests/e2e_server.py; each browser project gets its own directory.
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')

test('add a project, run a task, approve a command, follow up', async ({ page }, info) => {
  const name = `e2e-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  // Everything, fonts included, is served by Agent Talk itself.
  const external: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== new URL(info.project.use.baseURL!).origin) external.push(request.url())
  })

  await page.goto('/')
  await expect(page.getByTitle('Connected')).toBeVisible()

  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Project directory').fill(directory)
  await dialog.getByLabel('Spoken name').fill(name)
  await dialog.getByLabel('Aliases').fill(`${name}-alias`)
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(directory)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()

  await page.getByLabel('Project').selectOption({ label: name })
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder(`Ask codex to do something in ${name}`)
  await composer.fill('run:npm test')
  const chimed = page.waitForRequest(/approval-chime/)
  await composer.press('Enter')

  // The approval waits under the disc, with a chime; the reply shows there once the task is done.
  const stage = page.getByRole('main')
  await expect(stage.getByText(`${name} wants to run a command`)).toBeVisible()
  await chimed
  await page.screenshot({ path: `test-results/${info.project.name}-approval.png` })
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(stage.getByText('Ran it.')).toBeVisible()

  // The timeline: a drawer on desktop, the tasks sheet on phones.
  const timeline = await openTask(page, 'run:npm test')
  await expect(timeline.getByText('Ran it.')).toBeVisible()
  await expect(timeline.getByText('Queued')).toHaveCount(0)
  await expect(timeline.getByText('Done', { exact: true })).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-timeline.png` })
  if (info.project.name === 'phone') await page.getByRole('button', { name: 'Close' }).click()

  await page.getByRole('button', { name: 'Type instead' }).click()
  const followUp = page.getByPlaceholder('Follow up on this task')
  await followUp.fill('thanks')
  await followUp.press('Enter')
  await expect(stage.getByText('You said: thanks')).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}.png`, fullPage: true })
  const loaded = await page.evaluate(() => [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replaceAll('"', '')))
  expect(loaded).toContain('Inter Variable')
  expect(external).toEqual([])
})

test('add a Claude Code project, and it reports work it left running', async ({ page }, info) => {
  const name = `claude-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  await page.goto('/')
  await expect(page.getByTitle('Connected')).toBeVisible()

  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('logged in (pro plan)')).toBeVisible()
  await dialog.getByLabel('Project directory').fill(directory)
  await dialog.getByLabel('Spoken name').fill(name)
  await dialog.getByLabel('Agent for the new project').selectOption({ label: 'Claude Code' })
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(directory)).toBeVisible()
  await expect(dialog.getByText("Load this project's own Claude Code settings.", { exact: false }).last()).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()

  await page.getByLabel('Project').selectOption({ label: name })
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder(`Ask claude to do something in ${name}`)
  await composer.fill('bg:make build')
  await composer.press('Enter')
  const stage = page.getByRole('main')
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(stage.getByText('The background command make build finished.')).toBeVisible()
  const timeline = await openTask(page, 'bg:make build')
  await expect(timeline.getByText('Started it in the background.')).toBeVisible()
  await expect(timeline.getByText('The agent picked this up again')).toBeVisible()
})

/** The selected task's timeline: picked from the opened rail into the drawer on desktop; on phones the
 * tasks sheet opens on the selected task. */
async function openTask(page: Page, title: string) {
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole('button', { name: 'Tasks', exact: true }).click()
    return page.getByRole('region', { name: 'Tasks' })
  }
  await page.getByRole('button', { name: 'Show tasks' }).click()
  await page.getByRole('complementary', { name: 'Tasks' }).getByRole('button', { name: new RegExp(title) }).click()
  return page.getByRole('complementary', { name: 'Task', exact: true })
}
