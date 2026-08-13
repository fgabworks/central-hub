/**
 * Inspect current CLIMATE AI/explorer/bottom DOM before fixes.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "ide-fix-before");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/work/climate`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("#climate-workbench");
  await page.waitForTimeout(600);

  const data = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const wb = q("#climate-workbench");
    const ai = q("#climate-ai");
    const right = parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10);
    return {
      url: location.href,
      workbenchClasses: wb.className,
      rightVar: right,
      ai: box(ai),
      aiRail: box(q("#climate-ai-expand")),
      aiFull: box(q(".climate-ai-full")),
      aiFullDisplay: q(".climate-ai-full") ? getComputedStyle(q(".climate-ai-full")).display : null,
      aiRailDisplay: q("#climate-ai-expand") ? getComputedStyle(q("#climate-ai-expand")).display : null,
      explorerSections: qa(".climate-explorer-sections button").map((b) => b.textContent.trim()),
      bottomTabs: qa(".climate-bottom-tabs [data-panel]").map((b) => ({
        panel: b.dataset.panel,
        text: b.textContent.replace(/\s+/g, " ").trim(),
        active: b.classList.contains("is-active"),
      })),
      bottomBodySample: ((q("#climate-bottom-body") || {}).textContent || "").replace(/\s+/g, " ").trim().slice(0, 240),
      shell: box(q(".climate-shell") || q("#climate-shell")),
      sidebar: box(q("#hub-sidebar")),
      localStorageKeys: Object.keys(localStorage).filter((k) => /climate/i.test(k)),
      prefsSample: (() => {
        try {
          const key = Object.keys(localStorage).find((k) => k.includes("climate") && k.includes("prefs") || k.includes("layout"));
          // try common climate storage keys
          const candidates = Object.keys(localStorage).filter((k) => k.startsWith("climate"));
          return candidates.map((k) => ({ k, v: localStorage.getItem(k).slice(0, 200) }));
        } catch (_) { return []; }
      })(),
    };
  });

  // Try forcing a narrow --right like the screenshot
  const narrow = await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-collapsed", "is-ai-closed", "is-ai-maximized");
    wb.style.setProperty("--right", "48px");
    const ai = document.querySelector("#climate-ai");
    const full = document.querySelector(".climate-ai-full");
    const rail = document.querySelector("#climate-ai-expand");
    const r = ai.getBoundingClientRect();
    return {
      rightVar: parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10),
      aiW: Math.round(r.width),
      fullDisplay: full ? getComputedStyle(full).display : null,
      railDisplay: rail ? getComputedStyle(rail).display : null,
      classes: wb.className,
      distortedText: ((ai.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80)),
    };
  });

  await page.screenshot({ path: path.join(outDir, "full.png"), fullPage: false });
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "ai.png") }).catch(() => {});
  fs.writeFileSync(path.join(outDir, "inspect.json"), JSON.stringify({ data, narrow }, null, 2));
  console.log(JSON.stringify({ data, narrow }, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
