const REQUEST_MARKER = "omajdownload-cnl-request-v1";
const RESPONSE_MARKER = "omajdownload-cnl-response-v1";

function showFailure(message) {
  const old = document.getElementById("omajdownload-cnl-error");
  if (old) old.remove();
  const notice = document.createElement("div");
  notice.id = "omajdownload-cnl-error";
  notice.setAttribute("role", "alert");
  notice.textContent = `OmaJD-Remote: ${message}`;
  Object.assign(notice.style, {
    position: "fixed", top: "16px", left: "50%", transform: "translateX(-50%)",
    zIndex: "2147483647", padding: "10px 14px", borderRadius: "8px",
    color: "#fff", background: "#9f2936", font: "600 14px system-ui, sans-serif",
    boxShadow: "0 8px 30px rgba(0,0,0,.35)"
  });
  (document.body || document.documentElement).appendChild(notice);
  setTimeout(() => notice.remove(), 7000);
}

window.addEventListener("message", event => {
  if (event.source !== window || !event.data || event.data.marker !== REQUEST_MARKER) return;
  const request = event.data;
  if (request.type === "cancel") {
    chrome.runtime.sendMessage({ type: "omajdownload-cnl-cancel", requestId: request.id });
    return;
  }
  chrome.runtime.sendMessage({
    type: "omajdownload-cnl",
    requestId: request.id,
    url: request.url,
    method: request.method,
    body: request.body
  }, response => {
    const runtimeError = chrome.runtime.lastError;
    const result = runtimeError
      ? { ok: false, status: 0, message: runtimeError.message }
      : (response || { ok: false, status: 0, message: "No response from browser extension" });
    window.postMessage({ marker: RESPONSE_MARKER, id: request.id, ...result }, "*");
    if (!result.ok && request.probe !== true) showFailure(result.message || `Click'n'Load failed (${result.status})`);
  });
});
