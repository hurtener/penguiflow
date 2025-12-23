import { test, expect } from '@playwright/test';

test.describe('Trajectory', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('shows empty trajectory state', async ({ page }) => {
    await expect(page.getByText('Execution Trajectory')).toBeVisible();
    await expect(page.getByText('No trajectory yet')).toBeVisible();
    await expect(page.getByText('Send a prompt to see steps.')).toBeVisible();
  });
});
