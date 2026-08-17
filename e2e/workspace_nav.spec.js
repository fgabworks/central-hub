import { test, expect } from "@playwright/test";

function sidebar(page) {
  return page.locator(".sidebar-nav");
}

test.describe("VANTA / ARCTIC navigation scope", () => {
  test("VANTA shows Code Workspace and Repositories; ARCTIC hides them", async ({ page }) => {
    test.setTimeout(30000);
    const response = await page.goto("/work", { waitUntil: "domcontentloaded" }).catch(() => null);
    if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");

    await expect(sidebar(page).getByText("Dashboard", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("CLIMATE Chat", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Settings", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Code Workspace", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Repositories", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Workspace Assistant", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("AiriX", { exact: true })).toHaveCount(0);

    const sideText = await sidebar(page).innerText();
    expect(sideText.indexOf("Settings")).toBeLessThan(sideText.indexOf("Code Workspace"));
    expect(sideText.indexOf("Code Workspace")).toBeLessThan(sideText.indexOf("Repositories"));

    await page.locator(".workspace-btn", { hasText: "ARCTIC" }).click();
    await page.waitForURL(/\/personal(?:\/|$|\?)/);

    await expect(sidebar(page).getByText("Code Workspace", { exact: true })).toHaveCount(0);
    await expect(sidebar(page).getByText("Repositories", { exact: true })).toHaveCount(0);
    await expect(sidebar(page).getByText("SQL Workspace", { exact: true })).toHaveCount(0);
    await expect(sidebar(page).getByText("CLIMATE Chat", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Dashboard", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Tasks", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Notebook", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Settings", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Personal Files", { exact: true })).toBeVisible();

    await page.locator(".workspace-btn", { hasText: "VANTA" }).click();
    await page.waitForURL(/\/work/);
    await expect(sidebar(page).getByText("Code Workspace", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Repositories", { exact: true })).toBeVisible();

    const climate = await page.goto("/work/climate", { waitUntil: "domcontentloaded" });
    expect(climate && climate.ok()).toBeTruthy();
    await expect(page.locator("#climate-monaco")).toBeAttached();

    const repos = await page.goto("/repositories", { waitUntil: "domcontentloaded" });
    expect(repos && repos.ok()).toBeTruthy();
    await expect(sidebar(page).getByText("Repositories", { exact: true })).toBeVisible();
    await expect(sidebar(page).getByText("Code Workspace", { exact: true })).toBeVisible();

    const hidden = await page.goto("/personal/climate", { waitUntil: "domcontentloaded" });
    expect(hidden && hidden.ok()).toBeTruthy();
    await expect(sidebar(page).getByText("Code Workspace", { exact: true })).toHaveCount(0);
    await expect(sidebar(page).getByText("Repositories", { exact: true })).toHaveCount(0);
  });
});
