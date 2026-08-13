/**
 * Measure CLIMATE shell stability between Dashboard and Code Workspace.
 * Usage: node scripts/climate_shell_stability.js [label]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const label = process.argv[2] || "before";
const outDir = path.join("tmp", "climate-ui", `shell-${label}`);
fs.mkdirSync(outDir, { recursive: true });

function box(el) {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    x: Math.round(r.x),
    y: Math.round(r.y),
    w: Math.round(r.width),
    h: Math.round(r.height),
    right: Math.round(r.right),
    bottom: Math.round(r.bottom),
  };
}

async function measure(page) {
  return page.evaluate((boxFn) => {
    const q = (s) => document.querySelector(s);
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
        right: Math.round(r.right),
        bottom: Math.round(r.bottom),
      };
    };
    const cs = (el, prop) => (el ? getComputedStyle(el).getPropertyValue(prop) : null);
    const sidebar = q("#hub-sidebar, .sidebar");
    const topbar = q(".topbar");
    const content = q("main.content");
    const mainCol = q(".main-column");
    const appShell = q(".app-shell");
    const brand = q(".sidebar-brand");
    const switcher = q(".workspace-switcher");
    const climateShell = q(".climate-shell");
    return {
      url: location.href,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      scroll: { x: window.scrollX, y: window.scrollY },
      bodyBg: cs(document.body, "background-color"),
      appShell: box(appShell),
      appShellPadL: cs(appShell, "padding-left"),
      appShellPadR: cs(appShell, "padding-right"),
      appShellClasses: appShell ? appShell.className : "",
      sidebar: box(sidebar),
      sidebarW: sidebar ? Math.round(sidebar.getBoundingClientRect().width) : null,
      sidebarBrand: box(brand),
      sidebarBrandDisplay: cs(brand, "display"),
      workspaceSwitcher: box(switcher),
      topbar: box(topbar),
      topbarDisplay: cs(topbar, "display"),
      topbarH: topbar ? Math.round(topbar.getBoundingClientRect().height) : null,
      mainColumn: box(mainCol),
      content: box(content),
      contentPad: cs(content, "padding"),
      climateShell: box(climateShell),
      climateTitlebar: box(q(".climate-titlebar")),
      firstNav: ((q(".sidebar-nav .nav-link-label") || {}).textContent || "").trim(),
      navCount: document.querySelectorAll(".sidebar-nav .nav-link").length,
    };
  });
}

function shellKey(m) {
  return {
    sidebar: m.sidebar,
    sidebarBrand: m.sidebarBrand,
    sidebarBrandDisplay: m.sidebarBrandDisplay,
    workspaceSwitcher: m.workspaceSwitcher,
    topbar: m.topbar,
    topbarDisplay: m.topbarDisplay,
    contentOrigin: m.content ? { x: m.content.x, y: m.content.y } : null,
    contentW: m.content ? m.content.w : null,
    appShellPadL: m.appShellPadL,
    appShellPadR: m.appShellPadR,
    viewport: m.viewport,
    bodyBg: m.bodyBg,
    firstNav: m.firstNav,
  };
}

function diff(a, b, path = "") {
  const out = [];
  if (a === b) return out;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) {
    out.push({ path, before: a, after: b });
    return out;
  }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    out.push(...diff(a[k], b[k], path ? `${path}.${k}` : k));
  }
  return out;
}

async function captureRoute(page, route, name) {
  await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(400);
  const m = await measure(page);
  await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: false });
  return m;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript(() => {
    try {
      Object.keys(localStorage).forEach((k) => {
        if (k.startsWith("climate:") || k.startsWith("hub:sidebar")) localStorage.removeItem(k);
      });
    } catch (_) {}
  });

  const report = { label, base: BASE, pages: {}, diffs: {}, roundTrip: {} };

  const routes = [
    ["/work", "work_dashboard"],
    ["/work/climate", "work_climate"],
    ["/personal", "personal_dashboard"],
    ["/personal/climate", "personal_climate"],
  ];

  for (const [route, name] of routes) {
    report.pages[name] = await captureRoute(page, route, name);
  }

  // Round-trip VANTA
  const vantaDash1 = await captureRoute(page, "/work", "rt_work_dash_1");
  const vantaCode = await captureRoute(page, "/work/climate", "rt_work_climate");
  const vantaDash2 = await captureRoute(page, "/work", "rt_work_dash_2");
  report.roundTrip.vanta = {
    dash1: shellKey(vantaDash1),
    code: shellKey(vantaCode),
    dash2: shellKey(vantaDash2),
    dashVsCode: diff(shellKey(vantaDash1), shellKey(vantaCode)),
    dashRoundTrip: diff(shellKey(vantaDash1), shellKey(vantaDash2)),
  };

  // Round-trip ARCTIC
  const arcticDash1 = await captureRoute(page, "/personal", "rt_personal_dash_1");
  const arcticCode = await captureRoute(page, "/personal/climate", "rt_personal_climate");
  const arcticDash2 = await captureRoute(page, "/personal", "rt_personal_dash_2");
  report.roundTrip.arctic = {
    dash1: shellKey(arcticDash1),
    code: shellKey(arcticCode),
    dash2: shellKey(arcticDash2),
    dashVsCode: diff(shellKey(arcticDash1), shellKey(arcticCode)),
    dashRoundTrip: diff(shellKey(arcticDash1), shellKey(arcticDash2)),
  };

  report.diffs.work_dashboard_vs_climate = diff(
    shellKey(report.pages.work_dashboard),
    shellKey(report.pages.work_climate)
  );
  report.diffs.personal_dashboard_vs_climate = diff(
    shellKey(report.pages.personal_dashboard),
    shellKey(report.pages.personal_climate)
  );

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({
    label,
    workDiffCount: report.diffs.work_dashboard_vs_climate.length,
    personalDiffCount: report.diffs.personal_dashboard_vs_climate.length,
    workDiffs: report.diffs.work_dashboard_vs_climate,
    personalDiffs: report.diffs.personal_dashboard_vs_climate,
    workDash: shellKey(report.pages.work_dashboard),
    workCode: shellKey(report.pages.work_climate),
  }, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
