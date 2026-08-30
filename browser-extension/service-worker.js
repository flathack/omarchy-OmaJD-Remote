const CNL_LIMIT = 1024 * 1024;

function isClickNLoadUrl(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "http:"
      && (url.hostname === "127.0.0.1" || url.hostname === "localhost")
      && url.port === "9666"
      && (url.pathname === "/flash/add" || url.pathname === "/flash/addcrypted2");
  } catch (_error) {
    return false;
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "omajdownload-cnl" || !isClickNLoadUrl(message.url)) return false;
  const body = String(message.body || "");
  if (new TextEncoder().encode(body).byteLength > CNL_LIMIT) {
    sendResponse({ ok: false, status: 413, message: "Click'n'Load request is too large" });
    return false;
  }

  fetch(message.url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
    body,
    cache: "no-store",
    credentials: "omit"
  }).then(async response => {
    const responseBody = await response.text();
    sendResponse({ ok: response.ok, status: response.status, body: responseBody });
  }).catch(error => {
    sendResponse({ ok: false, status: 0, message: String(error && error.message || error) });
  });
  return true;
});
