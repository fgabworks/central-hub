/**
 * Inspect CLIMATE VANTA Terminal layout before/after toolbar + PTY fixes.
 * Usage: node scripts/climate_terminal_inspect.js [before|after]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const PHASE = process.argv[2] === "after" ? "after" : "before";
const outDir = path.join("tmp", "climate-ui", "terminal-" + PHASE);
fs.mkdirSync(outDir, { recursive: true });

const VIEWPORTS = [
  { name: "1366x768", width: 1366, height: 768 },
  { name: "1600x900", width: 1600, height: 900 },
];

function box(el) {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    x: Math.round(r.x),
    y: Math.round(r.y),
    w: Math.round(r.width),
    h: Math.round(r.height),
  };
}

async function measure(page) {
  return page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const boxOf = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const cs = (el, prop) => (el ? getComputedStyle(el).getPropertyValue(prop).trim() : "");
    const toolbar = q(".climate-wc-terminal .wc-term-toolbar");
    const tabs = q("#wc-term-session-tabs");
    const stage = q("#wc-term-stage");
    const xterm = q("#wc-xterm-a");
    const bottomTabs = q(".climate-bottom-tabs");
    const problemsBar = q("#climate-pane-problems .climate-bottom-toolbar");
    const overflowX = toolbar ? cs(toolbar, "overflow-x") : "";
    const scrollW = toolbar ? toolbar.scrollWidth : 0;
    const clientW = toolbar ? toolbar.clientWidth : 0;
    return {
      vw: window.innerWidth,
      vh: window.innerHeight,
      bottom: boxOf(q("#climate-bottom")),
      bottomTabs: boxOf(bottomTabs),
      bottomTabH: bottomTabs ? Math.round(bottomTabs.getBoundingClientRect().height) : 0,
      problemsToolbarH: problemsBar ? Math.round(problemsBar.getBoundingClientRect().height) : 0,
      toolbar: boxOf(toolbar),
      toolbarH: toolbar ? Math.round(toolbar.getBoundingClientRect().height) : 0,
      toolbarOverflowX: overflowX,
      toolbarScroll: { scrollWidth: scrollW, clientWidth: clientW, overflow: scrollW > clientW + 1 },
      repoSelect: boxOf(q("#wc-term-repo")),
      shellSelect: boxOf(q("#wc-term-shell")),
      newBtn: boxOf(q("#wc-term-new")),
      addBtn: boxOf(q("#wc-term-add")),
      splitBtn: boxOf(q("#wc-term-split")),
      restartBtn: boxOf(q("#wc-term-restart")),
      killBtn: boxOf(q("#wc-term-kill")),
      sessionTabs: boxOf(tabs),
      sessionTabCount: qa(".wc-term-tab").length,
      sessionTabs: qa(".wc-term-tab").map((t) => ({
        label: (t.querySelector(".wc-term-tab-label") || t).textContent.trim(),
        box: boxOf(t),
        overflow: t.scrollWidth > t.clientWidth + 1,
      })),
      stage: boxOf(stage),
      xterm: boxOf(xterm),
      labelFs: cs(q(".climate-wc-terminal .wc-field-label"), "font-size"),
      selectH: q("#wc-term-repo") ? Math.round(q("#wc-term-repo").getBoundingClientRect().height) : 0,
      newBtnH: q("#wc-term-new") ? Math.round(q("#wc-term-new").getBoundingClientRect().height) : 0,
      tabH: qa(".wc-term-tab")[0] ? Math.round(qa(".wc-term-tab")[0].getBoundingClientRect().height) : 0,
      clipped: (() => {
        const items = [
          ["new", q("#wc-term-new")],
          ["add", q("#wc-term-add")],
          ["split", q("#wc-term-split")],
          ["kill", q("#wc-term-kill")],
          ["repo", q("#wc-term-repo")],
          ["shell", q("#wc-term-shell")],
        ];
        const tb = toolbar ? toolbar.getBoundingClientRect() : null;
        if (!tb) return [];
        return items
          .filter(([, el]) => el)
          .map(([name, el]) => {
            const r = el.getBoundingClientRect();
            return {
              name,
              above: r.top < tb.top - 1,
              below: r.bottom > tb.bottom + 1,
              left: r.left < tb.left - 1,
              right: r.right > tb.right + 1,
            };
          })
          .filter((row) => row.above || row.below || row.left || row.right);
      })(),
    };
  });
}

async function closeExtraSessions(page, keep) {
  await page.evaluate(async (keepCount) => {
    const res = await fetch("/api/workspace-console/terminal/sessions", { credentials: "same-origin" });
    const data = await res.json();
    const sessions = data.sessions || [];
    for (let i = keepCount; i < sessions.length; i++) {
      await fetch(
        "/api/workspace-console/terminal/sessions/" + sessions[i].id + "?confirm=1",
        { method: "DELETE", credentials: "same-origin" }
      );
    }
  }, keep);
}

async function openTerminal(page) {
  await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("#climate-workbench", { timeout: 30000 });
  await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    if (wb) {
      wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
      wb.style.setProperty("--right", "360px");
    }
    const center = document.getElementById("climate-center");
    if (center) center.classList.remove("is-bottom-closed");
  });
  await page.click('.climate-bottom-tabs [data-panel="terminal"]');
  await page.waitForSelector("#climate-terminal-panel", { timeout: 15000 });
  await page.waitForTimeout(400);
  await closeExtraSessions(page, 0);
  await page.evaluate(() => {
    if (window.WCTerminal && window.WCTerminal.refreshSessions) return window.WCTerminal.refreshSessions();
  });
  await page.waitForTimeout(400);
  const repo = page.locator("#wc-term-repo");
  if (await repo.count()) {
    const value = await repo.evaluate((el) => {
      const opt = [...el.options].find((o) => /live-processing/i.test(o.value + o.textContent));
      if (opt) el.value = opt.value;
      return el.value;
    });
    if (value) await repo.dispatchEvent("change");
  }
  const emptyNew = page.locator("#wc-term-empty-new");
  const hasSession = await page.locator(".wc-term-tab").count();
  if (!hasSession && (await emptyNew.count()) && (await emptyNew.isVisible())) {
    await emptyNew.click();
    await page.waitForTimeout(1500);
  } else if (!hasSession) {
    const neu = page.locator("#wc-term-new");
    if (await neu.count()) {
      await neu.click();
      await page.waitForTimeout(1500);
    }
  }
  await page.waitForTimeout(400);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { base: BASE, phase: PHASE, ok: true, viewports: {} };
  try {
    for (const vp of VIEWPORTS) {
      const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
      page.on("dialog", (d) => d.accept());
      await openTerminal(page);
      const one = await measure(page);
      await page.screenshot({
        path: path.join(outDir, vp.name + "-one-session.png"),
        fullPage: false,
      });
      const termShot = page.locator("#climate-terminal-panel");
      if (await termShot.count()) {
        await termShot.screenshot({ path: path.join(outDir, vp.name + "-terminal-panel.png") });
      }

      // extra sessions
      for (let i = 0; i < 3; i++) {
        const add = page.locator("#wc-term-add");
        if (await add.count()) await add.click();
        await page.waitForTimeout(700);
      }
      const several = await measure(page);
      await page.screenshot({
        path: path.join(outDir, vp.name + "-several-sessions.png"),
        fullPage: false,
      });
      if (await termShot.count()) {
        await termShot.screenshot({ path: path.join(outDir, vp.name + "-terminal-several.png") });
      }

      // narrow AI
      await page.evaluate(() => {
        const wb = document.querySelector("#climate-workbench");
        if (wb) wb.style.setProperty("--right", "340px");
      });
      await page.waitForTimeout(300);
      const narrow = await measure(page);
      await page.screenshot({
        path: path.join(outDir, vp.name + "-narrow-ai.png"),
        fullPage: false,
      });

      // taller / shorter bottom
      await page.evaluate(() => {
        const wb = document.querySelector("#climate-workbench");
        if (wb) wb.style.setProperty("--bottom", "320px");
      });
      await page.waitForTimeout(200);
      const tall = await measure(page);
      await page.screenshot({
        path: path.join(outDir, vp.name + "-bottom-tall.png"),
        fullPage: false,
      });
      await page.evaluate(() => {
        const wb = document.querySelector("#climate-workbench");
        if (wb) wb.style.setProperty("--bottom", "140px");
      });
      await page.waitForTimeout(200);
      const short = await measure(page);
      await page.screenshot({
        path: path.join(outDir, vp.name + "-bottom-short.png"),
        fullPage: false,
      });

      report.viewports[vp.name] = { one, several, narrow, tall, short };
      await page.close();
    }
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ phase: PHASE, ok: report.ok, dir: outDir, error: report.error || null }, null, 2));
  if (!report.ok) process.exit(1);
})();
