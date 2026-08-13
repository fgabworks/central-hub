/**
 * CLIMATE UI inspection — before/after Playwright capture.
 * Usage: node scripts/climate_ui_inspect.js [label]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const label = process.argv[2] || "before";
const outDir = path.join("tmp", "climate-ui", label);
fs.mkdirSync(outDir, { recursive: true });

async function measure(page) {
  return page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        w: Math.round(r.width),
        h: Math.round(r.height),
        t: Math.round(r.top),
        l: Math.round(r.left),
        text: (el.innerText || "").slice(0, 120).replace(/\s+/g, " "),
      };
    };
    return {
      url: location.href,
      title: document.title,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      sidebarBrand: box(q(".sidebar-brand")),
      brandMark: box(q(".brand-mark")),
      topbarBrand: box(q(".topbar-brand")),
      climateBrand: box(q(".climate-brand")),
      climateWelcomeLogo: box(q(".climate-welcome-logo")),
      explorer: box(q(".climate-explorer")),
      editor: box(q(".climate-editor-host, .climate-center")),
      ai: box(q(".climate-ai")),
      bottom: box(q(".climate-bottom")),
      bottomClosed: !!(q(".climate-center") && q(".climate-center").classList.contains("is-bottom-closed")),
      aiMaximized: !!(q(".climate-workbench") && q(".climate-workbench").classList.contains("is-ai-maximized")),
      providerCards: qa("#climate-provider-cards > *").length,
      providerSelect: !!q("#climate-provider"),
      modelSelect: !!q("#climate-model, #climate-model-panel"),
      workspaceSwitcher: qa(".workspace-btn").map((el) => el.textContent.trim()),
      firstNav: ((qa(".sidebar-nav .nav-link-label")[0] || {}).textContent || "").trim(),
      wcHostVisible: !!(q(".wc-host") && !q(".wc-host").hidden && q(".wc-host").getBoundingClientRect().height > 0),
      climateBrandCount: qa(".sidebar-brand, .topbar-brand, .climate-brand, .climate-welcome-logo").filter((el) => el.getBoundingClientRect().height > 0).length,
      bodyClasses: document.body.className,
      appShellClasses: (q(".app-shell") || {}).className || "",
    };
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { label, base: BASE, pages: {} };

  await page.addInitScript(() => {
    try {
      Object.keys(localStorage).forEach((k) => {
        if (k.startsWith("climate:")) localStorage.removeItem(k);
      });
    } catch (_) {}
  });

  for (const route of ["/", "/work", "/work/climate", "/personal"]) {
    const name = route.replace(/\//g, "_").replace(/^_/, "") || "root";
    await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(500);
    report.pages[route] = await measure(page);
    await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: false });
  }

  await page.goto(BASE + "/work/climate", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(500);
  await page.locator("#climate-bottom-close").click({ force: true });
  await page.waitForTimeout(300);
  report.pages.bottom_collapsed = await measure(page);
  await page.screenshot({ path: path.join(outDir, "climate_bottom_collapsed.png") });

  await page.locator("#climate-toggle-bottom").click({ force: true });
  await page.waitForTimeout(200);
  await page.locator("#climate-maximize-ai").click({ force: true });
  await page.waitForTimeout(300);
  report.pages.ai_maximized = await measure(page);
  await page.screenshot({ path: path.join(outDir, "climate_ai_maximized.png") });

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
