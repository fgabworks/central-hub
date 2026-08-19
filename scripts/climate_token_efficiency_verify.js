/**
 * Playwright UI-only check for CLIMATE Token Efficiency states.
 * Token numbers are seeded fixtures — savings must come from backend Codex usage.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const outDir = path.join("tmp", "climate-ui", "token-efficiency");
fs.mkdirSync(outDir, { recursive: true });

const VIEWPORTS = [
  { name: "1366x768", width: 1366, height: 768 },
  { name: "1600x900", width: 1600, height: 900 },
];

const STATES = {
  "not-measured": {
    status: "Not measured",
    reason: "",
    eligible: true,
    climate: { total: 493229, input: 400000, cached_input: 12000, output: 93229, preflight_tokens_est: 527, runtime_ms: 120000, files_inspected: 8 },
    direct: null,
    comparison: null,
  },
  measuring: {
    status: "Measuring…",
    reason: "",
    eligible: true,
    climate: { total: 708792, preflight_tokens_est: 553, files_inspected: 2, source_candidates: 17 },
    direct: { status: "Measuring…", started_at: new Date(Date.now() - 42000).toISOString() },
    comparison: null,
  },
  measured: {
    status: "Measured",
    reason: "",
    eligible: true,
    climate: { total: 493229, input: 400000, cached_input: 12000, output: 93229, runtime_ms: 120000, files_inspected: 8, source_candidates: 19, session_fresh: true, session_reused: false },
    direct: { status: "Measured", usage: { input_tokens: 520000, cached_input_tokens: 18000, output_tokens: 134900, total_tokens: 654900 }, runtime_ms: 180000, files_inspected: 21, success: true },
    comparison: { climate_total: 493229, direct_total: 654900, difference: -161671, percent: -24.7, relative: 0.75, tone: "savings", primary: "Saved 161,671 tokens (-24.7%)", secondary: "CLIMATE used 24.7% fewer provider tokens", headline: "Saved 161,671 tokens (-24.7%)" },
    comparability_note: "CLIMATE and Direct were both fresh provider sessions.",
  },
  negative: {
    status: "Measured",
    reason: "",
    eligible: true,
    climate: { total: 708792, input: 702221, cached_input: 557056, output: 6571, runtime_ms: 42000, files_inspected: 2, source_candidates: 17, session_fresh: false, session_reused: true },
    direct: { status: "Measured", usage: { input_tokens: 278723, cached_input_tokens: 239616, output_tokens: 2185, total_tokens: 280908 }, runtime_ms: 66000, files_inspected: 4, success: true },
    comparison: { climate_total: 708792, direct_total: 280908, difference: 427884, percent: 152.3, relative: 2.52, tone: "increase", primary: "Used 427,884 more tokens (+152.3%)", secondary: "CLIMATE used 2.52× Direct provider usage", headline: "Used 427,884 more tokens (+152.3%)" },
    comparability_note: "CLIMATE resumed a prior Codex session; Direct was a fresh ephemeral run. CLIMATE provider totals include accumulated prior-turn context.",
  },
  unavailable: {
    status: "Benchmark unavailable",
    reason: "This run was recorded without a commit SHA. Re-run the prompt on the current commit to enable a measured comparison.",
    eligible: true,
    climate: { total: 493229, preflight_tokens_est: 527 },
    direct: null,
    comparison: null,
  },
  "not-comparable": {
    status: "Not comparable",
    reason: "Benchmark cannot be reproduced: repository commit has changed.",
    eligible: true,
    climate: { total: 493229, preflight_tokens_est: 527 },
    direct: null,
    comparison: null,
  },
  large: {
    status: "Measured",
    reason: "",
    eligible: true,
    climate: { total: 4932290, input: 4000000, cached_input: 120000, output: 932290, files_inspected: 8, source_candidates: 19 },
    direct: { status: "Measured", usage: { total_tokens: 6549000, input_tokens: 5200000, cached_input_tokens: 180000, output_tokens: 1349000 }, files_inspected: 21, success: true },
    comparison: { climate_total: 4932290, direct_total: 6549000, difference: -1616710, percent: -24.7, relative: 0.75, tone: "savings", primary: "Saved 1,616,710 tokens (-24.7%)", secondary: "CLIMATE used 24.7% fewer provider tokens", headline: "Saved 1,616,710 tokens (-24.7%)" },
  },
};

function assistantMessage(stateKey, te) {
  return {
    id: "a-" + stateKey,
    role: "assistant",
    provider: "codex",
    model: "gpt-5.4",
    status: "completed",
    taskMode: "ask",
    runId: "run-te-" + stateKey,
    text: "ANC Binary is derived from visit thresholds.",
    diagnostics: "[climate_context_resolver]\nResolving repo",
    sources: ["app.py"],
    filesInspected: 3,
    elapsedMs: 36000,
    tokenEfficiency: te,
    ts: Date.now() - 1000,
  };
}

async function seed(page, stateKey, te) {
  await page.evaluate(
    ({ stateKey: key, te: payload, msg }) => {
      localStorage.setItem(
        "climate:workspace:v1:work",
        JSON.stringify({
          activeId: "chat-te",
          sessions: [
            {
              id: "chat-te",
              title: "Token efficiency " + key,
              createdAt: Date.now() - 60000,
              updatedAt: Date.now(),
              provider: "codex",
              model: "gpt-5.4",
              lastRunProvider: "codex",
              workspace: "work",
              repositoryId: "",
              messages: [
                {
                  id: "u1",
                  role: "user",
                  text: "Give me the logic of the ANC.\nCite the exact implementation files/functions.\nDo not edit anything.",
                  ts: Date.now() - 40000,
                },
                msg,
              ],
            },
          ],
        })
      );
    },
    { stateKey, te, msg: assistantMessage(stateKey, te) }
  );
}

async function openClimate(page) {
  await page.goto(BASE + "/work/climate", { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForSelector("#climate-ai-feed", { timeout: 120000 });
}

async function snapshot(page, viewport, stateKey) {
  const te = page.locator(".climate-token-efficiency");
  await te.waitFor({ timeout: 15000 });
  const text = ((await te.innerText()) || "").replace(/\s+/g, " ");
  const html = await te.innerHTML();
  const shot = path.join(outDir, viewport + "-" + stateKey + ".png");
  await page.screenshot({ path: shot, fullPage: false });
  return { text, html, shot };
}

async function main() {
  const report = { ok: true, evaluatePostsBeforeClick: 0, states: {}, error: "" };
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    for (const vp of VIEWPORTS) {
      const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
      const ctx = { te: STATES["not-measured"], evaluatePosts: 0 };
      await page.route(/token-efficiency/, async (route) => {
        const req = route.request();
        const url = req.url();
        if (req.method() === "POST" && url.includes("/evaluate")) {
          ctx.evaluatePosts += 1;
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ ok: true, token_efficiency: STATES.measuring }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, token_efficiency: ctx.te }),
        });
      });
      await openClimate(page);
      report.evaluatePostsBeforeClick = ctx.evaluatePosts;
      if (ctx.evaluatePosts !== 0) report.ok = false;
      for (const [stateKey, te] of Object.entries(STATES)) {
        ctx.te = te;
        await seed(page, stateKey, te);
        await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
        await page.waitForSelector(".climate-token-efficiency", { timeout: 20000 });
        const result = await snapshot(page, vp.name, stateKey);
        const checks = [];
        if (stateKey === "not-measured") {
          checks.push(result.text.includes("Not measured"));
          checks.push(result.html.includes("Compare with Direct"));
          checks.push(result.text.includes("493,229") || result.text.includes("493229"));
        }
        if (stateKey === "measuring") {
          checks.push(result.text.includes("Measuring"));
          checks.push(result.html.includes("Cancel"));
          checks.push(result.text.includes("Fresh read-only benchmark running"));
          checks.push((result.text.match(/Measuring/g) || []).length <= 2);
        }
        if (stateKey === "measured") {
          checks.push(result.text.includes("Measured"));
          checks.push(result.text.includes("654,900") || result.text.includes("654900"));
          checks.push(result.text.includes("Saved 161,671 tokens"));
          checks.push(result.text.includes("-24.7%"));
          checks.push(!result.html.includes("Compare with Direct"));
          checks.push((result.text.match(/Saved 161,671 tokens/g) || []).length === 1);
          await page.locator(".climate-te-details summary").click();
          const open = ((await page.locator(".climate-token-efficiency").innerText()) || "");
          checks.push(open.includes("Cached portion"));
          checks.push(open.includes("Candidate sources"));
          checks.push(open.includes("Files actually inspected"));
        }
        if (stateKey === "negative") {
          checks.push(result.text.includes("Used 427,884 more tokens"));
          checks.push(result.text.includes("+152.3%"));
          checks.push(result.text.includes("2.52× Direct"));
          checks.push(!result.text.includes("Saved"));
        }
        if (stateKey === "unavailable") {
          checks.push(result.text.includes("Benchmark unavailable"));
        }
        if (stateKey === "not-comparable") {
          checks.push(result.text.includes("Not comparable"));
          checks.push(result.text.includes("repository commit has changed"));
        }
        if (stateKey === "large") {
          checks.push(result.text.includes("4,932,290") || result.text.includes("4932290"));
        }
        const jsonlLeak = result.html.includes("turn.completed") || result.html.includes("direct_benchmark.jsonl");
        report.states[vp.name + ":" + stateKey] = {
          ok: checks.every(Boolean) && !jsonlLeak,
          checks,
          jsonlLeak,
          text: result.text.slice(0, 400),
          shot: result.shot,
        };
        if (!checks.every(Boolean) || jsonlLeak) report.ok = false;
      }
      report.evaluatePostsBeforeClick = ctx.evaluatePosts;
      if (ctx.evaluatePosts !== 0) report.ok = false;
      ctx.te = STATES["not-measured"];
      ctx.evaluatePosts = 0;
      await seed(page, "not-measured", STATES["not-measured"]);
      await page.reload({ waitUntil: "domcontentloaded", timeout: 120000 });
      await page.waitForSelector("[data-te-action='evaluate']", { timeout: 20000 });
      await page.click("[data-te-action='evaluate']");
      await page.waitForTimeout(400);
      report.states[vp.name + ":click-evaluate"] = {
        ok: ctx.evaluatePosts === 1,
        evaluatePosts: ctx.evaluatePosts,
      };
      if (ctx.evaluatePosts !== 1) report.ok = false;
      await page.close();
    }
  } catch (error) {
    report.ok = false;
    report.error = String(error && error.message ? error.message : error);
  } finally {
    if (browser) await browser.close();
  }
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
}

main();
