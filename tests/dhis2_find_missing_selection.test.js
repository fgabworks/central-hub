const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class MemoryStorage {
  constructor() {
    this.map = new Map();
  }
  getItem(key) {
    return this.map.has(key) ? this.map.get(key) : null;
  }
  setItem(key, value) {
    this.map.set(key, String(value));
  }
}

function fromVm(value) {
  return JSON.parse(JSON.stringify(value));
}

const source = fs.readFileSync("static/js/dhis2_find_missing.js", "utf8");
const context = {
  window: {},
  globalThis: {},
  sessionStorage: new MemoryStorage(),
  document: {
    addEventListener() {},
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context);

const api = context.window.__dhis2FindMissingSelection;
assert.equal(typeof api.createSelectionStore, "function");

const storage = new MemoryStorage();
const store = api.createSelectionStore("test-scan", storage);

assert.deepEqual(fromVm(store.toArray()), []);
assert.equal(store.size(), 0);

store.selectVisible(["A", "B"]);
assert.deepEqual(fromVm(store.toArray()), ["A", "B"]);
assert.equal(store.size(), 2);

// Survive "pagination": keep A/B while selecting page 2
store.selectVisible(["C"]);
assert.deepEqual(fromVm(store.toArray()), ["A", "B", "C"]);

store.deselectVisible(["A"]);
assert.deepEqual(fromVm(store.toArray()), ["B", "C"]);

store.selectAllFiltered(["A", "B", "C", "D"]);
assert.deepEqual(fromVm(store.toArray()), ["A", "B", "C", "D"]);
assert.equal(store.size(), 4);

const statePartial = fromVm(store.visibleState(["A", "Z"]));
assert.equal(statePartial.some, true);
assert.equal(statePartial.all, false);

store.clear();
assert.deepEqual(fromVm(store.toArray()), []);
assert.equal(store.size(), 0);

// Persistence across store instances (filter/page reload)
store.selectAllFiltered(["X", "Y"]);
const reloaded = api.createSelectionStore("test-scan", storage);
assert.deepEqual(fromVm(reloaded.toArray()), ["X", "Y"]);

assert.match(source, /preview-selected/);
assert.match(source, /selectAllFiltered/);
assert.match(source, /selectVisible/);
assert.match(source, /sessionStorage/);

console.log("dhis2_find_missing selection tests ok");
