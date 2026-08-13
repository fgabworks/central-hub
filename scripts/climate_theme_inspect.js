/**
 * Sample CLIMATE shell theme tokens from rendered pages.
 * Usage: node scripts/climate_theme_inspect.js [label] [baseUrl]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[3] || process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const label = process.argv[2] || "theme-before";
const outDir = path.join("tmp", "climate-ui", label);
fs.mkdirSync(outDir, { recursive: true });

async function sample(page) {
  return page.evaluate(() => {
    const pick = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        sel,
        bg: cs.backgroundColor,
        color: cs.color,
        border: cs.borderTopColor || cs.borderColor,
        w: Math.round(r.width),
        h: Math.round(r.height),
      };
    };
    const root = getComputedStyle(document.documentElement);
    const body = getComputedStyle(document.body);
    const vars = [
      "--climate-bg",
      "--climate-bg-elevated",
      "--climate-panel",
      "--climate-panel-2",
      "--climate-border",
      "--climate-text",
      "--charcoal",
      "--panel",
      "--panel-2",
      "--border",
      "--text",
      "--crimson",
      "--workspace-accent",
      "--accent-border",
      "--accent-soft",
      "--accent-metallic",
      "--success",
      "--warning",
    ];
    const tokenMap = {};
    vars.forEach((v) => {
      tokenMap[v] = body.getPropertyValue(v).trim() || root.getPropertyValue(v).trim();
    });
    return {
      url: location.href,
      bodyClass: document.body.className,
      tokens: tokenMap,
      samples: {
        body: pick("body"),
        appShell: pick(".app-shell"),
        sidebar: pick(".sidebar"),
        topbar: pick(".topbar"),
        content: pick(".content"),
        card: pick(".card, .panel, .stat-card"),
        climateShell: pick(".climate-shell"),
        climateExplorer: pick(".climate-explorer"),
        climateCenter: pick(".climate-center"),
        climateAi: pick(".climate-ai"),
        workspaceActive: pick(".workspace-btn.is-active"),
        navActive: pick(".nav-link.is-active"),
        brandMark: pick(".brand-mark"),
      },
    };
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { label, base: BASE, pages: {} };

  for (const route of ["/work", "/personal", "/work/climate", "/personal/climate"]) {
    const name = route.replace(/\//g, "_").replace(/^_/, "");
    await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(400);
    report.pages[route] = await sample(page);
    await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: false });
  }

  // Compare VANTA vs ARCTIC shell surfaces
  const work = report.pages["/work"];
  const personal = report.pages["/personal"];
  const surfaceKeys = ["--climate-bg", "--climate-panel", "--climate-panel-2", "--climate-border", "--charcoal", "--panel", "--border", "--text"];
  const accentKeys = ["--workspace-accent", "--crimson", "--accent-border", "--accent-soft"];
  report.diff = {
    surfaceSame: surfaceKeys.every((k) => (work.tokens[k] || "") === (personal.tokens[k] || "")),
    surfacePairs: Object.fromEntries(surfaceKeys.map((k) => [k, { vanta: work.tokens[k], arctic: personal.tokens[k] }])),
    accentPairs: Object.fromEntries(accentKeys.map((k) => [k, { vanta: work.tokens[k], arctic: personal.tokens[k] }])),
    bodyBgSame: work.samples.body?.bg === personal.samples.body?.bg,
    sidebarBgSame: work.samples.sidebar?.bg === personal.samples.sidebar?.bg,
    panelBgSame: work.samples.card?.bg === personal.samples.card?.bg,
  };

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ label, diff: report.diff, workBody: work.samples.body, personalBody: personal.samples.body, workTokens: work.tokens, personalTokens: personal.tokens, climate: report.pages["/work/climate"].samples }, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
