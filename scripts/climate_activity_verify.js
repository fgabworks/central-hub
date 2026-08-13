/**
 * Verify CLIMATE AI live activity UI: running → completed, layout stable, compact/max.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8095";
const outDir = path.join("tmp", "climate-ui", "activity-after");
fs.mkdirSync(outDir, { recursive: true });

function seedChat(page, mode) {
  return page.evaluate((kind) => {
    const key = "climate:chat:v1:work";
    const diagnostics =
      "tool_call read path=analytics/derive_member_scores.py\n" +
      "Searching repository for ANC Binary\n" +
      "Reading analytics/derive_member_scores.py\n" +
      "Reading logic/anc_compliance.py\n" +
      "Exploring 2 files\n" +
      "Checking related logic\n" +
      "Running tests\n" +
      "9 passed\n" +
      "0 issues found\n" +
      "[tool] read({\"path\":\"analytics/derive_member_scores.py\"})\n";
    const runningMsg = {
      id: "a-running",
      role: "assistant",
      provider: "codex",
      model: "gpt-5.4-mini",
      status: "running",
      text: "",
      startedAt: Date.now() - 4000,
      diagnostics,
      ts: Date.now() - 4000,
    };
    const completedMsg = {
      id: "a-done",
      role: "assistant",
      provider: "codex",
      model: "gpt-5.4-mini",
      status: "completed",
      text: "ANC Binary is derived from member score thresholds.",
      startedAt: Date.now() - 18000,
      elapsedMs: 18000,
      diagnostics,
      changedFiles: [],
      filesInspected: 2,
      tests: { passed: true, count: 9, label: "9 passed", suite: "tests" },
      proposal: kind === "with-proposal"
        ? { state: "pending", edits: [{ path: "logic/anc_compliance.py", content: "x=1\n" }] }
        : null,
      ts: Date.now() - 1000,
    };
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
          usage: {
            input: 9000, output: 2000, cached: 1400, total: 12400, currentRun: 0,
            byProvider: { codex: 12400 }, startedAt: Date.now() - 60000, source: "provider",
          },
          messages: [
            {
              id: "u1",
              role: "user",
              text: "How is ANC Binary derived from member scores?",
              ts: Date.now() - 50000,
            },
            kind === "completed" || kind === "with-proposal" ? completedMsg : runningMsg,
          ],
        },
      ],
    };
    localStorage.setItem(key, JSON.stringify(store));
    // Clear layout prefs that might collapse AI
    Object.keys(localStorage).filter((k) => k.startsWith("climate:v1:")).forEach((k) => {
      try {
        const prefs = JSON.parse(localStorage.getItem(k) || "{}");
        prefs.right = 360;
        prefs.aiCollapsed = false;
        prefs.aiClosed = false;
        prefs.aiMaximized = false;
        localStorage.setItem(k, JSON.stringify(prefs));
      } catch (_) {}
    });
  }, mode);
}

async function snap(page, label) {
  return page.evaluate((name) => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const progress = q(".climate-activity-progress");
    const steps = qa(".climate-activity-step").map((el) => ({
      text: (el.textContent || "").replace(/\s+/g, " ").trim(),
      cls: el.className,
    }));
    return {
      label: name,
      shell: box(q(".climate-shell")),
      sidebar: box(q("#hub-sidebar")),
      ai: box(q("#climate-ai")),
      classes: (q("#climate-workbench") || {}).className || "",
      hasProgress: !!progress,
      progressTitle: progress ? ((progress.querySelector(".climate-activity-title") || {}).textContent || "").trim() : "",
      steps,
      hasPulse: !!q(".climate-activity-mark.is-pulse"),
      hasExploreBtn: !!q("[data-activity-explore]"),
      exploreOpen: !!(q("[data-explore-panel]") && !q("[data-explore-panel]").hidden),
      exploreFiles: qa(".climate-activity-explore-files li").map((el) => (el.textContent || "").trim()),
      hasComplete: !!q(".climate-activity-complete"),
      completeText: ((q(".climate-activity-complete") || {}).textContent || "").replace(/\s+/g, " ").trim().slice(0, 220),
      hasViewDetails: !!q("[data-activity-details]"),
      hasShowChanges: !!qa("[data-chat-action='review']").find((b) => /Show Changes/i.test(b.textContent || "")),
      hasWorkingPlain: /Working…|Working\.\.\./.test(((q(".climate-chat-text") || {}).textContent || "")),
      rawInBody: /tool_call|thread\.started|function_call/.test(((q(".climate-chat-text") || {}).textContent || "")),
      detailsCount: qa(".climate-chat-details").length,
    };
  }, label);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const report = { base: BASE, errors, states: [] };

  await page.goto(`${BASE}/work/climate`, { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForSelector("#climate-workbench", { timeout: 20000 });

  // Live / running
  await seedChat(page, "running");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".climate-activity-progress", { timeout: 15000 });
  await page.waitForTimeout(400);
  let s = await snap(page, "running");
  report.states.push(s);
  if (!s.hasProgress) errors.push("running: missing progress block");
  if (!/Working on your request/i.test(s.progressTitle)) errors.push("running: missing title");
  if (!s.steps.length) errors.push("running: no evidence steps");
  if (!s.hasPulse) errors.push("running: missing current-step pulse");
  if (s.hasWorkingPlain) errors.push("running: still shows plain Working…");
  if (s.rawInBody) errors.push("running: raw protocol leaked into chat body");
  if (!s.steps.some((x) => /Exploring/i.test(x.text))) errors.push("running: missing Exploring step");
  if (!s.steps.some((x) => /Searching repository/i.test(x.text))) errors.push("running: missing search step");
  if (!s.steps.some((x) => /Reading/i.test(x.text))) errors.push("running: missing reading step");
  // Invented pending steps should not appear without evidence
  if (s.steps.some((x) => /Preparing response/i.test(x.text) && /is-pending/.test(x.cls))) {
    errors.push("running: invented pending prepare step");
  }
  if (s.hasExploreBtn) {
    await page.click("[data-activity-explore]");
    await page.waitForTimeout(150);
    s = await snap(page, "explore-open");
    report.states.push(s);
    if (!s.exploreOpen) errors.push("explore: panel did not open");
    if (s.exploreFiles.length < 1) errors.push("explore: expected filenames");
  }
  const shellRunning = s.shell;
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "running.png") });

  // Completed
  await seedChat(page, "with-proposal");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".climate-activity-complete", { timeout: 15000 });
  await page.waitForTimeout(400);
  s = await snap(page, "completed");
  report.states.push(s);
  if (!s.hasComplete) errors.push("completed: missing summary");
  if (!/Worked for/i.test(s.completeText)) errors.push("completed: missing elapsed");
  if (!/Explored/i.test(s.completeText)) errors.push("completed: missing explored");
  if (!s.hasViewDetails) errors.push("completed: missing View Details");
  if (!s.hasShowChanges) errors.push("completed: missing Show Changes for pending proposal");
  if (s.hasProgress) errors.push("completed: live progress should be collapsed away");
  if (s.shell && shellRunning && (s.shell.x !== shellRunning.x || Math.abs(s.shell.w - shellRunning.w) > 2)) {
    errors.push("layout: shell shifted between running/completed");
  }
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "completed.png") });

  // Compact width
  await page.evaluate(() => {
    const wb = document.querySelector("#climate-workbench");
    wb.classList.remove("is-ai-collapsed", "is-ai-closed", "is-ai-maximized");
    wb.style.setProperty("--right", "340px");
  });
  await seedChat(page, "running");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".climate-activity-progress", { timeout: 15000 });
  s = await snap(page, "compact-340");
  report.states.push(s);
  if (!s.hasProgress || s.ai.w < 330) errors.push(`compact: progress missing or ai too narrow ${s.ai && s.ai.w}`);

  // Maximized
  await page.click("#climate-maximize-ai");
  await page.waitForTimeout(200);
  s = await snap(page, "maximized");
  report.states.push(s);
  if (!s.classes.includes("is-ai-maximized")) errors.push("maximized: class missing");
  if (!s.hasProgress) errors.push("maximized: progress missing");
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "maximized.png") });

  report.ok = errors.length === 0;
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, errors, states: report.states.map((x) => ({
    label: x.label, hasProgress: x.hasProgress, steps: x.steps.length, hasComplete: x.hasComplete, aiW: x.ai && x.ai.w, classes: x.classes,
  })) }, null, 2));
  await browser.close();
  process.exit(report.ok ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
