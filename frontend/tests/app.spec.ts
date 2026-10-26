import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { chooseProject } from './projects'

// Created by tests/e2e_server.py; each browser project gets its own directory.
const home = path.resolve(import.meta.dirname, '../../.e2e-home/repo')

test('add a project, run a task, approve a command, follow up', async ({ page }, info) => {
  const name = `e2e-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  // Everything, fonts included, is served by Sennet itself.
  const external: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).origin !== new URL(info.project.use.baseURL!).origin) external.push(request.url())
  })

  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()

  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Project directory').fill(directory)
  await dialog.getByLabel('Project name').fill(name)
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(directory)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()

  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('run:npm test')
  const chimed = page.waitForRequest(/approval-chime/)
  await composer.press('Enter')

  // The approval waits under the disc, with a chime; the reply shows there once the task is done.
  const stage = page.getByRole('main')
  await expect(stage.getByText(`${name} wants to run a command`)).toBeVisible()
  await chimed
  // Tasks in one project run side by side, so it says which task asks, and that opens it.
  const asking = stage.getByRole('button', { name: 'run:npm test' })
  await expect(asking).toBeVisible()
  if (info.project.name === 'desktop') {
    await asking.click()
    await expect(page.getByRole('complementary', { name: 'Task', exact: true }).getByText('Wants to')).toBeVisible()
  }
  await page.screenshot({ path: `test-results/${info.project.name}-approval.png` })
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(stage.getByText('Ran it.')).toBeVisible()

  // The timeline: a drawer on desktop, the tasks sheet on phones.
  const timeline = await openTask(page, 'run:npm test')
  await expect(timeline.getByText('Ran it.')).toBeVisible()
  // Titled from what was typed, so those words show once, as the heading.
  await expect(timeline.getByText('run:npm test', { exact: true })).toHaveCount(1)
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

test('an edited file opens to its diff in the timeline', async ({ page }, info) => {
  const name = `diff-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  expect((await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })).ok()).toBe(true)
  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  await page.getByPlaceholder('Start a new task').fill('edit:notes.txt')
  await page.keyboard.press('Enter')
  const stage = page.getByRole('main')
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(stage.getByText('Edited.')).toBeVisible()
  const timeline = await openTask(page, 'edit:notes.txt')
  const file = timeline.getByRole('button', { name: 'notes.txt' })
  await file.click()
  await expect(file).toHaveAttribute('aria-expanded', 'true')
  await expect(timeline.getByText('+b', { exact: true })).toBeVisible()
})

test('a task shows the tokens it used, and Settings the agents\' limits', async ({ page }, info) => {
  const name = `usage-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  expect((await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })).ok()).toBe(true)
  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  await page.getByPlaceholder('Start a new task').fill('count the tokens')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main').getByText('You said: count the tokens')).toBeVisible()

  // The fake Codex reports two model responses of 1,000 tokens in and 20 out.
  const timeline = await openTask(page, 'count the tokens')
  await expect(timeline.getByText('2k in, 40 out')).toBeVisible()
  await expect(page.getByRole('group', { name: 'Usage limits' })).toHaveCount(0) // not in the task list
  if (info.project.name === 'phone') await page.getByRole('button', { name: 'Close' }).click()

  await page.getByRole('button', { name: 'Settings' }).click()
  const usage = page.getByRole('dialog').getByRole('group', { name: 'Usage limits' })
  await expect(usage.first()).toBeVisible()
  await expect(usage.getByRole('meter', { name: '5-hour limit used' }).first()).toBeVisible()
  await expect(usage.getByText(/^30% used · resets /).first()).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-usage.png` })
})

/** The task list: the rail on desktop, the tasks sheet on phones, with nothing selected. */
async function openList(page: Page) {
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole('button', { name: 'Tasks', exact: true }).click()
    return page.getByRole('region', { name: 'Tasks' })
  }
  await page.getByRole('button', { name: /^Show tasks/ }).click()
  return page.getByRole('complementary', { name: 'Tasks' })
}

async function finishedTasks(page: Page, name: string, texts: string[]) {
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const headers = { 'X-Sennet': '1' }
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  for (const text of texts) {
    const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text }, headers })).json()
    await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')
  }
}

