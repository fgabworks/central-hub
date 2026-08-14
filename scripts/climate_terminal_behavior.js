/**
 * Exercise PowerShell PTY input/output in CLIMATE Terminal.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "terminal-behavior");
fs.mkdirSync(outDir, { recursive: true });

async function buffer(page) {
  return page.evaluate(() => (window.WCTerminal && window.WCTerminal.getBufferText
    ? window.WCTerminal.getBufferText()
    : ""));
}

async function waitPrompt(page) {
  await page.waitForFunction(() => {
    const t = window.WCTerminal && window.WCTerminal.getBufferText && window.WCTerminal.getBufferText();
    return !!(t && /PS [A-Z]:\\/.test(t));
  }, null, { timeout: 25000 });
}

async function typeCmd(page, text) {
  const helper = page.locator("#wc-xterm-a textarea.xterm-helper-textarea, #wc-xterm-a textarea");
  await page.locator("#wc-xterm-a").click({ force: true });
  if (await helper.count()) await helper.first().focus();
  await page.keyboard.type(text, { delay: 25 });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(700);
}

function promptStarts(text) {
  const lines = String(text || "").split(/\n/).map((l) => l.replace(/\s+$/g, ""));
  const prompts = lines.filter((l) => /^PS [A-Z]:\\/.test(l));
  const glued = /PS [A-Z]:\\[^\n]{0,80}> .+\S.*PS [A-Z]:\\/.test(text.replace(/\n/g, ""));
  return { count: prompts.length, glued, prompts: prompts.slice(-6), lines: lines.filter(Boolean).slice(-12) };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const report = { base: BASE, ok: true, checks: [] };
  function check(name, cond, detail) {
    report.checks.push({ name, ok: !!cond, detail: detail || "" });
    if (!cond) report.ok = false;
  }
  try {
    await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("#climate-workbench", { timeout: 30000 });
    await page.evaluate(() => {
      const center = document.getElementById("climate-center");
      if (center) {
        center.classList.remove("is-bottom-closed");
        center.style.setProperty("--bottom", "280px");
      }
    });
    await page.click('.climate-bottom-tabs [data-panel="terminal"]');
    await page.waitForTimeout(800);
    await page.evaluate(async () => {
      const res = await fetch("/api/workspace-console/terminal/sessions", { credentials: "same-origin" });
      const data = await res.json();
      for (const s of data.sessions || []) {
        await fetch("/api/workspace-console/terminal/sessions/" + s.id + "?confirm=1", {
          method: "DELETE",
          credentials: "same-origin",
        });
      }
    });
    await page.waitForTimeout(400);
    await page.evaluate(() => {
      const repo = document.getElementById("wc-term-repo");
      if (!repo) return;
      const opt = [...repo.options].find((o) => /live-processing/i.test(o.value + o.textContent));
      if (opt) {
        repo.value = opt.value;
        repo.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    const neu = page.locator("#wc-term-empty-new");
    if ((await neu.count()) && (await neu.isVisible())) await neu.click();
    else await page.locator("#wc-term-new").click();
    await page.waitForTimeout(1800);
    await waitPrompt(page);

    await typeCmd(page, "echo one");
    await typeCmd(page, "echo two");
    await typeCmd(page, "sadsadasda");
    await typeCmd(page, "Get-ChildItem -Name | Select-Object -First 5");
    await page.waitForTimeout(1200);

    const text = await buffer(page);
    const info = promptStarts(text);
    fs.writeFileSync(path.join(outDir, "buffer.txt"), text);
    await page.screenshot({ path: path.join(outDir, "after-commands.png") });

    check("echo one", /\bone\b/.test(text), text.slice(-400));
    check("echo two", /\btwo\b/.test(text), "");
    check("invalid command visible", /sadsadasda/.test(text), "");
    check("multiline listing", info.lines.length >= 6, info.lines.join(" | "));
    check("no glued prompts", !info.glued && !/sadsadasda\\pmnp-live-processing/.test(text), info.prompts.join(" || "));
    check("multiple prompts", info.count >= 4, String(info.count));
    const last = info.prompts[info.prompts.length - 1] || "";
    check("last prompt at column 0", /^PS [A-Z]:\\/.test(last) && !/^[^\n]*\S+PS [A-Z]:\\/.test(last), last);
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exit(1);
})();
