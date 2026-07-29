const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("static/js/quick_notepad.js", "utf8");
const context = {
  window: {},
  document: {
    readyState: "loading",
    addEventListener() {},
  },
};
vm.createContext(context);
vm.runInContext(source, context);

const normalize = context.window.__qnNormalizePanelSize;
const escapeAction = context.window.__qnResolveEscapeAction;

assert.equal(typeof normalize, "function");
assert.equal(normalize("maximized"), "maximized");
assert.equal(normalize("Expanded"), "expanded");
assert.equal(normalize("nope"), "normal");

// Escape minimizes from Expanded/Maximized; closes only from Normal.
assert.equal(escapeAction("maximized", true), "minimize");
assert.equal(escapeAction("expanded", true), "minimize");
assert.equal(escapeAction("normal", true), "close");
assert.equal(escapeAction("maximized", false), null);

assert.match(source, /minimizeSize/);
assert.match(source, /qn-minimize/);
assert.match(source, /captureCaret/);
assert.match(source, /restoreCaret/);
assert.match(source, /qn-maximized/);
assert.match(source, /panel_size/);
assert.doesNotMatch(source, /previousSizeMode/);
assert.doesNotMatch(source, /function restoreSize/);
// Revision Restore class remains distinct from drawer Minimize id.
assert.match(source, /\.qn-restore/);
assert.match(source, /id=\"qn-minimize\"|getElementById\(\"qn-minimize\"\)/);

console.log("Quick Notepad size JS: OK");