test('the list starts with the latest finished tasks and shows more on asking', async ({ page }, info) => {
  const texts = Array.from({ length: 11 }, (_, i) => `paged ${info.project.name} ${i}`)
  await finishedTasks(page, `paged-${info.project.name}`, texts)
  await page.goto('/')
  const list = await openList(page)
  const earlier = list.getByRole('region', { name: 'Earlier' })
  await expect(earlier.getByText(texts[10], { exact: true })).toBeVisible()
  await expect(earlier.getByRole('listitem')).toHaveCount(10)
  await expect(earlier.getByText(texts[0], { exact: true })).toHaveCount(0)
  await earlier.getByRole('button', { name: 'Show more' }).click()
  await expect(earlier.getByText(texts[0], { exact: true })).toBeVisible()
})

test('on a phone, a long task opens at its latest, with the way back and its stop button in reach', async ({ page }, info) => {
  test.skip(info.project.name !== 'phone', 'the tasks sheet is the phone\'s')
  const name = `long-${info.project.name}`
  await finishedTasks(page, name, ['long 0'])
  const headers = { 'X-Sennet': '1' }
  const task = (await (await page.request.get('/api/state')).json()).tasks.find((t: { title: string }) => t.title === 'long 0')
  for (let i = 1; i <= 15; i++) {
    await page.request.post(`/api/tasks/${task.id}/messages`, { data: { text: `long ${i}` }, headers })
    await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')
  }
  await page.request.post(`/api/tasks/${task.id}/messages`, { data: { text: 'hang' }, headers }) // still working
  await page.goto('/')
  const sheet = await openList(page)
  await sheet.getByText('long 0', { exact: true }).click()
  await expect(sheet.getByText('You said: long 15')).toBeInViewport()
  await expect(sheet.getByText('You said: long 0')).not.toBeInViewport()
  const stop = sheet.getByRole('button', { name: 'Stop task' })
  await expect(stop).toBeInViewport()
  await sheet.getByText('You said: long 0').scrollIntoViewIfNeeded()
  await expect(stop).toBeInViewport()
  await stop.click()
  await expect(stop).toHaveCount(0)
  const back = sheet.getByRole('button', { name: 'Tasks', exact: true })
  await expect(back).toBeInViewport()
  await back.click()
  await expect(sheet.getByRole('searchbox', { name: 'Search tasks' })).toBeVisible()
})

test('a command shows on one line, and a tap opens the whole of it and its output', async ({ page }, info) => {
  const name = `commands-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const headers = { 'X-Sennet': '1' }
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  const command = 'NODE_ENV=test npm run test -- --coverage "src/components/TaskDetail.test.tsx" 2>&1 | tee $HOME/test.log # all of it'
  const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text: `run:${command}` }, headers })).json()
  let approval: { id: number } | undefined
  await expect.poll(async () => {
    approval = (await (await page.request.get('/api/state')).json()).approvals.find((a: { task_id: number }) => a.task_id === task.id)
    return approval !== undefined
  }).toBe(true)
  await page.request.post(`/api/approvals/${approval!.id}`, { data: { decision: 'approve' }, headers })
  await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')

  await page.goto('/')
  const list = await openList(page)
  await list.getByText(/^run:NODE_ENV=test npm run test/).first().click()
  const timeline = info.project.name === 'phone' ? page.getByRole('region', { name: 'Tasks' }) : page.getByRole('complementary', { name: 'Task' })
  const row = timeline.locator('summary', { hasText: 'npm run test' })
  await expect(row).toBeVisible()
  const line = (await row.boundingBox())!.height
  expect(line).toBeLessThan(24) // one line, however long the command
  await expect(timeline.getByText('ok', { exact: true })).toBeHidden()
  await page.screenshot({ path: `test-results/${info.project.name}-command-closed.png` })
  await row.click()
  await expect(timeline.getByText('ok', { exact: true })).toBeVisible()
  expect((await row.boundingBox())!.height).toBeGreaterThan(line) // the whole command, wrapped
  await page.screenshot({ path: `test-results/${info.project.name}-command-open.png` })
})

test('tool calls in a row fold into one quiet line, which opens to list each', async ({ page }, info) => {
  const name = `tools-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const headers = { 'X-Sennet': '1' }
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text: 'look' }, headers })).json()
  await expect.poll(async () => (await (await page.request.get(`/api/tasks/${task.id}`)).json()).status).toBe('completed')

  await page.goto('/')
  const list = await openList(page)
  await list.getByText(/^look$/).first().click()
  const timeline = info.project.name === 'phone' ? page.getByRole('region', { name: 'Tasks' }) : page.getByRole('complementary', { name: 'Task' })
  const row = timeline.locator('summary', { hasText: 'glossary: look up · Web search' })
  await expect(row).toBeVisible()
  await expect(timeline.getByText('Sennet board game')).toBeHidden()
  await row.click()
  await expect(timeline.getByText('Sennet board game')).toBeVisible()
  await expect(timeline.getByText('sennet', { exact: true })).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-tools-open.png` })
})

test('a command shows while it runs, shimmering, and its result takes its place', async ({ page }, info) => {
  const name = `running-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const headers = { 'X-Sennet': '1' }
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  // The fake agent leaves `sleep 25` running after its turn, and says it finished this much later.
  await page.request.post('/api/tasks', { data: { project_id: project.id, text: 'bg:6' }, headers })

  await page.goto('/')
  const list = await openList(page)
  await list.getByText('bg:6').first().click()
  const timeline = info.project.name === 'phone' ? page.getByRole('region', { name: 'Tasks' }) : page.getByRole('complementary', { name: 'Task' })
  const row = timeline.locator('summary', { hasText: 'sleep 25' })
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('running')
  await expect(row.locator('code')).toHaveClass(/animate-shimmer/)
  // Pinned to the foot of the timeline while it runs, as well as where it started.
  const pinned = timeline.getByRole('region', { name: 'Running in the background' })
  await expect(pinned).toContainText('sleep 25')
  await page.waitForTimeout(1000) // past the row's fade in
  await page.screenshot({ path: `test-results/${info.project.name}-command-running.png` })
  await expect(row).toContainText('done', { timeout: 15_000 })
  await expect(row).toHaveCount(1)
  await expect(row.locator('code')).not.toHaveClass(/animate-shimmer/)
  await expect(pinned).toBeHidden()
})

