import { test, expect } from "@playwright/test";

const ANC = `# ANC / PNC

| Field | UID |
| --- | --- |
| ANC Binary | \`deAbc123XYZ\` |
| PNC Four | \`dePnc456\` |

## Implementation

\`lookup/convergence/derive_anc.py\` — \`derive_anc_score\`

\`\`\`python
def derive_anc_score(ctx):
    return ctx
\`\`\`
`;

const LONG_CODE = "```python\n" + Array.from({ length: 40 }, (_, i) => `print(${i})`).join("\n") + "\n```";

test.describe("CLIMATE Markdown + file viewer", () => {
  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1600, height: 900 },
  ]) {
    test(`ANC/PNC table and long code at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      test.setTimeout(30000);
      await page.setViewportSize(viewport);
      const response = await page.goto("/work/climate", { waitUntil: "domcontentloaded" }).catch(() => null);
      if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");
      await page.waitForFunction(() => window.ClimateMarkdown && typeof window.ClimateMarkdown.render === "function");
      const html = await page.evaluate((md) => window.ClimateMarkdown.render(md), ANC + "\n\n" + LONG_CODE);
      expect(html).toContain("<table");
      expect(html).toContain("<th>");
      expect(html).toContain("deAbc123XYZ");
      expect(html).toContain("language-python");
      expect(html).toContain("data-md-copy");
      expect(html).toContain("data-uid-copy");
      expect(html).toContain("Copy UID");
      expect(html).toContain("data-open-file");
      expect(html).toContain("data-open-symbol");
      expect(html).toContain("lookup/convergence/derive_anc.py");
      const tree = page.locator("#climate-tree .climate-file-row");
      if (await tree.count()) {
        const py = page.locator('#climate-tree .climate-file-row[data-path$=".py"]').first();
        if (await py.count()) {
          await py.click();
          await expect(page.locator("#climate-file-path")).not.toHaveText("Open a file from Explorer");
          await expect(page.locator("#climate-file-readonly")).toBeVisible();
        }
        const readme = page.locator('#climate-tree .climate-file-row[data-path$="README.md"]').first();
        if (await readme.count()) {
          await readme.click();
          await expect(page.locator("#climate-file-modes")).toBeVisible();
          await page.locator('#climate-file-modes [data-file-mode="preview"]').click();
          await expect(page.locator("#climate-md-preview")).toBeVisible();
          await page.locator('#climate-file-modes [data-file-mode="source"]').click();
        }
        const second = page.locator("#climate-tree .climate-file-row").nth(1);
        if (await second.count()) {
          await second.click();
          await expect(page.locator(".climate-tab")).toHaveCount(await page.locator(".climate-tab").count());
        }
      }
    });
  }
});

test.describe("CLIMATE Monaco long-file scroll", () => {
  test("fills editor area, scrolls, and relayouts with the bottom panel", async ({ page }) => {
    test.setTimeout(45000);
    await page.setViewportSize({ width: 1600, height: 900 });
    const longPy = Array.from({ length: 280 }, (_, i) => `print("line ${i + 1}")`).join("\n");
    const longMd = Array.from({ length: 60 }, (_, i) => `## Heading ${i + 1}\n\nParagraph ${i + 1} with enough text to overflow the preview pane.\n`).join("\n");
    await page.route(/\/api\/climate\/[^/]+\/repositories\/[^/]+\/file/, async (route) => {
      const url = new URL(route.request().url());
      const filePath = url.searchParams.get("path") || "";
      const markdown = /\.(md|markdown)$/i.test(filePath);
      const content = markdown ? longMd : longPy;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: {
          ok: true,
          file: {
            content,
            language: markdown ? "markdown" : "python",
            binary: false,
            size: content.length,
            empty: false,
            editable: false,
          },
        },
      });
    });
    const response = await page.goto("/work/climate", { waitUntil: "domcontentloaded" }).catch(() => null);
    if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");
    const tree = page.locator("#climate-tree .climate-file-row");
    try {
      await tree.first().waitFor({ timeout: 12000 });
    } catch (_) {
      test.skip(true, "Explorer files not available");
    }
    const py = page.locator('#climate-tree .climate-file-row[data-path$=".py"]').first();
    if (!(await py.count())) test.skip(true, "No Python file in Explorer");
    await py.click();
    const monacoReady = await page.waitForSelector("#climate-monaco .monaco-editor", { timeout: 20000 }).catch(() => null);
    if (!monacoReady) test.skip(true, "Monaco did not load");
    await expect(page.locator("#climate-file-readonly")).toBeVisible();
    await page.waitForTimeout(120);

    async function metrics() {
      return page.evaluate(() => {
        const host = document.getElementById("climate-monaco");
        const box = document.getElementById("climate-editor-host");
        const editor = host && host.querySelector(".monaco-editor");
        const slider = host && host.querySelector(".scrollbar.vertical .slider");
        const track = host && host.querySelector(".scrollbar.vertical");
        const line = host && host.querySelector(".view-lines .view-line");
        const hostR = host.getBoundingClientRect();
        const boxR = box.getBoundingClientRect();
        const preview = document.getElementById("climate-md-preview");
        return {
          hostHidden: !!(host && host.hidden),
          hostH: Math.round(hostR.height),
          boxH: Math.round(boxR.height),
          editorH: editor ? Math.round(editor.getBoundingClientRect().height) : 0,
          sliderH: slider ? Math.round(slider.getBoundingClientRect().height) : 0,
          trackH: track ? Math.round(track.getBoundingClientRect().height) : 0,
          firstLine: line ? line.textContent : "",
          previewHidden: !!(preview && preview.hidden),
          previewOverflow: preview ? getComputedStyle(preview).overflowY : "",
          previewClient: preview ? preview.clientHeight : 0,
          previewScroll: preview ? preview.scrollHeight : 0,
        };
      });
    }

    const openBottom = await metrics();
    expect(openBottom.hostHidden).toBe(false);
    expect(openBottom.hostH).toBeGreaterThan(80);
    expect(Math.abs(openBottom.hostH - openBottom.boxH)).toBeLessThan(3);
    expect(Math.abs(openBottom.editorH - openBottom.hostH)).toBeLessThan(3);
    expect(openBottom.sliderH).toBeGreaterThan(8);
    expect(openBottom.sliderH).toBeLessThan(openBottom.trackH - 4);

    await page.locator("#climate-monaco").hover();
    await page.mouse.wheel(0, 2400);
    await page.waitForTimeout(80);
    const scrolled = await metrics();
    expect(scrolled.firstLine).not.toBe(openBottom.firstLine);

    await page.locator("#climate-toggle-bottom").click();
    await page.waitForTimeout(120);
    const closedBottom = await metrics();
    expect(closedBottom.hostH).toBeGreaterThan(openBottom.hostH + 20);
    expect(Math.abs(closedBottom.editorH - closedBottom.hostH)).toBeLessThan(3);
    expect(closedBottom.sliderH).toBeGreaterThan(8);
    expect(closedBottom.sliderH).toBeLessThan(closedBottom.trackH - 4);

    const md = page.locator('#climate-tree .climate-file-row[data-path$=".md"]').filter({ visible: true }).first();
    if (await md.count()) {
      await md.click();
    } else {
      const opened = await page.evaluate(() => {
        const row = document.querySelector('#climate-tree .climate-file-row[data-path$=".md"]');
        if (!row) return false;
        row.click();
        return true;
      });
      if (!opened) return;
    }
    await expect(page.locator("#climate-file-modes")).toBeVisible();
    await page.locator('#climate-file-modes [data-file-mode="preview"]').click();
    await expect(page.locator("#climate-md-preview")).toBeVisible();
    const preview = await metrics();
    expect(preview.hostHidden).toBe(true);
    expect(preview.previewHidden).toBe(false);
    expect(["auto", "scroll"]).toContain(preview.previewOverflow);
    expect(preview.previewScroll).toBeGreaterThan(preview.previewClient);
    await page.evaluate(() => {
      const el = document.getElementById("climate-md-preview");
      el.scrollTop = el.scrollHeight;
    });
    const previewScrolled = await page.evaluate(() => document.getElementById("climate-md-preview").scrollTop);
    expect(previewScrolled).toBeGreaterThan(20);
  });
});

