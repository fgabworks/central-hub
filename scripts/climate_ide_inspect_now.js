/**
 * Inspect current CLIMATE Code Workspace state before targeted fixes.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "ide-inspect-now");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(`${BASE}/work/climate`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("#climate-workbench");
  await page.waitForTimeout(500);

  const data = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const cs = (el, prop) => (el ? getComputedStyle(el).getPropertyValue(prop).trim() : "");
    const wb = q("#climate-workbench");
    const file = q(".climate-file-row .climate-file-name") || q(".climate-file-row");
    const folder = q(".climate-tree summary") || q(".climate-tree details > summary");
    const active = q(".climate-file-row.is-active .climate-file-name") || q(".climate-file-row.is-active");
    return {
      url: location.href,
      cssHref: (q('link[href*="climate.css"]') || {}).href || "",
      jsSrc: (q('script[src*="climate.js"]') || {}).src || "",
      termJs: (q('script[src*="climate_terminal"]') || {}).src || "",
      wcTermJs: (q('script[src*="wc_terminal"]') || {}).src || "",
      classes: wb.className,
      rightVar: parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10),
      ai: box(q("#climate-ai")),
      aiFullMin: cs(q(".climate-ai-full"), "min-width"),
      gridCols: cs(wb, "grid-template-columns"),
      explorerSections: qa(".climate-explorer-sections button").map((b) => b.textContent.trim()),
      explorerSectionsDisplay: q(".climate-explorer-sections") ? cs(q(".climate-explorer-sections"), "display") : "missing",
      bottomTabs: qa(".climate-bottom-tabs [data-panel]").map((b) => b.dataset.panel),
      terminalPanel: !!q("#climate-terminal-panel"),
      climateTerminal: typeof window.ClimateTerminal,
      wcTerminal: typeof window.WCTerminal,
      fileFont: file ? { family: cs(file, "font-family"), weight: cs(file, "font-weight"), size: cs(file, "font-size") } : null,
      folderFont: folder ? { family: cs(folder, "font-family"), weight: cs(folder, "font-weight"), size: cs(folder, "font-size") } : null,
      activeFont: active ? { family: cs(active, "font-family"), weight: cs(active, "font-weight") } : null,
      shell: box(q(".climate-shell")),
      sidebar: box(q("#hub-sidebar")),
    };
  });

  // Probe shrink below min without collapse class
  const narrow = await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-collapsed", "is-ai-closed", "is-ai-maximized");
    wb.style.setProperty("--right", "200px");
    const ai = document.querySelector("#climate-ai").getBoundingClientRect();
    return {
      rightVar: parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10),
      aiW: Math.round(ai.width),
      fullDisplay: getComputedStyle(document.querySelector(".climate-ai-full")).display,
      railDisplay: getComputedStyle(document.querySelector("#climate-ai-expand")).display,
    };
  });

  // Maximize formula probe
  const maxProbe = await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    const btn = document.getElementById("climate-maximize-ai");
    wb.classList.remove("is-ai-collapsed", "is-ai-closed");
    wb.style.setProperty("--right", "360px");
    if (btn) btn.click();
    const right = parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10);
    const vw = window.innerWidth;
    return { right, vw, expectedClamp: Math.min(720, Math.max(480, Math.round(vw * 0.45))), classes: wb.className };
  });

  await page.screenshot({ path: path.join(outDir, "full.png") });
  fs.writeFileSync(path.join(outDir, "inspect.json"), JSON.stringify({ data, narrow, maxProbe }, null, 2));
  console.log(JSON.stringify({ data, narrow, maxProbe }, null, 2));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
