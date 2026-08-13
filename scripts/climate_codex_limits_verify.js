/**
 * Verify Codex account rate-limit capacity chrome (mocked app-server API).
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8096";
const outDir = path.join("tmp", "climate-ui", "codex-limits");
fs.mkdirSync(outDir, { recursive: true });

const MOCK_LIMITS = {
  ok: true,
  available: true,
  message: null,
  detail: null,
  planType: "pro",
  remainingPercent: 69,
  buckets: [
    {
      limitId: "codex",
      limitName: "codex",
      window: "primary",
      usedPercent: 31,
      remainingPercent: 69,
      windowDurationMins: 300,
      resetsAt: Math.floor(Date.now() / 1000) + 3600,
      planType: "pro",
      credits: { hasCredits: true, unlimited: false, balance: "42.5" },
    },
    {
      limitId: "codex",
      limitName: "codex (secondary)",
      window: "secondary",
      usedPercent: 12,
      remainingPercent: 88,
      windowDurationMins: 10080,
      resetsAt: Math.floor(Date.now() / 1000) + 86400,
      planType: "pro",
      credits: { hasCredits: true, unlimited: false, balance: "42.5" },
    },
  ],
  credits: { hasCredits: true, unlimited: false, balance: "42.5" },
  source: "account/rateLimits/read",
  fetchedAt: Math.floor(Date.now() / 1000),
  workspace: "work",
  cached: false,
};

const UNAVAILABLE = {
  ok: true,
  available: false,
  message: "Codex limit unavailable",
  detail: "chatgpt authentication required to read rate limits",
  authRequired: true,
  planType: null,
  remainingPercent: null,
  buckets: [],
  credits: null,
  source: null,
  fetchedAt: Math.floor(Date.now() / 1000),
  workspace: "work",
  cached: false,
};

async function seedUsage(page) {
  await page.evaluate(() => {
    localStorage.setItem(
      "climate:chat:v1:work",
      JSON.stringify({
        activeId: "chat-limits",
        sessions: [
          {
            id: "chat-limits",
            title: "Codex limits check",
            createdAt: Date.now() - 60000,
            updatedAt: Date.now(),
            provider: "codex",
            model: "gpt-5.4-mini",
            lastRunProvider: "codex",
            workspace: "work",
            repositoryId: "",
            usage: {
              input: 28000,
              output: 10900,
              cached: 0,
              total: 38900,
              currentRun: 900,
              byProvider: { codex: 38900 },
              since: Date.now() - 60000,
              source: "exact",
            },
            messages: [],
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
  }
  await page.waitForTimeout(150);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const report = { base: BASE, checks: [], ok: true };

  function check(name, cond, detail) {
    const row = { name, ok: !!cond, detail: detail || "" };
    report.checks.push(row);
    if (!cond) report.ok = false;
    console.log((cond ? "PASS" : "FAIL") + " " + name + (detail ? " — " + detail : ""));
  }

  try {
    // Available limits
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    let hits = 0;
    await page.route("**/api/climate/work/providers/codex/rate-limits**", async (route) => {
      hits += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_LIMITS),
      });
    });
    await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("#climate-token-pill", { timeout: 30000 });
    await seedUsage(page);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#climate-token-label", { timeout: 30000 });
    await page.waitForTimeout(400);
    await openUsage(page);
    await page.waitForSelector("#climate-usage-limits:not([hidden])", { timeout: 10000 });

    const pillText = await page.locator("#climate-token-pill").innerText();
    const compact = pillText.replace(/\s+/g, " ").trim();
    check("pill keeps session tokens", /38\.9k tokens/i.test(compact), compact);
    check("pill shows actual remaining %", /69%/.test(compact), compact);
    check("quota meter visible when available", !(await page.locator("#climate-token-quota").isHidden()));

    const panelText = await page.locator("#climate-usage-panel").innerText();
    check("session total present", /Session total/i.test(panelText));
    check("current run present", /Current run/i.test(panelText));
    check("primary bucket shown", /69% left/i.test(panelText), panelText.slice(0, 240));
    check("secondary bucket shown", /88% left/i.test(panelText));
    check("credits shown", /Credits:\s*42\.5/i.test(panelText));
    check("refresh control present", await page.locator("#climate-usage-refresh").count() === 1);

    const before = hits;
    await page.click("#climate-usage-refresh");
    await page.waitForTimeout(300);
    check("refresh hits API", hits > before, String(hits));

    await page.screenshot({ path: path.join(outDir, "limits-available.png"), fullPage: false });
    await page.close();

    // Unavailable path
    const page2 = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page2.route("**/api/climate/work/providers/codex/rate-limits**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(UNAVAILABLE),
      });
    });
    await page2.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page2.waitForSelector("#climate-token-pill", { timeout: 30000 });
    await seedUsage(page2);
    await page2.reload({ waitUntil: "domcontentloaded" });
    await page2.waitForSelector("#climate-token-label", { timeout: 30000 });
    await page2.waitForTimeout(400);
    await openUsage(page2);

    const pill2 = (await page2.locator("#climate-token-pill").innerText()).replace(/\s+/g, " ").trim();
    check("unavailable hides fabricated %", /38\.9k tokens/i.test(pill2) && !/\d+%/.test(pill2), pill2);
    check("unavailable quota chrome hidden", await page2.locator("#climate-token-quota").isHidden());
    const panel2 = await page2.locator("#climate-usage-panel").innerText();
    check("unavailable message shown", /Codex limit unavailable/i.test(panel2), panel2.slice(0, 200));
    check("unavailable does not invent bucket %", !/69% left|88% left/i.test(panel2));

    await page2.screenshot({ path: path.join(outDir, "limits-unavailable.png"), fullPage: false });
    await page2.close();
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.stack ? err.stack : err);
    console.error(report.error);
  }

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  if (!report.ok) process.exit(1);
  console.log("OK climate_codex_limits_verify");
})();
