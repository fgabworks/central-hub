/**
 * Verify CLIMATE IDE fixes: AI min 340, collapse rail, explorer cleanup, terminal tab.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "ide-fix-after");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const report = { base: BASE, errors, states: [] };

  await page.goto(`${BASE}/work/climate`, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForSelector("#climate-workbench");
  await page.evaluate(() => {
    Object.keys(localStorage).filter((k) => k.startsWith("climate")).forEach((k) => localStorage.removeItem(k));
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector("#climate-workbench");
  await page.waitForTimeout(700);

  const shellBefore = await page.evaluate(() => {
    const el = document.querySelector(".climate-shell");
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w || r.width), h: Math.round(r.h || r.height) };
  });

  async function snap(label) {
    const s = await page.evaluate((name) => {
      const q = (s) => document.querySelector(s);
      const wb = q("#climate-workbench");
      const ai = q("#climate-ai");
      const box = (el) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) };
      };
      const title = q("#climate-chat-title");
      const token = q("#climate-token-label");
      return {
        label: name,
        classes: wb.className,
        rightVar: parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10),
        ai: box(ai),
        fullVisible: !!(q(".climate-ai-full") && q(".climate-ai-full").offsetParent),
        railVisible: !!(q("#climate-ai-expand") && getComputedStyle(q("#climate-ai-expand")).display !== "none"),
        titleFs: title ? parseFloat(getComputedStyle(title).fontSize) : 0,
        tokenFs: token ? parseFloat(getComputedStyle(token).fontSize) : 0,
        explorerSections: Array.from(document.querySelectorAll(".climate-explorer-sections button")).map((b) => b.textContent.trim()),
        bottomTabs: Array.from(document.querySelectorAll(".climate-bottom-tabs [data-panel]")).map((b) => b.dataset.panel),
        termPanel: !!q("#climate-terminal-panel"),
        termVisible: !!(q("#climate-terminal-panel") && !q("#climate-terminal-panel").hidden),
        shell: box(q(".climate-shell")),
        sidebar: box(q("#hub-sidebar")),
      };
    }, label);
    report.states.push(s);
    return s;
  }

  // Default preferred ~360
  await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
    wb.style.setProperty("--right", "360px");
  });
  let s = await snap("360");
  if (s.ai.w < 340) errors.push(`360: ai too narrow ${s.ai.w}`);
  if (s.titleFs < 12) errors.push(`360: title font scaled ${s.titleFs}`);
  if (s.explorerSections.length) errors.push(`explorer sections still present: ${s.explorerSections.join(",")}`);
  if (!s.bottomTabs.includes("terminal")) errors.push("missing terminal tab");
  if (["problems", "output", "tests", "git", "terminal"].some((t) => !s.bottomTabs.includes(t))) {
    errors.push(`bottom tabs incomplete: ${s.bottomTabs.join(",")}`);
  }

  // Min width 340 — full UI
  await page.evaluate(() => {
    document.querySelector("#climate-workbench").style.setProperty("--right", "340px");
  });
  s = await snap("340");
  if (Math.abs(s.ai.w - 340) > 4) errors.push(`340: expected ~340 got ${s.ai.w}`);
  if (s.railVisible) errors.push("340: should not be rail");
  if (!s.fullVisible) errors.push("340: full UI should be visible");
  if (s.titleFs < 12 || s.tokenFs < 10) errors.push("340: fonts distorted");

  // Attempt to force ultra-narrow WITHOUT collapse class — CSS minmax must keep >=340
  await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-collapsed", "is-ai-closed", "is-ai-maximized");
    wb.style.setProperty("--right", "48px");
  });
  s = await snap("forced-narrow-css-guard");
  if (s.ai.w < 340) errors.push(`css guard failed: ai.w=${s.ai.w} with --right 48 and not collapsed`);
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "css-guard.png") });

  // Collapse via drag
  await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-collapsed", "is-ai-maximized", "is-ai-closed");
    wb.style.setProperty("--right", "360px");
    wb.dataset.aiPrevRight = "360px";
  });
  const sash = page.locator('[data-resize="right"]');
  const sashBox = await sash.boundingBox();
  if (sashBox) {
    await page.mouse.move(sashBox.x + 1, sashBox.y + sashBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(sashBox.x + 120, sashBox.y + sashBox.height / 2, { steps: 10 });
    await page.mouse.up();
  }
  await page.waitForTimeout(150);
  s = await snap("collapsed");
  if (!s.classes.includes("is-ai-collapsed")) errors.push("collapsed: missing class");
  if (!s.railVisible || s.fullVisible) errors.push("collapsed: expected rail only");
  if (s.ai.w > 60) errors.push(`collapsed: rail too wide ${s.ai.w}`);
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "collapsed.png") });

  await page.click("#climate-ai-expand");
  await page.waitForTimeout(150);
  s = await snap("restore");
  if (s.classes.includes("is-ai-collapsed")) errors.push("restore: still collapsed");
  if (s.ai.w < 340) errors.push(`restore: too narrow ${s.ai.w}`);

  // Maximize
  await page.click("#climate-maximize-ai");
  await page.waitForTimeout(150);
  s = await snap("maximized");
  const wbW = await page.evaluate(() => document.querySelector("#climate-workbench").getBoundingClientRect().width);
  if (!s.classes.includes("is-ai-maximized")) errors.push("maximized: missing class");
  if (s.rightVar < wbW * 0.38 || s.rightVar > wbW * 0.48) {
    errors.push(`maximized: ${s.rightVar} not ~40-45% of ${wbW}`);
  }

  // Terminal panel
  await page.click('.climate-bottom-tabs [data-panel="terminal"]');
  await page.waitForTimeout(200);
  s = await snap("terminal-tab");
  if (!s.termVisible) errors.push("terminal panel not visible");
  const termUi = await page.evaluate(() => {
    const empty = document.querySelector("#climate-term-empty");
    const shell = document.querySelector("#climate-term-shell");
    return {
      emptyVisible: !!(empty && !empty.hidden),
      emptyText: (empty && empty.textContent || "").replace(/\s+/g, " ").trim(),
      shellDefault: shell ? shell.value : "",
      hasNew: !!document.querySelector("#climate-term-new"),
      hasRestart: !!document.querySelector("#climate-term-restart"),
      hasClose: !!document.querySelector("#climate-term-close"),
      climateTerminal: typeof window.ClimateTerminal !== "undefined",
    };
  });
  report.termUi = termUi;
  if (!termUi.climateTerminal) errors.push("ClimateTerminal missing");
  if (!termUi.emptyVisible) errors.push("terminal empty state missing");
  if (!/never auto-executes|Manual commands/i.test(termUi.emptyText)) {
    errors.push("terminal copy should mention no auto-execute");
  }
  if (termUi.shellDefault !== "powershell") errors.push(`expected powershell default, got ${termUi.shellDefault}`);

  // Start terminal (if API available)
  let termStart = null;
  try {
    await page.click("#climate-term-empty-new");
    await page.waitForTimeout(1500);
    termStart = await page.evaluate(() => {
      const stage = document.querySelector("#climate-term-stage");
      const fail = document.querySelector("#climate-term-fail");
      const xterm = document.querySelector("#climate-term-xterm .xterm");
      const cwd = (document.querySelector("#climate-term-cwd") || {}).textContent || "";
      return {
        hasSession: !!(window.ClimateTerminal && window.ClimateTerminal.hasSession()),
        stageVisible: !!(stage && !stage.hidden),
        failVisible: !!(fail && !fail.hidden),
        failMsg: fail ? ((fail.querySelector(".climate-term-fail-msg") || {}).textContent || "") : "",
        hasXterm: !!xterm,
        cwd: cwd.trim(),
      };
    });
    report.termStart = termStart;
    if (termStart.hasSession) {
      if (!termStart.stageVisible || !termStart.hasXterm) errors.push("terminal session UI incomplete");
      // Type a harmless command manually
      await page.click("#climate-term-xterm .xterm textarea, #climate-term-xterm .xterm", { force: true }).catch(() => {});
      await page.keyboard.type("echo climate-term-ok");
      await page.keyboard.press("Enter");
      await page.waitForTimeout(800);
      // Switch file tab context shouldn't kill session — open terminal still has session
      await page.click('.climate-bottom-tabs [data-panel="problems"]');
      await page.waitForTimeout(200);
      await page.click('.climate-bottom-tabs [data-panel="terminal"]');
      await page.waitForTimeout(300);
      const still = await page.evaluate(() => window.ClimateTerminal && window.ClimateTerminal.hasSession());
      if (!still) errors.push("terminal session lost after switching bottom tabs");
    } else {
      report.termStartNote = "Session did not start (API/env may be unavailable in this run); UI contracts still checked.";
    }
  } catch (e) {
    report.termStartError = String(e);
  }

  const shellAfter = await page.evaluate(() => {
    const el = document.querySelector(".climate-shell");
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  });
  if (shellBefore.x !== shellAfter.x || shellBefore.y !== shellAfter.y) {
    errors.push(`shell shifted ${JSON.stringify(shellBefore)} -> ${JSON.stringify(shellAfter)}`);
  }
  report.shellBefore = shellBefore;
  report.shellAfter = shellAfter;
  report.ok = errors.length === 0;

  await page.screenshot({ path: path.join(outDir, "full.png") });
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, errors, termStart: report.termStart, termUi }, null, 2));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
