const REQUEST_MARKER = "omajdownload-cnl-request-v1";
const RESPONSE_MARKER = "omajdownload-cnl-response-v1";
const REQUEST_ID_BYTE_LIMIT = 128;
const REQUEST_URL_BYTE_LIMIT = 4096;
const REQUEST_BODY_BYTE_LIMIT = 1024 * 1024;
const MAX_PENDING_REQUESTS = 32;
const pending = new Set();

function rejectRequest(id, status, message) {
  window.postMessage({ marker: RESPONSE_MARKER, id: typeof id === "string" ? id : "", ok: false, status, message }, "*");
}

function clickNLoadRoute(value, method) {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:"
        || (url.hostname !== "127.0.0.1" && url.hostname !== "localhost")
        || url.port !== "9666") return null;
    if (method === "POST" && (url.pathname === "/flash/add" || url.pathname === "/flash/addcrypted2"))
      return { url: url.href, method: "POST", probe: false };
    if (method === "GET" && (url.pathname.replace(/\/$/, "") === "/flash" || url.pathname === "/jdcheck.js"))
      return { url: url.href, method: "GET", probe: true };
    return null;
  } catch (_error) {
    return null;
  }
}

function validateRequest(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) return { error: "Invalid Click'n'Load request" };
  if (request.type !== "request") return { error: "Invalid Click'n'Load request type" };
  if (typeof request.id !== "string" || !request.id || new TextEncoder().encode(request.id).byteLength > REQUEST_ID_BYTE_LIMIT)
    return { error: "Invalid Click'n'Load request ID" };
  if (typeof request.url !== "string" || typeof request.method !== "string" || typeof request.body !== "string" || typeof request.probe !== "boolean")
    return { id: request.id, error: "Invalid Click'n'Load request fields" };
  if (new TextEncoder().encode(request.url).byteLength > REQUEST_URL_BYTE_LIMIT)
    return { id: request.id, error: "Click'n'Load URL is too large" };
  const method = request.method.toUpperCase();
  const route = clickNLoadRoute(request.url, method);
  if (!route || request.probe !== route.probe) return { id: request.id, error: "Invalid Click'n'Load route" };
  if (new TextEncoder().encode(request.body).byteLength > REQUEST_BODY_BYTE_LIMIT)
    return { id: request.id, error: "Click'n'Load request is too large", status: 413 };
  return { id: request.id, route, method };
}

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
    if (typeof request.id !== "string" || new TextEncoder().encode(request.id).byteLength > REQUEST_ID_BYTE_LIMIT || !pending.has(request.id)) return;
    pending.delete(request.id);
    chrome.runtime.sendMessage({ type: "omajdownload-cnl-cancel", requestId: request.id });
    return;
  }
  const validated = validateRequest(request);
  if (validated.error) {
    rejectRequest(validated.id, validated.status || 400, validated.error);
    return;
  }
  if (pending.size >= MAX_PENDING_REQUESTS) {
    rejectRequest(validated.id, 429, "Too many concurrent Click'n'Load requests");
    return;
  }
  if (pending.has(validated.id)) {
    rejectRequest(validated.id, 409, "Duplicate Click'n'Load request ID");
    return;
  }
  pending.add(validated.id);
  chrome.runtime.sendMessage({
    type: "omajdownload-cnl",
    requestId: validated.id,
    url: validated.route.url,
    method: validated.method,
    body: request.body
  }, response => {
    pending.delete(validated.id);
    const runtimeError = chrome.runtime.lastError;
    const result = runtimeError
      ? { ok: false, status: 0, message: runtimeError.message }
      : (response || { ok: false, status: 0, message: "No response from browser extension" });
    window.postMessage({ marker: RESPONSE_MARKER, id: validated.id, ...result }, "*");
    if (!result.ok && request.probe !== true) showFailure(result.message || `Click'n'Load failed (${result.status})`);
  });
});
