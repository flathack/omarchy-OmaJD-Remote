(() => {
  const REQUEST_MARKER = "omajdownload-cnl-request-v1";
  const RESPONSE_MARKER = "omajdownload-cnl-response-v1";
  const pending = new Map();
  let sequence = 0;

  function cnlUrl(value) {
    try {
      const url = new URL(String(value), document.baseURI);
      if (url.protocol !== "http:") return null;
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") return null;
      if (url.port !== "9666") return null;
      if (url.pathname !== "/flash/add" && url.pathname !== "/flash/addcrypted2") return null;
      return url.href;
    } catch (_error) {
      return null;
    }
  }

  function encodeBody(body) {
    if (body instanceof URLSearchParams) return body.toString();
    if (body instanceof FormData) {
      const params = new URLSearchParams();
      for (const [key, value] of body.entries()) params.append(key, typeof value === "string" ? value : value.name);
      return params.toString();
    }
    return String(body || "");
  }

  function forward(url, body) {
    const id = `${Date.now()}-${++sequence}`;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new TypeError("OmaJDownLoad did not answer"));
      }, 10000);
      pending.set(id, { resolve, reject, timeout });
      window.postMessage({ marker: REQUEST_MARKER, id, url, body: encodeBody(body) }, "*");
    });
  }

  window.addEventListener("message", event => {
    if (event.source !== window || !event.data || event.data.marker !== RESPONSE_MARKER) return;
    const entry = pending.get(event.data.id);
    if (!entry) return;
    clearTimeout(entry.timeout);
    pending.delete(event.data.id);
    if (event.data.ok) entry.resolve(event.data);
    else entry.reject(new TypeError(event.data.message || `OmaJDownLoad returned ${event.data.status}`));
  });

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init = {}) {
    const url = cnlUrl(typeof input === "string" || input instanceof URL ? input : input.url);
    if (!url) return nativeFetch(input, init);
    const body = init.body !== undefined ? init.body : (input instanceof Request ? input.body : "");
    return forward(url, body).then(result => new Response(result.body || "success\r\n", {
      status: result.status || 200,
      headers: { "Content-Type": "text/plain; charset=utf-8" }
    }));
  };

  document.addEventListener("submit", event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const url = cnlUrl(form.action);
    if (!url) return;
    event.preventDefault();
    forward(url, new FormData(form)).catch(() => {});
  }, true);

  const nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function() {
    const url = cnlUrl(this.action);
    if (!url) return nativeSubmit.call(this);
    forward(url, new FormData(this)).catch(() => {});
  };

  const NativeXHR = window.XMLHttpRequest;
  class OmaJDownLoadXHR extends NativeXHR {
    open(method, url, ...rest) {
      this.__omajUrl = String(method).toUpperCase() === "POST" ? cnlUrl(url) : null;
      if (!this.__omajUrl) return super.open(method, url, ...rest);
      this.__omajReadyState = 1;
    }
    setRequestHeader(name, value) {
      if (!this.__omajUrl) return super.setRequestHeader(name, value);
    }
    send(body) {
      if (!this.__omajUrl) return super.send(body);
      forward(this.__omajUrl, body).then(result => {
        this.__omajStatus = result.status || 200;
        this.__omajResponseText = result.body || "success\r\n";
        this.__omajReadyState = 4;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("load"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      }).catch(() => {
        this.__omajStatus = 0;
        this.__omajReadyState = 4;
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("error"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      });
    }
    get readyState() { return this.__omajUrl ? (this.__omajReadyState || 0) : super.readyState; }
    get status() { return this.__omajUrl ? (this.__omajStatus || 0) : super.status; }
    get responseText() { return this.__omajUrl ? (this.__omajResponseText || "") : super.responseText; }
    get response() { return this.__omajUrl ? (this.__omajResponseText || "") : super.response; }
  }
  window.XMLHttpRequest = OmaJDownLoadXHR;
})();
