import {describeDocument, positionPage, restoreDocument, cropBounds, showCaptureError} from './capture.mjs';

const BASE = 'http://127.0.0.1:5000';
const running = new Set();

async function api(job, path, options = {}) {
  const response = await fetch(`${BASE}/chrome_capture/${job.jobId}/${path}`, {
    ...options,
    headers: {...options.headers, Authorization: `Bearer ${job.token}`},
  });
  if (!response.ok) {
    if (response.status === 403 || response.status === 409) {
      throw new Error('Job đã hết hạn hoặc ứng dụng đã khởi động lại. Tạo job mới tại 127.0.0.1:5000 và dùng tab tài liệu mới được mở.');
    }
    throw new Error(`Ứng dụng trả lỗi ${response.status} tại bước ${path}.`);
  }
  return response.json();
}

const event = (job, data) => api(job, 'event', {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
});

function isStudocu(url) {
  try {
    const parsed = new URL(url);
    return ['http:', 'https:'].includes(parsed.protocol) &&
      ['studocu.com', 'studocu.vn'].some(domain => parsed.hostname === domain || parsed.hostname.endsWith(`.${domain}`));
  } catch { return false; }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'prepare') return;
  const expected = `${BASE}/chrome_capture/${message.jobId}`;
  if (!/^[a-f0-9]{12}$/.test(message.jobId) || !/^[\w-]{40,100}$/.test(message.token) ||
      !sender.tab || sender.url?.split('#')[0] !== expected) {
    sendResponse({ok: false, error: 'Yêu cầu mở Chrome không hợp lệ.'});
    return;
  }
  (async () => {
    const job = {jobId: message.jobId, token: message.token};
    const info = await api(job, 'info');
    if (!isStudocu(info.target_url)) throw new Error('Job không phải tài liệu Studocu.');
    const tab = await chrome.tabs.create({url: info.target_url, windowId: sender.tab.windowId, active: true});
    await chrome.storage.session.set({[`job:${tab.id}`]: job});
    await chrome.action.setBadgeText({tabId: tab.id, text: 'GO'});
    sendResponse({ok: true});
  })().catch(error => sendResponse({ok: false, error: error.message}));
  return true;
});

async function assertActive(tab) {
  const [active] = await chrome.tabs.query({windowId: tab.windowId, active: true});
  if (active?.id !== tab.id) throw new Error('Tab tài liệu mất tiêu điểm. Hãy giữ tab tài liệu hoạt động khi chụp và tạo job mới.');
}

async function inject(tabId, func, args = []) {
  const [result] = await chrome.scripting.executeScript({target: {tabId}, func, args});
  if (!result) throw new Error(`Chrome không trả kết quả ở bước ${func.name}. Tải lại trang và thử lại.`);
  if (result.error) throw new Error(result.error.message || String(result.error));
  if (result.result?.captureError) throw new Error(result.result.captureError);
  if (result.result == null && func !== restoreDocument && func !== showCaptureError) {
    throw new Error(`Không đọc được nội dung ở bước ${func.name}. Hãy tải lại tiện ích và trang Studocu rồi thử lại.`);
  }
  return result.result;
}

function readableError(error) {
  const message = error?.message || String(error);
  if (/cannot access|permission|not been granted/i.test(message)) {
    return 'Tiện ích chưa được cấp quyền đọc trang Studocu. Mở chrome://extensions, tải lại Document Page Screenshot, cho phép quyền trên studocu.vn/studocu.com, rồi tạo job mới.';
  }
  if (/failed to fetch/i.test(message)) return 'Không kết nối được ứng dụng. Chạy python app.py tại 127.0.0.1:5000 rồi tạo job mới.';
  return message;
}

async function capture(tab, job) {
  let prepared = false;
  const keepAlive = setInterval(() => chrome.runtime.getPlatformInfo().catch(() => {}), 20000);
  try {
    await assertActive(tab);
    if (!isStudocu(tab.url)) throw new Error('Hãy quay lại tab tài liệu Studocu của job.');
    const {total} = await inject(tab.id, describeDocument);
    prepared = true;
    await event(job, {event: 'start', total});
    for (let index = 0; index < total; index++) {
      await assertActive(tab);
      await event(job, {event: 'progress', message: `Đang chụp Chrome trang ${index + 1}/${total}`});
      const rect = await inject(tab.id, positionPage, [index]);
      await assertActive(tab);
      const screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, {format: 'png'});
      // Discard the screenshot if the user switched tabs while capture was pending.
      await assertActive(tab);
      const image = await createImageBitmap(await (await fetch(screenshot)).blob());
      let png;
      try {
        const bounds = cropBounds(rect, image.width, image.height);
        const canvas = new OffscreenCanvas(bounds.width, bounds.height);
        canvas.getContext('2d').drawImage(image, bounds.left, bounds.top, bounds.width, bounds.height, 0, 0, bounds.width, bounds.height);
        png = await canvas.convertToBlob({type: 'image/png'});
      } finally { image.close(); }
      await api(job, `page/${index + 1}`, {method: 'POST', headers: {'Content-Type': 'image/png'}, body: png});
      if (!rect.hasContent) await event(job, {event: 'progress', message: `Trang ${index + 1} chưa hiện nội dung; đã lưu ảnh khung đang hiển thị.`});
      await chrome.action.setBadgeText({tabId: tab.id, text: `${index + 1}`});
    }
    await event(job, {event: 'finish'});
    await chrome.action.setBadgeText({tabId: tab.id, text: 'OK'});
    await chrome.storage.session.remove(`job:${tab.id}`);
  } catch (error) {
    const message = readableError(error);
    // Before starting, allow the user to finish loading/verification and click again.
    await event(job, {event: prepared ? 'error' : 'wait', message}).catch(() => {});
    await chrome.action.setBadgeText({tabId: tab.id, text: 'ERR'}).catch(() => {});
    await chrome.action.setTitle({tabId: tab.id, title: message}).catch(() => {});
    await inject(tab.id, showCaptureError, [message]).catch(() => {});
  } finally {
    clearInterval(keepAlive);
    if (prepared) await inject(tab.id, restoreDocument).catch(() => {});
    running.delete(tab.id);
  }
}

chrome.action.onClicked.addListener(async tab => {
  if (running.has(tab.id)) return;
  const key = `job:${tab.id}`;
  const job = (await chrome.storage.session.get(key))[key];
  if (!job) {
    await chrome.action.setBadgeText({tabId: tab.id, text: 'NEW'});
    await chrome.action.setTitle({tabId: tab.id, title: 'Tạo job tại http://127.0.0.1:5000 trước khi chụp.'});
    return;
  }
  running.add(tab.id);
  await capture(tab, job);
});

chrome.tabs.onRemoved.addListener(tabId => {
  const key = `job:${tabId}`;
  chrome.storage.session.get(key).then(async data => {
    if (data[key] && !running.has(tabId)) await event(data[key], {event: 'error', message: 'Bạn đã đóng tab tài liệu trước khi chụp.'}).catch(() => {});
    await chrome.storage.session.remove(key);
  });
});
