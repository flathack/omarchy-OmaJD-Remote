const CNL_LIMIT = 1024 * 1024;
const MAX_CONCURRENT_REQUESTS = 32;
const REQUEST_TIMEOUT_MS = 10000;
const TOKEN_BYTE_LIMIT = 128;
const RESPONSE_BYTE_LIMIT = 64 * 1024;
const REQUEST_ID_LIMIT = 128;
const ORIGIN_BYTE_LIMIT = 2048;
const controllers = new Map();

function senderKey(sender) {
  const tabId = sender && sender.tab && sender.tab.id !== undefined ? sender.tab.id : "no-tab";
  const frameId = sender && sender.frameId !== undefined ? sender.frameId : 0;
  const documentId = String(sender && sender.documentId || sender && sender.url || "unknown-document");
  return `${tabId}:${frameId}:${documentId}`;
}

function controllerKey(sender, requestId) {
  return `${senderKey(sender)}:${String(requestId || "")}`;
}

function clickNLoadRoute(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== "http:"
        || (url.hostname !== "127.0.0.1" && url.hostname !== "localhost")
        || url.port !== "9666") return null;
    if (url.pathname === "/flash/add" || url.pathname === "/flash/addcrypted2") return { url, method: "POST" };
    if (url.pathname.replace(/\/$/, "") === "/flash" || url.pathname === "/jdcheck.js") return { url, method: "GET" };
    return null;
  } catch (_error) {
    return null;
  }
}

async function boundedText(response, limit, label) {
  const declared = response.headers.get("Content-Length");
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) {
    if (response.body) await response.body.cancel();
    throw new Error(`${label} is too large`);
  }
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let result = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error(`${label} is too large`);
      result += decoder.decode(value, { stream: true });
    }
    return result + decoder.decode();
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "omajdownload-cnl-cancel") {
    const controller = controllers.get(controllerKey(sender, message.requestId));
    if (controller) controller.abort();
    return false;
  }
  const route = message && message.type === "omajdownload-cnl" ? clickNLoadRoute(message.url) : null;
  const method = String(message && message.method || "POST").toUpperCase();
  if (!route || method !== route.method) return false;
  const requestId = String(message.requestId || "");
  if (!requestId || new TextEncoder().encode(requestId).byteLength > REQUEST_ID_LIMIT) {
    sendResponse({ ok: false, status: 400, message: "Invalid Click'n'Load request ID" });
    return false;
  }
  const body = String(message.body || "");
  if (method === "POST" && new TextEncoder().encode(body).byteLength > CNL_LIMIT) {
    sendResponse({ ok: false, status: 413, message: "Click'n'Load request is too large" });
    return false;
  }
  const requestKey = controllerKey(sender, requestId);
  const superseded = controllers.get(requestKey);
  if (!superseded && controllers.size >= MAX_CONCURRENT_REQUESTS) {
    sendResponse({ ok: false, status: 429, message: "Too many concurrent Click'n'Load requests" });
    return false;
  }
  if (superseded) superseded.abort();

  (async () => {
    const controller = new AbortController();
    controllers.set(requestKey, controller);
    const timeout = setTimeout(() => controller.abort("deadline"), REQUEST_TIMEOUT_MS);
    try {
      const options = { method, cache: "no-store", credentials: "omit", headers: {}, signal: controller.signal };
      if (method === "POST") {
        const tokenUrl = `${route.url.origin}/omajdownload/extension-token`;
        const tokenResponse = await fetch(tokenUrl, { cache: "no-store", credentials: "omit", signal: controller.signal });
        if (!tokenResponse.ok) throw new Error("OmaJD-Remote extension handshake failed");
        const token = await boundedText(tokenResponse, TOKEN_BYTE_LIMIT, "OmaJD-Remote extension token");
        if (!/^[0-9a-f]{32}$/.test(token)) throw new Error("OmaJD-Remote extension token is invalid");
        const senderUrl = String(sender && (sender.url || (sender.tab && sender.tab.url)) || "");
        if (new TextEncoder().encode(senderUrl).byteLength > ORIGIN_BYTE_LIMIT)
          throw new Error("Click'n'Load source URL is too large");
        options.headers = {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-OmaJDownLoad-Token": token,
          "X-OmaJDownLoad-Origin": senderUrl
        };
        options.body = body;
      }
      const response = await fetch(route.url.href, options);
      const responseBody = await boundedText(response, RESPONSE_BYTE_LIMIT, "Click'n'Load response");
      sendResponse({ ok: response.ok, status: response.status, body: responseBody });
    } catch (error) {
      const timedOut = controller.signal.aborted && controller.signal.reason === "deadline";
      sendResponse({
        ok: false,
        status: timedOut ? 408 : 0,
        message: timedOut ? "Click'n'Load request timed out" : String(error && error.message || error)
      });
    } finally {
      clearTimeout(timeout);
      if (controllers.get(requestKey) === controller) controllers.delete(requestKey);
    }
  })();
  return true;
});
