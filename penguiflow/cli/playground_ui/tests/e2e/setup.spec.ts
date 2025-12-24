import { test, expect } from '@playwright/test';

test.describe('Setup', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Navigate to Setup tab
    await page.getByRole('button', { name: 'Setup' }).click();
  });

  test('displays all setup fields', async ({ page }) => {
    await expect(page.getByText('Session ID')).toBeVisible();
    await expect(page.getByText('Tenant ID')).toBeVisible();
    await expect(page.getByText('User ID')).toBeVisible();
    await expect(page.getByText('Tool Context (JSON)')).toBeVisible();
    await expect(page.getByText('LLM Context (JSON)')).toBeVisible();
  });

  test('has New button for session', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'New' })).toBeVisible();
  });

  test('can edit tenant id', async ({ page }) => {
    // Find the Tenant ID input (second input field)
    const inputs = page.locator('.setup-input');
    const tenantInput = inputs.nth(1); // Session ID is first, Tenant ID is second
    
    await tenantInput.clear();
    await tenantInput.fill('my-custom-tenant');
    
    await expect(tenantInput).toHaveValue('my-custom-tenant');
  });

  test('can edit user id', async ({ page }) => {
    const inputs = page.locator('.setup-input');
    const userInput = inputs.nth(2); // User ID is third
    
    await userInput.clear();
    await userInput.fill('test-user-123');
    
    await expect(userInput).toHaveValue('test-user-123');
  });

  test('displays hint text', async ({ page }) => {
    await expect(page.getByText('Used to scope short-term memory and trajectory lookups.')).toBeVisible();
    await expect(page.getByText('Merged with tenant/user and injected as runtime tool_context.')).toBeVisible();
  });

  test('New button generates new session', async ({ page }) => {
    const inputs = page.locator('.setup-input');
    const sessionInput = inputs.first();
    
    const originalValue = await sessionInput.inputValue();
    
    await page.getByRole('button', { name: 'New' }).click();
    
    const newValue = await sessionInput.inputValue();
    expect(newValue).not.toBe(originalValue);
  });
});
