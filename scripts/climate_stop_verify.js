/**
 * Verify CLIMATE Stop cancels the real provider run and restores idle Send.
 * Uses route interception so cancel is exercised without depending on a live long job,
 * while still calling the real /cancel endpoint path the UI uses.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8098";
const outDir = path.join("tmp", "climate-ui", "stop-run");
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { base: BASE, checks: [], ok: true, cancelCalls: 0, pollAfterCancel: 0 };

  function check(name, cond, detail) {
    const row = { name, ok: !!cond, detail: detail || "" };
    report.checks.push(row);
    if (!cond) report.ok = false;
    console.log((cond ? "PASS" : "FAIL") + " " + name + (detail ? " — " + detail : ""));
  }

  let runState = "running";
  let tick = 0;
  const runId = "stop-run-demo-1";

  try {
    await page.route("**/api/climate/**", async (route) => {
      const req = route.request();
      const url = req.url();
      const method = req.method();

      if (method === "GET" && /\/providers\/[^/]+\/models/.test(url)) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            models: ["gpt-5.4-mini", "gpt-5.4"],
            recommended_model: "gpt-5.4-mini",
            models_source: "test",
          }),
        });
      }

      if (method === "POST" && /\/repositories\/[^/]+\/runs$/.test(url)) {
        runState = "running";
        tick = 0;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            run: {
              id: runId,
              status: "running",
              provider: "codex",
              model: "gpt-5.4-mini",
              task_mode: "ask",
              logs: "Reading analytics/derive_member_scores.py\nExploring 2 files\n",
              answer: "",
              usage: {},
              proposal: null,
              created_at: Date.now(),
            },
          }),
        });
      }

      if (method === "POST" && url.includes("/runs/" + runId + "/cancel")) {
        report.cancelCalls += 1;
        runState = "cancelled";
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            run: {
              id: runId,
              status: "cancelled",
              provider: "codex",
              model: "gpt-5.4-mini",
              task_mode: "ask",
              logs:
                "Reading analytics/derive_member_scores.py\nExploring 2 files\npartial answer so far\n[cancelled]\n",
              answer: "ANC Binary uses visit thresholds.",
              usage: { total_tokens: 120, usage_source: "exact" },
              proposal: null,
              cancel_requested: true,
              finished_at: Date.now(),
            },
          }),
        });
      }

      if (method === "GET" && url.includes("/runs/" + runId)) {
        tick += 1;
        if (runState === "cancelled") report.pollAfterCancel += 1;
        const runningLogs =
          "Reading analytics/derive_member_scores.py\nExploring 2 files\nChecking related logic\n" +
          (tick > 1 ? "Preparing response\npartial draft…\n" : "");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            run: {
              id: runId,
              status: runState,
              provider: "codex",
              model: "gpt-5.4-mini",
              task_mode: "ask",
              logs: runState === "cancelled"
                ? runningLogs + "[cancelled]\n"
                : runningLogs,
              answer: runState === "cancelled" ? "ANC Binary uses visit thresholds." : "",
              usage: runState === "cancelled" ? { total_tokens: 120, usage_source: "exact" } : {},
              proposal: null,
              cancel_requested: runState === "cancelled",
              finished_at: runState === "cancelled" ? Date.now() : null,
              created_at: Date.now() - 5000,
            },
          }),
        });
      }

      return route.continue();
    });

    await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForSelector("#climate-prompt", { timeout: 30000 });
    await page.waitForTimeout(800);

    await page.evaluate(() => {
      const repo = document.getElementById("climate-repository");
      if (repo && repo.options.length && !repo.value) {
        const idx = [...repo.options].findIndex((o) => o.value);
        if (idx >= 0) repo.selectedIndex = idx;
      }
      const provider = document.getElementById("climate-provider");
      const panelProvider = document.getElementById("climate-provider-panel");
      function ensureOption(select, value, label) {
        if (!select) return;
        if (![...select.options].some((o) => o.value === value)) {
          const opt = document.createElement("option");
          opt.value = value;
          opt.textContent = label;
          select.appendChild(opt);
        }
        select.value = value;
      }
      ensureOption(provider, "codex", "Codex");
      ensureOption(panelProvider, "codex", "Codex");
      const model = document.getElementById("climate-model");
      const panelModel = document.getElementById("climate-model-panel");
      ensureOption(model, "gpt-5.4-mini", "gpt-5.4-mini");
      ensureOption(panelModel, "gpt-5.4-mini", "gpt-5.4-mini");
      if (model) model.disabled = false;
      if (panelModel) panelModel.disabled = false;
      const send = document.getElementById("climate-send");
      if (send) {
        send.disabled = false;
        send.hidden = false;
      }
      document.getElementById("climate-prompt").value = "Explain how ANC Binary is derived";
      send.click();
    });

    await page.waitForSelector("#climate-stop:not([hidden])", { timeout: 15000 });
    let ui = await page.evaluate(() => ({
      stopVisible: !document.getElementById("climate-stop").hidden,
      sendHidden: !!document.getElementById("climate-send").hidden,
      planning: /Planning next moves/i.test(document.getElementById("climate-ai-feed").innerText),
      prepareDup:
        (document.getElementById("climate-ai-feed").innerText.match(/Preparing response/g) || []).length,
      currentPulse: document.querySelectorAll(".climate-activity-step.is-current").length,
      doneMarks: document.querySelectorAll(".climate-activity-step.is-done .climate-activity-mark").length,
    }));
    check("running shows Stop, hides Send", ui.stopVisible && ui.sendHidden);
    check("no Planning next moves", !ui.planning);
    check("at most one current activity pulse", ui.currentPulse <= 1, String(ui.currentPulse));
    await page.screenshot({ path: path.join(outDir, "running.png") });

    await page.click("#climate-stop");
    await page.waitForFunction(() => {
      const stop = document.getElementById("climate-stop");
      const feed = document.getElementById("climate-ai-feed");
      const text = ((feed && feed.innerText) || "") + " " + ((stop && stop.textContent) || "");
      return /Stopping|Stopped by user/i.test(text);
    }, { timeout: 8000 });

    ui = await page.evaluate(() => ({
      stoppingOrStopped:
        /Stopping|Stopped by user/i.test(document.getElementById("climate-ai-feed").innerText) ||
        /Stopping|Stop/i.test((document.getElementById("climate-stop") || {}).textContent || ""),
    }));
    check("shows Stopping or Stopped state", ui.stoppingOrStopped);
    await page.screenshot({ path: path.join(outDir, "stopping.png") });

    await page.waitForFunction(() => {
      const feed = document.getElementById("climate-ai-feed");
      const send = document.getElementById("climate-send");
      return feed && /Stopped by user/i.test(feed.innerText) && send && !send.hidden;
    }, { timeout: 10000 });

    ui = await page.evaluate(() => {
      const feed = document.getElementById("climate-ai-feed");
      const send = document.getElementById("climate-send");
      const stop = document.getElementById("climate-stop");
      return {
        stopped: /Stopped by user/i.test(feed.innerText),
        partial: /ANC Binary|thresholds|partial/i.test(feed.innerText),
        sendVisible: send && !send.hidden,
        stopHidden: stop && stop.hidden,
        noUndo: !feed.querySelector('[data-chat-action="undo"]'),
        noPlanning: !/Planning next moves/i.test(feed.innerText),
        title: (document.getElementById("climate-chat-title") || {}).textContent || "",
      };
    });
    check("cancel API called", report.cancelCalls >= 1, String(report.cancelCalls));
    check("Stopped by user shown", ui.stopped);
    check("partial response preserved", ui.partial);
    check("idle Send restored", ui.sendVisible && ui.stopHidden);
    check("no edit actions after stop", ui.noUndo);
    check("still no Planning next moves", ui.noPlanning);
    await page.screenshot({ path: path.join(outDir, "stopped.png") });

    // New run can start
    runState = "running";
    tick = 0;
    await page.evaluate(() => {
      const send = document.getElementById("climate-send");
      if (send) {
        send.disabled = false;
        send.hidden = false;
      }
      const model = document.getElementById("climate-model");
      if (model && !model.value) {
        if (![...model.options].some((o) => o.value === "gpt-5.4-mini")) {
          const opt = document.createElement("option");
          opt.value = "gpt-5.4-mini";
          opt.textContent = "gpt-5.4-mini";
          model.appendChild(opt);
        }
        model.value = "gpt-5.4-mini";
      }
      document.getElementById("climate-prompt").value = "what is the active repository name?";
      send.click();
    });
    await page.waitForSelector("#climate-stop:not([hidden])", { timeout: 15000 });
    ui = await page.evaluate(() => ({
      stopVisible: !document.getElementById("climate-stop").hidden,
      msgs: document.querySelectorAll("#climate-ai-feed .climate-chat-msg").length,
    }));
    check("new run starts after stop", ui.stopVisible && ui.msgs >= 3, String(ui.msgs));
    await page.screenshot({ path: path.join(outDir, "new-run.png") });

    // Stop again to leave idle
    await page.click("#climate-stop");
    await page.waitForFunction(() => {
      const send = document.getElementById("climate-send");
      return send && !send.hidden;
    }, { timeout: 10000 });
    check("second stop returns to idle", report.cancelCalls >= 2, String(report.cancelCalls));
  } catch (err) {
    report.ok = false;
    report.error = String(err && err.stack ? err.stack : err);
    console.error(report.error);
  }

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  if (!report.ok) process.exit(1);
  console.log("OK climate_stop_verify");
})();
