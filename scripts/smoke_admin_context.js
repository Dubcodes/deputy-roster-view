"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "static", "admin-context.js"), "utf8");

class HTMLFormElement {
  constructor(action, method = "post", dataset = {}) {
    this.action = action;
    this.method = method;
    this.dataset = dataset;
  }
  getAttribute(name) {
    return name === "action" ? this.action : null;
  }
}

function storageFixture(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    values,
  };
}

function load({details, storage, hash = "", pathname = "/admin"}) {
  const listeners = {};
  const scrolls = [];
  const context = {
    URL,
    HTMLFormElement,
    document: {
      querySelectorAll: (selector) => selector === "details" ? details : [],
      querySelector: (selector) => selector.includes("manual-work-days") ? details.find((item) => item.dataset.adminDisclosureKey === "manual-work-days") || null : null,
      addEventListener: (name, callback) => { listeners[name] = callback; },
    },
    window: {
      location: {origin: "https://redeputy.example", pathname, href: `https://redeputy.example${pathname}${hash}`, hash},
      sessionStorage: storage,
      scrollY: 487,
      scrollTo: (x, y) => scrolls.push([x, y]),
      requestAnimationFrame: (callback) => callback(),
    },
  };
  vm.runInNewContext(source, context);
  return {api: context.window.__redeputyAdminContext, context, listeners, scrolls};
}

const details = [
  {dataset: {adminDisclosureKey: "accounts"}, open: true},
  {dataset: {adminDisclosureKey: "user-8"}, open: true},
  {dataset: {}, open: false},
];
const storage = storageFixture();
let loaded = load({details, storage});
const postForm = new HTMLFormElement("/admin/users/8/pin");
assert.strictEqual(loaded.api.save(postForm), true);
const saved = JSON.parse(storage.values.get(loaded.api.STORAGE_KEY));
assert.deepStrictEqual([...saved.open], ["accounts", "user-8"]);
assert.strictEqual(saved.scrollY, 487);

details.forEach((item) => { item.open = false; });
assert.strictEqual(loaded.api.restore(), true);
assert.strictEqual(details[0].open, true);
assert.strictEqual(details[1].open, true);
assert.deepStrictEqual(loaded.scrolls.at(-1), [0, 487]);
assert.strictEqual(storage.values.has(loaded.api.STORAGE_KEY), false);
assert.strictEqual(loaded.api.restore(), false, "return state must be consumed once");

assert.strictEqual(loaded.api.save(new HTMLFormElement("/admin", "get")), false);
assert.strictEqual(loaded.api.save(new HTMLFormElement("https://evil.example/admin")), false);
assert.strictEqual(loaded.api.save(new HTMLFormElement("/settings")), false);
assert.strictEqual(loaded.api.save(new HTMLFormElement("/admin", "post", {adminNoContext: ""})), false);

storage.setItem(loaded.api.STORAGE_KEY, JSON.stringify({path: "/admin", open: ["accounts"], scrollY: 1, savedAt: 1, expiresAt: 2}));
assert.strictEqual(loaded.api.consume(), null, "expired state must not restore");
assert.strictEqual(storage.values.has(loaded.api.STORAGE_KEY), false, "expired state must still be discarded");

const manual = {dataset: {adminDisclosureKey: "manual-work-days"}, open: false, scrollIntoViewCalled: false, scrollIntoView() { this.scrollIntoViewCalled = true; }};
load({details: [manual], storage: storageFixture(), hash: "#manual-work-days"});
assert.strictEqual(manual.open, true);
assert.strictEqual(manual.scrollIntoViewCalled, true);

console.log("Admin one-shot context restoration smoke ok");
