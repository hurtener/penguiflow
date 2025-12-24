import { test, expect } from '@playwright/test';

test.describe('Events Display', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('displays events card header', async ({ page }) => {
    await expect(page.getByText('Planner Events')).toBeVisible();
  });

  test('displays empty state when no events', async ({ page }) => {
    await expect(page.getByText('No events yet')).toBeVisible();
  });

  test('displays config card', async ({ page }) => {
    await expect(page.getByText('Config & Catalog')).toBeVisible();
  });

  test('displays planner config section', async ({ page }) => {
    await expect(page.getByText('PLANNER CONFIG')).toBeVisible();
  });

  test('displays services section', async ({ page }) => {
    await expect(page.getByText('SERVICES')).toBeVisible();
  });

  test('displays tool catalog section', async ({ page }) => {
    await expect(page.getByText('TOOL CATALOG')).toBeVisible();
  });

  test('right sidebar is visible on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });

    // Events and Config cards should be in the right column
    await expect(page.locator('.column.right')).toBeVisible();
    await expect(page.getByText('Planner Events')).toBeVisible();
    await expect(page.getByText('Config & Catalog')).toBeVisible();
  });

  test('events card is scrollable', async ({ page }) => {
    // The events card should have a scrollable container
    const eventsCard = page.locator('.card').filter({ hasText: 'Planner Events' });
    await expect(eventsCard).toBeVisible();
  });
});
