(() => {
  const match = location.pathname.match(/^\/chrome_capture\/([a-f0-9]{12})$/);
  if (!match || !location.hash) return;
  const token = location.hash.slice(1);
  const status = document.getElementById("extension-status");
  chrome.runtime.sendMessage({type: "prepare", jobId: match[1], token}, response => {
    if (chrome.runtime.lastError || !response?.ok) {
      status.textContent = response?.error || chrome.runtime.lastError?.message || "Không kết nối được ứng dụng.";
      return;
    }
    history.replaceState(null, "", location.pathname);
    status.textContent = "Đã mở tab tài liệu. Hãy bấm biểu tượng tiện ích trong tab đó để chụp.";
  });
})();