test('a lot of work in the background folds to its first, and opens to show it all', async ({ page }, info) => {
  const name = `folded-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  const headers = { 'X-Sennet': '1' }
  const project = await (await page.request.post('/api/projects', { data: { path: directory, name }, headers })).json()
  const task = await (await page.request.post('/api/tasks', { data: { project_id: project.id, text: 'bg:20' }, headers })).json()
  // Each turn leaves one more command running, the fake agent's `sleep 25`.
  const running = async () => {
    const detail = await (await page.request.get(`/api/tasks/${task.id}`)).json()
    return detail.events.findLast((e: { kind: string }) => e.kind === 'background')?.data.tasks.length ?? 0
  }
  for (let count = 1; count <= 4; count++) {
    await expect.poll(running).toBe(count)
    if (count < 4) await page.request.post(`/api/tasks/${task.id}/messages`, { data: { text: 'bg:20' }, headers })
  }

  await page.goto('/')
  const list = await openList(page)
  await list.getByText('bg:20').first().click()
  const panel = info.project.name === 'phone' ? page.getByRole('region', { name: 'Tasks' }) : page.getByRole('complementary', { name: 'Task' })
  const pinned = panel.getByRole('region', { name: 'Running in the background' })
  await expect(pinned.locator('code')).toHaveCount(1)
  await expect(pinned.getByRole('button', { name: 'and 3 more' })).toBeVisible()
  await page.waitForTimeout(1000) // past the rows' fade in
  await page.screenshot({ path: `test-results/${info.project.name}-background-folded.png` })
  await pinned.getByRole('button', { name: /Running in the background/ }).click()
  await expect(pinned.locator('code')).toHaveCount(4)
  await page.waitForTimeout(1000)
  await page.screenshot({ path: `test-results/${info.project.name}-background-open.png` })
})

test('search finds a task by what was said in it, typos and all', async ({ page }, info) => {
  const word = `quokka${info.project.name}`
  await finishedTasks(page, `search-${info.project.name}`, [`feed the ${word} at noon`, 'something else'])
  await page.goto('/')
  const list = await openList(page)
  await list.getByRole('searchbox', { name: 'Search tasks' }).fill(word.replace('kk', 'k')) // a typo
  const results = list.getByRole('list', { name: 'Search results' })
  // The fake agent's reply repeats the request, so the match is in a message as well as the title.
  await expect(results.getByRole('listitem')).toHaveCount(1)
  await expect(results.locator('mark').first()).toHaveText(word)
  await page.screenshot({ path: `test-results/${info.project.name}-search.png` })
  await results.getByRole('button', { name: new RegExp(`feed the ${word}`) }).first().click()
  if (info.project.name === 'desktop') {
    await expect(page.getByRole('complementary', { name: 'Task', exact: true }).getByText(`You said: feed the ${word} at noon`)).toBeVisible()
  } else {
    await expect(page.getByRole('region', { name: 'Tasks' }).getByText(`You said: feed the ${word} at noon`)).toBeVisible()
  }
})

test('a finished task is deleted from the list, after asking', async ({ page }, info) => {
  // Long enough to run past a phone's edge, and short of the 80 characters a title is cut to.
  const title = `delete me ${info.project.name}, a title long enough to run well past the list`
  await finishedTasks(page, `delete-${info.project.name}`, [title])
  await page.goto('/')
  const list = await openList(page)
  const row = list.getByRole('listitem').filter({ hasText: title })
  // It ends in an ellipsis, inside the list, rather than widening its row.
  const fits = await row.evaluate((li) => {
    const span = li.querySelector('span.truncate')!
    return { cut: span.scrollWidth > span.clientWidth, inside: li.getBoundingClientRect().right <= li.parentElement!.getBoundingClientRect().right + 0.5 }
  })
  expect(fits).toEqual({ cut: true, inside: true })
  const confirm = page.getByRole('alertdialog')
  if (info.project.name === 'desktop') {
    await row.hover()
    await page.screenshot({ path: `test-results/${info.project.name}-delete.png` })
    await row.getByRole('button', { name: `Delete ${title}` }).click()
    await confirm.getByRole('button', { name: 'Cancel' }).click() // changed their mind
    await expect(confirm).toBeHidden()
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: `Delete ${title}` }).click()
  } else {
    // Swipe the row left to show its Delete.
    const box = (await row.boundingBox())!
    const at = (x: number) => ({ pointerType: 'touch', isPrimary: true, pointerId: 1, clientX: x, clientY: box.y + box.height / 2 })
    const surface = row.locator('> div > div')
    await surface.dispatchEvent('pointerdown', at(box.x + box.width - 20))
    for (const dx of [10, 40, 80, 110]) await surface.dispatchEvent('pointermove', at(box.x + box.width - 20 - dx))
    await surface.dispatchEvent('pointerup', at(box.x + box.width - 130))
    await page.screenshot({ path: `test-results/${info.project.name}-delete.png` })
    const button = (await row.getByRole('button', { name: 'Delete', exact: true }).boundingBox())!
    expect(button.x + button.width).toBeLessThanOrEqual(page.viewportSize()!.width)
    await row.getByRole('button', { name: 'Delete', exact: true }).click()
  }
  await expect(confirm).toHaveAccessibleName(`Delete “${title}”?`)
  await confirm.evaluate((sheet) => Promise.all(sheet.getAnimations().map((a) => a.finished))) // risen into place
  await page.screenshot({ path: `test-results/${info.project.name}-delete-confirm.png` })
  await confirm.getByRole('button', { name: 'Delete' }).click()
  await expect(row).toHaveCount(0)
})

test('removing a project asks first, over Settings, and a tap beside the question dismisses it', async ({ page }, info) => {
  const name = `remove-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Project directory').fill(directory)
  await dialog.getByLabel('Project name').fill(name)
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(directory)).toBeVisible()

  const confirm = page.getByRole('alertdialog')
  await dialog.getByRole('button', { name: `Remove ${name}` }).click()
  await expect(confirm).toHaveAccessibleName(`Remove ${name}?`)
  await expect(page.locator('[data-slot=dialog-overlay]').last()).toBeVisible() // dims Settings, and takes the tap below
  const box = (await page.locator('[data-slot=dialog-content]').boundingBox())! // Settings, hidden from roles under the question
  await page.mouse.click(box.x + 24, box.y + 24) // over Settings, beside the question
  await expect(confirm).toBeHidden()
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText(directory)).toBeVisible()

  await dialog.getByRole('button', { name: `Remove ${name}` }).click()
  await confirm.getByRole('button', { name: 'Remove' }).click()
  await expect(dialog.getByText(directory)).toBeHidden()
  await expect(dialog).toBeVisible()
})

