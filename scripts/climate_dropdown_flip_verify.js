/**
 * Verify provider/model dropdowns flip up and escape AI panel overflow.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8095";
const outDir = path.join("tmp", "climate-ui", "dropdown-flip");
fs.mkdirSync(outDir, { recursive: true });

const VIEWPORTS = [
  { w: 1366, h: 768 },
  { w: 1600, h: 900 },
  { w: 1920, h: 1080 },
];

async function measureOpen(page, which) {
  const triggerSel =
    which === "provider"
      ? '#climate-provider-panel'
      : '#climate-model-panel';
  // Click custom trigger near the native select
  const trigger = page.locator(`${triggerSel}`).locator("xpath=ancestor::div[contains(@class,'climate-dd')][1]//button[contains(@class,'climate-dd-trigger')]");
  await trigger.click();
  await page.waitForTimeout(120);
  return page.evaluate((kind) => {
    const menu = document.querySelector("body > .climate-dd-menu.is-portal:not([hidden])");
    const ai = document.querySelector("#climate-ai");
    const status = document.querySelector(".climate-statusbar");
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), bottom: Math.round(r.bottom) };
    };
    const m = box(menu);
    const a = box(ai);
    const s = box(status);
    const vh = window.innerHeight;
    let clippedByAi = false;
    if (menu && ai) {
      const style = getComputedStyle(ai);
      // If menu is a body portal, AI overflow cannot clip it.
      clippedByAi = menu.parentElement !== document.body;
    }
    const overlapsStatus = !!(m && s && m.bottom > s.top + 2 && m.top < s.bottom);
    const fullyInViewport = !!(m && m.top >= 0 && m.bottom <= vh);
    return {
      kind,
      portaled: !!(menu && menu.parentElement === document.body),
      isUp: !!(menu && menu.classList.contains("is-up")),
      maxHeight: menu ? getComputedStyle(menu).maxHeight : null,
      zIndex: menu ? getComputedStyle(menu).zIndex : null,
      menu: m,
      ai: a,
      status: s,
      clippedByAi,
      overlapsStatus,
      fullyInViewport,
      optionCount: menu ? menu.querySelectorAll(".climate-dd-option").length : 0,
      shellX: Math.round((document.querySelector(".climate-shell") || { getBoundingClientRect: () => ({ x: 0 }) }).getBoundingClientRect().x),
    };
  }, which);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { base: BASE, errors: [], viewports: {} };

  for (const vp of VIEWPORTS) {
    const page = await browser.newPage({ viewport: { width: vp.w, height: vp.h } });
    const errs = [];
    await page.goto(`${BASE}/work/climate`, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForSelector("#climate-workbench", { timeout: 20000 });
    await page.waitForSelector("#climate-provider-panel", { timeout: 15000 });
    await page.waitForTimeout(500);

    // Ensure AI panel open at compact width (bottom controls visible)
    await page.evaluate(() => {
      const wb = document.querySelector("#climate-workbench");
      wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
      wb.style.setProperty("--right", "360px");
    });
    await page.waitForTimeout(100);

    const before = await page.evaluate(() => {
      const r = document.querySelector(".climate-shell").getBoundingClientRect();
      return { x: Math.round(r.x), w: Math.round(r.width) };
    });

    let provider = await measureOpen(page, "provider");
    if (!provider.portaled) errs.push("provider: menu not portaled to body");
    if (!provider.isUp) errs.push("provider: expected upward open near panel bottom");
    if (provider.clippedByAi) errs.push("provider: still clipped by AI ancestor");
    if (!provider.fullyInViewport) errs.push(`provider: not fully in viewport ${JSON.stringify(provider.menu)}`);
    if (provider.overlapsStatus) errs.push("provider: overlaps status bar");
    if (parseInt(provider.zIndex, 10) < 1000) errs.push(`provider: z-index too low ${provider.zIndex}`);
    if (provider.maxHeight && parseInt(provider.maxHeight, 10) > 240) errs.push(`provider: max-height ${provider.maxHeight}`);
    await page.screenshot({ path: path.join(outDir, `${vp.w}-provider.png`) });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(80);

    let model = await measureOpen(page, "model");
    if (!model.portaled) errs.push("model: menu not portaled to body");
    if (!model.isUp) errs.push("model: expected upward open near panel bottom");
    if (!model.fullyInViewport) errs.push(`model: not fully in viewport ${JSON.stringify(model.menu)}`);
    if (model.overlapsStatus) errs.push("model: overlaps status bar");
    await page.screenshot({ path: path.join(outDir, `${vp.w}-model.png`) });
    await page.keyboard.press("Escape");

    // Maximized AI still flips correctly
    await page.click("#climate-maximize-ai");
    await page.waitForTimeout(200);
    const maxProvider = await measureOpen(page, "provider");
    if (!maxProvider.portaled || !maxProvider.fullyInViewport) {
      errs.push("maximized provider: portal/viewport failed");
    }
    if (!maxProvider.isUp && maxProvider.menu && maxProvider.ai) {
      // With wider panel, may still open up because control is near bottom
      const spaceBelow = vp.h - maxProvider.menu.top;
      // tolerate down if clearly enough space and fully visible
      if (!maxProvider.fullyInViewport) errs.push("maximized provider not fully visible");
    }
    await page.keyboard.press("Escape");

    const after = await page.evaluate(() => {
      const r = document.querySelector(".climate-shell").getBoundingClientRect();
      return { x: Math.round(r.x), w: Math.round(r.width) };
    });
    if (before.x !== after.x) errs.push(`shell shifted ${before.x} -> ${after.x}`);

    report.viewports[`${vp.w}x${vp.h}`] = { errors: errs, provider, model, maxProvider };
    report.errors.push(...errs.map((e) => `${vp.w}x${vp.h}: ${e}`));
    await page.close();
  }

  report.ok = report.errors.length === 0;
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, errors: report.errors }, null, 2));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
