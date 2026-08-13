/**
 * Inspect / verify CLIMATE AI chat panel vs mockup.
 * Usage: node scripts/climate_ai_chat_inspect.js [before|after]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const label = process.argv[2] || "before";
const outDir = path.join("tmp", "climate-ui", `ai-chat-${label}`);
fs.mkdirSync(outDir, { recursive: true });

async function measure(page) {
  return page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const qa = (s) => Array.from(document.querySelectorAll(s));
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
    };
    const text = (el) => ((el && el.textContent) || "").replace(/\s+/g, " ").trim().slice(0, 200);
    const ai = q("#climate-ai");
    const feed = q("#climate-ai-feed, #climate-chat-feed");
    const compose = q(".climate-compose, .climate-chat-composer");
    const rawHints = qa(".climate-chat-msg .climate-chat-text, .climate-chat-msg .climate-chat-summary, .climate-ai-message")
      .map((el) => el.textContent || "")
      .join("\n");
    return {
      url: location.href,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      sidebar: box(q("#hub-sidebar")),
      content: box(q("main.content")),
      ai: box(ai),
      aiStructure: {
        title: text(q(".climate-chat-header .climate-chat-label")),
        conversationTitle: text(q("#climate-chat-title, .climate-chat-title")),
        hasNewChat: !!q("#climate-chat-new, [data-chat-action='new']"),
        hasHistory: !!q("#climate-chat-history, [data-chat-action='history']"),
        hasMenu: !!q("#climate-chat-menu, [data-chat-action='menu']"),
        hasTokenPill: !!q("#climate-token-pill"),
        tokenLabel: text(q("#climate-token-label")),
        hasUsagePopover: !!q("#climate-usage-panel"),
        hasRunSummary: !!q(".climate-run-summary"),
        hasReviewChanges: /Review Changes/i.test((q("#climate-ai") || {}).innerText || ""),
        hasProviderCards: !!q("#climate-provider-cards"),
        providerCardsVisible: !!(q("#climate-provider-cards") && q("#climate-provider-cards").offsetParent !== null),
        composerPlaceholder: (q("#climate-prompt") || {}).placeholder || "",
        composerNearBottom: compose ? compose.getBoundingClientRect().bottom > (window.innerHeight - 120) : false,
        providerSelectNearComposer: !!q(".climate-chat-composer #climate-provider, .climate-chat-footer-controls #climate-provider, .climate-chat-footer-controls #climate-model-panel, #climate-provider-panel"),
        messageCount: qa(".climate-chat-msg, .climate-ai-message").length,
        hasYouLabel: !!qa(".climate-chat-msg, .climate-ai-message").find((el) => /You/.test(el.textContent || "")),
        hasUndoKeepReview: !!q("[data-chat-action='undo'], #climate-undo-all, #climate-reject") && !!q("[data-chat-action='keep'], #climate-keep-all, #climate-accept") && !!q("[data-chat-action='review'], #climate-review"),
        hasDetailsDiagnostics: !!q(".climate-chat-details, #climate-chat-details"),
        rawEventLeak: /(thread\.started|turn\.started|tool_call|response\.output_item)/i.test(rawHints),
        feedTextSample: text(feed),
      },
      workbenchClasses: (q("#climate-workbench") || {}).className || "",
    };
  });
}

async function seedDemoChat(page) {
  await page.evaluate(() => {
    const key = "climate:chat:v1:work";
    const session = {
      id: "demo-1",
      title: "Fix CLIMATE navigation consistency",
      createdAt: Date.now() - 600000,
      updatedAt: Date.now(),
      provider: "codex",
      model: "gpt-5.4-mini",
      lastRunProvider: "codex",
      workspace: "work",
      repositoryId: "",
      branch: "",
      context: { currentFile: true, selection: true, selectedFiles: false, repoContext: false },
      usage: {
        input: 9820, output: 1950, cached: 660, total: 12430, currentRun: 2140,
        byProvider: { codex: 8420, "claude-code": 4010, "cursor-agent": 0 },
        source: "exact",
        since: Date.now() - 3600000
      },
      messages: [
        { id: "m1", role: "user", text: "Fix the layout shifting between Dashboard and Code Workspace. Keep the same shell.", ts: Date.now() - 180000 },
        {
          id: "m2",
          role: "assistant",
          provider: "codex",
          model: "gpt-5.4-mini",
          text: "Updated the shared shell so Dashboard and Code Workspace keep identical chrome.",
          ts: Date.now() - 120000,
          elapsedMs: 8000,
          summary: ["Inspected 6 files", "Updated layout shell", "Tests passed"],
          filesInspected: 6,
          changedFiles: [
            "templates/climate.html",
            "static/css/climate.css",
            "static/js/climate.js",
            "tests/test_climate.py",
            "scripts/climate_ai_chat_inspect.js"
          ],
          tests: { passed: true, count: 9, label: "9 passed", suite: "tests.test_climate" },
          lines: { plus: 132, minus: 18 },
          diagnostics: "thread.started\nturn.started\n{\"type\":\"response.output_item\"}",
          status: "completed",
          proposal: { state: "pending", edits: [
            { path: "templates/climate.html", diff: "+a\n-b\n" },
            { path: "static/css/climate.css", diff: "+c\n" }
          ] },
        },
      ],
    };
    localStorage.setItem(key, JSON.stringify({ activeId: session.id, sessions: [session] }));
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { label, base: BASE, checks: {} };

  await page.goto(BASE + "/work/climate", { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(500);
  report.checks.initial = await measure(page);
  await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "ai_panel.png") }).catch(() => {});
  await page.screenshot({ path: path.join(outDir, "work_climate.png"), fullPage: false });

  if (label === "after") {
    await seedDemoChat(page);
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(600);
    report.checks.afterSeed = await measure(page);
    await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "ai_panel_seeded.png") }).catch(() => {});

    // New chat
    if (await page.locator("#climate-chat-new").count()) {
      await page.locator("#climate-chat-new").click();
      await page.waitForTimeout(200);
      report.checks.afterNewChat = await measure(page);
      const title = await page.locator("#climate-chat-title").textContent();
      report.checks.newChatClears = /new chat|untitled|ask/i.test(title || "") || ((await page.locator(".climate-chat-msg").count()) === 0);
    }

    // Restore history
    if (await page.locator("#climate-chat-history").count()) {
      await page.locator("#climate-chat-history").click();
      await page.waitForTimeout(200);
      const item = page.locator(".climate-chat-history-item", { hasText: "navigation consistency" }).first();
      if (await item.count()) {
        await item.click();
        await page.waitForTimeout(250);
        report.checks.afterHistoryRestore = await measure(page);
        report.checks.historyRestoredTitle = (await page.locator("#climate-chat-title").textContent() || "").trim();
      } else {
        report.checks.historyListMissingTarget = await page.locator("#climate-chat-history-list").innerText().catch(() => "");
      }
    }

    // Provider switch keeps same UI and must not fire provider/model requests
    const provider = page.locator("#climate-provider");
    if (await provider.count()) {
      const values = [];
      for (const opt of await provider.locator("option").all()) values.push(await opt.getAttribute("value"));
      const current = await provider.inputValue();
      const next = values.find((v) => v && v !== current) || values[0];
      if (next) {
        const beforeFetch = await page.evaluate(() => (window.__climateFetchCount = window.__climateFetchCount || 0));
        await page.evaluate(() => {
          window.__climateFetchLog = [];
          const orig = window.fetch;
          window.fetch = function () {
            const url = String(arguments[0] || "");
            window.__climateFetchLog.push(url);
            return orig.apply(this, arguments);
          };
        });
        await provider.selectOption(next);
        await page.waitForTimeout(400);
        const urls = await page.evaluate(() => window.__climateFetchLog || []);
        report.checks.providerSwitchRequests = urls;
        report.checks.providerSwitchNoRequest = !urls.some((u) => /\/providers\/.+\/models|\/runs/.test(u));
        report.checks.afterProviderSwitch = await measure(page);
      }
    }

    // Maximize preserves conversation
    if (await page.locator("#climate-maximize-ai").count()) {
      const beforeMsgs = report.checks.afterHistoryRestore?.aiStructure?.messageCount
        || report.checks.afterSeed?.aiStructure?.messageCount
        || 0;
      await page.locator("#climate-maximize-ai").click();
      await page.waitForTimeout(250);
      const maxMeasure = await measure(page);
      report.checks.afterMaximize = maxMeasure;
      report.checks.maximizePreserves = maxMeasure.aiStructure.messageCount === beforeMsgs
        || maxMeasure.aiStructure.messageCount >= 1;
      await page.locator("#climate-ai").screenshot({ path: path.join(outDir, "ai_panel_maximized.png") }).catch(() => {});
      await page.locator("#climate-maximize-ai").click();
    }

    // Shell stability vs dashboard
    const dash = await page.goto(BASE + "/work", { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(300);
    const dashShell = await page.evaluate(() => {
      const q = (s) => document.querySelector(s);
      const box = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      return { sidebar: box(q("#hub-sidebar")), topbar: box(q(".topbar")), content: box(q("main.content")) };
    });
    await page.goto(BASE + "/work/climate", { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(300);
    const codeShell = await page.evaluate(() => {
      const q = (s) => document.querySelector(s);
      const box = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      return { sidebar: box(q("#hub-sidebar")), topbar: box(q(".topbar")), content: { x: Math.round(q("main.content").getBoundingClientRect().x), y: Math.round(q("main.content").getBoundingClientRect().y), w: Math.round(q("main.content").getBoundingClientRect().width) } };
    });
    report.checks.shellStable = {
      sidebarMatch: JSON.stringify(dashShell.sidebar) === JSON.stringify(codeShell.sidebar),
      topbarH: dashShell.topbar.h === codeShell.topbar.h,
      contentOrigin: dashShell.content.x === codeShell.content.x && dashShell.content.y === codeShell.content.y,
      contentW: dashShell.content.w === codeShell.content.w,
    };

    // Token popover
    if (await page.locator("#climate-token-pill").count()) {
      await page.locator("#climate-token-pill").click();
      await page.waitForTimeout(150);
      report.checks.usagePopoverOpen = !(await page.locator("#climate-usage-panel").getAttribute("hidden"));
      report.checks.usagePopoverText = (await page.locator("#climate-usage-panel").innerText()).replace(/\s+/g, " ").slice(0, 240);
      await page.locator("#climate-token-pill").click();
    }

    report.pass = {
      newChat: !!report.checks.newChatClears,
      history: /navigation consistency/i.test(report.checks.historyRestoredTitle || ""),
      noRawEvents: report.checks.afterHistoryRestore
        ? !report.checks.afterHistoryRestore.aiStructure.rawEventLeak
        : !report.checks.afterSeed.aiStructure.rawEventLeak,
      summaryUi: !!(report.checks.afterHistoryRestore || report.checks.afterSeed).aiStructure.hasRunSummary,
      tokenPill: !!(report.checks.afterSeed || report.checks.initial).aiStructure.hasTokenPill,
      tokenPopover: !!report.checks.usagePopoverOpen,
      providerSwitchNoRequest: report.checks.providerSwitchNoRequest !== false,
      sameUiAfterProviderSwitch: !!(report.checks.afterProviderSwitch || {}).aiStructure?.hasNewChat,
      maximizePreserves: !!report.checks.maximizePreserves,
      shellStable: Object.values(report.checks.shellStable || {}).every(Boolean),
    };
  }

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
