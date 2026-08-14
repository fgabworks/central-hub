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
assert.match(capture, /return;/);
assert.doesNotMatch(capture, /tab\.content = editorValue/);

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

console.log("CLIMATE read-only viewer JS: OK");
