const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const REF = path.join(
  process.env.USERPROFILE || "",
  ".cursor/projects/c-PMNP-personal-central-hub/assets",
  "c__Users_fjdga_AppData_Roaming_Cursor_User_workspaceStorage_0990eee19f2598856270d44f0f78fb62_images_image-d530a99f-d73c-4c57-beab-d2cf60ea1b53.png"
);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const b64 = fs.readFileSync(REF).toString("base64");
  await page.setContent(`<!doctype html><canvas id="c"></canvas><img id="i" src="data:image/png;base64,${b64}">`);
  await page.waitForFunction(() => document.getElementById("i").complete && document.getElementById("i").naturalWidth > 0);
  const samples = await page.evaluate(() => {
    const img = document.getElementById("i");
    const c = document.getElementById("c");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const w = img.naturalWidth;
    const h = img.naturalHeight;
    const at = (x, y) => {
      const d = ctx.getImageData(x | 0, y | 0, 1, 1).data;
      return { hex: "#" + [d[0], d[1], d[2]].map((n) => n.toString(16).padStart(2, "0")).join(""), r: d[0], g: d[1], b: d[2] };
    };
    const avg = (x0, y0, x1, y1) => {
      let r = 0, g = 0, b = 0, n = 0;
      for (let y = y0; y < y1; y += 2) {
        for (let x = x0; x < x1; x += 2) {
          const d = ctx.getImageData(x, y, 1, 1).data;
          // skip bright text/icons
          if (d[0] + d[1] + d[2] > 140) continue;
          if (d[0] > 120 && d[0] > d[1] + 30) continue; // red accents
          if (d[2] > 120 && d[2] > d[0] + 30) continue; // cyan accents
          r += d[0]; g += d[1]; b += d[2]; n++;
        }
      }
      if (!n) return null;
      r = (r / n) | 0; g = (g / n) | 0; b = (b / n) | 0;
      return { hex: "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join(""), r, g, b, n };
    };
    let red = null, cyan = null, greens = [];
    const data = ctx.getImageData(0, 0, w, h).data;
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        const i = (y * w + x) * 4;
        const r = data[i], g = data[i + 1], b = data[i + 2];
        if (!red && r > 170 && r > g + 50 && r > b + 40 && g < 120) red = { x, y, hex: "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join(""), r, g, b };
        if (!cyan && b > 170 && b > r + 40 && g > 140 && g < 230) cyan = { x, y, hex: "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join(""), r, g, b };
        if (g > 150 && g > r + 40 && g > b + 20 && r < 120) greens.push({ hex: "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join(""), r, g, b });
      }
    }
    return {
      size: { w, h },
      pageBg: avg((w * 0.45) | 0, (h * 0.1) | 0, (w * 0.7) | 0, (h * 0.14) | 0),
      sidebar: avg((w * 0.01) | 0, (h * 0.25) | 0, (w * 0.08) | 0, (h * 0.55) | 0),
      cardTop: avg((w * 0.2) | 0, (h * 0.16) | 0, (w * 0.3) | 0, (h * 0.24) | 0),
      cardMid: avg((w * 0.55) | 0, (h * 0.35) | 0, (w * 0.7) | 0, (h * 0.48) | 0),
      vantaPanel: avg((w * 0.2) | 0, (h * 0.58) | 0, (w * 0.4) | 0, (h * 0.75) | 0),
      arcticPanel: avg((w * 0.58) | 0, (h * 0.58) | 0, (w * 0.78) | 0, (h * 0.75) | 0),
      contentGap: avg((w * 0.48) | 0, (h * 0.52) | 0, (w * 0.52) | 0, (h * 0.56) | 0),
      red,
      cyan,
      greenSample: greens[0] || null,
      point: {
        logoBg: at(w * 0.06, h * 0.06),
        header: at(w * 0.5, h * 0.04),
      },
    };
  });
  console.log(JSON.stringify(samples, null, 2));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
