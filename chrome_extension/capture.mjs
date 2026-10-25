// These functions are serialized by chrome.scripting; keep DOM helpers self-contained.
export function describeDocument() {
  try {
    document.getElementById('__dps_capture_error')?.remove();
    const text = (document.title + ' ' + (document.body?.innerText || '').slice(0, 3000)).toLowerCase();
    if (text.includes('access blocked') || text.includes('suspicious activity')) {
      throw new Error('Studocu đang chặn trang này. Hãy mở tài liệu thành công trong Chrome trước khi chụp.');
    }
    if (text.includes('verifying you are human') || text.includes('verify you are human')) {
      throw new Error('Hãy hoàn tất xác minh trong tab Chrome, rồi bấm lại biểu tượng tiện ích.');
    }
    const selectors = [
      '#page-container > [data-page-index] > .pf',
      '[data-testid="document-page"]', '[data-test="document-page"]',
      '[data-test-selector="document-page"]', '.document-page', '[data-page-number]',
    ];
    let frames = [];
    for (const selector of selectors) {
      frames = [...document.querySelectorAll(selector)].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width >= 80 && r.height >= 80;
      });
      if (frames.length) break;
    }
    if (!frames.length) throw new Error('Chưa tìm thấy khung giấy. Chờ tài liệu hiển thị rồi bấm lại tiện ích.');
    const previous = globalThis.__documentPageScreenshot;
    if (previous) {
      for (const [el, zoom] of previous.zooms) el.style.zoom = zoom;
    }
    globalThis.__documentPageScreenshot = {
      frames, zooms: new Map(), scrollX, scrollY,
    };
    return {total: frames.length};
  } catch (error) {
    // Chrome may return null instead of propagating an exception from an injected function.
    return {captureError: error.message || String(error)};
  }
}

export async function positionPage(index) {
  try {
    const state = globalThis.__documentPageScreenshot;
    const frame = state?.frames[index];
    if (!frame?.isConnected) throw new Error('Khung trang đã thay đổi. Hãy bắt đầu lại job.');
    const container = frame.closest('#page-container') || frame.parentElement;
    if (!state.zooms.has(container)) state.zooms.set(container, container.style.zoom);
    function fit() {
      const r = frame.getBoundingClientRect();
      const center = r.left + r.width / 2;
      let top = 64, bottom = 64;
      for (const el of document.querySelectorAll('header, nav, aside, section, div')) {
        if (el.contains(frame) || frame.contains(el)) continue;
        if (!['fixed', 'sticky'].includes(getComputedStyle(el).position)) continue;
        const box = el.getBoundingClientRect();
        if (box.left > center || box.right < center || box.height <= 0 || box.height > innerHeight / 2) continue;
        if (box.top <= innerHeight / 2 && box.bottom > 0) top = Math.max(top, box.bottom + 16);
        if (box.bottom >= innerHeight / 2 && box.top < innerHeight) bottom = Math.max(bottom, innerHeight - box.top + 16);
      }
      const available = Math.max(150, innerHeight - top - bottom);
      const scale = Math.min(1, (innerWidth - 64) / r.width, available / r.height);
      if (scale > 0 && scale < 1) {
        container.style.zoom = String((parseFloat(getComputedStyle(container).zoom) || 1) * scale);
      }
      frame.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
      const fitted = frame.getBoundingClientRect();
      window.scrollBy({top: fitted.top - top - Math.max(0, (available - fitted.height) / 2), behavior: 'instant'});
    }
    frame.scrollIntoView({block: 'center', behavior: 'instant'});
    fit();
    await new Promise(resolve => setTimeout(resolve, 700));
    fit();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const r = frame.getBoundingClientRect();
    const content = frame.querySelector('.page-content');
    return {
      x: r.left, y: r.top, width: r.width, height: r.height,
      viewportWidth: innerWidth, viewportHeight: innerHeight,
      hasContent: !content || (content.getClientRects().length > 0 &&
        (!!content.textContent.trim() || !!content.querySelector('img, canvas, svg'))),
    };
  } catch (error) {
    return {captureError: error.message || String(error)};
  }
}

export function showCaptureError(message) {
  document.getElementById('__dps_capture_error')?.remove();
  const panel = document.createElement('div');
  panel.id = '__dps_capture_error';
  panel.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;width:min(420px,90vw);';
  const shadow = panel.attachShadow({mode: 'closed'});
  const body = document.createElement('div');
  body.style.cssText = 'font:14px/1.5 system-ui;background:#fff;color:#202124;border:2px solid #d93025;border-radius:12px;box-shadow:0 8px 32px #0004;padding:18px;';
  const title = document.createElement('strong');
  title.textContent = 'Document Page Screenshot — chưa chụp được';
  const detail = document.createElement('p');
  detail.style.whiteSpace = 'pre-wrap';
  detail.textContent = message;
  const hint = document.createElement('p');
  hint.textContent = 'Xem tiến trình tại 127.0.0.1:5000. Nếu trang chưa tải xong, chờ rồi bấm lại biểu tượng tiện ích.';
  const close = document.createElement('button');
  close.textContent = 'Đóng thông báo';
  close.onclick = () => panel.remove();
  body.append(title, detail, hint, close);
  shadow.append(body);
  document.documentElement.append(panel);
}

export function restoreDocument() {
  const state = globalThis.__documentPageScreenshot;
  if (!state) return;
  for (const [el, zoom] of state.zooms) el.style.zoom = zoom;
  window.scrollTo({left: state.scrollX, top: state.scrollY, behavior: 'instant'});
  delete globalThis.__documentPageScreenshot;
}

export function cropBounds(rect, imageWidth, imageHeight) {
  if (![rect.x, rect.y, rect.width, rect.height, rect.viewportWidth, rect.viewportHeight, imageWidth, imageHeight].every(Number.isFinite) ||
      rect.width <= 0 || rect.height <= 0 || rect.viewportWidth <= 0 || rect.viewportHeight <= 0 ||
      imageWidth <= 0 || imageHeight <= 0 ||
      rect.x < -1 || rect.y < -1 || rect.x + rect.width > rect.viewportWidth + 1 || rect.y + rect.height > rect.viewportHeight + 1) {
    throw new Error('Khung trang chưa nằm trọn trong màn hình. Hãy phóng to cửa sổ Chrome rồi bắt đầu lại.');
  }
  const sx = imageWidth / rect.viewportWidth, sy = imageHeight / rect.viewportHeight;
  if (Math.abs(sx - sy) > Math.max(sx, sy) * 0.03) {
    throw new Error('Kích thước cửa sổ đã thay đổi trong lúc chụp. Hãy giữ kích thước Chrome ổn định và tạo job mới.');
  }
  const left = Math.max(0, Math.round(rect.x * sx));
  const top = Math.max(0, Math.round(rect.y * sy));
  return {
    left, top,
    width: Math.min(imageWidth - left, Math.round(rect.width * sx)),
    height: Math.min(imageHeight - top, Math.round(rect.height * sy)),
  };
}
