/**
 * Verify CLIMATE dropdown theming + typography (VANTA/ARCTIC).
 * Usage: node scripts/climate_dropdown_theme_inspect.js [label]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CLIMATE_BASE_URL || "http://127.0.0.1:8091";
const label = process.argv[2] || "after";
const outDir = path.join("tmp", "climate-ui", `dropdown-${label}`);
fs.mkdirSync(outDir, { recursive: true });

function parseRgb(str) {
  if (!str) return null;
  const m = String(str).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!m) return null;
  return { r: +m[1], g: +m[2], b: +m[3] };
}

function isDark(rgb) {
  if (!rgb) return false;
  const luma = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
  return luma < 90;
}

function isLightText(rgb) {
  if (!rgb) return false;
  const luma = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
  return luma > 160;
}

async function measureTheme(page, route, name) {
  await page.goto(BASE + route, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(500);

  const base = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const cs = (el, prop) => (el ? getComputedStyle(el).getPropertyValue(prop) : "");
    const ui = q(".climate-chat-title, .climate-chat-label, .climate-ai-empty");
    const tech = q("#climate-token-label, .climate-chat-file, #climate-provider-panel, .climate-dd-value");
    const chat = q(".climate-chat-text, .climate-ai-empty span, #climate-prompt");
    return {
      shellFont: cs(q(".climate-shell"), "font-family"),
      uiFont: cs(ui, "font-family"),
      techFont: cs(tech, "font-family"),
      chatFont: cs(chat, "font-family"),
      colorScheme: cs(q(".climate-shell"), "color-scheme"),
      hasCustomDd: !!q(".climate-dd"),
      providerDd: !!q('#climate-provider-panel') || !!q('.climate-dd[data-for="climate-provider-panel"]'),
    };
  });

  // Open provider dropdown (custom preferred)
  const customTrigger = page.locator('.climate-dd[data-for="climate-provider-panel"] .climate-dd-trigger, .climate-chat-footer-controls .climate-dd-trigger').first();
  const nativeSelect = page.locator("#climate-provider-panel");
  let openStats = null;
  if (await customTrigger.count()) {
    await customTrigger.click();
    await page.waitForTimeout(200);
    openStats = await page.evaluate(() => {
      const menu = document.querySelector(".climate-dd-menu:not([hidden])") || document.querySelector(".climate-dd.is-open .climate-dd-menu");
      if (!menu) return null;
      const cs = getComputedStyle(menu);
      const opt = menu.querySelector(".climate-dd-option, [role='option']");
      const ocs = opt ? getComputedStyle(opt) : null;
      const hover = menu.querySelector(".climate-dd-option.is-active, .climate-dd-option[aria-selected='true']") || opt;
      const hcs = hover ? getComputedStyle(hover) : null;
      return {
        menuBg: cs.backgroundColor,
        menuColor: cs.color,
        menuBorder: cs.borderColor,
        optionColor: ocs ? ocs.color : null,
        optionBg: ocs ? ocs.backgroundColor : null,
        selectedBg: hcs ? hcs.backgroundColor : null,
        selectedColor: hcs ? hcs.color : null,
        menuFont: cs.fontFamily,
        optionFont: ocs ? ocs.fontFamily : null,
        visible: menu.getBoundingClientRect().height > 0,
      };
    });
    await page.locator("#climate-ai").screenshot({ path: path.join(outDir, `${name}_provider_open.png`) }).catch(() => {});
    await page.keyboard.press("Escape");
  } else if (await nativeSelect.count()) {
    // Fallback: style probe on closed select + option element styles
    openStats = await page.evaluate(() => {
      const sel = document.querySelector("#climate-provider-panel");
      const cs = getComputedStyle(sel);
      const opt = sel && sel.options[sel.selectedIndex];
      // option computed style often unavailable for popup; report select surface
      return {
        menuBg: cs.backgroundColor,
        menuColor: cs.color,
        menuBorder: cs.borderColor,
        optionColor: cs.color,
        optionBg: cs.backgroundColor,
        selectedBg: cs.backgroundColor,
        selectedColor: cs.color,
        menuFont: cs.fontFamily,
        optionFont: cs.fontFamily,
        visible: true,
        nativeOnly: true,
      };
    });
  }

  return {
    route,
    bodyTheme: await page.evaluate(() => document.body.className),
    base,
    openStats,
    darkMenu: openStats ? true : false,
  };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const report = { label, base: BASE, pages: {} };

  for (const [route, name] of [
    ["/work/climate", "vanta"],
    ["/personal/climate", "arctic"],
  ]) {
    const m = await measureTheme(page, route, name);
    const menuBg = parseRgb(m.openStats && m.openStats.menuBg);
    const menuFg = parseRgb(m.openStats && (m.openStats.optionColor || m.openStats.menuColor));
    m.pass = {
      darkMenuBg: isDark(menuBg),
      lightMenuText: isLightText(menuFg),
      customDropdown: !!m.base.hasCustomDd,
      uiNotMono: !/mono|consolas|jetbrains|cascadia/i.test(m.base.uiFont || ""),
      techIsMono: /mono|consolas|jetbrains|cascadia/i.test(m.base.techFont || ""),
      optionTechMono: !m.openStats || /mono|consolas|jetbrains|cascadia/i.test(m.openStats.optionFont || ""),
      chatNotMono: !/mono|consolas|jetbrains|cascadia/i.test(m.base.chatFont || ""),
      colorSchemeDark: /dark/i.test(m.base.colorScheme || ""),
    };
    report.pages[name] = m;
  }

  report.passAll = Object.values(report.pages).every((p) => Object.values(p.pass).every(Boolean));
  fs.writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
  if (!report.passAll) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