test.describe("CLIMATE clickable file references", () => {
  test("opens the read-only viewer and jumps near the function", async ({ page }) => {
    test.setTimeout(45000);
    await page.setViewportSize({ width: 1600, height: 900 });
    const lines = Array.from({ length: 21 }, (_, i) => `value_${i} = ${i}`);
    lines.push("def derive_anc_score(ctx):");
    lines.push("    return ctx");
    const content = lines.join("\n") + "\n";
    await page.route(/\/api\/climate\/[^/]+\/repositories\/[^/]+\/file/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: {
          ok: true,
          file: {
            content,
            language: "python",
            binary: false,
            size: content.length,
            empty: false,
            editable: false,
          },
        },
      });
    });
    await page.route(/\/api\/climate\/[^/]+\/repositories\/[^/]+\/search/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: {
          ok: true,
          q: "derive_anc_score",
          mode: "content",
          count: 1,
          matches: [{ path: "lookup/convergence/derive_anc.py", line: 22, snippet: "def derive_anc_score(ctx):" }],
        },
      });
    });
    const response = await page.goto("/work/climate", { waitUntil: "domcontentloaded" }).catch(() => null);
    if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");
    await page.waitForFunction(() => window.ClimateMarkdown && typeof window.ClimateMarkdown.render === "function");
    await page.evaluate(() => {
      const html = window.ClimateMarkdown.render("`lookup/convergence/derive_anc.py` — `derive_anc_score`");
      const feed = document.getElementById("climate-ai-feed");
      feed.innerHTML = '<div class="climate-md climate-chat-text">' + html + "</div>";
    });
    const chip = page.locator(".climate-file-ref[data-open-file]").first();
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("data-open-symbol", "derive_anc_score");
    await chip.click();
    const monacoReady = await page.waitForSelector("#climate-monaco .monaco-editor", { timeout: 20000 }).catch(() => null);
    if (!monacoReady) test.skip(true, "Monaco did not load");
    await expect(page.locator("#climate-file-readonly")).toBeVisible();
    await expect(page.locator("#climate-file-path")).toContainText("derive_anc");
    await page.waitForFunction(() => document.getElementById("climate-line") && document.getElementById("climate-line").textContent === "22", null, { timeout: 8000 });
    await page.evaluate(() => {
      const html = window.ClimateMarkdown.render("Also `hub/climate/file_view.py`");
      const feed = document.getElementById("climate-ai-feed");
      feed.insertAdjacentHTML("beforeend", '<div class="climate-md">' + html + "</div>");
    });
    await page.locator('.climate-file-ref[data-open-file="hub/climate/file_view.py"]').click();
    await expect(page.locator("#climate-file-path")).toContainText("file_view");
  });
});

