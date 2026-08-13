const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage();
  const hits = [];
  let runState = "running";
  await p.route("**/api/climate/**", async (route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();
    hits.push(method + " " + url.split("/api/climate")[1]);
    if (method === "POST" && /\/repositories\/[^/]+\/runs$/.test(url)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          run: {
            id: "r1",
            status: "running",
            provider: "codex",
            model: "gpt-5.4-mini",
            task_mode: "ask",
            logs: "Reading a.py\n",
            answer: "",
            usage: {},
            proposal: null,
            created_at: Date.now(),
          },
        }),
      });
    }
    if (method === "GET" && url.includes("/runs/r1")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          run: {
            id: "r1",
            status: runState,
            provider: "codex",
            model: "gpt-5.4-mini",
            task_mode: "ask",
            logs: "Reading a.py\n",
            answer: "",
            usage: {},
            proposal: null,
          },
        }),
      });
    }
    if (method === "POST" && url.includes("/cancel")) {
      runState = "cancelled";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          run: {
            id: "r1",
            status: "cancelled",
            provider: "codex",
            model: "gpt-5.4-mini",
            logs: "[cancelled]",
            answer: "partial",
            usage: {},
          },
        }),
      });
    }
    return route.continue();
  });
  await p.goto("http://127.0.0.1:8098/work/climate", { waitUntil: "domcontentloaded" });
  await p.waitForSelector("#climate-prompt");
  await p.waitForTimeout(500);
  const pre = await p.evaluate(() => {
    const repo = document.getElementById("climate-repository");
    if (repo && repo.options.length) {
      repo.selectedIndex = 0;
      repo.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const provider = document.getElementById("climate-provider");
    const panelProvider = document.getElementById("climate-provider-panel");
    if (provider) {
      if (![...provider.options].some((o) => o.value === "codex")) {
        const opt = document.createElement("option");
        opt.value = "codex";
        opt.textContent = "Codex";
        provider.appendChild(opt);
      }
      provider.value = "codex";
    }
    if (panelProvider) {
      if (![...panelProvider.options].some((o) => o.value === "codex")) {
        const opt = document.createElement("option");
        opt.value = "codex";
        opt.textContent = "Codex";
        panelProvider.appendChild(opt);
      }
      panelProvider.value = "codex";
    }
    const model = document.getElementById("climate-model");
    const panelModel = document.getElementById("climate-model-panel");
    function ensureModel(select) {
      if (!select) return;
      if (![...select.options].some((o) => o.value === "gpt-5.4-mini")) {
        const o = document.createElement("option");
        o.value = "gpt-5.4-mini";
        o.textContent = "gpt-5.4-mini";
        select.appendChild(o);
      }
      select.value = "gpt-5.4-mini";
      select.disabled = false;
    }
    ensureModel(model);
    ensureModel(panelModel);
    const send = document.getElementById("climate-send");
    if (send) {
      send.disabled = false;
      send.hidden = false;
    }
    return {
      repo: repo && repo.value,
      provider: provider && provider.value,
      model: model && model.value,
      sendDisabled: send && send.disabled,
    };
  });
  console.log("pre", pre);
  await p.fill("#climate-prompt", "Explain ANC");
  await p.click("#climate-send", { force: true });
  await p.waitForTimeout(2000);
  console.log("hits", hits);
  console.log(
    "ui",
    await p.evaluate(() => ({
      stopHidden: document.getElementById("climate-stop").hidden,
      sendHidden: document.getElementById("climate-send").hidden,
      status: document.getElementById("climate-status").textContent,
      feed: (document.getElementById("climate-ai-feed").innerText || "").slice(0, 250),
    }))
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
