/**
 * Verify CLIMATE AI panel width states: 360 / 380 / 420 / maximized / collapsed / restore.
 * Usage: node scripts/climate_ai_resize_verify.js
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "ai-resize");
fs.mkdirSync(outDir, { recursive: true });

async function snapshot(page, label) {
  return page.evaluate((name) => {
    const q = (s) => document.querySelector(s);
    const workbench = q("#climate-workbench");
    const ai = q("#climate-ai");
    const full = q(".climate-ai-full");
    const rail = q("#climate-ai-expand");
    const title = q("#climate-chat-title");
    const token = q("#climate-token-label");
    const composer = q("#climate-prompt");
    const provider = q("#climate-provider-panel");
    const model = q("#climate-model-panel");
    const shell = q(".climate-shell") || q("#climate-shell");
    const sidebar = q("#hub-sidebar");
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
      };
    };
    const cs = (el, prop) => (el ? getComputedStyle(el).getPropertyValue(prop) : "");
    const overlaps = (a, b) => {
      if (!a || !b) return false;
      return !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
    };
    const providerBox = box(provider && provider.closest(".climate-chat-pill"));
    const modelBox = box(model && model.closest(".climate-chat-pill"));
    const titleFs = parseFloat(cs(title, "font-size")) || 0;
    const tokenFs = parseFloat(cs(token, "font-size")) || 0;
    const composeFs = parseFloat(cs(composer, "font-size")) || 0;
    const rightVar = parseInt(getComputedStyle(workbench).getPropertyValue("--right"), 10);
    return {
      label: name,
      classes: workbench.className,
      rightVar,
      ai: box(ai),
      full: box(full),
      rail: box(rail),
      railVisible: !!(rail && rail.offsetParent !== null && getComputedStyle(rail).display !== "none"),
      fullVisible: !!(full && full.offsetParent !== null && getComputedStyle(full).display !== "none"),
      title: {
        text: ((title && title.textContent) || "").trim(),
        tip: (title && title.getAttribute("title")) || "",
        fontSize: titleFs,
        overflow: cs(title, "text-overflow"),
        box: box(title),
      },
      token: { text: ((token && token.textContent) || "").trim(), fontSize: tokenFs, box: box(token) },
      composer: { fontSize: composeFs, box: box(composer), disabled: !!(composer && composer.disabled) },
      providerModelOverlap: overlaps(providerBox, modelBox),
      providerBox,
      modelBox,
      shell: box(shell),
      sidebar: box(sidebar),
      aiPrevRight: workbench.dataset.aiPrevRight || "",
    };
  }, label);
}

function assertState(s, expect) {
  const errors = [];
  if (expect.right != null && Math.abs(s.rightVar - expect.right) > 2) {
    errors.push(`rightVar ${s.rightVar} != ${expect.right}`);
  }
  if (expect.aiW != null && Math.abs((s.ai && s.ai.w) - expect.aiW) > 4) {
    errors.push(`ai.w ${s.ai && s.ai.w} != ${expect.aiW}`);
  }
  if (expect.collapsed != null && s.classes.includes("is-ai-collapsed") !== expect.collapsed) {
    errors.push(`collapsed class mismatch (got ${s.classes})`);
  }
  if (expect.maximized != null && s.classes.includes("is-ai-maximized") !== expect.maximized) {
    errors.push(`maximized class mismatch (got ${s.classes})`);
  }
  if (expect.railVisible != null && s.railVisible !== expect.railVisible) {
    errors.push(`railVisible ${s.railVisible} != ${expect.railVisible}`);
  }
  if (expect.fullVisible != null && s.fullVisible !== expect.fullVisible) {
    errors.push(`fullVisible ${s.fullVisible} != ${expect.fullVisible}`);
  }
  if (expect.noDistortion) {
    if (s.title.fontSize && s.title.fontSize < 12) errors.push(`title font too small: ${s.title.fontSize}`);
    if (s.token.fontSize && s.token.fontSize < 10) errors.push(`token font too small: ${s.token.fontSize}`);
    if (s.composer.fontSize && s.composer.fontSize < 11) errors.push(`composer font too small: ${s.composer.fontSize}`);
    if (s.providerModelOverlap) errors.push("provider/model controls overlap");
    if (s.fullVisible && s.ai && s.ai.w < 330) errors.push(`full UI visible but ai too narrow: ${s.ai.w}`);
  }
  return errors;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { base: BASE, states: [], errors: [] };

  await page.goto(`${BASE}/work/climate`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("#climate-workbench");
  await page.evaluate(() => {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith("climate:"));
    keys.forEach((k) => localStorage.removeItem(k));
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector("#climate-workbench");
  await page.waitForTimeout(800);

  const shellBefore = await page.evaluate(() => {
    const el = document.querySelector(".climate-shell") || document.querySelector("#climate-shell");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y) };
  });

  async function setWidth(px) {
    await page.evaluate((w) => {
      const wb = document.querySelector("#climate-workbench");
      wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
      delete wb.dataset.prevRight;
      wb.style.setProperty("--right", w + "px");
      wb.dataset.aiPrevRight = w + "px";
      const ed = window.__climateEditor;
      if (ed && ed.layout) ed.layout();
    }, px);
    await page.waitForTimeout(120);
  }

  // Default / 360 preferred compact
  await setWidth(360);
  let s = await snapshot(page, "360");
  report.states.push(s);
  report.errors.push(...assertState(s, { right: 360, aiW: 360, collapsed: false, fullVisible: true, railVisible: false, noDistortion: true }).map((e) => `360: ${e}`));
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "360.png") });

  await setWidth(340);
  s = await snapshot(page, "340");
  report.states.push(s);
  report.errors.push(...assertState(s, { right: 340, aiW: 340, collapsed: false, fullVisible: true, railVisible: false, noDistortion: true }).map((e) => `340: ${e}`));
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "340.png") });

  await setWidth(380);
  s = await snapshot(page, "380");
  report.states.push(s);
  report.errors.push(...assertState(s, { right: 380, aiW: 380, collapsed: false, fullVisible: true, railVisible: false, noDistortion: true }).map((e) => `380: ${e}`));
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "380.png") });

  // Maximized via UI
  await setWidth(360);
  await page.click("#climate-maximize-ai");
  await page.waitForTimeout(200);
  s = await snapshot(page, "maximized");
  report.states.push(s);
  const wbW = await page.evaluate(() => document.querySelector("#climate-workbench").getBoundingClientRect().width);
  const expectedMax = Math.round(wbW * 0.43);
  report.errors.push(...assertState(s, {
    right: expectedMax,
    aiW: expectedMax,
    maximized: true,
    collapsed: false,
    fullVisible: true,
    railVisible: false,
    noDistortion: true,
  }).map((e) => `maximized: ${e}`));
  if (s.rightVar < wbW * 0.38 || s.rightVar > wbW * 0.48) {
    report.errors.push(`maximized: width ${s.rightVar} not ~40–45% of workbench ${wbW}`);
  }
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "maximized.png") });

  // Restore from maximize
  await page.click("#climate-maximize-ai");
  await page.waitForTimeout(200);
  s = await snapshot(page, "maximize-restore");
  report.states.push(s);
  report.errors.push(...assertState(s, { right: 360, maximized: false, collapsed: false, fullVisible: true, noDistortion: true }).map((e) => `maximize-restore: ${e}`));

  // Collapse below 340 via sash drag
  await setWidth(360);
  const sash = page.locator('[data-resize="right"]');
  const sashBox = await sash.boundingBox();
  if (sashBox) {
    await page.mouse.move(sashBox.x + sashBox.width / 2, sashBox.y + sashBox.height / 2);
    await page.mouse.down();
    // Drag right enough to push width below 360 (narrow AI)
    await page.mouse.move(sashBox.x + 200, sashBox.y + sashBox.height / 2, { steps: 12 });
    await page.mouse.up();
  }
  await page.waitForTimeout(200);
  s = await snapshot(page, "collapsed");
  report.states.push(s);
  report.errors.push(...assertState(s, {
    collapsed: true,
    maximized: false,
    railVisible: true,
    fullVisible: false,
    right: 48,
  }).map((e) => `collapsed: ${e}`));
  if (s.fullVisible) report.errors.push("collapsed: full UI still visible (should be rail only)");
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "collapsed.png") });

  const prevBeforeRestore = s.aiPrevRight;
  await page.click("#climate-ai-expand");
  await page.waitForTimeout(200);
  s = await snapshot(page, "restore");
  report.states.push(s);
  const expectedRestore = Math.max(340, parseInt(prevBeforeRestore, 10) || 360);
  report.errors.push(...assertState(s, {
    collapsed: false,
    railVisible: false,
    fullVisible: true,
    right: expectedRestore,
    noDistortion: true,
  }).map((e) => `restore: ${e}`));
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "restore.png") });

  const shellAfter = await page.evaluate(() => {
    const el = document.querySelector(".climate-shell") || document.querySelector("#climate-shell");
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y) };
  });
  if (shellBefore && shellAfter && (shellBefore.x !== shellAfter.x || shellBefore.y !== shellAfter.y)) {
    report.errors.push(`shell shifted: before=${JSON.stringify(shellBefore)} after=${JSON.stringify(shellAfter)}`);
  }
  report.shellBefore = shellBefore;
  report.shellAfter = shellAfter;
  report.ok = report.errors.length === 0;

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, errors: report.errors, states: report.states.map((x) => ({
    label: x.label, rightVar: x.rightVar, aiW: x.ai && x.ai.w, classes: x.classes, railVisible: x.railVisible, fullVisible: x.fullVisible,
  })) }, null, 2));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
