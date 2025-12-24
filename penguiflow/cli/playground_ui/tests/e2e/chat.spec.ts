import { test, expect } from '@playwright/test';

test.describe('Chat', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('shows empty state initially', async ({ page }) => {
    await expect(page.getByText('Ready to test agent behavior.')).toBeVisible();
    await expect(page.getByText('Type a message below to start a run.')).toBeVisible();
  });

  test('has chat input with placeholder', async ({ page }) => {
    const input = page.getByPlaceholder('Ask your agent something...');
    await expect(input).toBeVisible();
  });

  test('send button is disabled when input is empty', async ({ page }) => {
    const sendButton = page.getByRole('button', { name: '>' });
    await expect(sendButton).toBeDisabled();
  });

  test('send button enables when input has text', async ({ page }) => {
    const input = page.getByPlaceholder('Ask your agent something...');
    await input.fill('Test message');
    
    const sendButton = page.getByRole('button', { name: '>' });
    await expect(sendButton).toBeEnabled();
  });

  test('can switch between Chat and Setup tabs', async ({ page }) => {
    // Initially on Chat tab
    await expect(page.getByText('Ready to test agent behavior.')).toBeVisible();
    
    // Switch to Setup tab
    await page.getByRole('button', { name: 'Setup' }).click();
    
    // Should see setup fields
    await expect(page.getByText('Session ID')).toBeVisible();
    await expect(page.getByText('Tenant ID')).toBeVisible();
    await expect(page.getByText('User ID')).toBeVisible();
    
    // Switch back to Chat
    await page.getByRole('button', { name: 'Chat' }).click();
    await expect(page.getByText('Ready to test agent behavior.')).toBeVisible();
  });
});
