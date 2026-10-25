import test from 'node:test';
import assert from 'node:assert/strict';
import {cropBounds} from '../chrome_extension/capture.mjs';

test('crop matches CSS frame at device scale factor three', () => {
  assert.deepEqual(cropBounds({x: 100, y: 90, width: 400, height: 600, viewportWidth: 1200, viewportHeight: 800}, 3600, 2400),
    {left: 300, top: 270, width: 1200, height: 1800});
});

test('reject offscreen and invalid geometry instead of clipping a partial page', () => {
  for (const rect of [
    {x: 0, y: -20, width: 400, height: 600, viewportWidth: 1200, viewportHeight: 800},
    {x: 0, y: 300, width: 400, height: 600, viewportWidth: 1200, viewportHeight: 800},
    {x: 0, y: 0, width: NaN, height: 600, viewportWidth: 1200, viewportHeight: 800},
  ]) assert.throws(() => cropBounds(rect, 3600, 2400));
});

test('reject a capture taken at a changed window aspect ratio', () => {
  assert.throws(() => cropBounds({x: 100, y: 90, width: 400, height: 600, viewportWidth: 1200, viewportHeight: 800}, 1200, 600));
});

async function workerHarness({switchTab = false, injectionError = null} = {}) {
  const listeners = {}, calls = [], store = {};
  let active = 17;
  globalThis.chrome = {
    runtime: {onMessage: {addListener(fn) { listeners.message = fn; }}, async getPlatformInfo() {}},
    storage: {session: {
      async get(key) { return {[key]: store[key]}; },
      async set(data) { Object.assign(store, data); },
      async remove(key) { delete store[key]; },
    }},
    tabs: {
      async create() { return {id: 17, windowId: 1}; },
      async query() { return [{id: active}]; },
      async captureVisibleTab() { calls.push('screenshot'); return 'data:image/png;base64,AA=='; },
      onRemoved: {addListener(fn) { listeners.removed = fn; }},
    },
    action: {
      async setBadgeText() {}, async setTitle() {},
      onClicked: {addListener(fn) { listeners.clicked = fn; }},
    },
    scripting: {async executeScript({func}) {
      if (func.name === 'describeDocument' && injectionError === 'permission') {
        throw new Error('Cannot access contents of the page. Extension manifest must request permission to access the respective host.');
      }
      if (func.name === 'describeDocument' && injectionError === 'null') return [{result: null}];
      if (func.name === 'describeDocument' && injectionError === 'document') return [{result: {captureError: 'Chưa tìm thấy khung giấy.'}}];
      if (func.name === 'describeDocument') return [{result: {total: 2}}];
      if (func.name === 'positionPage') {
        if (switchTab) active = 99;
        return [{result: {x: 10, y: 20, width: 400, height: 600, viewportWidth: 1200, viewportHeight: 800, hasContent: true}}];
      }
      return [{result: undefined}];
    }},
  };
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, ...options});
    return {ok: true, async json() {return {target_url: 'https://www.studocu.vn/vn/document/example/123'};}, async blob() {return new Blob();}};
  };
  globalThis.createImageBitmap = async () => ({width: 1200, height: 800, close() {}});
  globalThis.OffscreenCanvas = class {
    getContext() {return {drawImage() {}};}
    async convertToBlob() {return new Blob(['png']);}
  };
  await import(`../chrome_extension/background.js?test=${Math.random()}`);
  const response = await new Promise(resolve => listeners.message(
    {type: 'prepare', jobId: 'abcdef123456', token: 'a'.repeat(43)},
    {url: 'http://127.0.0.1:5000/chrome_capture/abcdef123456', tab: {id: 5, windowId: 1}}, resolve,
  ));
  assert.equal(response.ok, true);
  return {listeners, calls};
}

test('extension sends each screenshot to its authenticated job and completes', async () => {
  const {listeners, calls} = await workerHarness();
  await listeners.clicked({id: 17, windowId: 1, url: 'https://www.studocu.vn/vn/document/example/123'});
  assert.equal(calls.filter(call => call === 'screenshot').length, 2);
  const uploads = calls.filter(call => call.url?.includes('/page/'));
  assert.equal(uploads.length, 2);
  assert.ok(uploads.every(call => call.headers.Authorization === `Bearer ${'a'.repeat(43)}`));
  assert.ok(calls.some(call => call.body && typeof call.body === 'string' && JSON.parse(call.body).event === 'finish'));
});

test('switching tabs aborts before taking or uploading a screenshot', async () => {
  const {listeners, calls} = await workerHarness({switchTab: true});
  await listeners.clicked({id: 17, windowId: 1, url: 'https://www.studocu.vn/vn/document/example/123'});
  assert.equal(calls.filter(call => call === 'screenshot').length, 0);
  assert.equal(calls.filter(call => call.url?.includes('/page/')).length, 0);
  assert.ok(calls.some(call => call.body && typeof call.body === 'string' && JSON.parse(call.body).event === 'error'));
});

test('permission failures include actionable Studocu access instructions', async () => {
  const {listeners, calls} = await workerHarness({injectionError: 'permission'});
  await listeners.clicked({id: 17, windowId: 1, url: 'https://www.studocu.vn/vn/document/example/123'});
  const wait = calls.filter(call => typeof call.body === 'string').map(call => JSON.parse(call.body)).find(body => body.event === 'wait');
  assert.match(wait.message, /chưa được cấp quyền/);
  assert.match(wait.message, /chrome:\/\/extensions/);
  assert.equal(calls.filter(call => call === 'screenshot').length, 0);
});

test('null injection results do not become total destructuring errors', async () => {
  const {listeners, calls} = await workerHarness({injectionError: 'null'});
  await listeners.clicked({id: 17, windowId: 1, url: 'https://www.studocu.vn/vn/document/example/123'});
  const wait = calls.filter(call => typeof call.body === 'string').map(call => JSON.parse(call.body)).find(body => body.event === 'wait');
  assert.match(wait.message, /Không đọc được nội dung/);
  assert.doesNotMatch(wait.message, /destructure/);
});

test('injected DOM failures retain their original diagnostic', async () => {
  const {listeners, calls} = await workerHarness({injectionError: 'document'});
  await listeners.clicked({id: 17, windowId: 1, url: 'https://www.studocu.vn/vn/document/example/123'});
  const wait = calls.filter(call => typeof call.body === 'string').map(call => JSON.parse(call.body)).find(body => body.event === 'wait');
  assert.equal(wait.message, 'Chưa tìm thấy khung giấy.');
});
