import { test, expect } from '@playwright/test'

test.describe('Create Session', () => {
  test('loads the create session page', async ({ page }) => {
    await page.goto('/sessions/new')
    await expect(page.getByText('Create New Session')).toBeVisible()
  })

  test('shows form fields', async ({ page }) => {
    await page.goto('/sessions/new')
    // Title and rubric inputs
    await expect(page.getByLabel(/title/i)).toBeVisible()
    await expect(page.locator('textarea').first()).toBeVisible()
  })

  test('shows max score presets', async ({ page }) => {
    await page.goto('/sessions/new')
    const main = page.locator('main')
    await expect(main.getByText('100')).toBeVisible()
  })

  test('validates required fields on submit', async ({ page }) => {
    await page.goto('/sessions/new')
    const submitBtn = page.getByRole('button', { name: /create/i })
    await submitBtn.click()
    // Should show validation errors
    const errorText = page.getByText(/required/i)
    await expect(errorText.first()).toBeVisible()
  })

  test('can fill in form fields', async ({ page }) => {
    await page.goto('/sessions/new')
    const titleInput = page.getByLabel(/title/i)
    await titleInput.fill('Test Session')
    await expect(titleInput).toHaveValue('Test Session')
  })
})
