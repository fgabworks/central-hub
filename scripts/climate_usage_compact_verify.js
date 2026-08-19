/**
 * Verify compact Session Usage / token UI in normal + maximized AI panel.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8096";
const outDir = path.join("tmp", "climate-ui", "usage-compact");
fs.mkdirSync(outDir, { recursive: true });

const WIDTHS = [1366, 1600, 1920];

async function seedUsage(page) {
  await page.evaluate(() => {
    localStorage.setItem(
      "climate:workspace:v1:work",
      JSON.stringify({
        activeId: "chat-usage",
        sessions: [
          {
            id: "chat-usage",
            title: "Session usage compact check",
            createdAt: Date.now() - 60000,
            updatedAt: Date.now(),
            provider: "codex",
            model: "gpt-5.4-mini",
            lastRunProvider: "codex",
            workspace: "work",
            repositoryId: "",
            usage: {
              input: 28000,
              output: 8400,
              cached: 2000,
              total: 38400,
              currentRun: 1200,
              byProvider: { codex: 30000, "claude-code": 5400, "cursor-agent": 3000 },
              since: Date.now() - 60000,
              source: "exact",
            },
            messages: [
              {
                id: "u1",
                role: "user",
                text: "Explain how ANC Binary is derived",
                ts: Date.now() - 40000,
              },
              {
                id: "a1",
                role: "assistant",
                provider: "codex",
                status: "completed",
                taskMode: "ask",
                text: "ANC Binary is derived from visit thresholds.",
                diagnostics: "tool_call read path=docs/a.md",
                sources: ["docs/a.md"],
                elapsedMs: 12000,
                ts: Date.now() - 30000,
              },
            ],
          },
        ],
      })
    );
  });
}

async function openUsage(page) {
  const panel = page.locator("#climate-usage-panel");
  if (await panel.isHidden()) {
    await page.click("#climate-token-pill");
  } else {
    // Force chrome refresh while open
    await page.click("#climate-token-pill");
    await page.click("#climate-token-pill");
  }
  await page.waitForTimeout(200);
  await page.evaluate(() => {
    const panel = document.getElementById("climate-usage-panel");
    if (panel) panel.hidden = false;
  });
}

async function measureUsage(page, label) {
  return page.evaluate((name) => {
    const pill = document.getElementById("climate-token-pill");
    const panel = document.getElementById("climate-usage-panel");
    const total = document.getElementById("climate-usage-total");
    const run = document.getElementById("climate-usage-run");
    const feed = document.getElementById("climate-ai-feed");
    const ai = document.querySelector(".climate-ai");
    const workbench = document.getElementById("climate-workbench");
    const fill = document.getElementById("climate-usage-battery-fill");
    const limits = document.getElementById("climate-usage-limits");
    const status = document.getElementById("climate-usage-quota-status");
    function styleNum(el, prop) {
      if (!el) return null;
      const n = parseFloat(getComputedStyle(el)[prop]);
      return isNaN(n) ? null : n;
    }
    const pillRect = pill ? pill.getBoundingClientRect() : null;
    const panelRect = panel && !panel.hidden ? panel.getBoundingClientRect() : null;
    const aiRect = ai ? ai.getBoundingClientRect() : null;
    const overflowX =
      !!ai &&
      (ai.scrollWidth > ai.clientWidth + 1 ||
        (pillRect && aiRect && pillRect.right > aiRect.right + 2));
    const overflowPanel =
      !!panelRect &&
      !!aiRect &&
      (panelRect.right > aiRect.right + 2 || panelRect.left < aiRect.left - 2);
    return {
      label: name,
      maximized: !!(workbench && workbench.classList.contains("is-ai-maximized")),
      aiWidth: aiRect ? Math.round(aiRect.width) : 0,
      pillText: pill ? pill.innerText.replace(/\s+/g, " ").trim() : "",
      pillFont: styleNum(document.getElementById("climate-token-label"), "fontSize"),
      totalFont: styleNum(total, "fontSize"),
      runFont: styleNum(run, "fontSize"),
      panelWidth: panelRect ? Math.round(panelRect.width) : 0,
      panelHeight: panelRect ? Math.round(panelRect.height) : 0,
      batteryWidth: fill ? fill.style.width : "",
      limitsText: limits ? limits.innerText.replace(/\s+/g, " ").trim() : "",
      statusText: status ? status.innerText.replace(/\s+/g, " ").trim() : "",
      hasSessionTotal: !!(panel && /Session total/i.test(panel.innerText)),
      hasCurrentRun: !!(panel && /Current run/i.test(panel.innerText)),
      hasCodexCapacity: !!(panel && /Codex capacity/i.test(panel.innerText)),
      hasRefresh: !!(panel && /Refresh/i.test(panel.innerText)),
      source: (document.getElementById("climate-usage-source") || {}).textContent || "",
      diagnosticsInFeed: /usage_source=/.test(feed ? feed.innerText : ""),
      overflowX,
      overflowPanel,
      pillHeight: pillRect ? Math.round(pillRect.height) : 0,
    };
  }, label);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { base: BASE, checks: [], samples: [], ok: true };

  function check(name, cond, detail) {
    const row = { name, ok: !!cond, detail: detail || "" };
    report.checks.push(row);
    if (!cond) report.ok = false;
    console.log((cond ? "PASS" : "FAIL") + " " + name + (detail ? " — " + detail : ""));
  }

  try {
    for (const width of WIDTHS) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.route("**/api/climate/*/providers/codex/rate-limits**", async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            available: true,
            remainingPercent: 78,
            buckets: [
              {
                limitId: "codex",
                limitName: "codex",
                window: "primary",
                usedPercent: 22,
                remainingPercent: 78,
                windowDurationMins: 300,
                resetsAt: Math.floor(Date.now() / 1000) + 3600,
                planType: "pro",
                credits: null,
              },
            ],
            credits: null,
            message: null,
            workspace: "work",
          }),
        });
      });
      await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.waitForSelector("#climate-token-pill", { timeout: 30000 });
      await seedUsage(page);
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForSelector("#climate-token-label", { timeout: 30000 });
      await openUsage(page);

      let m = await measureUsage(page, width + "-normal");
      report.samples.push(m);
      await page.screenshot({
        path: path.join(outDir, `usage-${width}-normal.png`),
        fullPage: false,
      });

      check(`${width} compact pill has session tokens`, /38\.4k tokens/i.test(m.pillText), m.pillText);
      check(`${width} compact pill shows quota %`, /78%/.test(m.pillText), m.pillText);
      check(`${width} pill font ≤ 13px`, m.pillFont != null && m.pillFont <= 13, String(m.pillFont));
      check(`${width} session total font 14–16px`, m.totalFont >= 14 && m.totalFont <= 16, String(m.totalFont));
      check(`${width} current-run font ≤ 13px`, m.runFont != null && m.runFont <= 13, String(m.runFont));
      check(`${width} popover compact width`, m.panelWidth > 0 && m.panelWidth <= 250, String(m.panelWidth));
      check(`${width} popover compact height`, m.panelHeight > 0 && m.panelHeight <= 360, String(m.panelHeight));
      check(`${width} no AI overflow`, !m.overflowX && !m.overflowPanel);
      check(`${width} session+run rows present`, m.hasSessionTotal && m.hasCurrentRun);
      check(`${width} Codex capacity present`, m.hasCodexCapacity);
      check(`${width} bucket % left`, /78% left/i.test(m.limitsText), m.limitsText);
      check(`${width} refresh present`, m.hasRefresh);
      check(`${width} source exact`, /exact/i.test(m.source), m.source);
      check(`${width} no usage diagnostics in chat`, !m.diagnosticsInFeed);
      check(`${width} pill height compact`, m.pillHeight > 0 && m.pillHeight <= 28, String(m.pillHeight));

      const maxBtn = page.locator("#climate-maximize-ai");
      if (await maxBtn.count()) await maxBtn.click();
      else {
        await page.evaluate(() => {
          const wb = document.getElementById("climate-workbench");
          if (!wb) return;
          wb.classList.remove("is-ai-closed", "is-ai-collapsed");
          wb.classList.add("is-ai-maximized");
          const px = Math.round(Math.min(720, Math.max(480, window.innerWidth * 0.45)));
          wb.style.setProperty("--right", px + "px");
        });
      }
      await page.waitForTimeout(150);
      await openUsage(page);

      m = await measureUsage(page, width + "-max");
      report.samples.push(m);
      await page.screenshot({
        path: path.join(outDir, `usage-${width}-max.png`),
        fullPage: false,
      });

      check(`${width} max keeps total font 14–16px`, m.totalFont >= 14 && m.totalFont <= 16, String(m.totalFont));
      check(`${width} max keeps pill font ≤ 13px`, m.pillFont != null && m.pillFont <= 13, String(m.pillFont));
      check(`${width} max popover not enlarged`, m.panelWidth > 0 && m.panelWidth <= 250, String(m.panelWidth));
      check(`${width} max no overflow`, !m.overflowX && !m.overflowPanel);
      check(`${width} max AI wider`, m.aiWidth >= 450, String(m.aiWidth));

      await page.close();
    }
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.stack ? err.stack : err);
    console.error(report.error);
  }

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  if (!report.ok) process.exit(1);
  console.log("OK climate_usage_compact_verify");
})();
