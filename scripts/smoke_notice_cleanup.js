"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "one-shot-notice.js"),
  "utf8",
);

function clean(href, hasNotice = true) {
  const replacements = [];
  const context = {
    URL,
    document: {
      querySelector: () => (hasNotice ? {textContent: "Saved"} : null),
    },
    window: {
      location: {href},
      history: {
        state: {fixture: true},
        replaceState: (state, title, url) => replacements.push({state, title, url}),
      },
    },
  };
  vm.runInNewContext(source, context);
  return replacements;
}

let replacements = clean(
  "https://redeputy.example/admin?scope=accounts&notice=Saved&filter=inactive#target",
);
assert.strictEqual(replacements.length, 1);
assert.strictEqual(replacements[0].url, "/admin?scope=accounts&filter=inactive#target");
assert.deepStrictEqual(replacements[0].state, {fixture: true});

replacements = clean("https://redeputy.example/settings?notice=Updated#security");
assert.strictEqual(replacements.length, 1);
assert.strictEqual(replacements[0].url, "/settings#security");

assert.strictEqual(clean("https://redeputy.example/admin?scope=accounts", false).length, 0);
assert.strictEqual(clean("https://redeputy.example/admin?scope=accounts", true).length, 0);

console.log("one-shot notice URL cleanup smoke ok");
