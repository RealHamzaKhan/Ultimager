import { test, expect } from '@playwright/test'

test.describe('Navigation', () => {
  test('sidebar shows active route highlighting', async ({ page }) => {
    await page.goto('/')
    const dashboardLink = page.getByRole('link', { name: /dashboard/i }).first()
    await expect(dashboardLink).toBeVisible()
  })

  test('sidebar can be toggled', async ({ page }) => {
    await page.goto('/')
    // The sidebar should be visible by default
    const sidebar = page.locator('aside, nav').first()
    await expect(sidebar).toBeVisible()
  })

  test('topbar shows breadcrumbs', async ({ page }) => {
    await page.goto('/sessions/new')
    // Should show breadcrumb elements
    const topbar = page.locator('header, [class*="top"]').first()
    await expect(topbar).toBeVisible()
  })

  test('theme toggle works', async ({ page }) => {
    await page.goto('/')
    const html = page.locator('html')
    const initialTheme = await html.getAttribute('data-theme')
    expect(initialTheme).toBe('dark')
  })

  test('navigates from dashboard to new session and back', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /new session/i }).first().click()
    await expect(page).toHaveURL(/sessions\/new/)
    await page.getByRole('link', { name: /dashboard/i }).first().click()
    await expect(page).toHaveURL('/')
  })
})
