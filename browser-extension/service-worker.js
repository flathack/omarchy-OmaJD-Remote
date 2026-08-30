const CNL_LIMIT = 1024 * 1024;

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
  const route = message && message.type === "omajdownload-cnl" ? clickNLoadRoute(message.url) : null;
  const method = String(message && message.method || "POST").toUpperCase();
  if (!route || method !== route.method) return false;
  const body = String(message.body || "");
  if (method === "POST" && new TextEncoder().encode(body).byteLength > CNL_LIMIT) {
    sendResponse({ ok: false, status: 413, message: "Click'n'Load request is too large" });
    return false;
  }

  (async () => {
    try {
      const options = { method, cache: "no-store", credentials: "omit", headers: {} };
      if (method === "POST") {
        const tokenUrl = `${route.url.origin}/omajdownload/extension-token`;
        const tokenResponse = await fetch(tokenUrl, { cache: "no-store", credentials: "omit" });
        if (!tokenResponse.ok) throw new Error("OmaJDownLoad extension handshake failed");
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
    }
  })();
  return true;
});
