'use strict';

const assert = require('assert');

const handlers = {};
global.self = {
  location: { origin: 'https://redeputy.example' },
  registration: { showNotification: async () => undefined },
  addEventListener: (name, handler) => { handlers[name] = handler; },
};
global.clients = {};
require('../app/static/service-worker.js');

const click = async ({ target, windows = [], navigateResult, navigateError, focusError }) => {
  const opened = [];
  const focused = [];
  for (const client of windows) {
    client.navigate = async (url) => {
      if (navigateError) throw new Error('navigate failed');
      if (navigateResult === null) return null;
      return {
        focus: async () => {
          if (focusError) throw new Error('focus failed');
          focused.push(url);
          return { url };
        },
      };
    };
  }
  global.clients.matchAll = async () => windows;
  global.clients.openWindow = async (url) => { opened.push(url); return { url }; };
  let pending;
  handlers.notificationclick({
    notification: { data: { url: target }, close: () => undefined },
    waitUntil: (promise) => { pending = promise; },
  });
  await pending;
  return { opened, focused };
};

(async () => {
  const sameOrigin = { url: 'https://redeputy.example/month' };
  assert.deepStrictEqual(await click({ target: '/day/2026-08-24', windows: [sameOrigin] }), {
    opened: [], focused: ['/day/2026-08-24'],
  });
  assert.deepStrictEqual((await click({ target: '/month', windows: [sameOrigin], navigateResult: null })).opened, ['/month']);
  assert.deepStrictEqual((await click({ target: '/month', windows: [sameOrigin], navigateError: true })).opened, ['/month']);
  assert.deepStrictEqual((await click({ target: '/month', windows: [sameOrigin], focusError: true })).opened, ['/month']);
  assert.deepStrictEqual((await click({ target: 'https://evil.example/steal' })).opened, ['/month']);
  assert.deepStrictEqual((await click({ target: '//evil.example/steal' })).opened, ['/month']);
  assert.deepStrictEqual((await click({ target: '/settings', windows: [{ url: 'https://other.example/month' }] })).opened, ['/settings']);
  console.log('service worker navigation smoke ok');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
