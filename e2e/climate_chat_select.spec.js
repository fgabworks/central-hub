import { test, expect } from "@playwright/test";

function parseRgb(str) {
  const m = String(str || "").match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!m) return null;
  return { r: +m[1], g: +m[2], b: +m[3] };
}

function luma(rgb) {
  if (!rgb) return 255;
  return 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
}

test.describe("CLIMATE Chat provider/model selectors", () => {
  test("custom dropdown is dark-themed and placeholder is not a valid model", async ({ page }) => {
    test.setTimeout(30000);
    const response = await page.goto("/work/chat", { waitUntil: "domcontentloaded" }).catch(() => null);
    if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");

    await page.waitForSelector(".ax-chat-controls .climate-dd-trigger", { timeout: 15000 });
    await page.waitForFunction(() => {
      const sel = document.querySelector("#ax-model");
      return !!(sel && Array.from(sel.options || []).some((opt) => opt.value && !opt.disabled));
    }, { timeout: 15000 });
    await expect(page.locator("#ax-provider")).toBeAttached();
    await expect(page.locator("#ax-model")).toBeAttached();
    await expect(page.locator('.climate-dd[data-for="ax-model"]')).toBeVisible();

    const placeholderDisabled = await page.locator('#ax-model option[value=""]').evaluate((el) => el.disabled);
    expect(placeholderDisabled).toBe(true);

    await page.locator('.climate-dd[data-for="ax-model"] .climate-dd-trigger').click();
    const menu = page.locator("body > .climate-dd-menu.is-portal");
    await expect(menu).toBeVisible();

    const stats = await menu.evaluate((el) => {
      const cs = getComputedStyle(el);
      const opt = el.querySelector(".climate-dd-option:not(:disabled)");
      const ocs = opt ? getComputedStyle(opt) : null;
      const placeholder = el.querySelector('.climate-dd-option[data-value=""]');
      return {
        menuBg: cs.backgroundColor,
        menuColor: cs.color,
        optionColor: ocs ? ocs.color : null,
        optionBg: ocs ? ocs.backgroundColor : null,
        placeholderDisabled: placeholder ? placeholder.disabled : null,
      };
    });
    expect(luma(parseRgb(stats.menuBg))).toBeLessThan(90);
    expect(luma(parseRgb(stats.optionColor || stats.menuColor))).toBeGreaterThan(160);
    expect(stats.placeholderDisabled).toBe(true);

    await page.screenshot({ path: "tmp/climate-ui/dropdown-chat/chat_model_open.png" });
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
  });
});
