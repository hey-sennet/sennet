import { expect, test } from '@playwright/test'
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

  await page.getByLabel('Project').first().selectOption({ label: name })
  const composer = page.getByPlaceholder(`Ask codex to do something in ${name}`)
  await composer.fill('run:npm test')
  await composer.press('Enter')

  await expect(page.getByText('Wants to run npm test')).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-approval.png` })
  await page.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByText('Ran it.')).toBeVisible()
  await expect(page.getByText('Queued')).toHaveCount(0)
  await expect(page.locator('header').getByText('Done')).toBeVisible()

  const followUp = page.getByPlaceholder('Follow up on this task')
  await followUp.fill('thanks')
  await followUp.press('Enter')
  await expect(page.getByText('You said: thanks')).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}.png`, fullPage: true })
  const loaded = await page.evaluate(() => [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replaceAll('"', '')))
  expect(loaded).toContain('Inter Variable')
  expect(external).toEqual([])
})
