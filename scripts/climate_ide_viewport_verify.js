/**
 * Verify CLIMATE IDE fixes across 1366 / 1600 / 1920 viewports.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "ide-verify-viewports");
fs.mkdirSync(outDir, { recursive: true });

const WIDTHS = [1366, 1600, 1920];

async function measure(page, label) {
  return page.evaluate((name) => {
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
    const folder = q(".climate-tree summary");
    const active = q(".climate-file-row.is-active .climate-file-name") || q(".climate-file-row.is-active");
    return {
      label: name,
      vw: window.innerWidth,
      classes: wb.className,
      rightVar: parseInt(getComputedStyle(wb).getPropertyValue("--right"), 10),
      ai: box(q("#climate-ai")),
      fullVisible: !!(q(".climate-ai-full") && q(".climate-ai-full").offsetParent),
      railVisible: !!(q("#climate-ai-expand") && getComputedStyle(q("#climate-ai-expand")).display !== "none"),
      titleFs: parseFloat(cs(q("#climate-chat-title"), "font-size")) || 0,
      explorerSections: qa(".climate-explorer-sections button").map((b) => b.textContent.trim()),
      explorerSectionsDisplay: q(".climate-explorer-sections") ? cs(q(".climate-explorer-sections"), "display") : "missing",
      bottomTabs: qa(".climate-bottom-tabs [data-panel]").map((b) => b.dataset.panel),
      fileWeight: file ? cs(file, "font-weight") : null,
      folderWeight: folder ? cs(folder, "font-weight") : null,
      activeWeight: active ? cs(active, "font-weight") : null,
      fileFamily: file ? cs(file, "font-family") : null,
      shell: box(q(".climate-shell")),
      sidebar: box(q("#hub-sidebar")),
      wcTerminal: typeof window.WCTerminal,
      climateTerminal: typeof window.ClimateTerminal,
      termPanel: !!q("#climate-terminal-panel"),
      wcXterm: !!q("#wc-xterm-a"),
    };
  }, label);
}

function expectedMax(vw) {
  return Math.min(720, Math.max(480, Math.round(vw * 0.45)));
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { base: BASE, errors: [], widths: {} };

  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.on("dialog", (d) => d.accept());
    const errs = [];
    await page.goto(`${BASE}/work/climate`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("#climate-workbench", { timeout: 30000 });
    await page.evaluate(() => {
      Object.keys(localStorage).filter((k) => k.startsWith("climate")).forEach((k) => localStorage.removeItem(k));
      const wb = document.querySelector("#climate-workbench");
      if (wb) {
        wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
        wb.style.setProperty("--right", "360px");
        delete wb.dataset.prevRight;
        wb.dataset.aiPrevRight = "360px";
      }
    });
    await page.waitForTimeout(500);

    const shellBefore = await page.evaluate(() => {
      const r = document.querySelector(".climate-shell").getBoundingClientRect();
      const s = document.querySelector("#hub-sidebar").getBoundingClientRect();
      return { shellX: Math.round(r.x), shellY: Math.round(r.y), sideW: Math.round(s.width) };
    });

    await page.evaluate(() => {
      const wb = document.querySelector("#climate-workbench");
      wb.classList.remove("is-ai-closed", "is-ai-collapsed", "is-ai-maximized");
      wb.style.setProperty("--right", "360px");
    });
    let s = await measure(page, `${width}-360`);
    if (s.ai.w < 340) errs.push(`360: ai ${s.ai.w} < 340`);
    if (s.titleFs && s.titleFs < 12) errs.push(`title font scaled ${s.titleFs}`);
    if (s.explorerSections.length) errs.push(`dead sections present: ${s.explorerSections}`);
    if (s.explorerSectionsDisplay !== "missing" && s.explorerSectionsDisplay !== "none") {
      errs.push(`explorer-sections display=${s.explorerSectionsDisplay}`);
    }
    if (!s.bottomTabs.includes("terminal")) errs.push("missing terminal tab");
    if (s.climateTerminal !== "undefined") errs.push("legacy ClimateTerminal still present");
    if (s.wcTerminal !== "object") errs.push("WCTerminal missing");
    if (!s.wcXterm) errs.push("wc-xterm-a missing");
    if (s.fileWeight && !["400", "normal"].includes(s.fileWeight)) errs.push(`file weight ${s.fileWeight}`);
    if (s.folderWeight) {
      const fw = parseInt(s.folderWeight, 10) || (s.folderWeight === "bold" ? 700 : 400);
      if (fw > 500) errs.push(`folder weight too bold ${s.folderWeight}`);
    }
    if (s.activeWeight && !["400", "normal"].includes(s.activeWeight)) errs.push(`active weight ${s.activeWeight}`);

    // Force narrow CSS var without collapse — must not distort below min
    await page.evaluate(() => {
      const wb = document.querySelector("#climate-workbench");
      wb.classList.remove("is-ai-collapsed", "is-ai-closed", "is-ai-maximized");
      wb.style.setProperty("--right", "200px");
    });
    s = await measure(page, `${width}-forced-200`);
    if (s.ai.w < 340 && !s.classes.includes("is-ai-collapsed")) {
      errs.push(`forced 200px still narrow without collapse: ${s.ai.w}`);
    }

    // Collapse via sash
    await page.evaluate(() => {
      const wb = document.querySelector("#climate-workbench");
      wb.classList.remove("is-ai-collapsed", "is-ai-maximized", "is-ai-closed");
      wb.style.setProperty("--right", "360px");
      wb.dataset.aiPrevRight = "360px";
    });
    const sash = page.locator('[data-resize="right"]');
    const box = await sash.boundingBox();
    if (box) {
      await page.mouse.move(box.x + 1, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 100, box.y + box.height / 2, { steps: 8 });
      await page.mouse.up();
    }
    await page.waitForTimeout(150);
    s = await measure(page, `${width}-collapsed`);
    if (!s.classes.includes("is-ai-collapsed") || !s.railVisible || s.fullVisible) {
      errs.push("collapse rail failed");
    }

    await page.click("#climate-ai-expand");
    await page.waitForTimeout(150);
    s = await measure(page, `${width}-restore`);
    if (s.classes.includes("is-ai-collapsed") || s.ai.w < 340) errs.push(`restore failed ai=${s.ai && s.ai.w}`);

    await page.click("#climate-maximize-ai");
    await page.waitForTimeout(150);
    s = await measure(page, `${width}-max`);
    const exp = expectedMax(width);
    if (!s.classes.includes("is-ai-maximized")) errs.push("maximize class missing");
    if (Math.abs(s.rightVar - exp) > 2) errs.push(`maximize ${s.rightVar} != clamp ${exp}`);

    // Terminal functional
    await page.click('.climate-bottom-tabs [data-panel="terminal"]');
    await page.waitForTimeout(400);
    await page.waitForFunction(() => {
      const repo = document.getElementById("wc-term-repo");
      return !!(window.WCTerminal && repo && repo.options && repo.options.length > 0 && repo.options[0].value);
    }, null, { timeout: 8000 }).catch(() => {});
    const term = await page.evaluate(async () => {
      const ready = window.WCTerminal ? true : false;
      if (!ready) return { ready: false };
      const repo = document.getElementById("wc-term-repo");
      const empty = document.getElementById("wc-term-empty");
      return {
        ready: true,
        repoValue: repo ? repo.value : "",
        repoOptions: repo ? repo.options.length : 0,
        emptyVisible: !!(empty && !empty.hidden),
        shell: (document.getElementById("wc-term-shell") || {}).value || "",
      };
    });
    if (!term.ready) errs.push("WCTerminal not ready");
    if (term.repoOptions < 1) errs.push("wc-term-repo not populated");

    let termLive = null;
    if (term.ready && term.repoOptions >= 1 && width === 1600) {
      await page.click("#wc-term-new");
      await page.waitForTimeout(1800);
      termLive = await page.evaluate(() => {
        const stage = document.getElementById("wc-term-stage");
        const xterm = document.querySelector("#wc-xterm-a .xterm");
        const fail = document.getElementById("wc-term-fail-a");
        return {
          activeId: window.WCTerminal && window.WCTerminal.getActiveId ? window.WCTerminal.getActiveId() : "",
          stageVisible: !!(stage && !stage.hidden),
          hasXterm: !!xterm,
          failVisible: !!(fail && !fail.hidden),
          failMsg: fail ? ((fail.querySelector(".wc-term-ws-fail-msg") || {}).textContent || "") : "",
        };
      });
      if (!termLive.activeId || !termLive.stageVisible || !termLive.hasXterm) {
        errs.push(`terminal start failed: ${JSON.stringify(termLive)}`);
      } else {
        await page.keyboard.type("echo climate-ok");
        await page.keyboard.press("Enter");
        await page.waitForTimeout(600);
        await page.click('.climate-bottom-tabs [data-panel="problems"]');
        await page.waitForTimeout(200);
        await page.click('.climate-bottom-tabs [data-panel="terminal"]');
        await page.waitForTimeout(300);
        const kept = await page.evaluate(() => window.WCTerminal.getActiveId());
        if (!kept) errs.push("terminal session lost after panel switch");
        // Close session to avoid PTY buildup across viewports
        await page.evaluate(async () => {
          const id = window.WCTerminal.getActiveId();
          if (!id) return;
          await fetch("/api/workspace-console/terminal/sessions/" + id + "?confirm=1", {
            method: "DELETE",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirm: true }),
          });
        });
      }
    } else if (term.ready) {
      termLive = { skippedStart: true, reason: "UI/catalog checked; live start verified at 1600 only" };
    }

    const shellAfter = await page.evaluate(() => {
      const r = document.querySelector(".climate-shell").getBoundingClientRect();
      const s = document.querySelector("#hub-sidebar").getBoundingClientRect();
      return { shellX: Math.round(r.x), shellY: Math.round(r.y), sideW: Math.round(s.width) };
    });
    if (shellBefore.shellX !== shellAfter.shellX || shellBefore.sideW !== shellAfter.sideW) {
      errs.push(`shell shift ${JSON.stringify(shellBefore)} -> ${JSON.stringify(shellAfter)}`);
    }

    await page.screenshot({ path: path.join(outDir, `${width}.png`) });
    report.widths[width] = { errors: errs, term, termLive, shellBefore, shellAfter, maximizeExpected: expectedMax(width) };
    report.errors.push(...errs.map((e) => `${width}: ${e}`));
    await page.close();
  }

  // ARCTIC shell stability spot-check at 1600
  const arctic = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await arctic.goto(`${BASE}/personal/climate`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await arctic.waitForSelector("#climate-workbench", { timeout: 30000 }).catch(() => null);
  const arcticShell = await arctic.evaluate(() => {
    const shell = document.querySelector(".climate-shell");
    const side = document.querySelector("#hub-sidebar");
    if (!shell || !side) return null;
    return {
      shellX: Math.round(shell.getBoundingClientRect().x),
      sideW: Math.round(side.getBoundingClientRect().width),
      hasWorkbench: !!document.querySelector("#climate-workbench"),
    };
  });
  report.arctic = arcticShell;
  if (!arcticShell || !arcticShell.hasWorkbench) report.errors.push("arctic: climate workbench missing");
  if (arcticShell && arcticShell.shellX < 100) report.errors.push(`arctic: shell x unexpected ${arcticShell.shellX}`);
  await arctic.close();

  report.ok = report.errors.length === 0;
  report.terminalStatus = "WCTerminal (Workspace Console PTY) embedded in CLIMATE bottom Terminal tab";
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, errors: report.errors, terminalStatus: report.terminalStatus, arctic: report.arctic }, null, 2));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
