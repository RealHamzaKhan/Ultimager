import { test, expect } from '@playwright/test'

test.describe('Dashboard', () => {
  test('loads the dashboard page', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/GradeForge/)
  })

  test('shows the sidebar with navigation', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('GradeForge')).toBeVisible()
    // Sidebar should have navigation links
    const nav = page.locator('[aria-label="Main navigation"], nav').first()
    await expect(nav).toBeVisible()
  })

  test('shows hero stats section', async ({ page }) => {
    await page.goto('/')
    // Stats cards should be present even if loading
    await expect(page.getByText('Total Sessions')).toBeVisible()
  })

  test('shows session grid', async ({ page }) => {
    await page.goto('/')
    // Either sessions or empty state
    const content = page.locator('main')
    await expect(content).toBeVisible()
  })

  test('navigates to new session page', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /new session/i }).first().click()
    await expect(page).toHaveURL(/sessions\/new/)
  })

  test('command palette opens with Cmd+K', async ({ page }) => {
    await page.goto('/')
    await page.keyboard.press('Meta+k')
    await expect(page.getByTestId('command-palette')).toBeVisible()
  })

  test('command palette closes with Escape', async ({ page }) => {
    await page.goto('/')
    await page.keyboard.press('Meta+k')
    await expect(page.getByTestId('command-palette')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('command-palette')).not.toBeVisible()
  })
})
