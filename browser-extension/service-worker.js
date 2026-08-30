const CNL_LIMIT = 1024 * 1024;
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === "omajdownload-cnl-cancel") {
    const controller = controllers.get(controllerKey(sender, message.requestId));
    if (controller) controller.abort();
    return false;
  }
  const route = message && message.type === "omajdownload-cnl" ? clickNLoadRoute(message.url) : null;
  const method = String(message && message.method || "POST").toUpperCase();
  if (!route || method !== route.method) return false;
  const body = String(message.body || "");
  if (method === "POST" && new TextEncoder().encode(body).byteLength > CNL_LIMIT) {
    sendResponse({ ok: false, status: 413, message: "Click'n'Load request is too large" });
    return false;
  }

  (async () => {
    const requestId = String(message.requestId || "");
    const requestKey = controllerKey(sender, requestId);
    const controller = new AbortController();
    if (requestId) controllers.set(requestKey, controller);
    try {
      const options = { method, cache: "no-store", credentials: "omit", headers: {}, signal: controller.signal };
      if (method === "POST") {
        const tokenUrl = `${route.url.origin}/omajdownload/extension-token`;
        const tokenResponse = await fetch(tokenUrl, { cache: "no-store", credentials: "omit", signal: controller.signal });
        if (!tokenResponse.ok) throw new Error("OmaJD-Remote extension handshake failed");
        const token = await tokenResponse.text();
        const senderUrl = String(sender && (sender.url || (sender.tab && sender.tab.url)) || "");
        options.headers = {
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
          "X-OmaJDownLoad-Token": token,
          "X-OmaJDownLoad-Origin": senderUrl
        };
        options.body = body;
      }
      const response = await fetch(message.url, options);
      const responseBody = await response.text();
      sendResponse({ ok: response.ok, status: response.status, body: responseBody });
    } catch (error) {
      sendResponse({ ok: false, status: 0, message: String(error && error.message || error) });
    } finally {
      if (requestId && controllers.get(requestKey) === controller) controllers.delete(requestKey);
    }
  })();
  return true;
});