const ANC_READABILITY = `# ANC / PNC

DE \`JV4XSWHKnaU\` is the ANC Binary data element.

| Field | UID | Window | Implementation |
| --- | --- | --- | --- |
| ANC Binary | \`JV4XSWHKnaU\` | 1st–3rd trimester | \`lookup/convergence/derive_anc.py\` |
| PNC Four | \`iPA4CCa6tFd\` | postnatal window | \`lookup/convergence/derive_pnc.py\` |

- Edge case uses \`Kl5LLsA10rk\`
- Keep \`lookup/convergence/derive_anc.py\` and \`derive_anc_score\`

## Implementation

\`lookup/convergence/derive_anc.py\` — \`derive_anc_score\`
`;

test.describe("CLIMATE Markdown chat readability", () => {
  for (const width of [360, 340]) {
    test(`ANC answer stays readable at AI panel ${width}px`, async ({ page }) => {
      test.setTimeout(30000);
      await page.setViewportSize({ width: 1600, height: 900 });
      const response = await page.goto("/work/climate", { waitUntil: "domcontentloaded" }).catch(() => null);
      if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");
      await page.waitForFunction(() => window.ClimateMarkdown && typeof window.ClimateMarkdown.render === "function");
      const metrics = await page.evaluate(({ md, widthPx }) => {
        const workbench = document.getElementById("climate-workbench");
        workbench.style.setProperty("--right", widthPx + "px");
        workbench.classList.remove("is-ai-closed", "is-ai-collapsed");
        const html = window.ClimateMarkdown.render(md);
        const feed = document.getElementById("climate-ai-feed");
        feed.innerHTML = '<article class="climate-assistant-msg is-assistant"><div class="climate-assistant-body"><div class="climate-assistant-text climate-md">' + html + "</div></div></article>";
        const root = feed.querySelector(".climate-chat-text.climate-md");
        const wrap = root.querySelector(".climate-md-table-wrap");
        const table = wrap && wrap.querySelector("table");
        const td = root.querySelector("td");
        const h2 = root.querySelector("h2");
        const li = root.querySelector("li");
        const uid = root.querySelector(".climate-uid");
        const fileRef = root.querySelector(".climate-file-ref");
        const code = Array.prototype.find.call(root.querySelectorAll("code"), function (el) {
          return !el.closest("pre") && (el.textContent || "").indexOf("derive_anc_score") >= 0;
        });
        function box(el) {
          if (!el) return null;
          const s = getComputedStyle(el);
          return {
            overflowX: s.overflowX,
            whiteSpace: s.whiteSpace,
            paddingTop: parseFloat(s.paddingTop),
            paddingLeft: parseFloat(s.paddingLeft),
            marginTop: parseFloat(s.marginTop),
            marginBottom: parseFloat(s.marginBottom),
            fontWeight: s.fontWeight,
            bg: s.backgroundColor,
            color: s.color,
            border: s.borderTopColor,
            width: Math.round(el.getBoundingClientRect().width),
          };
        }
        return {
          panelWidth: Math.round(document.getElementById("climate-ai").getBoundingClientRect().width),
          wrap: box(wrap),
          wrapClient: wrap ? wrap.clientWidth : 0,
          wrapScroll: wrap ? wrap.scrollWidth : 0,
          tableScroll: table ? table.scrollWidth : 0,
          td: box(td),
          h2: box(h2),
          li: box(li),
          uid: box(uid),
          fileRef: box(fileRef),
          code: box(code),
          uidCount: root.querySelectorAll(".climate-uid").length,
        };
      }, { md: ANC_READABILITY, widthPx: width });

      expect(metrics.panelWidth).toBeGreaterThan(width - 24);
      expect(metrics.wrap.overflowX).toBe("auto");
      expect(metrics.tableScroll).toBeGreaterThanOrEqual(metrics.wrapClient);
      if (width <= 340) {
        expect(metrics.wrapScroll).toBeGreaterThan(metrics.wrapClient + 8);
      }
      expect(metrics.td.whiteSpace).toBe("nowrap");
      expect(metrics.td.paddingTop).toBeGreaterThanOrEqual(8);
      expect(metrics.td.paddingLeft).toBeGreaterThanOrEqual(12);
      expect(metrics.h2.marginTop).toBeGreaterThanOrEqual(16);
      expect(metrics.h2.marginBottom).toBeGreaterThanOrEqual(6);
      expect(metrics.li.marginTop).toBeGreaterThanOrEqual(4);
      expect(metrics.uidCount).toBeGreaterThanOrEqual(3);
      expect(metrics.uid.whiteSpace).toBe("nowrap");
      expect(parseInt(metrics.uid.fontWeight, 10)).toBeGreaterThanOrEqual(600);
      expect(metrics.uid.bg).not.toBe(metrics.code.bg);
      expect(metrics.uid.bg).not.toBe(metrics.fileRef.bg);
      expect(metrics.code.bg).not.toBe(metrics.fileRef.bg);
    });
  }
});

