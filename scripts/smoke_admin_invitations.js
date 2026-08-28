"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "static", "admin-invitations.js"), "utf8");
const STORAGE_KEY = "redeputy:admin-invite-links:v1";

function storageFixture() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
    values,
  };
}

function rowFixture(key, expiresAt, available = true) {
  const nodes = {
    "[data-invite-link-input]": {value: "", focus() {}, select() {}},
    "[data-invite-link]": {hidden: true},
    "[data-invite-link-missing]": {hidden: false},
    "[data-invite-countdown]": {textContent: ""},
  };
  const [kind, id] = key.split(":");
  return {
    dataset: {inviteKey: key, inviteKind: kind, inviteId: id, inviteExpiresAt: expiresAt, inviteAvailable: String(available)},
    querySelector: (selector) => nodes[selector] || null,
    nodes,
  };
}

function encode(payload) {
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
}

function run({storage, rows, hash = ""}) {
  const replacements = [];
  const disclosures = {};
  const context = {
    document: {
      querySelectorAll: (selector) => selector === "[data-invite-row]" ? rows : [],
      querySelector: (selector) => {
        if (selector.includes("data-admin-disclosure-key")) {
          disclosures[selector] ||= {setAttribute(name) { this[name] = true; }};
          return disclosures[selector];
        }
        return null;
      },
      addEventListener() {},
      execCommand() {},
    },
    navigator: {clipboard: {writeText: async () => {}}},
    window: {
      atob: (value) => Buffer.from(value, "base64").toString("utf8"),
      location: {origin: "https://redeputy.example", pathname: "/admin", search: "?notice=Created", hash},
      history: {state: {fixture: true}, replaceState: (state, title, url) => replacements.push({state, title, url})},
      sessionStorage: storage,
      setInterval() {},
    },
  };
  vm.runInNewContext(source, context);
  return {api: context.window.__redeputyAdminInvitations, replacements, disclosures};
}

const storage = storageFixture();
const expiresAt = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
const token = "A".repeat(54);
const payload = {kind: "account", id: 7, token, expiresAt};
let row = rowFixture("account:7", expiresAt);
let loaded = run({storage, rows: [row], hash: `#redeputy-invite=${encode(payload)}`});
assert.strictEqual(loaded.replacements.length, 1);
assert.strictEqual(loaded.replacements[0].url, "/admin?notice=Created", "handoff fragment must be cleaned immediately");
assert.strictEqual(row.nodes["[data-invite-link-input]"].value, `https://redeputy.example/account/invite/${token}`);
assert.strictEqual(row.nodes["[data-invite-link]"].hidden, false);
assert.strictEqual(row.nodes["[data-invite-link-missing]"].hidden, true);
assert.match(row.nodes["[data-invite-countdown]"].textContent, /^Expires in 24h 0m$|^Expires in 23h 60m$/);
assert.ok(storage.values.get(STORAGE_KEY).includes(token));

row = rowFixture("account:7", expiresAt);
run({storage, rows: [row]});
assert.ok(row.nodes["[data-invite-link-input]"].value.endsWith(token), "same-tab refresh must retain the activation link");

row = rowFixture("account:7", expiresAt, false);
run({storage, rows: [row]});
assert.strictEqual(storage.values.has(STORAGE_KEY), false, "terminal invitation must clear its cached token");

storage.setItem(STORAGE_KEY, JSON.stringify({"contractor:9": {token: "B".repeat(54), expiresAt}}));
run({storage, rows: []});
assert.strictEqual(storage.values.has(STORAGE_KEY), false, "a removed invitation row must clear its cached token");

loaded = run({storage: storageFixture(), rows: []});
assert.strictEqual(loaded.api.decodeHandoff("not-base64"), null);
assert.strictEqual(loaded.api.countdownText(new Date(Date.now() - 1000).toISOString()), "Expired");

console.log("Admin invitation fragment/session lifecycle smoke ok");
