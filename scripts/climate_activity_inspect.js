/**
 * Inspect current CLIMATE AI chat message DOM + sample run output shapes.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8094";
const outDir = path.join("tmp", "climate-ui", "activity-before");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/work/climate`, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForSelector("#climate-workbench", { timeout: 20000 });
  await page.waitForTimeout(500);

  const before = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    return {
      feedHtmlSample: (q("#climate-ai-feed") || {}).innerHTML?.slice(0, 500) || "",
      msgClasses: qa(".climate-assistant-msg, .climate-chat-msg").map((el) => el.className),
      hasActivity: !!q(".climate-activity-block, .climate-run-progress, [data-activity]"),
      hasRunSummary: !!q(".climate-run-summary"),
      detailsCount: qa(".climate-chat-details").length,
      aiW: Math.round((q("#climate-ai") || { getBoundingClientRect: () => ({ width: 0 }) }).getBoundingClientRect().width),
      shellX: Math.round((q(".climate-shell") || { getBoundingClientRect: () => ({ x: 0 }) }).getBoundingClientRect().x),
    };
  });

  // Seed a running + completed message shape into localStorage chat store and reload render via evaluate
  const seeded = await page.evaluate(() => {
    const key = Object.keys(localStorage).find((k) => k.startsWith("climate:workspace:v1:") || k.startsWith("climate:chat:v1:")) || "climate:workspace:v1:work";
    const store = {
      activeId: "chat-activity-demo",
      sessions: [
        {
          id: "chat-activity-demo",
          title: "Explain how ANC Binary is derived",
          createdAt: Date.now() - 60000,
          updatedAt: Date.now(),
          provider: "codex",
          model: "gpt-5.4-mini",
          lastRunProvider: "codex",
          workspace: "work",
          repositoryId: "",
          usage: { input: 9000, output: 2000, cached: 1400, total: 12400, currentRun: 0, byProvider: { codex: 12400 }, startedAt: Date.now() - 60000, source: "provider" },
          messages: [
            {
              id: "u1",
              role: "user",
              text: "How is ANC Binary derived from member scores?",
              createdAt: Date.now() - 50000,
            },
            {
              id: "a-running",
              role: "assistant",
              provider: "codex",
              model: "gpt-5.4-mini",
              status: "running",
              text: "",
              startedAt: Date.now() - 4000,
              diagnostics:
                'tool_call read path=analytics/derive_member_scores.py\nSearching repository for ANC Binary\nReading analytics/derive_member_scores.py\nExploring 2 files\n',
              createdAt: Date.now() - 4000,
            },
          ],
        },
      ],
    };
    localStorage.setItem(key, JSON.stringify(store));
    return key;
  });

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("#climate-ai-feed", { timeout: 20000 });
  await page.waitForTimeout(800);

  const afterSeed = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    return {
      messageCount: qa(".climate-assistant-msg, .climate-chat-msg").length,
      assistantHtml: (qa(".climate-assistant-msg.is-assistant, .climate-chat-msg.is-assistant")[0] || {}).innerHTML?.slice(0, 1200) || "",
      bodyText: ((qa(".climate-assistant-msg.is-assistant .climate-assistant-body, .climate-chat-msg.is-assistant .climate-chat-body")[0] || {}).textContent || "").slice(0, 400),
      hasProgress: !!q(".climate-activity-progress, .climate-run-progress"),
    };
  });

  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "seeded-running.png") }).catch(() => {});
  fs.writeFileSync(path.join(outDir, "inspect.json"), JSON.stringify({ before, seeded, afterSeed }, null, 2));
  console.log(JSON.stringify({ before, afterSeed }, null, 2));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
