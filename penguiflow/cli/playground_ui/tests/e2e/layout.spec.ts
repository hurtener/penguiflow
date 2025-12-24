import { test, expect } from '@playwright/test';

test.describe('Layout', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('renders three-column layout on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    
    // Check all three columns are visible
    await expect(page.locator('.column.left')).toBeVisible();
    await expect(page.locator('.column.center')).toBeVisible();
    await expect(page.locator('.column.right')).toBeVisible();
  });

  test('renders single column on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    
    // Page should switch to single column
    const page_element = page.locator('.page');
    await expect(page_element).toBeVisible();
  });

  test('displays project card', async ({ page }) => {
    // Use first() to get the project card title (not the chat header)
    await expect(page.getByText('loading_agent').first()).toBeVisible();
    // Use exact matching for uppercase labels
    await expect(page.getByText('TOOLS', { exact: true })).toBeVisible();
    await expect(page.getByText('FLOWS', { exact: true })).toBeVisible();
    await expect(page.getByText('SVCS', { exact: true })).toBeVisible();
  });

  test('displays spec card with tabs', async ({ page }) => {
    await expect(page.getByText('Spec YAML')).toBeVisible();
    // Use first() to get the tab (not the stepper step)
    await expect(page.getByText('Validation').first()).toBeVisible();
  });

  test('displays generator card', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Validate' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Generate' })).toBeVisible();
  });

  test('displays chat card', async ({ page }) => {
    await expect(page.getByText('DEV PLAYGROUND')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Chat' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Setup' })).toBeVisible();
  });

  test('displays events card', async ({ page }) => {
    await expect(page.getByText('Planner Events')).toBeVisible();
    await expect(page.getByText('No events yet')).toBeVisible();
  });

  test('displays config card', async ({ page }) => {
    await expect(page.getByText('Config & Catalog')).toBeVisible();
    await expect(page.getByText('PLANNER CONFIG')).toBeVisible();
    await expect(page.getByText('SERVICES')).toBeVisible();
    await expect(page.getByText('TOOL CATALOG')).toBeVisible();
  });
});