test('a project is picked by browsing the folders on the computer', async ({ page }, info) => {
  const parent = path.join(home, `browse-${info.project.name}`)
  mkdirSync(path.join(parent, 'picked', '.git'), { recursive: true })
  mkdirSync(path.join(parent, 'other'), { recursive: true })
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Project directory').fill(parent) // starts where the box points
  await dialog.getByRole('button', { name: 'Browse' }).click()
  await expect(dialog.getByRole('button', { name: 'other' })).toBeVisible()
  await dialog.getByRole('button', { name: /^picked/ }).click()
  await expect(dialog.getByText('A git repository')).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-browse.png` })
  await dialog.getByRole('button', { name: 'Use this folder' }).click()
  await expect(dialog.getByLabel('Project directory')).toHaveValue(path.join(parent, 'picked'))
  await expect(dialog.getByLabel('Project name')).toHaveValue('picked')
  await dialog.getByLabel('Project name').fill(`picked-${info.project.name}`)
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(path.join(parent, 'picked'), { exact: true })).toBeVisible()
})

test('a running task stops from the main screen, and the task being followed up is named', async ({ page }, info) => {
  const name = `stop-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  expect((await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })).ok()).toBe(true)
  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('hang')
  await composer.press('Enter')

  const stage = page.getByRole('main')
  // Words now go to this task, and it says which.
  await expect(stage.getByText('Following up')).toHaveCount(0) // working, not yet following up
  // On a laptop the closed task tab counts it, as the phone's tasks glyph does.
  if (info.project.name === 'desktop') await expect(page.getByRole('button', { name: /^Show tasks, \d+ active/ })).toBeVisible()
  await stage.getByRole('button', { name: 'Stop task' }).click()
  await expect(stage.getByRole('button', { name: 'Stop task' })).toHaveCount(0)
  await expect(stage.getByText('Following up')).toBeVisible()
  await expect(stage.getByText('hang', { exact: true }).first()).toBeVisible() // the task's title, in its chip
  await page.screenshot({ path: `test-results/${info.project.name}-following-up.png` })
  // The bottom row's new-task glyph leaves it for a new task, in one click on any screen.
  await page.getByTitle('New task (N)', { exact: true }).click()
  await expect(stage.getByText('New task', { exact: true })).toBeVisible()
  await expect(page.getByTitle('New task (N)', { exact: true })).toBeDisabled()

  // On a keyboard: / opens the type bar, Escape closes it, N leaves a task for a new one.
  await page.keyboard.press('/')
  await expect(page.getByPlaceholder('Start a new task')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Type instead' })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: 'Type instead' }).click()
  await page.getByPlaceholder('Start a new task').fill('second')
  await page.keyboard.press('Enter')
  await expect(stage.getByText('Following up')).toBeVisible()
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur()) // out of the type bar
  await page.keyboard.press('n')
  await expect(stage.getByText('New task', { exact: true })).toBeVisible()
})

