const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/js/climate.js", "utf8");
const html = fs.readFileSync("templates/climate.html", "utf8");

function functionSource(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist`);
  const next = source.indexOf("\n  function ", start + 1);
  assert.notEqual(next, -1, `function after ${name} must exist`);
  return source.slice(start, next);
}

const context = {};
vm.createContext(context);
vm.runInContext(
  functionSource("languageId") +
  functionSource("isMarkdownPath") +
  functionSource("applyReadOnlyFile") +
  functionSource("shouldShowFileResponse"),
  context
);

assert.equal(context.languageId("python", "app.py"), "python");
assert.equal(context.languageId("", "file.tsx"), "typescript");
assert.equal(context.languageId("", "file.jsx"), "javascript");
assert.equal(context.languageId("", "config.toml"), "ini");
assert.equal(context.languageId("", "settings.ini"), "ini");
assert.equal(context.languageId("", ".env.example"), "ini");
assert.equal(context.languageId("markdown", "README.md"), "markdown");
assert.equal(context.languageId("json", "AI_REFERENCE/reference-json/data-element.json"), "json");
assert.equal(context.isMarkdownPath("README.md", "markdown"), true);
assert.equal(context.isMarkdownPath("AI_REFERENCE.md", "markdown"), true);
assert.equal(context.isMarkdownPath("app.py", "python"), false);

const jsonTab = context.applyReadOnlyFile({}, {
  content: '{"id":"deAbc123XYZ"}\n',
  language: "json",
  binary: false,
  size: 21,
});
assert.equal(jsonTab.content, '{"id":"deAbc123XYZ"}\n');
assert.equal(jsonTab.loaded, true);
assert.equal(jsonTab.unavailable, false);
assert.equal(jsonTab.empty, false);
assert.equal(jsonTab.binary, false);

const mdTab = context.applyReadOnlyFile({ viewMode: "source" }, {
  content: "# Title\n\nHello.\n",
  language: "markdown",
  binary: false,
  size: 16,
});
assert.equal(mdTab.content, "# Title\n\nHello.\n");
assert.equal(mdTab.viewMode, "source");
const preview = Object.assign({}, mdTab, { viewMode: "preview" });
assert.equal(preview.content, mdTab.content);

const pyTab = context.applyReadOnlyFile({}, {
  content: "value = 1\n",
  language: "python",
  binary: false,
  size: 10,
});
assert.equal(pyTab.content, "value = 1\n");

const models = {
  "a.json": context.applyReadOnlyFile({ path: "a.json" }, { content: "{}\n", language: "json", binary: false, size: 3 }),
  "b.py": context.applyReadOnlyFile({ path: "b.py" }, { content: "x=1\n", language: "python", binary: false, size: 4 }),
};
assert.equal(models["a.json"].content, "{}\n");
assert.equal(models["b.py"].content, "x=1\n");

assert.equal(context.shouldShowFileResponse("a.json", "a.json"), true);
assert.equal(context.shouldShowFileResponse("b.py", "a.json"), false);

const staleActive = "b.py";
const staleRequested = "a.json";
assert.equal(context.shouldShowFileResponse(staleActive, staleRequested), false);

const failed = context.applyReadOnlyFile({}, { error: "not found", content: "", binary: false });
assert.equal(failed.unavailable, true);
assert.match(failed.error, /Unable to read file: not found/);
assert.equal(failed.content, "");
assert.equal(failed.empty, false);

const empty = context.applyReadOnlyFile({}, { content: "", binary: false, size: 0, language: "plaintext" });
assert.equal(empty.empty, true);
assert.equal(empty.unavailable, false);

const binary = context.applyReadOnlyFile({}, { binary: true, content: "nope", error: "Preview unavailable for this file type" });
assert.equal(binary.content, "");
assert.equal(binary.unavailable, true);

const capture = functionSource("captureActive");
assert.match(capture, /saveTabViewState\(currentTab\(\)\)/);
assert.doesNotMatch(capture, /tab\.content\s*=/);
assert.doesNotMatch(capture, /editorValue\(\)/);

const layoutSrc = functionSource("layoutEditor");
assert.match(layoutSrc, /editor\.layout\(\{\s*width:\s*width,\s*height:\s*height\s*\}\)/);
assert.match(source, /function scheduleEditorLayout\(/);
assert.match(source, /window\.addEventListener\("resize"/);
assert.match(source, /scheduleEditorLayout\(\)/);
assert.match(source, /saveViewState\(\)/);
assert.match(source, /restoreViewState\(/);
assert.match(source, /vertical:\s*"visible"/);
assert.match(source, /alwaysConsumeMouseWheel:\s*true/);
assert.doesNotMatch(source, /if\s*\(editor\)\s*editor\.layout\(\)/);
assert.doesNotMatch(source, /if\(editor\)editor\.layout\(\)/);

const css = fs.readFileSync("static/css/climate.css", "utf8");
assert.match(css, /\.climate-editor-host\s*\{[^}]*overflow:\s*hidden/);
assert.match(css, /\.climate-monaco\s*\{[^}]*overflow:\s*hidden/);
assert.match(css, /\.climate-monaco\s*\{[^}]*height:\s*100%/);
assert.match(css, /\.climate-md-preview\s*\{[^}]*overflow:\s*auto/);
assert.match(css, /\.climate-center\s*\{[^}]*overflow:\s*hidden/);
assert.match(css, /grid-template-rows:\s*36px\s+28px\s+minmax\(0,\s*1fr\)/);
assert.match(html, /climate\.css.*\?v=46/);
assert.match(html, /climate\.js.*\?v=38/);

assert.match(source, /readOnly:\s*true/);
assert.match(source, /domReadOnly:\s*true/);
assert.match(source, /createModel\(text, lang, uri\)/);
assert.match(source, /if \(model.getValue\(\) !== text\) model.setValue\(text\)/);
assert.match(source, /shouldShowFileResponse\(state\.active, path\)/);
assert.match(source, /Unable to read file/);
assert.match(source, /Empty file/);
assert.match(source, /\[climate-viewer\]/);
assert.match(source, /function saveFile\(\) \{\s*setStatus\("Read-only viewer/);
assert.doesNotMatch(source, /preview-save/);
assert.match(html, /climate-md-preview/);
assert.match(html, /climate-monaco/);
assert.match(html, /climate-file-empty/);
assert.match(html, /Source<\/button>/);
assert.match(html, /Preview<\/button>/);

assert.match(source, /function findSymbolLine\(/);
assert.match(source, /function locateSymbolInRepo\(/);
assert.match(source, /search\?mode=content/);
assert.match(source, /data-open-symbol/);
assert.match(source, /closest\("\[data-open-file\]"\)/);

const findCtx = {};
vm.createContext(findCtx);
vm.runInContext(
  functionSource("normalizeRepoPath") + functionSource("findSymbolLine") + functionSource("pickSearchLine"),
  findCtx
);
const sample = "import x\n\ndef helper():\n    return 1\n\ndef anc_trimester_rule_summary(ctx):\n    return ctx\n";
assert.equal(findCtx.findSymbolLine(sample, "anc_trimester_rule_summary"), 6);
assert.equal(findCtx.findSymbolLine(sample, "helper"), 3);
assert.equal(findCtx.findSymbolLine("print(anc_trimester_rule_summary)\n", "anc_trimester_rule_summary"), 1);
assert.equal(findCtx.findSymbolLine(sample, "missing_fn"), 0);
assert.equal(findCtx.pickSearchLine([
  { path: "lookup/a.py", line: 9, snippet: "def anc_trimester_rule_summary(ctx):" },
  { path: "other.py", line: 2, snippet: "anc_trimester_rule_summary" }
], "lookup/a.py", "anc_trimester_rule_summary"), 9);

console.log("CLIMATE read-only viewer JS: OK");
