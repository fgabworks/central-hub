import { test, expect } from '@playwright/test';

test('Central Hub opens', async ({ page }) => {
  await page.goto('/');

  await expect(page).toHaveTitle(/Central Hub/i);
});