/**
 * Compare live CLIMATE UI metrics to the reference mockup image.
 * Usage: node scripts/climate_ref_compare.js [label] [baseUrl]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[3] || process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8080";
const label = process.argv[2] || "ref-before";
const outDir = path.join("tmp", "climate-ui", label);
const REF =
  process.env.CLIMATE_REF ||
  path.join(
    process.env.USERPROFILE || "",
    ".cursor/projects/c-PMNP-personal-central-hub/assets",
    "c__Users_fjdga_AppData_Roaming_Cursor_User_workspaceStorage_0990eee19f2598856270d44f0f78fb62_images_image-d530a99f-d73c-4c57-beab-d2cf60ea1b53.png"
  );

fs.mkdirSync(outDir, { recursive: true });

function rgbToHex(r, g, b) {
  return "#" + [r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

async function samplePage(page) {
  return page.evaluate(() => {
    const cs = (el) => (el ? getComputedStyle(el) : null);
    const box = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return {
        bg: s.backgroundColor,
        color: s.color,
        border: s.borderTopColor,
        radius: s.borderRadius,
        fontSize: s.fontSize,
        fontWeight: s.fontWeight,
        w: Math.round(r.width),
        h: Math.round(r.height),
        gap: s.gap || null,
      };
    };
    const body = document.body;
    const root = getComputedStyle(document.documentElement);
    const bodyCs = getComputedStyle(body);
    const vars = [
      "--climate-bg",
      "--climate-bg-elevated",
      "--climate-panel",
      "--climate-panel-2",
      "--climate-border",
      "--climate-text",
      "--climate-muted",
      "--workspace-accent",
      "--climate-coral",
      "--climate-arctic",
      "--success",
      "--warning",
      "--sidebar-w",
      "--topbar-h",
      "--radius",
      "--charcoal",
      "--panel",
      "--border",
      "--text",
      "--muted",
    ];
    const tokens = {};
    vars.forEach((v) => {
      tokens[v] = (bodyCs.getPropertyValue(v) || root.getPropertyValue(v) || "").trim();
    });
    const cards = [...document.querySelectorAll(".card, .panel, .stat-card")].slice(0, 6).map(box);
    const content = document.querySelector(".content");
    let contentGap = null;
    if (content) {
      const kids = [...content.children].filter((c) => c.offsetParent !== null);
      if (kids.length >= 2) {
        contentGap = Math.round(kids[1].getBoundingClientRect().top - kids[0].getBoundingClientRect().bottom);
      }
    }
    const brandMark = document.querySelector(".brand-mark, .climate-brand-mark, img.brand-logo-mark, .sidebar-brand img");
    const brandText = document.querySelector(".brand-text strong, .brand-wordmark, .climate-brand strong");
    return {
      url: location.href,
      bodyClass: body.className,
      tokens,
      body: box(body),
      sidebar: box(document.querySelector(".sidebar")),
      topbar: box(document.querySelector(".topbar")),
      content: box(content),
      card: cards[0] || null,
      cards,
      contentGap,
      navActive: box(document.querySelector(".nav-link.is-active")),
      workspaceActive: box(document.querySelector(".workspace-btn.is-active")),
      brandMark: box(brandMark),
      brandText: brandText
        ? {
            ...box(brandText),
            text: brandText.textContent.trim(),
          }
        : null,
      climateShell: box(document.querySelector(".climate-shell")),
      climateExplorer: box(document.querySelector(".climate-explorer")),
      climateCenter: box(document.querySelector(".climate-center")),
      climateAi: box(document.querySelector(".climate-ai")),
      climateTitlebar: box(document.querySelector(".climate-titlebar")),
    };
  });
}

async function sampleReferencePixels(page, refPath) {
  if (!fs.existsSync(refPath)) return { error: "ref missing", path: refPath };
  const dataUrl = "data:image/png;base64," + fs.readFileSync(refPath).toString("base64");
  await page.setContent(`<!doctype html><html><body style="margin:0;background:#000">
    <canvas id="c"></canvas>
    <img id="i" src="${dataUrl}"/>
    <script>
      window.__ready = new Promise((resolve) => {
        const img = document.getElementById('i');
        img.onload = () => {
          const c = document.getElementById('c');
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          const ctx = c.getContext('2d');
          ctx.drawImage(img, 0, 0);
          const at = (x, y) => {
            const d = ctx.getImageData(Math.floor(x), Math.floor(y), 1, 1).data;
            return { r:d[0], g:d[1], b:d[2], hex: '#'+[d[0],d[1],d[2]].map(n=>n.toString(16).padStart(2,'0')).join('') };
          };
          const w = img.naturalWidth, h = img.naturalHeight;
          // Sample regions typical of this mockup
          window.__ref = {
            size: { w, h },
            pageBg: at(w * 0.55, h * 0.12),
            sidebarBg: at(w * 0.04, h * 0.35),
            headerBg: at(w * 0.55, h * 0.045),
            cardBg: at(w * 0.28, h * 0.18),
            cardBg2: at(w * 0.55, h * 0.42),
            borderLikely: at(w * 0.22, h * 0.155),
            vantaAccent: at(w * 0.22, h * 0.62),
            arcticAccent: at(w * 0.62, h * 0.62),
            textPrimary: at(w * 0.28, h * 0.08),
            mutedText: at(w * 0.40, h * 0.085),
          };
          resolve(true);
        };
      });
    </script></body></html>`);
  await page.waitForFunction(() => window.__ref);
  return page.evaluate(() => window.__ref);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { label, base: BASE, refPath: REF, reference: null, pages: {} };

  report.reference = await sampleReferencePixels(page, REF);

  const routes = [
    { route: "/work", name: "vanta" },
    { route: "/personal", name: "arctic" },
    { route: "/work/climate", name: "code_workspace" },
    { route: "/personal/climate", name: "code_workspace_arctic" },
  ];

  for (const { route, name } of routes) {
    await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(500);
    report.pages[name] = await samplePage(page);
    await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: false });
  }

  // Copy reference into outDir for side-by-side
  if (fs.existsSync(REF)) {
    fs.copyFileSync(REF, path.join(outDir, "reference.png"));
  }

  const v = report.pages.vanta;
  const a = report.pages.arctic;
  report.compare = {
    surfacesIdentical:
      v.tokens["--climate-bg"] === a.tokens["--climate-bg"] &&
      v.tokens["--climate-panel"] === a.tokens["--climate-panel"] &&
      v.tokens["--climate-border"] === a.tokens["--climate-border"],
    accentsDiffer: v.tokens["--workspace-accent"] !== a.tokens["--workspace-accent"],
    sidebarWidth: v.sidebar?.w,
    topbarHeight: v.topbar?.h,
    cardRadius: v.card?.radius,
    brandMark: v.brandMark,
    vsRef: {
      refPageBg: report.reference?.pageBg,
      livePageBg: v.body?.bg,
      refSidebar: report.reference?.sidebarBg,
      liveSidebar: v.sidebar?.bg,
      refCard: report.reference?.cardBg,
      liveCard: v.card?.bg,
      refVanta: report.reference?.vantaAccent,
      liveVantaAccent: v.tokens["--workspace-accent"],
      refArctic: report.reference?.arcticAccent,
      liveArcticAccent: a.tokens["--workspace-accent"],
    },
  };

  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ label, compare: report.compare, reference: report.reference, vanta: { tokens: v.tokens, body: v.body, sidebar: v.sidebar, topbar: v.topbar, card: v.card, brandMark: v.brandMark, brandText: v.brandText }, code: { shell: report.pages.code_workspace.climateShell, explorer: report.pages.code_workspace.climateExplorer, titlebar: report.pages.code_workspace.climateTitlebar } }, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
