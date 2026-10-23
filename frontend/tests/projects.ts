import { expect, type Page } from '@playwright/test'

/** Choose where new tasks go, from the header's project menu. */
export async function chooseProject(page: Page, name: string) {
  await page.getByRole('button', { name: /^Project: / }).click()
  await page.getByRole('radio', { name, exact: true }).click()
  await expect(page.getByRole('button', { name: `Project: ${name}`, exact: true })).toBeVisible()
}
