import { test, expect } from '@playwright/test'

test.describe('Accessibility', () => {
  test('dashboard has proper page title', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/GradeForge/)
  })

  test('inputs have associated labels', async ({ page }) => {
    await page.goto('/sessions/new')
    // The form should have labeled inputs
    const titleInput = page.getByLabel(/title/i)
    await expect(titleInput).toBeVisible()
  })

  test('interactive elements are keyboard accessible', async ({ page }) => {
    await page.goto('/')
    // Tab through navigation items
    await page.keyboard.press('Tab')
    const focused = page.locator(':focus')
    await expect(focused).toBeVisible()
  })

  test('color scheme is dark by default', async ({ page }) => {
    await page.goto('/')
    const theme = await page.locator('html').getAttribute('data-theme')
    expect(theme).toBe('dark')
  })

  test('page has proper heading hierarchy', async ({ page }) => {
    await page.goto('/sessions/new')
    const h1 = page.locator('h1')
    await expect(h1.first()).toBeVisible()
  })
})