test.describe("CLIMATE execution mode selector", () => {
  async function openClimate(page) {
    const response = await page.goto("/work/climate", { waitUntil: "domcontentloaded" }).catch(() => null);
    if (!response || !response.ok()) test.skip(true, "CLIMATE server not available");
    await page.waitForSelector("#climate-mode-pill", { timeout: 20000 });
    await page.waitForSelector("#climate-execution-mode", { state: "attached", timeout: 20000 });
  }

  async function modeLabel(page) {
    const pressed = page.locator("#climate-mode-pill [data-execution-mode][aria-pressed='true']");
    if (await pressed.count()) return (await pressed.innerText()).trim();
    const trigger = page.locator("#climate-mode-pill .climate-dd-value");
    if (await trigger.count()) return (await trigger.innerText()).trim();
    return page.locator("#climate-execution-mode").evaluate((el) => {
      const opt = el.options[el.selectedIndex];
      return (opt && opt.textContent || el.value || "").trim();
    });
  }

  async function selectMode(page, value) {
    const btn = page.locator(`#climate-mode-pill [data-execution-mode="${value}"]`);
    if (await btn.count()) {
      await btn.click();
      return;
    }
    const trigger = page.locator("#climate-mode-pill .climate-dd-trigger");
    if (await trigger.count()) {
      await trigger.click();
      const option = page.locator(`.climate-dd-option[data-value="${value}"]`).last();
      await option.click();
    } else {
      await page.locator("#climate-execution-mode").selectOption(value);
    }
  }

  async function seedTokenCard(page, executionMode) {
    const te = {
      status: "Not measured",
      reason: "",
      eligible: true,
      execution_mode: executionMode,
      compare_label: executionMode === "direct" ? "Compare with CLIMATE" : "Compare with Direct",
      climate: { total: 1000, preflight_tokens_est: 40 },
      direct: executionMode === "direct"
        ? { usage: { total_tokens: 1000 } }
        : null,
      comparison: null,
    };
    await page.evaluate(({ te: payload, mode }) => {
      localStorage.setItem(
        "climate:workspace:v1:work",
        JSON.stringify({
          activeId: "chat-mode",
          sessions: [
            {
              id: "chat-mode",
              title: "Mode benchmark",
              createdAt: Date.now() - 60000,
              updatedAt: Date.now(),
              provider: "codex",
              model: "gpt-5.4",
              executionMode: mode,
              lastRunProvider: "codex",
              workspace: "work",
              repositoryId: "",
              messages: [
                { id: "u1", role: "user", text: "Explain ANC", ts: Date.now() - 40000 },
                {
                  id: "a1",
                  role: "assistant",
                  provider: "codex",
                  model: "gpt-5.4",
                  status: "completed",
                  taskMode: "ask",
                  executionMode: mode,
                  runId: "run-mode-" + mode,
                  text: "ANC Binary uses visit thresholds.",
                  tokenEfficiency: payload,
                  ts: Date.now() - 1000,
                },
              ],
            },
          ],
        })
      );
    }, { te, mode: executionMode });
  }

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1600, height: 900 },
  ]) {
    test(`selector, persist, labels, overflow at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      test.setTimeout(45000);
      await page.setViewportSize(viewport);
      await page.route(/\/token-efficiency/, async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            token_efficiency: {
              status: "Not measured",
              eligible: true,
              execution_mode: "climate_assisted",
              compare_label: "Compare with Direct",
              climate: { total: 1000 },
              direct: null,
              comparison: null,
            },
          }),
        });
      });
      await openClimate(page);
      await expect(page.locator("#climate-mode-pill")).toBeVisible();
      await expect(page.locator("#climate-provider-panel")).toBeVisible();
      await expect(page.locator("#climate-model-panel")).toBeVisible();
      expect(await modeLabel(page)).toBe("AiriX");
      expect(await page.locator("#climate-mode-pill").getAttribute("title")).toContain("CLIMATE orchestration");

      await selectMode(page, "direct");
      expect(await modeLabel(page)).toBe("Direct");
      expect(await page.locator("#climate-execution-mode")).toHaveValue("direct");
      expect(await page.locator("#climate-mode-pill").getAttribute("title")).toContain("minimal CLIMATE orchestration");

      const overflow = await page.evaluate(() => {
        const footer = document.querySelector(".climate-chat-footer-controls");
        const composer = document.getElementById("climate-chat-composer");
        const pill = document.getElementById("climate-mode-pill");
        function box(el) {
          if (!el) return { overflow: false };
          return {
            overflow: el.scrollWidth > el.clientWidth + 2,
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
          };
        }
        return { footer: box(footer), composer: box(composer), pill: box(pill) };
      });
      expect(overflow.footer.overflow, `footer overflow ${JSON.stringify(overflow.footer)}`).toBe(false);
      expect(overflow.composer.overflow, `composer overflow ${JSON.stringify(overflow.composer)}`).toBe(false);

      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForSelector("#climate-mode-pill", { timeout: 20000 });
      await page.waitForSelector("#climate-execution-mode", { state: "attached", timeout: 20000 });
      await page.waitForFunction(() => {
        const el = document.getElementById("climate-execution-mode");
        return el && el.value === "direct";
      });
      expect(await modeLabel(page)).toBe("Direct");

      await selectMode(page, "climate_assisted");
      expect(await modeLabel(page)).toBe("AiriX");
    });

    test(`benchmark button wording follows mode at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      test.setTimeout(45000);
      await page.setViewportSize(viewport);
      let tePayload = {
        status: "Not measured",
        eligible: true,
        execution_mode: "climate_assisted",
        compare_label: "Compare with Direct",
        climate: { total: 1000, preflight_tokens_est: 40 },
        direct: null,
        comparison: null,
      };
      await page.route(/\/token-efficiency/, async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, token_efficiency: tePayload }),
        });
      });
      await openClimate(page);
      await seedTokenCard(page, "climate_assisted");
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForSelector(".climate-token-efficiency", { timeout: 20000 });
      await expect(page.locator('[data-te-action="evaluate"]')).toHaveText("Compare with Direct");

      tePayload = {
        status: "Not measured",
        eligible: true,
        execution_mode: "direct",
        compare_label: "Compare with CLIMATE",
        climate: { total: null },
        direct: { usage: { total_tokens: 1000 } },
        comparison: null,
      };
      await seedTokenCard(page, "direct");
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForSelector(".climate-token-efficiency", { timeout: 20000 });
      await expect(page.locator('[data-te-action="evaluate"]')).toHaveText("Compare with CLIMATE");
      await expect(page.locator(".climate-token-efficiency")).toContainText("Direct run total");
    });
  }
});
