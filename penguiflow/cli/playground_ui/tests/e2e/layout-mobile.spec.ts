import { test, expect } from '@playwright/test';

test.describe('Mobile Layout', () => {
  test.beforeEach(async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');
  });

  test('renders mobile header instead of desktop layout', async ({ page }) => {
    // Mobile header should be visible
    await expect(page.locator('.mobile-header')).toBeVisible();

    // Desktop page layout should not be visible
    await expect(page.locator('.page')).not.toBeVisible();
  });

  test('displays agent name in mobile header', async ({ page }) => {
    const agentName = page.locator('.agent-name');
    await expect(agentName).toBeVisible();
  });

  test('renders hamburger menu button', async ({ page }) => {
    const menuBtn = page.getByLabel('Toggle menu');
    await expect(menuBtn).toBeVisible();
  });

  test('hamburger menu opens drawer', async ({ page }) => {
    const menuBtn = page.getByLabel('Toggle menu');
    await menuBtn.click();

    // Drawer should appear
    await expect(page.locator('.drawer')).toBeVisible();

    // Backdrop should appear
    await expect(page.getByLabel('Close menu')).toBeVisible();
  });

  test('drawer shows Info, Spec, Actions tabs', async ({ page }) => {
    const menuBtn = page.getByLabel('Toggle menu');
    await menuBtn.click();

    await expect(page.getByRole('button', { name: 'Info' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Spec' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Actions' })).toBeVisible();
  });

  test('drawer tabs switch content', async ({ page }) => {
    const menuBtn = page.getByLabel('Toggle menu');
    await menuBtn.click();

    // Info tab active by default - should show project info
    const infoTab = page.getByRole('button', { name: 'Info' });
    await expect(infoTab).toHaveClass(/active/);

    // Click Spec tab
    const specTab = page.getByRole('button', { name: 'Spec' });
    await specTab.click();
    await expect(specTab).toHaveClass(/active/);
    await expect(infoTab).not.toHaveClass(/active/);
  });

  test('backdrop closes drawer', async ({ page }) => {
    const menuBtn = page.getByLabel('Toggle menu');
    await menuBtn.click();

    await expect(page.locator('.drawer')).toBeVisible();

    // Click backdrop
    const backdrop = page.getByLabel('Close menu');
    await backdrop.click();

    await expect(page.locator('.drawer')).not.toBeVisible();
  });

  test('renders mobile bottom panel', async ({ page }) => {
    await expect(page.locator('.bottom-panel')).toBeVisible();
  });

  test('bottom panel shows toggle button', async ({ page }) => {
    const toggleBtn = page.getByLabel('Show details panel');
    await expect(toggleBtn).toBeVisible();
    await expect(page.getByText('Show Details')).toBeVisible();
  });

  test('bottom panel expands on toggle click', async ({ page }) => {
    const toggleBtn = page.getByLabel('Show details panel');
    await toggleBtn.click();

    // Panel should have open class
    const panel = page.locator('.bottom-panel');
    await expect(panel).toHaveClass(/open/);

    // Tabs should be visible
    await expect(page.getByRole('button', { name: 'Steps' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Events' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Config' })).toBeVisible();
  });

  test('bottom panel label changes when open', async ({ page }) => {
    const toggleBtn = page.getByLabel('Show details panel');
    await toggleBtn.click();

    await expect(page.getByText('Hide Details')).toBeVisible();
  });

  test('bottom panel tabs switch content', async ({ page }) => {
    const toggleBtn = page.getByLabel('Show details panel');
    await toggleBtn.click();

    // Steps tab active by default
    const stepsTab = page.getByRole('button', { name: 'Steps' });
    await expect(stepsTab).toHaveClass(/active/);

    // Click Events tab
    const eventsTab = page.getByRole('button', { name: 'Events' });
    await eventsTab.click();
    await expect(eventsTab).toHaveClass(/active/);
    await expect(stepsTab).not.toHaveClass(/active/);
  });

  test('bottom panel collapses on second toggle click', async ({ page }) => {
    const toggleBtn = page.getByLabel('Show details panel');
    await toggleBtn.click();

    await expect(page.locator('.bottom-panel')).toHaveClass(/open/);

    // Click again to close
    const closeBtn = page.getByLabel('Hide details panel');
    await closeBtn.click();

    await expect(page.locator('.bottom-panel')).not.toHaveClass(/open/);
  });

  test('chat interface is accessible on mobile', async ({ page }) => {
    // Chat card should be visible in mobile main area
    await expect(page.locator('.chat-card')).toBeVisible();

    // Chat input should be accessible
    await expect(page.getByPlaceholder('Ask your agent something...')).toBeVisible();
  });

  test('chat remains usable when bottom panel is open', async ({ page }) => {
    // Open bottom panel
    const toggleBtn = page.getByLabel('Show details panel');
    await toggleBtn.click();

    // Chat should still be visible (though smaller)
    await expect(page.locator('.chat-card')).toBeVisible();
    await expect(page.getByPlaceholder('Ask your agent something...')).toBeVisible();
  });
});

test.describe('Responsive Breakpoint', () => {
  test('switches to mobile layout at 1200px', async ({ page }) => {
    // Desktop at 1201px
    await page.setViewportSize({ width: 1201, height: 800 });
    await page.goto('/');

    await expect(page.locator('.page')).toBeVisible();
    await expect(page.locator('.mobile-header')).not.toBeVisible();

    // Mobile at 1200px
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.waitForTimeout(100); // Allow for resize handler

    await expect(page.locator('.mobile-header')).toBeVisible();
    await expect(page.locator('.page')).not.toBeVisible();
  });

  test('switches to desktop layout above 1200px', async ({ page }) => {
    // Start mobile
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');

    await expect(page.locator('.mobile-header')).toBeVisible();

    // Resize to desktop
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(100); // Allow for resize handler

    await expect(page.locator('.page')).toBeVisible();
    await expect(page.locator('.mobile-header')).not.toBeVisible();
  });
});
