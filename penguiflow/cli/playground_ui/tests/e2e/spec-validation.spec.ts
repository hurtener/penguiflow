import { test, expect } from '@playwright/test';

test.describe('Spec Validation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('displays spec card', async ({ page }) => {
    await expect(page.getByText('Spec YAML')).toBeVisible();
  });

  test('displays validation tab', async ({ page }) => {
    // Use first() since "Validation" might appear in multiple places
    await expect(page.getByText('Validation').first()).toBeVisible();
  });

  test('displays validate button', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Validate' })).toBeVisible();
  });

  test('displays generate button', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Generate' })).toBeVisible();
  });

  test('can click validate button', async ({ page }) => {
    const validateBtn = page.getByRole('button', { name: 'Validate' });
    await expect(validateBtn).toBeEnabled();
  });

  test('can switch between spec tabs', async ({ page }) => {
    // Click on Validation tab
    const validationTab = page.getByText('Validation').first();
    await validationTab.click();

    // Click back to Spec YAML tab
    const specTab = page.getByText('Spec YAML');
    await specTab.click();
    await expect(specTab).toBeVisible();
  });

  test('spec editor is visible', async ({ page }) => {
    // The spec card should contain some editor or textarea area
    const specCard = page.locator('.card').filter({ hasText: 'Spec YAML' });
    await expect(specCard).toBeVisible();
  });
});
