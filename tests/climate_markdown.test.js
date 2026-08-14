const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/js/climate_markdown.js", "utf8");

function loadMarkdown(extras) {
  extras = extras || {};
  const copied = extras.copied || [];
  const context = {
    marked: extras.marked,
    DOMPurify: extras.DOMPurify,
    hljs: extras.hljs || null,
    navigator: {
      clipboard: {
        writeText: function (text) {
          copied.push(text);
          return Promise.resolve();
        }
      }
    }
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context);
  context._copied = copied;
  return context;
}

function gfmStub() {
  return {
    parse(text) {
      let html = String(text);
      html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, function (_, lang, body) {
        const cls = lang ? ` class="language-${lang}"` : "";
        return `<pre><code${cls}>${body.replace(/</g, "&lt;")}</code></pre>`;
      });
      html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
      html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
      html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
      html = html.replace(/^(?:[-*]|\d+\.) (.+)$/gm, "<li>$1</li>");
      html = html.replace(/(?:<li>.*<\/li>\n?)+/g, function (block) {
        return "<ul>" + block + "</ul>";
      });
      html = html.replace(
        /(\|.+\|(?:\r?\n\|[-: ]+\|)?(?:\r?\n\|.+\|)+)/g,
        function (block) {
          const rows = block.split(/\r?\n/).filter((line) => line.includes("|") && !/^\s*\|?\s*-+/.test(line));
          const cells = rows.map((row) => row.split("|").map((c) => c.trim()).filter(Boolean));
          if (cells.length < 2) return block;
          const head = cells[0].map((c) => `<th>${c}</th>`).join("");
          const body = cells.slice(1).map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
          return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
        }
      );
      return html.split(/\n{2,}/).map(function (block) {
        if (/^\s*</.test(block) || !block.trim()) return block;
        return "<p>" + block + "</p>";
      }).join("\n");
    },
  };
}

function purifyStub() {
  return {
    sanitize(html) {
      return String(html)
        .replace(/<script\b[\s\S]*?<\/script>/gi, "")
        .replace(/\son\w+\s*=\s*(['"]).*?\1/gi, "")
        .replace(/javascript:/gi, "");
    },
  };
}

const anc = `# ANC / PNC

DE \`JV4XSWHKnaU\` is the ANC Binary data element.

| Field | UID |
| --- | --- |
| ANC Binary | \`JV4XSWHKnaU\` |
| PNC Four | \`iPA4CCa6tFd\` |

- Edge case uses \`Kl5LLsA10rk\`
- Keep \`lookup/convergence/derive_anc.py\` and \`derive_anc_score\`

## Implementation

\`lookup/convergence/derive_anc.py\` — \`derive_anc_score\`

\`\`\`python
def derive_anc_score(ctx):
    uid = "JV4XSWHKnaU"
    return ctx
\`\`\`

<script>alert(1)</script>
`;

const ctx = loadMarkdown({ marked: gfmStub(), DOMPurify: purifyStub() });
const html = ctx.ClimateMarkdown.render(anc);

assert.match(html, /<table[\s\S]*<th>Field<\/th>/);
assert.match(html, /<td>ANC Binary<\/td>/);
assert.match(html, /<h1>ANC \/ PNC<\/h1>/);
assert.match(html, /<h2>Implementation<\/h2>/);
assert.match(html, /<pre><code class="language-python">/);
assert.match(html, /data-md-copy/);
assert.doesNotMatch(html, /<script/i);
assert.doesNotMatch(html, /alert\(1\)/);
assert.ok(ctx.ClimateMarkdown.ALLOWED_TAGS.includes("table"));
assert.ok(ctx.ClimateMarkdown.ALLOWED_TAGS.includes("pre"));

assert.equal(ctx.ClimateMarkdown.isDhis2Uid("JV4XSWHKnaU"), true);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("iPA4CCa6tFd"), true);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("Kl5LLsA10rk"), true);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("deAbc123XYZ"), true);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("description"), false);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("derive_anc_score"), false);
assert.equal(ctx.ClimateMarkdown.isDhis2Uid("dePnc456"), false);

