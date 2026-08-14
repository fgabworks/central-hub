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
