const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/js/climate.js", "utf8");

function functionSource(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist`);
  const next = source.indexOf("\n  function ", start + 1);
  assert.notEqual(next, -1, `function after ${name} must exist`);
  return source.slice(start, next);
}

const context = {};
vm.createContext(context);
vm.runInContext([
  "isRawProviderLine",
  "looksLikeEditsJson",
  "extractEditsContents",
  "humanizeAnswer",
  "looksLikeProtocolDump",
  "splitRunOutput",
].map(functionSource).join("\n"), context);

const logs = '[thread.started]\n{"type":"turn.started"}\nprovider stderr';
const pending = context.splitRunOutput(logs, "", "ask");
assert.equal(pending.text, "");
assert.equal(pending.diagnostics, logs);

const completed = context.splitRunOutput(logs, "ANC is derived by score_member().", "ask");
assert.equal(completed.text, "ANC is derived by score_member().");
assert.equal(completed.diagnostics, logs);
assert.doesNotMatch(completed.text, /thread\.started|turn\.started|provider stderr/);

console.log("CLIMATE output separation JS: OK");

vm.runInContext([
  "escapeHtml",
  "formatElapsed",
  "collectActivityFiles",
  "extractReadPaths",
  "isSearchCommand",
  "isReadCommand",
  "commandFailed",
  "providerInvestigationFiles",
  "renderActivityComplete",
].map(functionSource).join("\n"), context);

const providerFiles = context.providerInvestigationFiles({
  logs: "[climate_context_resolver_diagnostics]\nsource_files=a.py,b.py,c.py\n[tool] Get-Content pkg\\anc.py (completed)",
  tool_activity: [
    {
      type: "command_execution",
      name: "rg -n ANC pkg/scoring.py",
      status: "completed",
      detail: "pkg/scoring.py:42:def derive_anc():\npkg/other.py:1:child helper",
    },
    {
      type: "command_execution",
      name: "Get-Content pkg\\anc.py",
      status: "completed",
      detail: "def anc():\n    return 1\n",
    },
  ],
});
assert.deepEqual(Array.from(providerFiles).sort(), ["pkg/anc.py"]);

const getContentPath = context.providerInvestigationFiles({
  tool_activity: [
    {
      type: "command_execution",
      name: "Get-Content -Path derive_fic.py",
      status: "completed",
      detail: "from lookup.child_age_correction import x\n",
    },
  ],
});
assert.deepEqual(Array.from(getContentPath), ["derive_fic.py"]);

const searchOnly = context.providerInvestigationFiles({
  tool_activity: [
    {
      type: "command_execution",
      name: "rg -n ANC lookup",
      status: "completed",
      detail: "lookup/derive_anc.py:1:def derive_anc\nlookup/other.py:4:helper",
    },
  ],
});
assert.deepEqual(Array.from(searchOnly), []);

const candidates = Array.from({ length: 21 }, (_, index) => `candidate-${index}.py`);
const completedActivity = context.renderActivityComplete({
  id: "anc-run",
  taskMode: "ask",
  status: "completed",
  elapsedMs: 1000,
  sources: candidates,
  filesInspected: 2,
  searchMatchedFiles: 308,
  activity: { explore: { count: 21 }, steps: [] },
});
assert.match(completedActivity, /Explored 2 files/);
assert.match(completedActivity, /End-to-end runtime/);
assert.doesNotMatch(completedActivity, /Worked for /);
assert.doesNotMatch(completedActivity, /Explored 21 files/);
assert.doesNotMatch(completedActivity, /Explored 308 files/);
assert.match(completedActivity, /308 search matches/);
assert.match(completedActivity, /21 candidate sources/);

const candidatesOnly = context.renderActivityComplete({
  id: "anc-preflight-only",
  taskMode: "ask",
  status: "completed",
  elapsedMs: 1000,
  sources: candidates,
  filesInspected: 0,
  activity: { explore: { count: 21 }, steps: [] },
});
assert.doesNotMatch(candidatesOnly, /Explored /);

const completeFn = functionSource("renderActivityComplete");
assert.match(completeFn, /exploreCount = Number\(msg\.filesInspected\)/);
assert.equal(
  completeFn.split("\n").filter(function (line) {
    return /exploreCount/.test(line) && /sources/.test(line);
  }).length,
  0,
  "completed Explored count must not derive from msg.sources",
);

console.log("CLIMATE provider exploration count JS: OK");

vm.runInContext([
  "filterOutputLines",
  "parseDiagnosticLines",
].map(functionSource).join("\n"), context);

assert.equal(
  context.filterOutputLines("[thread.started]\n{\"type\":\"turn.started\"}\nhello from climate"),
  "hello from climate",
);
assert.equal(context.filterOutputLines("{\"type\":\"item.completed\"}"), "");
assert.doesNotMatch(context.filterOutputLines("thread.started\nprovider stderr"), /thread\.started/);

const diags = context.parseDiagnosticLines(
  'File "pkg/app.py", line 12, in <module>\nSyntaxError: invalid syntax\nok.py:4:1: error: missing comma\n[thread.started]',
  "runtime",
);
assert.equal(diags[0].path, "pkg/app.py");
assert.equal(diags[0].line, 12);
assert.equal(diags[0].source, "runtime");
assert.equal(diags[1].path, "ok.py");
assert.equal(diags[1].line, 4);
assert.equal(diags[1].severity, "error");
assert.equal(diags.length, 2);

console.log("CLIMATE bottom panel output/problems JS: OK");
