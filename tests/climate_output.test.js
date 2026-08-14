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