test('an approval is asked on the device that started its task; another sees a quiet line', async ({ page, browser }, info) => {
  const name = `elsewhere-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  expect((await page.request.post('/api/projects', { data: { path: directory, name }, headers: { 'X-Sennet': '1' } })).ok()).toBe(true)
  const other = await browser.newContext({ ...info.project.use })
  const phone = await other.newPage()
  const chimes: string[] = []
  phone.on('request', (request) => {
    if (/approval-chime/.test(request.url())) chimes.push(request.url())
  })
  await phone.goto('/')
  await expect(phone.locator('header[data-connection="live"]')).toBeVisible()

  await page.goto('/')
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('run:npm test')
  await composer.press('Enter')
  await expect(page.getByRole('main').getByText(`${name} wants to run a command`)).toBeVisible()

  // The other device didn't start it: no card and no chime, just a line that opens the task.
  const line = phone.getByRole('main').getByRole('button', { name: /Another task needs you|other approvals need you/ })
  await expect(line).toBeVisible()
  await expect(phone.getByRole('main').getByText(`${name} wants to run a command`)).toHaveCount(0)
  expect(chimes).toEqual([])
  await line.click() // selects the task here, so it asks here too, and can be answered
  const stage = phone.getByRole('main')
  await expect(stage.getByText(`${name} wants to run a command`)).toBeVisible()
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(page.getByRole('main').getByText('Ran it.')).toBeVisible()
  await other.close()
})

test('add a Claude Code project, and it reports work it left running', async ({ page }, info) => {
  const name = `claude-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()

  await page.getByRole('button', { name: 'Settings' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('logged in (pro plan)')).toBeVisible()
  await dialog.getByLabel('Project directory').fill(directory)
  await dialog.getByLabel('Project name').fill(name)
  await dialog.getByLabel('Agent for the new project').selectOption({ label: 'Claude Code' })
  await dialog.getByRole('button', { name: 'Add' }).click()
  await expect(dialog.getByText(directory)).toBeVisible()
  await expect(dialog.getByText("Load this project's own Claude Code settings.", { exact: false }).last()).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()

  await chooseProject(page, name)
  // The next task's model, chosen from the dock before it starts.
  await page.getByRole('button', { name: /^Model: Default/ }).click()
  let menu = page.getByRole('dialog', { name: `Model for a new task in ${name}` })
  await menu.getByRole('radio', { name: 'Haiku' }).click()
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await page.getByRole('button', { name: 'Type instead' }).click()
  const composer = page.getByPlaceholder('Start a new task')
  await composer.fill('bg:make build')
  await composer.press('Enter')
  const stage = page.getByRole('main')
  await stage.getByRole('button', { name: 'Approve', exact: true }).click()
  await expect(stage.getByText('The background command make build finished.')).toBeVisible()

  // The task kept the choice; changing it applies from the task's next message.
  await page.getByRole('button', { name: 'Model: Haiku' }).click()
  menu = page.getByRole('dialog', { name: 'Model for this task' })
  await expect(menu.getByRole('radio', { name: 'Haiku' })).toHaveAttribute('aria-checked', 'true')
  await menu.getByRole('radio', { name: 'Sonnet' }).click()
  await menu.getByRole('radio', { name: 'high', exact: true }).click()
  await expect(menu.getByRole('radio', { name: 'high', exact: true })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('button', { name: 'Model: Sonnet, high effort' })).toBeVisible()
  await page.screenshot({ path: `test-results/${info.project.name}-model.png` })
  await page.keyboard.press('Escape')

  const timeline = await openTask(page, 'bg:make build')
  await expect(timeline.getByText('Started it in the background.')).toBeVisible()
  await expect(timeline.getByText('The agent picked this up again')).toBeVisible()
})

test('an open task catches up on what it missed while the phone was offline', async ({ page }, info) => {
  const name = `offline-${info.project.name}`
  const directory = path.join(home, name)
  mkdirSync(directory, { recursive: true })
  // The events socket can be cut off from the server without the page knowing, as a phone on a train is.
  let offline = false
  let events: WebSocketRoute | undefined
  await page.routeWebSocket('**/api/events', (ws) => {
    const server = ws.connectToServer()
    server.onMessage((message) => { if (!offline) ws.send(message) })
    events = ws
  })
  const headers = { 'X-Sennet': '1' }
  await page.request.post('/api/projects', { headers, data: { path: directory, name } })

  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await chooseProject(page, name)
  await page.getByRole('button', { name: 'Type instead' }).click()
  await page.getByPlaceholder('Start a new task').fill('hello')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main').getByText('You said: hello')).toBeVisible()
  const timeline = await openTask(page, 'hello')
  await expect(timeline.getByText('You said: hello')).toBeVisible()

  offline = true
  const state = await (await page.request.get('/api/state')).json()
  const task = state.tasks.find((t: { title: string }) => t.title.includes('hello'))
  await page.request.post(`/api/tasks/${task.id}/messages`, { headers, data: { text: 'while away' } })
  await expect.poll(async () => JSON.stringify(await (await page.request.get(`/api/tasks/${task.id}`)).json()))
    .toContain('You said: while away')
  await expect(timeline.getByText('You said: while away')).toHaveCount(0)

  offline = false
  await events!.close({ code: 1006 })
  await expect(timeline.getByText('You said: while away')).toBeVisible()
})

/** The selected task's timeline: picked from the opened rail into the drawer on desktop; on phones the
 * tasks sheet opens on the selected task. */
async function openTask(page: Page, title: string) {
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.getByRole('button', { name: 'Tasks', exact: true }).click()
    return page.getByRole('region', { name: 'Tasks' })
  }
  await page.getByRole('button', { name: 'Show tasks' }).click()
  // The row's own button, not the delete button beside it.
  await page.getByRole('complementary', { name: 'Tasks' }).getByRole('listitem').filter({ hasText: title }).first()
    .getByRole('button').first().click()
  return page.getByRole('complementary', { name: 'Task', exact: true })
}

test('replies are muted on this device until unmuted, across a reload', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('header[data-connection="live"]')).toBeVisible()
  await page.getByRole('button', { name: 'Mute replies' }).click()
  await expect(page.getByRole('button', { name: 'Unmute replies' })).toHaveAttribute('aria-pressed', 'true')
  await page.reload()
  await page.getByRole('button', { name: 'Unmute replies' }).click()
  await expect(page.getByRole('button', { name: 'Mute replies' })).toHaveAttribute('aria-pressed', 'false')
})
