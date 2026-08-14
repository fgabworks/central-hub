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
      detail: "pkg/scoring.py:42:def derive_anc():",
    },
  ],
});
assert.deepEqual(Array.from(providerFiles).sort(), ["pkg/anc.py", "pkg/scoring.py"]);

const candidates = Array.from({ length: 21 }, (_, index) => `candidate-${index}.py`);
const completedActivity = context.renderActivityComplete({
  id: "anc-run",
  taskMode: "ask",
  status: "completed",
  elapsedMs: 1000,
  sources: candidates,
  filesInspected: 2,
  activity: { explore: { count: 21 }, steps: [] },
});
assert.match(completedActivity, /Explored 2 files/);
assert.doesNotMatch(completedActivity, /Explored 21 files/);

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