function uidCount(blob, uid) {
  const re = new RegExp('data-uid-copy[^>]*>' + uid + "<", "g");
  return (String(blob).match(re) || []).length;
}

assert.ok(uidCount(html, "JV4XSWHKnaU") >= 1, "UID chip in paragraph/table");
assert.match(html, /<p>[\s\S]*data-uid-copy[\s\S]*JV4XSWHKnaU/);
assert.match(html, /<li>[\s\S]*data-uid-copy[\s\S]*Kl5LLsA10rk/);
assert.match(html, /<td>[\s\S]*data-uid-copy[\s\S]*JV4XSWHKnaU/);
assert.match(html, /<td>[\s\S]*data-uid-copy[\s\S]*iPA4CCa6tFd/);
assert.ok(uidCount(html, "iPA4CCa6tFd") >= 1);
assert.ok(uidCount(html, "Kl5LLsA10rk") >= 1);
assert.match(html, /title="Copy UID"/);

const preChunk = html.match(/<pre[\s\S]*?<\/pre>/)[0];
assert.match(preChunk, /JV4XSWHKnaU/);
assert.doesNotMatch(preChunk, /climate-uid|data-uid-copy/);

assert.match(html, /<code>lookup\/convergence\/derive_anc\.py<\/code>/);
assert.match(html, /<code>derive_anc_score<\/code>/);
assert.doesNotMatch(html, /data-uid-copy[^>]*>lookup\/convergence\/derive_anc\.py/);
assert.doesNotMatch(html, /data-uid-copy[^>]*>derive_anc_score/);

const falsePositives = ctx.ClimateMarkdown.render(
  "Keep description application information and `derive_anc_score` plus CH_FIC."
);
assert.doesNotMatch(falsePositives, /data-uid-copy/);

const climateJs = fs.readFileSync("static/js/climate.js", "utf8");
assert.match(climateJs, /climate-md/);
assert.match(climateJs, /Candidate Sources/);
assert.match(climateJs, /Details \/ Diagnostics/);
assert.match(climateJs, /enhanceMarkdown\(feed\)/);
assert.match(climateJs, /renderMarkdownHtml\(bodyText\)/);
assert.match(climateJs, /End-to-end runtime/);
assert.match(climateJs, /Provider runtime/);
assert.doesNotMatch(climateJs, /parts\.push\("Worked for /);

function fakeChip(uid) {
  const chip = {
    attrs: {},
    handlers: {},
    textContent: uid,
    classList: {
      names: [],
      add: function (name) { this.names.push(name); },
      remove: function (name) { this.names = this.names.filter(function (n) { return n !== name; }); }
    },
    getAttribute: function (name) { return this.attrs[name]; },
    setAttribute: function (name, value) { this.attrs[name] = value; },
    addEventListener: function (type, fn) { this.handlers[type] = fn; }
  };
  return chip;
}

const copied = [];
const clickCtx = loadMarkdown({ marked: gfmStub(), DOMPurify: purifyStub(), copied: copied });
const chip = fakeChip("JV4XSWHKnaU");
clickCtx.ClimateMarkdown.enhance({
  querySelectorAll: function (sel) {
    if (String(sel).indexOf("data-uid-copy") >= 0) return [chip];
    return [];
  }
});
chip.handlers.click({ preventDefault: function () {}, stopPropagation: function () {} });

Promise.resolve().then(function () {
  assert.deepEqual(copied, ["JV4XSWHKnaU"]);
  assert.equal(chip.attrs.title, "Copied");
  assert.ok(chip.classList.names.indexOf("is-copied") >= 0);
  console.log("CLIMATE markdown renderer: OK");
}).catch(function (err) {
  console.error(err);
  process.exit(1);
});
