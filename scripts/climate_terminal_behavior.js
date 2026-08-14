/**
 * Exercise PowerShell PTY input/output in CLIMATE Terminal.
 * Sends input over the WebSocket only (no local echo, no Playwright key doubling).
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "terminal-behavior");
fs.mkdirSync(outDir, { recursive: true });

async function buffer(page) {
  return page.evaluate(() =>
    window.WCTerminal && window.WCTerminal.getBufferText ? window.WCTerminal.getBufferText() : ""
  );
}

async function waitPrompt(page) {
  await page.waitForFunction(() => {
    const t = window.WCTerminal && window.WCTerminal.getBufferText && window.WCTerminal.getBufferText();
    return !!(t && /PS [A-Z]:\\[^\n]*>\s*$/.test(t));
  }, null, { timeout: 25000 });
}

async function sendCmd(page, text) {
  const ok = await page.evaluate((cmd) => {
    return !!(window.WCTerminal && window.WCTerminal.sendInput && window.WCTerminal.sendInput(cmd + "\r"));
  }, text);
  if (!ok) throw new Error("sendInput failed for " + text);
  await page.waitForTimeout(900);
  await waitPrompt(page);
}

function promptStarts(text) {
  const lines = String(text || "").split(/\n/).map((l) => l.replace(/\s+$/g, ""));
  const prompts = lines.filter((l) => /^PS [A-Z]:\\/.test(l));
  const gluedLine = prompts.find((l) => (l.match(/PS [A-Z]:\\/g) || []).length > 1);
  return { count: prompts.length, glued: !!gluedLine, prompts: prompts.slice(-8), lines: lines.filter(Boolean).slice(-20) };
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
      const wb = document.querySelector("#climate-workbench");
      if (wb) {
        wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
        wb.style.setProperty("--bottom", "280px");
      }
      const center = document.getElementById("climate-center");
      if (center) center.classList.remove("is-bottom-closed");
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

    const size = await page.evaluate(() =>
      window.WCTerminal && window.WCTerminal.getTermSize ? window.WCTerminal.getTermSize() : null
    );
    report.termSize = size;

    await sendCmd(page, "echo one");
    await sendCmd(page, "echo two");
    await sendCmd(page, "sadsadasda");
    await sendCmd(page, "Get-ChildItem -Name | Select-Object -First 5");

    const text = await buffer(page);
    const info = promptStarts(text);
    fs.writeFileSync(path.join(outDir, "buffer.txt"), text);
    await page.screenshot({ path: path.join(outDir, "after-commands.png") });

    check("echo one", /\becho one\b/.test(text) && /\bone\b/.test(text), text.slice(-500));
    check("echo two", /\becho two\b/.test(text) && /\btwo\b/.test(text), "");
    check("invalid command visible", /sadsadasda/.test(text) && /CommandNotFoundException/.test(text), "");
    check("invalid command not duplicated", (text.match(/The term 'sadsadasda'/g) || []).length === 1, "");
    check("multiline listing", /\.claude/.test(text) && /\.pytest_cache/.test(text), info.lines.join(" | "));
    check("listing not duplicated", (text.match(/\.pytest_cache/g) || []).length === 1, "");
    check(
      "no glued prompts",
      !info.glued && !/echo oneNP\\/.test(text) && !/twoC:\\PMNP/.test(text) && !/echo onePS /.test(text),
      info.prompts.join(" || ")
    );
    check("multiple prompts", info.count >= 4, String(info.count));
    const last = info.prompts[info.prompts.length - 1] || "";
    check("last prompt at column 0", /^PS [A-Z]:\\[^\n]*>\s*$/.test(last), last);
    check("term width usable", !!(size && size.cols >= 40 && size.rows >= 8), JSON.stringify(size));
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
